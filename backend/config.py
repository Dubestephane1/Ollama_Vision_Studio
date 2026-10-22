"""Central configuration.

Every tunable lives here so behaviour can be changed without hunting through
route handlers. All values can be overridden with environment variables.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
PROJECT_DIR = BASE_DIR.parent

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "chat_history.db"
LOG_DIR = BASE_DIR / "logs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- Ollama ----
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "openbmb/minicpm-v4.7:1b")

# The default system prompt. Users can override this per conversation.
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, accurate assistant running locally on the user's machine.\n"
    "- Be concise and direct. Use markdown for structure.\n"
    "- If a task requires live or factual data you do not have, use one of the "
    "tools available to you rather than guessing.\n"
    "- Never claim to have searched, looked something up, or accessed a website "
    "unless a tool result is actually present in this conversation."
)

# ------------------------------------------------------------ Tool loop ----
# A model that can call tools may loop indefinitely; cap the rounds.
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "5"))

# How many times to probe a model for tool-calling support (cached per model).
TOOL_PROBE_TIMEOUT = 20.0

# Cap on the tokens the tool-support probe may generate. The probe only needs
# the model to emit a short tool call; without a cap a model that cannot call
# tools rambles for a whole generation and the probe costs several seconds.
TOOL_PROBE_MAX_TOKENS = int(os.getenv("TOOL_PROBE_MAX_TOKENS", "24"))

# ------------------------------------------------------------- Context -----
# How many trailing messages (user+assistant pairs) are replayed to the model.
HISTORY_MESSAGES = int(os.getenv("HISTORY_MESSAGES", "10"))

# How many of the most recent *turns* may carry image attachments forward.
# Re-sending every historical image is the dominant cost in a vision chat.
IMAGE_HISTORY_TURNS = int(os.getenv("IMAGE_HISTORY_TURNS", "2"))

# Rough chars-per-token used for context trimming heuristics.
CHARS_PER_TOKEN = 4

# -------------------------------------------------------------- Uploads ----
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
MAX_IMAGES_PER_MESSAGE = int(os.getenv("MAX_IMAGES_PER_MESSAGE", "8"))
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
# Unreferenced uploads older than this are swept at startup.
UPLOAD_RETENTION_DAYS = int(os.getenv("UPLOAD_RETENTION_DAYS", "30"))

# --------------------------------------------------------------- Server ----
HOST = os.getenv("APP_HOST", "127.0.0.1")
PORT = int(os.getenv("APP_PORT", "8000"))

# Never wildcard by default. This service can execute arbitrary Python, so any
# website the user visits must not be able to drive it.
_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    f"http://localhost:{PORT}",
    f"http://127.0.0.1:{PORT}",
]
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("APP_CORS_ORIGINS", ",".join(_default_origins)).split(",")
    if o.strip()
]

# Optional shared secret. When set, every /api request must send X-App-Token.
APP_TOKEN = os.getenv("APP_TOKEN", "").strip()

# When true, requests whose Host header is not localhost are rejected. This
# blocks DNS-rebinding attacks from a malicious page.
CHECK_LOCAL_HOST = os.getenv("APP_CHECK_LOCAL_HOST", "1") not in ("0", "false", "False")

# ------------------------------------------------------------- Defaults ----
DEFAULT_MODEL_OPTIONS = {
    "temperature": 0.7,
    "top_p": 0.9,
    "num_predict": -1,
    "num_ctx": 4096,
    "seed": None,
}

TOOL_MODE_AUTO = "auto"
TOOL_MODE_ON = "on"
TOOL_MODE_OFF = "off"
VALID_TOOL_MODES = (TOOL_MODE_AUTO, TOOL_MODE_ON, TOOL_MODE_OFF)