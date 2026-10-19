"""Ollama client construction and option sanitising."""

from typing import Any, Dict, Optional

import ollama

from backend import user_settings
from backend.config import OLLAMA_HOST


def get_host() -> str:
    """Active Ollama host: the in-app override if set, else the env default."""
    return user_settings.host_override() or OLLAMA_HOST


def make_client() -> ollama.AsyncClient:
    """Client carrying the configured API key, if any.

    Ollama cloud models authenticate with the key stored by ``ollama signin``,
    but a remote host or a second account needs an explicit bearer token.
    """
    headers = user_settings.auth_headers()
    if headers:
        return ollama.AsyncClient(host=get_host(), headers=headers)
    return ollama.AsyncClient(host=get_host())


def sanitize_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop option values that Ollama Cloud rejects.

    Local models treat ``num_predict: -1`` as "unlimited", but cloud models are
    proxied through an OpenAI-compatible backend that requires a *positive*
    ``max_tokens`` and fails with::

        max_tokens must be positive, got: -1

    Simply omitting the key restores Ollama's own unlimited default, which
    every backend accepts. ``None`` values are stripped for the same reason.
    """
    cleaned: Dict[str, Any] = {}

    for key, value in (options or {}).items():
        if value is None:
            continue
        if key == "num_predict":
            try:
                if int(value) <= 0:
                    continue  # unlimited -> let Ollama apply its own default
            except (TypeError, ValueError):
                continue
        cleaned[key] = value

    return cleaned


def friendly_error(exc: Exception) -> str:
    """Turn common Ollama failures into something actionable."""
    text = str(exc)

    if "max_tokens must be positive" in text:
        return (
            "This model rejected the reply-length limit (num_predict). "
            "This normally happens when a stale value is stored on the "
            "conversation - update it in Settings → Parameters, or pick the "
            "model again in a new chat."
        )

    lowered = text.lower()
    if "401" in text or "unauthorized" in lowered or "api key" in lowered:
        return (
            "Ollama rejected the request as unauthenticated (401). Cloud models "
            "need a signed-in account: run 'ollama signin' in a terminal, or add "
            "an API key under Settings → Connection."
        )
    if "402" in text or "payment" in lowered or "credit" in lowered:
        return (
            "Ollama rejected the request for billing reasons (402). This model "
            "is not included in your plan - check your Ollama subscription."
        )
    if "404" in text and "not found" in lowered:
        return "Ollama does not recognise that model. Try pulling it first."

    return text