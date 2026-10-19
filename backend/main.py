import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import ollama
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import database as db
from backend import user_settings
from backend.chat import ChatConfig, run_agent, sse
from backend.config import (
    APP_TOKEN,
    CHECK_LOCAL_HOST,
    CORS_ORIGINS,
    DEFAULT_MODEL,
    DEFAULT_MODEL_OPTIONS,
    DEFAULT_SYSTEM_PROMPT,
    FRONTEND_DIST,
    HOST,
    LOG_DIR,
    MAX_IMAGES_PER_MESSAGE,
    PORT,
    TOOL_MODE_OFF,
    UPLOAD_DIR,
    VALID_TOOL_MODES,
)
from backend.ollama_client import friendly_error, get_host, make_client
from backend.skills.base import auto_discover_skills, registry
from backend.uploads import UploadError, enforce_image_limit, save_upload, sweep_orphaned_uploads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("studio")

# Conversations with a generation currently in flight, used by /api/chat/stop.
_active_stops: Dict[str, asyncio.Event] = {}


def format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(size_bytes)
    while val >= 1024.0 and i < len(units) - 1:
        val /= 1024.0
        i += 1
    return f"{val:.1f} {units[i]}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    log.info("skills directory: %s", UPLOAD_DIR.parent / "skills")
    auto_discover_skills(str(UPLOAD_DIR.parent / "skills"), "backend.skills")
    log.info("registered skills: %s", ", ".join(sorted(registry.skills)) or "(none)")

    try:
        refs = await db.referenced_images()
        removed = await sweep_orphaned_uploads(refs)
        if removed:
            log.info("swept %d orphaned upload(s)", removed)
    except Exception as e:
        log.warning("upload sweep failed: %s", e)

    yield


app = FastAPI(
    title="Ollama Vision Studio API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # never "*": this service can execute Python
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-App-Token"],
)


@app.middleware("http")
async def security_gate(request: Request, call_next):
    """Block DNS rebinding and (optionally) require a shared token."""
    if request.url.path.startswith("/api"):
        if CHECK_LOCAL_HOST:
            host = (request.headers.get("host") or "").split(":")[0].strip("[]")
            if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": f"Host '{host}' is not permitted."},
                )

        if APP_TOKEN and request.headers.get("x-app-token") != APP_TOKEN:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid X-App-Token."},
            )

    return await call_next(request)


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


# ---------------------------------------------------------------- schemas --
class CreateConversationRequest(BaseModel):
    title: Optional[str] = "New Chat"
    model: Optional[str] = DEFAULT_MODEL
    system_prompt: Optional[str] = None


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    tool_mode: Optional[str] = None
    pinned: Optional[bool] = None


class ExecuteSkillRequest(BaseModel):
    skill_name: str
    args: Dict[str, Any] = {}


class ChatRequest(BaseModel):
    conversation_id: str
    prompt: str
    image_paths: Optional[List[str]] = []
    model: Optional[str] = None
    enabled_skills: Optional[List[str]] = None
    system_prompt: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    tool_mode: Optional[str] = None


class PullModelRequest(BaseModel):
    model_name: str


class EditMessageRequest(BaseModel):
    content: str
    images: Optional[List[str]] = None


def extract_model_details(m: Any) -> Dict[str, Any]:
    details = getattr(m, "details", None)
    mod_at = getattr(m, "modified_at", None)
    return {
        "name": getattr(m, "model", str(m)),
        "size": getattr(m, "size", 0) or 0,
        "size_formatted": format_size(getattr(m, "size", 0) or 0),
        "parameter_size": getattr(details, "parameter_size", "") or "",
        "quantization": getattr(details, "quantization_level", "") or "",
        "family": getattr(details, "family", "") or "",
        "modified_at": mod_at.isoformat() if hasattr(mod_at, "isoformat") else None,
    }


async def _fetch_models() -> Dict[str, Any]:
    models, ollama_ok, error_msg = [], False, None
    try:
        client = make_client()
        tags = await client.list()
        models = [extract_model_details(m) for m in tags.models]
        ollama_ok = True
    except Exception as e:
        error_msg = friendly_error(e)

    host = get_host()
    return {
        "ollama_online": ollama_ok,
        "ollama_host": host,
        "models": models,
        "default_model": DEFAULT_MODEL,
        "error": error_msg,
        "cloud_models": [m["name"] for m in models if ":cloud" in m["name"]],
        **user_settings.public_view(),
    }


# ----------------------------------------------------------------- system --
@app.get("/api/health")
async def health_check():
    data = await _fetch_models()
    return {
        "status": "online",
        "default_model": DEFAULT_MODEL,
        "default_system_prompt": DEFAULT_SYSTEM_PROMPT,
        "default_options": DEFAULT_MODEL_OPTIONS,
        **data,
    }


@app.get("/api/config")
async def get_config():
    return {
        "max_images_per_message": MAX_IMAGES_PER_MESSAGE,
        "tool_modes": list(VALID_TOOL_MODES),
        "default_model": DEFAULT_MODEL,
        "auth_required": bool(APP_TOKEN),
    }


class OllamaCredentials(BaseModel):
    api_key: Optional[str] = None
    host: Optional[str] = None


@app.get("/api/ollama/credentials")
async def get_credentials():
    """Report auth status. The key itself is never sent back to the browser."""
    return {**user_settings.public_view(), "ollama_host": get_host()}


@app.put("/api/ollama/credentials")
async def set_credentials(req: OllamaCredentials):
    """Set or clear the API key and/or host override."""
    updates: Dict[str, Any] = {}
    if req.api_key is not None:
        updates["ollama_api_key"] = req.api_key.strip()
    if req.host is not None:
        updates["ollama_host_override"] = req.host.strip()
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    user_settings.set_many(updates)
    log.info(
        "Ollama credentials updated (key=%s, host=%s)",
        "set" if user_settings.api_key() else "cleared",
        get_host(),
    )

    status = await _fetch_models()
    return {**user_settings.public_view(), **{k: status[k] for k in ("ollama_online", "ollama_host", "cloud_models", "error")}}


@app.delete("/api/ollama/credentials")
async def clear_credentials():
    user_settings.set_many({"ollama_api_key": "", "ollama_host_override": ""})
    return {**user_settings.public_view(), "status": "cleared"}


@app.post("/api/ollama/verify")
async def verify_credentials(payload: Optional[str] = Form(None)):
    """Test credentials against Ollama without persisting them.

    A local daemon that is already signed in ignores the Authorization header
    and uses its own stored key, so a plain model listing proves nothing. To
    give an honest answer we also run a one-token generation against a cloud
    model when one is installed.
    """
    import ollama as _ollama

    host = get_host()
    key = (payload or "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    def build():
        return (
            _ollama.AsyncClient(host=host, headers=headers)
            if headers
            else _ollama.AsyncClient(host=host)
        )

    try:
        client = build()
        names = [m.model for m in (await client.list()).models]
    except Exception as e:
        return {"ok": False, "host": host, "error": friendly_error(e)}

    cloud = [n for n in names if ":cloud" in n]
    result = {
        "ok": True,
        "host": host,
        "model_count": len(names),
        "cloud_models": cloud,
        "cloud_auth_ok": None,
    }

    if cloud:
        # Try a few cloud models: one may not be in the account's plan even
        # when others are, so a single failure would be misleading.
        last_error = None
        for candidate in cloud[:3]:
            try:
                await build().chat(
                    model=candidate,
                    messages=[{"role": "user", "content": "hi"}],
                    options={"num_predict": 1},
                )
                result["cloud_auth_ok"] = True
                result["cloud_tested"] = candidate
                last_error = None
                break
            except Exception as e:
                last_error = friendly_error(e)
        if last_error is not None:
            result["cloud_auth_ok"] = False
            result["cloud_error"] = last_error

    if not cloud:
        result["note"] = (
            "No :cloud models installed, so cloud authentication was not exercised."
        )

    return result


# ----------------------------------------------------------------- models --
@app.get("/api/models")
async def get_available_models():
    return await _fetch_models()


@app.post("/api/models/pull")
async def pull_ollama_model(req: PullModelRequest):
    name = req.model_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="model_name is required")

    async def gen():
        try:
            client = make_client()
            stream = await client.pull(model=name, stream=True)
            async for chunk in stream:
                yield sse(
                    {
                        "type": "progress",
                        "status": chunk.get("status", ""),
                        "total": chunk.get("total", 0),
                        "completed": chunk.get("completed", 0),
                        "done": False,
                    }
                )
            yield sse({"type": "progress", "status": "Complete", "done": True})
        except Exception as e:
            yield sse({"type": "progress", "status": f"Error: {e}", "done": True, "error": True})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------- conversations --
@app.get("/api/conversations")
async def list_conversations():
    return {"conversations": await db.get_all_conversations()}


@app.post("/api/conversations")
async def create_new_conversation(req: CreateConversationRequest):
    conv_id = await db.create_conversation(
        title=req.title or "New Chat",
        model=req.model or DEFAULT_MODEL,
        system_prompt=req.system_prompt,
    )
    return {"id": conv_id, "title": req.title, "model": req.model or DEFAULT_MODEL}


@app.get("/api/conversations/{conv_id}")
async def get_single_conversation(conv_id: str):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation": conv, "messages": await db.get_messages(conv_id)}


@app.patch("/api/conversations/{conv_id}")
async def update_conversation_endpoint(conv_id: str, req: UpdateConversationRequest):
    if req.tool_mode is not None and req.tool_mode not in VALID_TOOL_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"tool_mode must be one of {', '.join(VALID_TOOL_MODES)}",
        )
    await db.update_conversation(
        conv_id,
        title=req.title,
        model=req.model,
        system_prompt=req.system_prompt,
        options=req.options,
        tool_mode=req.tool_mode,
        pinned=req.pinned,
    )
    return {"status": "ok", "conversation": await db.get_conversation(conv_id)}


@app.delete("/api/conversations/{conv_id}")
async def remove_conversation(conv_id: str):
    await db.delete_conversation(conv_id)
    return {"status": "deleted", "conversation_id": conv_id}


@app.post("/api/conversations/{conv_id}/branch")
async def branch_conversation(conv_id: str, up_to_message_id: str):
    new_id = await db.branch_conversation(conv_id, up_to_message_id)
    if not new_id:
        raise HTTPException(status_code=404, detail="Message or conversation not found")
    return {"status": "ok", "id": new_id}


@app.get("/api/conversations/{conv_id}/export")
async def export_conversation(conv_id: str, format: str = "md"):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await db.get_messages(conv_id)

    if format == "json":
        return JSONResponse({"conversation": conv, "messages": messages})

    lines = [
        "---",
        f"title: {conv['title']}",
        f"model: {conv['model']}",
        f"created: {conv.get('created_at')}",
        f"updated: {conv.get('updated_at')}",
        "---",
        "",
        f"# {conv['title']}",
        "",
    ]
    for msg in messages:
        who = "You" if msg["role"] == "user" else conv["model"]
        lines.append(f"## {who}")
        if msg.get("images"):
            lines.append(f"*[attachments: {', '.join(Path(i).name for i in msg['images'])}]*")
            lines.append("")
        lines.append(msg["content"])
        if msg.get("tool_calls"):
            names = ", ".join(t.get("name", "?") for t in msg["tool_calls"])
            lines.append("")
            lines.append(f"> tools used: {names}")
        lines.append("")
    return StreamingResponse(
        iter(["\n".join(lines)]),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{conv_id[:8]}.md"'},
    )


@app.post("/api/conversations/{conv_id}/title")
async def generate_title(conv_id: str):
    """Ask the model for a short title. Falls back to a heuristic.

    Several small models return an empty completion when told to "reply with
    the title only", so we seed an assistant turn and let them continue -
    that reliably produces output.
    """
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await db.get_messages(conv_id)
    first_user = next((m for m in messages if m["role"] == "user" and m["content"]), None)
    if not first_user:
        raise HTTPException(status_code=400, detail="Nothing to summarise yet")

    snippet = first_user["content"][:400]
    title = ""

    try:
        client = make_client()
        res = await client.chat(
            model=conv["model"],
            messages=[
                {
                    "role": "system",
                    "content": "You write short descriptive titles for chat conversations.",
                },
                {"role": "user", "content": snippet},
                {"role": "assistant", "content": "Title:"},
            ],
            options={"temperature": 0.3, "num_predict": 40},
        )
        title = clean_title(res.message.content or "")
    except Exception as e:
        log.info("title generation error: %s", e)

    if not title:
        words = snippet.strip().split()
        title = " ".join(words[:6])
        if len(title) > 35:
            title = title[:35] + "..."

    await db.update_conversation(conv_id, title=title)
    return {"title": title}


def clean_title(raw: str) -> str:
    """Reduce a model completion to a short, single-line title."""
    text = (raw or "").strip()
    # Models often continue past the title into prose - keep the first line.
    for sep in ("\n", "\r"):
        if sep in text:
            text = text.split(sep)[0]
    text = text.strip()
    # Drop markdown emphasis, bullets and surrounding quotes.
    text = text.lstrip("#-*•> \t").strip()
    # If the model wrapped the title in quotes, keep only what is inside them
    # (otherwise a stray closing quote ends up mid-title).
    if text[:1] in ('"', "'"):
        quote = text[0]
        closing = text.find(quote, 1)
        if closing > 0:
            text = text[1:closing]
    text = text.strip().strip("`*").strip()
    # Cut at a sentence boundary.
    for stop in (". ", " - ", " — "):
        idx = text.find(stop)
        if idx > 8:
            text = text[:idx]
    text = " ".join(text.split())
    if len(text) > 48:
        text = text[:48].rsplit(" ", 1)[0]
    return text if len(text) >= 2 else ""


# ---------------------------------------------------------------- messages --
@app.patch("/api/messages/{message_id}")
async def edit_message(message_id: str, req: EditMessageRequest):
    msg = await db.get_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg["role"] != "user":
        raise HTTPException(status_code=400, detail="Only user messages can be edited")
    ok = await db.update_message(message_id, req.content, req.images)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update message")
    return {"status": "ok", "message": await db.get_message(message_id)}


@app.delete("/api/messages/{message_id}")
async def remove_message(message_id: str):
    conv_id = await db.delete_message(message_id)
    if not conv_id:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "deleted", "conversation_id": conv_id}


# ----------------------------------------------------------------- skills --
@app.get("/api/skills")
async def get_skills():
    return {"skills": registry.list_skills(), "default_enabled": registry.default_enabled()}


@app.post("/api/skills/execute")
async def run_skill(req: ExecuteSkillRequest):
    # resolve_name tolerates qualified names like "wikipedia.search" that small
    # models invent; without it the request 404s before the registry can cope.
    resolved = registry.resolve_name(req.skill_name)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Skill '{req.skill_name}' not found. "
                f"Available: {', '.join(sorted(registry.skills))}"
            ),
        )
    result = await registry.execute_skill(resolved, **req.args)
    return {"skill_name": resolved, "requested_as": req.skill_name, "result": result}


# ---------------------------------------------------------------- uploads --
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        return await save_upload(file)
    except UploadError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ------------------------------------------------------------------- chat --
@app.post("/api/chat/stream")
async def stream_chat(req: ChatRequest):
    if not req.prompt.strip() and not req.image_paths:
        raise HTTPException(status_code=400, detail="Prompt or an image is required.")
    if req.image_paths:
        await enforce_image_limit(req.image_paths)

    conv = await db.get_conversation(req.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Snapshot history BEFORE saving the new user message.
    history = await db.get_messages(req.conversation_id)

    # Auto-title on first message (heuristic; /title upgrades it later).
    new_title = None
    if conv["title"] in ("New Chat", "", None):
        words = req.prompt.strip().split()
        if words:
            new_title = (" ".join(words[:6])[:35] + "...") if len(" ".join(words[:6])) > 35 else " ".join(words[:6])
            await db.update_conversation(req.conversation_id, title=new_title)

    model_name = req.model or conv["model"] or DEFAULT_MODEL
    await db.update_conversation(
        req.conversation_id,
        model=model_name,
        system_prompt=req.system_prompt if req.system_prompt is not None else conv.get("system_prompt"),
        options=req.options if req.options is not None else conv.get("options"),
        tool_mode=req.tool_mode if req.tool_mode is not None else conv.get("tool_mode"),
    )
    conv = await db.get_conversation(req.conversation_id)

    enabled = (
        req.enabled_skills
        if req.enabled_skills is not None
        else registry.default_enabled()
    )
    if not enabled:
        log.warning("conversation %s has no enabled skills; running direct", conv["id"])

    await db.add_message(
        conv_id=req.conversation_id,
        role="user",
        content=req.prompt,
        images=req.image_paths,
        model=model_name,
    )

    cfg = ChatConfig(
        conversation_id=req.conversation_id,
        model=model_name,
        system_prompt=conv.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
        model_options=conv.get("options") or DEFAULT_MODEL_OPTIONS,
        tool_mode=conv.get("tool_mode") or "auto",
        enabled_skills=enabled,
        prompt=req.prompt,
        history=history,
        image_names=req.image_paths or [],
    )

    stop_event = asyncio.Event()
    _active_stops[req.conversation_id] = stop_event

    async def event_generator():
        full_text = ""
        tool_calls: List[Dict[str, Any]] = []
        stats: Dict[str, Any] = {}
        truncated = False

        try:
            async for event in run_agent(cfg):
                if stop_event.is_set():
                    truncated = True
                    break

                etype = event.get("type")

                if etype == "chunk":
                    full_text += event["content"]
                elif etype == "error":
                    truncated = True
                    full_text = event.get("full_content", full_text)
                    tool_calls = event.get("tool_calls", tool_calls)
                    await _persist(cfg, full_text, tool_calls, None, truncated)
                    yield sse(
                        {
                            "type": "error",
                            "error": event.get("error"),
                            "partial_saved": bool(full_text),
                            "message_id": None,
                        }
                    )
                    return
                elif etype == "complete":
                    full_text = event.get("full_content", full_text)
                    tool_calls = event.get("tool_calls", tool_calls)
                    stats = event.get("stats", {})
                    truncated = event.get("truncated", False)

                yield sse(event)

            # Persist even when stopped mid-stream so partial work is not lost.
            message_id = await _persist(cfg, full_text, tool_calls, stats, truncated)

            yield sse(
                {
                    "type": "done",
                    "message_id": message_id,
                    "title": new_title,
                    "full_content": full_text,
                    "stopped": stop_event.is_set(),
                    "truncated": truncated,
                }
            )
        except asyncio.CancelledError:
            # Client disconnected - salvage whatever streamed in.
            if full_text.strip():
                await _persist(cfg, full_text, tool_calls, stats, truncated=True)
            raise
        finally:
            _active_stops.pop(req.conversation_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _persist(
    cfg: ChatConfig,
    content: str,
    tool_calls: List[Dict[str, Any]],
    stats: Optional[Dict[str, Any]],
    truncated: bool,
) -> Optional[str]:
    # Nothing to record if no text arrived and no tool ran - saving here would
    # leave an empty bubble in the transcript.
    if not content.strip() and not tool_calls:
        return None
    return await db.add_message(
        conv_id=cfg.conversation_id,
        role="assistant",
        content=content,
        model=cfg.model,
        stats=stats or None,
        tool_calls=tool_calls or None,
        truncated=truncated,
    )


@app.post("/api/chat/stop")
async def stop_generation(conversation_id: str = Form(...)):
    ev = _active_stops.get(conversation_id)
    if ev is None:
        return {"status": "idle", "conversation_id": conversation_id}
    ev.set()
    log.info("stop requested for conversation %s", conversation_id)
    return {"status": "stopping", "conversation_id": conversation_id}


# ------------------------------------------------------------------- SPA --
@app.get("/")
async def serve_index():
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "message": "Backend is running. Run 'npm run build' in frontend/ to compile the UI."
    }


@app.get("/{full_path:path}")
async def serve_spa_assets(full_path: str):
    if full_path.startswith(("api", "uploads")):
        raise HTTPException(status_code=404, detail="Not found")
    target = FRONTEND_DIST / full_path
    if target.is_file():
        return FileResponse(target)
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True)