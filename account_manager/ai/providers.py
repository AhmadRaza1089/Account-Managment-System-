"""Provider implementations.

Each provider is optional. Claude and OpenAI need their own package
installed; Ollama needs nothing but a local Ollama server, which makes it
the option that costs nothing and keeps your financial data on your own
machine.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from ..config import Settings
from .base import AIError, AIProvider, AIUnavailable

logger = logging.getLogger(__name__)

PROVIDERS = ("claude", "openai", "ollama")


class ClaudeProvider:
    """Anthropic's Claude, via the official SDK."""

    name = "claude"
    DEFAULT_MODEL = "claude-opus-5"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise AIUnavailable(
                "The 'anthropic' package is not installed. "
                'Install it with: pip install "account-manager[claude]"'
            ) from exc

        self.model = model or self.DEFAULT_MODEL
        # With no api_key the SDK resolves ANTHROPIC_API_KEY or a stored
        # `ant auth login` profile itself.
        self._client = (
            anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        )

    def complete_json(self, *, system: str, prompt: str, schema: dict) -> dict:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            # Low effort: these are short extraction tasks, not reasoning ones.
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise AIError("Claude returned no text content.")
        return json.loads(text)

    def complete_text(self, *, system: str, prompt: str, max_tokens: int = 1024) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low"},
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()


class OpenAIProvider:
    """OpenAI, via the official SDK."""

    name = "openai"
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable(
                "The 'openai' package is not installed. "
                'Install it with: pip install "account-manager[openai]"'
            ) from exc

        self.model = model or self.DEFAULT_MODEL
        self._client = OpenAI(api_key=api_key) if api_key else OpenAI()

    def complete_json(self, *, system: str, prompt: str, schema: dict) -> dict:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            },
        )
        content = response.choices[0].message.content
        if not content:
            raise AIError("OpenAI returned an empty response.")
        return json.loads(content)

    def complete_text(self, *, system: str, prompt: str, max_tokens: int = 1024) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return (response.choices[0].message.content or "").strip()


class OllamaProvider:
    """A local model served by Ollama.

    Free, offline, and nothing leaves the machine — which matters when the
    data is a company's finances. Uses plain HTTP, so it needs no extra
    Python packages.
    """

    name = "ollama"
    DEFAULT_MODEL = "llama3.1"
    DEFAULT_HOST = "http://localhost:11434"

    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.host = (host or self.DEFAULT_HOST).rstrip("/")

    def _chat(self, payload: dict) -> str:
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read())
        except urllib.error.URLError as exc:
            raise AIUnavailable(
                f"Could not reach Ollama at {self.host}. Is it running? "
                "Start it with: ollama serve"
            ) from exc
        return body.get("message", {}).get("content", "")

    def complete_json(self, *, system: str, prompt: str, schema: dict) -> dict:
        content = self._chat(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "format": schema,  # Ollama constrains output to a JSON schema
                "stream": False,
            }
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIError(f"Ollama did not return valid JSON: {content[:200]}") from exc

    def complete_text(self, *, system: str, prompt: str, max_tokens: int = 1024) -> str:
        return self._chat(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "options": {"num_predict": max_tokens},
                "stream": False,
            }
        ).strip()


def get_provider(settings: Settings | None = None) -> AIProvider:
    """Build the configured provider, or explain what's missing."""
    settings = settings or Settings.from_env()
    choice = (settings.ai_provider or "").strip().lower()

    if not choice or choice == "none":
        raise AIUnavailable(
            "No AI provider configured. Set AI_PROVIDER to one of: "
            f"{', '.join(PROVIDERS)}. See .env.example for the options."
        )

    if choice == "claude":
        return ClaudeProvider(model=settings.ai_model, api_key=settings.anthropic_api_key)
    if choice == "openai":
        return OpenAIProvider(model=settings.ai_model, api_key=settings.openai_api_key)
    if choice == "ollama":
        return OllamaProvider(model=settings.ai_model, host=settings.ollama_host)

    raise AIUnavailable(
        f"Unknown AI_PROVIDER '{choice}'. Valid options: {', '.join(PROVIDERS)}."
    )


def available_providers() -> dict[str, bool]:
    """Which providers could actually run right now, for diagnostics."""
    import importlib.util

    return {
        "claude": importlib.util.find_spec("anthropic") is not None,
        "openai": importlib.util.find_spec("openai") is not None,
        "ollama": True,  # only needs a running server, no package
    }
