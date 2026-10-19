"""Chat pipeline: context assembly, the tool-calling agent loop, and SSE events.

The agent loop lets the model decide when to call a skill. When the active
model does not support tool-calling (most small local models do not), the loop
degrades to a single pass and the caller is told so it can fall back to
keyword-driven skill execution.
"""

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import ollama

from backend.config import (
    HISTORY_MESSAGES,
    IMAGE_HISTORY_TURNS,
    MAX_TOOL_ITERATIONS,
    OLLAMA_HOST,
    TOOL_PROBE_TIMEOUT,
)
from backend.ollama_client import friendly_error, make_client, sanitize_options
from backend.skills.base import SkillContext, registry, summarize_result
from backend.uploads import resolve_upload_path

# model name -> supports tool calling?
_tool_support_cache: Dict[str, bool] = {}


def sse(payload: Dict[str, Any]) -> str:
    """Encode one server-sent event."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@dataclass
class ChatConfig:
    conversation_id: str
    model: str
    system_prompt: str
    model_options: Dict[str, Any]
    tool_mode: str
    enabled_skills: List[str]
    prompt: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    image_names: List[str] = field(default_factory=list)


# ------------------------------------------------------------ tool probe --
async def model_supports_tools(client: ollama.AsyncClient, model: str) -> bool:
    """Probe (once per model) whether Ollama tool-calling works for this model."""
    if model in _tool_support_cache:
        return _tool_support_cache[model]

    probe_tool = [
        {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Respond with the word 'pong'. Use only when asked to ping.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    ]

    supported = False
    try:
        resp = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=[{"role": "user", "content": "Call the ping tool."}],
                tools=probe_tool,
                stream=False,
            ),
            timeout=TOOL_PROBE_TIMEOUT,
        )
        supported = bool(getattr(resp.message, "tool_calls", None))
    except Exception:
        supported = False

    _tool_support_cache[model] = supported
    return supported


# -------------------------------------------------------- context assembly --
def _encode_image(name: str) -> Optional[str]:
    path = resolve_upload_path(name)
    if not path:
        return None
    try:
        return base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception:
        return None


def _apply_image_policy(items: List[Dict[str, Any]], keep_turns: int) -> None:
    """Strip images from all but the most recent ``keep_turns`` user turns.

    Re-sending every historical image is by far the dominant cost in a vision
    chat: each one is base64-encoded and pushed through the model on every turn.
    """
    user_indices = [i for i, it in enumerate(items) if it["role"] == "user"]
    keep = set(user_indices[-keep_turns:]) if keep_turns > 0 else set()
    for i, it in enumerate(items):
        if i not in keep:
            it.pop("images", None)


def build_model_messages(
    history: List[Dict[str, Any]],
    cfg: ChatConfig,
) -> List[Dict[str, Any]]:
    """Turn stored history into the message list Ollama expects."""
    system = cfg.system_prompt or ""
    tool_names = [n for n in cfg.enabled_skills if registry.get_skill(n)]
    if tool_names:
        system += (
            "\n\nYou have access to these tools: "
            + ", ".join(tool_names)
            + ". Use them when a request needs live data or actions you cannot "
            "perform from memory. Never claim to have used a tool unless a tool "
            "result appears in this conversation."
        )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]

    recent = history[-HISTORY_MESSAGES:] if len(history) > HISTORY_MESSAGES else history

    for msg in recent:
        if msg["role"] not in ("user", "assistant"):
            continue
        item: Dict[str, Any] = {"role": msg["role"], "content": msg["content"]}
        if msg.get("images"):
            encoded = [
                b64
                for b64 in (_encode_image(Path(str(n)).name) for n in msg["images"])
                if b64
            ]
            if encoded:
                item["images"] = encoded
        messages.append(item)

    current: Dict[str, Any] = {"role": "user", "content": cfg.prompt}
    if cfg.image_names:
        encoded = [b64 for b64 in (_encode_image(Path(str(n)).name) for n in cfg.image_names) if b64]
        if encoded:
            current["images"] = encoded
    messages.append(current)

    _apply_image_policy(messages[1:], IMAGE_HISTORY_TURNS)
    return messages


# ----------------------------------------------------------- agent loop ----
async def run_agent(cfg: ChatConfig) -> AsyncIterator[Dict[str, Any]]:
    """Yield event dicts: status / tool_start / tool_result / chunk / stats / done / error."""
    client = make_client()

    messages = build_model_messages(cfg.history or [], cfg)

    tools = registry.get_ollama_tools(cfg.enabled_skills) or None

    use_tools = False
    if tools and cfg.tool_mode != "off":
        if cfg.tool_mode == "on":
            use_tools = True
        else:
            yield {"type": "status", "stage": "probe", "text": "Checking tool support…"}
            use_tools = await model_supports_tools(client, cfg.model)
            if not use_tools:
                yield {
                    "type": "status",
                    "stage": "probe",
                    "text": f"{cfg.model} does not support tool calling - using direct mode.",
                }

    if use_tools and tools:
        yield {
            "type": "status",
            "stage": "tools",
            "text": f"{len(tools)} tool{'s' if len(tools) != 1 else ''} available to the model.",
        }

    skill_ctx = SkillContext(
        conversation_id=cfg.conversation_id,
        image_paths=[str(resolve_upload_path(Path(str(n)).name)) for n in cfg.image_names],
        last_user_prompt=cfg.prompt,
    )

    options = sanitize_options(cfg.model_options)
    # Vision models need headroom for image tokens.
    if cfg.image_names:
        options.setdefault("num_ctx", 4096)

    full_text = ""
    tool_log: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {}
    start = time.perf_counter()
    truncated = False

    # Small models often re-issue the same tool call instead of answering.
    # Cache results by signature and, after a couple of repeats, force a
    # final pass with tools withheld so the model must produce prose.
    seen_calls: Dict[str, Any] = {}
    repeat_count = 0
    failed_count = 0
    forced_final = False

    def _signature(name: str, args: Dict[str, Any]) -> str:
        return name + "|" + json.dumps(args, sort_keys=True, default=str)

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            if iteration > 0:
                yield {
                    "type": "status",
                    "stage": "continue",
                    "text": "Continuing with tool results…",
                }

            pending: Dict[str, Dict[str, Any]] = {}
            chunk_stats: Dict[str, Any] = {}

            stream = await client.chat(
                model=cfg.model,
                messages=messages,
                tools=None if (forced_final or not use_tools) else tools,
                options=options,
                stream=True,
            )

            async for chunk in stream:
                msg = chunk.message

                if msg.content:
                    full_text += msg.content
                    yield {"type": "chunk", "content": msg.content}

                for call in (msg.tool_calls or []):
                    fn = call.function
                    # Ollama streams tool-call arguments; merge by name.
                    pending[fn.name] = {
                        "name": fn.name,
                        "arguments": {**(pending.get(fn.name, {}).get("arguments") or {}),
                                      **(fn.arguments or {})},
                    }

                if chunk.done:
                    chunk_stats = {
                        "tokens": chunk.eval_count,
                        "total_ms": (chunk.total_duration or 0) // 1_000_000,
                        "eval_ms": (chunk.eval_duration or 0) // 1_000_000,
                        "prompt_ms": (chunk.prompt_eval_duration or 0) // 1_000_000,
                        "done_reason": chunk.done_reason,
                    }

            if chunk_stats:
                stats = chunk_stats

            if not pending:
                break

            if forced_final:
                # Tools were already withheld and the model still wants one.
                # Do not execute it - fall through to the final answer pass.
                break

            for call in pending.values():
                name = call["name"]
                call_id = uuid.uuid4().hex[:8]
                args = call["arguments"] or {}

                yield {
                    "type": "tool_start",
                    "id": call_id,
                    "name": name,
                    "args": args,
                }

                sig = _signature(name, args)

                if sig in seen_calls:
                    # The model asked for something it already has. Reuse the
                    # result rather than paying for it again.
                    repeat_count += 1
                    result = seen_calls[sig]
                    duration_ms = 0
                    ok = "error" not in result
                    cached = True
                else:
                    t0 = time.perf_counter()
                    result = await registry.execute_skill(name, ctx=skill_ctx, **args)
                    duration_ms = int((time.perf_counter() - t0) * 1000)
                    ok = "error" not in result
                    seen_calls[sig] = result
                    cached = False

                if not ok:
                    failed_count += 1

                display = registry.get_skill(name)
                yield {
                    "type": "tool_result",
                    "id": call_id,
                    "name": name,
                    "display_name": display.display_name if display else name,
                    "ok": ok,
                    "cached": cached,
                    "duration_ms": duration_ms,
                    "summary": summarize_result(result, limit=600),
                }

                tool_log.append(
                    {
                        "id": call_id,
                        "name": name,
                        "args": args,
                        "ok": ok,
                        "cached": cached,
                        "duration_ms": duration_ms,
                        "result": result if ok else None,
                        "error": None if ok else result.get("error"),
                    }
                )

                # Record the assistant turn that requested the tool. Without
                # it the transcript has a tool result with no matching call,
                # which several backends refuse to continue from.
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": c["name"],
                                    "arguments": c["arguments"] or {},
                                }
                            }
                            for c in pending.values()
                        ],
                    }
                )

                messages.append(
                    {
                        "role": "tool",
                        "content": summarize_result(result),
                        "tool_name": name,
                    }
                )

            # The model is stuck - either re-issuing the same call, or calling a tool
            # that does not exist. Withhold the tools on the next pass so it is
            # forced to answer in prose using whatever it already has.
            if (repeat_count >= 2 or failed_count >= 2) and not forced_final:
                forced_final = True
                repeat_count = 0
                failed_count = 0
                yield {
                    "type": "status",
                    "stage": "continue",
                    "text": "Model is stuck on tools - requesting a final answer.",
                }

    except asyncio.CancelledError:
        # Client hung up (Stop button / tab closed). Persist what we have.
        truncated = True
        raise
    except Exception as e:
        yield {
            "type": "error",
            "error": friendly_error(e),
            "partial_saved": bool(full_text),
            "full_content": full_text,
            "tool_calls": tool_log,
        }
        return

    # Safety net: the loop ran out of iterations and the model never wrote
    # anything. Make one final pass with tools withheld so the user gets an
    # answer rather than silence.
    if not full_text.strip() and tool_log:
        yield {
            "type": "status",
            "stage": "continue",
            "text": "Summarising tool results…",
        }
        try:
            # An explicit directive works better than simply withholding tools:
            # the transcript already contains tool messages, which otherwise
            # invites the model to keep calling more of them.
            final_messages = list(messages) + [
                {
                    "role": "system",
                    "content": (
                        "Answer the user's original question now, in plain prose, "
                        "using the tool results above. Do not request any more tools. "
                        "If the results are incomplete, say what you do know."
                    ),
                }
            ]
            final_stream = await client.chat(
                model=cfg.model,
                messages=final_messages,
                tools=None,
                options=options,
                stream=True,
            )
            async for chunk in final_stream:
                token = chunk.message.content
                if token:
                    full_text += token
                    yield {"type": "chunk", "content": token}
                if chunk.done:
                    stats = {
                        "tokens": chunk.eval_count,
                        "total_ms": (chunk.total_duration or 0) // 1_000_000,
                        "eval_ms": (chunk.eval_duration or 0) // 1_000_000,
                        "prompt_ms": (chunk.prompt_eval_duration or 0) // 1_000_000,
                        "done_reason": chunk.done_reason,
                    }
        except asyncio.CancelledError:
            truncated = True
            raise
        except Exception as e:
            yield {
                "type": "error",
                "error": friendly_error(e),
                "partial_saved": bool(full_text),
                "full_content": full_text,
                "tool_calls": tool_log,
            }
            return

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    tok_per_sec = (
        round(stats["tokens"] / (stats["eval_ms"] / 1000), 1)
        if stats.get("tokens") and stats.get("eval_ms")
        else None
    )

    yield {
        "type": "stats",
        "model": cfg.model,
        "tokens": stats.get("tokens"),
        "tok_per_sec": tok_per_sec,
        "total_ms": stats.get("total_ms") or elapsed_ms,
        "eval_ms": stats.get("eval_ms"),
        "prompt_ms": stats.get("prompt_ms"),
        "done_reason": stats.get("done_reason"),
        "used_tools": len(tool_log),
    }
    yield {
        "type": "complete",
        "full_content": full_text,
        "tool_calls": tool_log,
        "stats": {k: v for k, v in stats.items() if v is not None},
        # "truncated" means the answer is incomplete: a stop or an error cut it
        # off. A completed tool loop is not truncation.
        "truncated": truncated,
    }