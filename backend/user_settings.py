"""Persisted user settings that should not live in the chat database.

Stored as JSON under ``backend/data/`` which is gitignored. The Ollama API key
is write-only from the UI's point of view: it can be set, cleared, and reported
as present/absent, but is never returned to the browser.
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None

DEFAULTS: Dict[str, Any] = {
    "ollama_api_key": "",
    "ollama_host_override": "",
}


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    data = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass
    _cache = data
    return data


def _save(data: Dict[str, Any]) -> None:
    global _cache
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _cache = data


def get(key: str, default: Any = None) -> Any:
    with _lock:
        return _load().get(key, default)


def set_many(values: Dict[str, Any]) -> None:
    with _lock:
        data = dict(_load())
        for k, v in values.items():
            if v is None:
                data[k] = ""
            else:
                data[k] = v
        _save(data)


def clear(key: str) -> None:
    set_many({key: ""})


def api_key() -> str:
    return (get("ollama_api_key") or "").strip()


def host_override() -> str:
    return (get("ollama_host_override") or "").strip().rstrip("/")


def auth_headers() -> Dict[str, str]:
    """Authorization header for Ollama, if a key is configured."""
    key = api_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def public_view() -> Dict[str, Any]:
    """Settings safe to send to the browser (never includes the key itself)."""
    key = api_key()
    return {
        "has_api_key": bool(key),
        "key_hint": (f"...{key[-4:]}" if len(key) >= 4 else ("set" if key else "")),
        "host_override": host_override(),
    }