"""
Anthropic Claude Client.

Drop-in replacement for OllamaClient that uses the Anthropic Claude API.
Exposes the same generate() and generate_json() interface so every AI
module (job_matcher, resume_tailor, scorer …) works without modification.

Usage:
    from ai.claude_client import ClaudeClient

    client = ClaudeClient()
    text   = client.generate("Explain quantum computing in one sentence.")
    data   = client.generate_json("Return a JSON object with key 'answer'.")
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import anthropic


@dataclass
class ClaudeClient:
    """
    Synchronous client for the Anthropic Claude API.

    Parameters:
        api_key   — Anthropic API key (reads CLAUDE_API_KEY from env if omitted)
        model     — Claude model to use  (default: claude-sonnet-4-5)
        max_tokens — Maximum tokens in the response (default: 4096)
        retries   — Number of retry attempts on transient failures (default: 2)
    """

    api_key: str = ""
    model: str = "claude-sonnet-4-5"
    max_tokens: int = 4096
    retries: int = 2

    # Internal client — created lazily so the dataclass __init__ stays clean
    _client: Optional[anthropic.Anthropic] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            import os
            self.api_key = os.getenv("CLAUDE_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "No Claude API key provided. Set CLAUDE_API_KEY in your .env file."
            )
        self._client = anthropic.Anthropic(api_key=self.api_key)

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """
        Return True if the Claude client is configured and ready.

        Does NOT make a real API call (to avoid wasting credits on startup).
        The API key format and connectivity are validated on the first
        actual generate() call.
        """
        return bool(self.api_key and self._client is not None)

    # ── Text generation ───────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str:
        """
        Send a prompt to Claude and return the generated text.
        Raises RuntimeError on permanent failure after retries.
        """
        messages = [{"role": "user", "content": prompt}]

        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        if system_prompt:
            kwargs["system"] = system_prompt

        # Claude temperature range is 0.0–1.0
        kwargs["temperature"] = max(0.0, min(1.0, temperature))

        last_error: Optional[str] = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._client.messages.create(**kwargs)
                return response.content[0].text.strip()

            except anthropic.RateLimitError as exc:
                last_error = f"Rate limit: {exc}"
            except anthropic.APIStatusError as exc:
                last_error = f"API error {exc.status_code}: {exc.message}"
            except anthropic.APIConnectionError as exc:
                last_error = f"Connection error: {exc}"
            except Exception as exc:
                last_error = f"Unexpected error: {exc}"

            if attempt < self.retries:
                wait = 2 ** attempt
                time.sleep(wait)

        raise RuntimeError(
            f"Claude generation failed after {self.retries} attempts. "
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
                f"Claude returned non-JSON response. Raw output:\n{raw[:500]}"
            )
