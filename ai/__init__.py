"""AI Engine — supports Ollama (local) and Claude (Anthropic API).

Use get_ai_client() everywhere instead of importing OllamaClient or
ClaudeClient directly. The correct client is chosen based on AI_PROVIDER
in .env:
  AI_PROVIDER=claude   →  ClaudeClient  (requires CLAUDE_API_KEY)
  AI_PROVIDER=ollama   →  OllamaClient  (requires Ollama running locally)
"""


def get_ai_client():
    """
    Return the configured AI client.

    Reads AI_PROVIDER from .env:
      'claude'  → ClaudeClient (Anthropic API — needs CLAUDE_API_KEY)
      'ollama'  → OllamaClient (local Ollama server — needs ollama serve)

    Raises ValueError if Claude is selected but CLAUDE_API_KEY is missing.
    """
    from config.settings import SETTINGS

    provider = SETTINGS.ai_provider.lower()

    if provider == "claude":
        from ai.claude_client import ClaudeClient
        if not SETTINGS.claude_api_key:
            raise ValueError(
                "AI_PROVIDER=claude but CLAUDE_API_KEY is not set in .env. "
                "Add: CLAUDE_API_KEY=sk-ant-... to your .env file."
            )
        return ClaudeClient(
            api_key=SETTINGS.claude_api_key,
            model=SETTINGS.claude_model,
        )

    # Default: Ollama
    from ai.ollama_client import OllamaClient
    return OllamaClient(
        base_url=SETTINGS.ollama_base_url,
        model=SETTINGS.ollama_model,
    )
