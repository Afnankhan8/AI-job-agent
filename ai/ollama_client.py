"""
Ollama HTTP Client.

Thin wrapper around the Ollama REST API that every AI module uses.
Handles connection errors, timeouts, retries, and JSON extraction
so the caller never has to deal with HTTP details.

Usage:
    from ai.ollama_client import OllamaClient

    client = OllamaClient()
    text   = client.generate("Explain quantum computing in one sentence.")
    data   = client.generate_json("Return a JSON object with key 'answer'.")
"""

import json
import re
import time
import requests
from dataclasses import dataclass
from typing import Optional


@dataclass
class OllamaClient:
    """
    Synchronous client for the local Ollama instance.

    Parameters:
        base_url  — Ollama server URL  (default: http://localhost:11434)
        model     — Model tag to use   (default: qwen3-coder:30b)
        timeout   — HTTP timeout in seconds per request  (default: 300)
        retries   — Number of retry attempts on transient failures (default: 2)
    """

    base_url: str = "http://localhost:11434"
    model: str = "qwen3-coder:30b"
    timeout: int = 300
    retries: int = 2

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Return True if Ollama is reachable and the configured model is loaded."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            if resp.status_code != 200:
                return False
            models = [m["name"] for m in resp.json().get("models", [])]
            return self.model in models
        except Exception:
            return False

    # ── Text generation ───────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str:
        """
        Send a prompt to Ollama and return the generated text.
        Raises RuntimeError on permanent failure after retries.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 4096,
            },
        }
        if system_prompt:
            payload["system"] = system_prompt

        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "").strip()

                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            except requests.ConnectionError as exc:
                last_error = f"Connection error: {exc}"
            except requests.Timeout as exc:
                last_error = f"Timeout after {self.timeout}s: {exc}"
            except Exception as exc:
                last_error = f"Unexpected error: {exc}"

            if attempt < self.retries:
                wait = 2 ** attempt
                time.sleep(wait)

        raise RuntimeError(
            f"Ollama generation failed after {self.retries} attempts. "
            f"Last error: {last_error}"
        )

    # ── Structured JSON generation ────────────────────────────────────────────

    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
    ) -> dict:
        """
        Like generate(), but parses the response as JSON.

        The prompt MUST ask the model to reply with a JSON object.
        Uses a lower temperature by default for more deterministic output.
        If the model wraps JSON in markdown fences, they are stripped.
        """
        raw = self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )

        # Strip thinking tags if present (Qwen3 sometimes wraps reasoning)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # Strip markdown code fences: ```json ... ``` or ``` ... ```
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract the first JSON object from the text
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            raise ValueError(
                f"Ollama returned non-JSON response. Raw output:\n{raw[:500]}"
            )
