"""
applier/browser_utils.py — Safe, collision-free Playwright browser launcher.

Prevents "Opening in existing browser session" errors by:
1. Cleaning stale lock files left behind by interrupted Chromium runs.
2. Gracefully creating an isolated profile copy if another active process holds a live lock.
"""

import os
import shutil
import tempfile
import atexit
from pathlib import Path
from typing import Tuple
from playwright.sync_api import Playwright, BrowserContext


def cleanup_profile_locks(user_data_dir: str | Path) -> None:
    """Remove stale Chromium lock files if present."""
    profile_path = Path(user_data_dir)
    if not profile_path.exists():
        return

    lock_files = [
        "SingletonLock",
        "SingletonCookie",
        "SingletonSocket",
        "lockfile",
        "parent.lock",
    ]
    for filename in lock_files:
        lock = profile_path / filename
        if lock.exists() or lock.is_symlink():
            try:
                lock.unlink(missing_ok=True)
            except Exception:
                pass


def launch_safe_context(
    p: Playwright,
    user_data_dir: str | Path = "browser_profile",
    headless: bool = True,
    viewport: dict | None = None,
    user_agent: str | None = None,
    extra_args: list[str] | None = None,
) -> Tuple[BrowserContext, bool]:
    """
    Safely launch a persistent browser context.

    If `user_data_dir` is locked by another running Chrome process,
    creates a temporary isolated profile directory with copied session cookies/storage.

    Returns:
        (BrowserContext, is_temp_copy)
    """
    base_dir = Path(user_data_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    viewport = viewport or {"width": 1280, "height": 900}
    user_agent = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if extra_args:
        args.extend(extra_args)

    # 1. Try main user_data_dir after clearing stale locks
    cleanup_profile_locks(base_dir)
    try:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(base_dir),
            headless=headless,
            args=args,
            user_agent=user_agent,
            viewport=viewport,
            locale="en-US",
        )
        return ctx, False
    except Exception as e:
        err_str = str(e)
        if "existing browser session" not in err_str.lower() and "already in use" not in err_str.lower():
            raise e

        # 2. Main profile is locked by another process — fallback to temporary session dir
        temp_dir = Path(tempfile.mkdtemp(prefix="browser_profile_fallback_"))
        
        # Copy session state (Cookies & Local Storage) if existing
        default_src = base_dir / "Default"
        if default_src.exists():
            default_dst = temp_dir / "Default"
            default_dst.mkdir(parents=True, exist_ok=True)
            for item in ["Cookies", "Cookies-journal", "Local Storage", "Network"]:
                src_item = default_src / item
                dst_item = default_dst / item
                if src_item.is_file():
                    try:
                        shutil.copy2(src_item, dst_item)
                    except Exception:
                        pass
                elif src_item.is_dir():
                    try:
                        shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                    except Exception:
                        pass

        cleanup_profile_locks(temp_dir)

        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(temp_dir),
            headless=headless,
            args=args,
            user_agent=user_agent,
            viewport=viewport,
            locale="en-US",
        )

        # Clean up temp dir when process exits or context closes
        def _cleanup():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

        atexit.register(_cleanup)
        return ctx, True
