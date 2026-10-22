"""Chat pipeline: context assembly, the tool-calling agent loop, and SSE events.

The agent loop lets the model decide when to call a skill. When the active
model does not support tool-calling (most small local models do not), the loop
degrades to a single pass and the caller is told so it can fall back to
keyword-driven skill execution.
"""

import asyncio
import base64
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import ollama

from backend.config import (
    DATA_DIR,
    HISTORY_MESSAGES,
    IMAGE_HISTORY_TURNS,
    MAX_TOOL_ITERATIONS,
    OLLAMA_HOST,
    TOOL_PROBE_MAX_TOKENS,
    TOOL_PROBE_TIMEOUT,
)
from backend.ollama_client import friendly_error, make_client, sanitize_options
from backend.skills.base import SkillContext, registry, summarize_result
from backend.uploads import resolve_upload_path

# model name -> supports tool calling?
_tool_support_cache: Dict[str, bool] = {}

# The probe result is cached on disk too: re-probing costs seconds, and the
# answer cannot change unless the model itself changes.
_TOOL_SUPPORT_PATH = DATA_DIR / "tool_support.json"


# --------------------------------------------------------------- small talk --
# Greetings and thanks never need a tool. Recognising them keeps the probe
# and the whole tool machinery out of the way for the most common opening
# message of a chat, and stops small models from inventing a tool call.
_SMALL_TALK_RE = re.compile(
    r"^\s*(hi|hey|hello|hallo|yo|sup|hiya|howdy"
    r"|good\s+(morning|afternoon|evening|day|night)"
    r"|thanks?|thank\s+you|thx|ty|cheers"
    r"|ok|okay|k|sure|cool|nice|great|awesome|perfect|fine"
    r"|bye|goodbye|good\s+bye|see\s+you|see\s+ya|cya"
    r"|how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|whats\s+up)"
    r"(\s+(there|everyone|all|friend|folks))?"
    r"[\s.!?…]*$",
    re.IGNORECASE,
)

# Long enough to cover "hi there!", short enough that a real question can
# never be mistaken for small talk.
_SMALL_TALK_MAX_CHARS = 40


def is_small_talk(prompt: str) -> bool:
    """True when the prompt is just a greeting / thanks / sign-off."""
    text = (prompt or "").strip()
    if not text or len(text) > _SMALL_TALK_MAX_CHARS:
        return False
    return bool(_SMALL_TALK_RE.match(text))


# ------------------------------------------------------- tool support cache --
def _load_tool_support_cache() -> Dict[str, bool]:
    """Read the persisted probe results (if any)."""
    try:
        if _TOOL_SUPPORT_PATH.exists():
            data = json.loads(_TOOL_SUPPORT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save_tool_support_cache(cache: Dict[str, bool]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _TOOL_SUPPORT_PATH.write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        pass  # a missing cache only costs one probe


def cached_tool_support(model: str) -> Optional[bool]:
    """Return the cached probe result for a model, or None if not probed yet."""
    if model in _tool_support_cache:
        return _tool_support_cache[model]
    disk = _load_tool_support_cache()
    if model in disk:
        _tool_support_cache.update(disk)
        return disk[model]
    return None



# -------------------------------------------------------- text tool parser --
# Some small models (e.g. MiniCPM, Llama variants) cannot use Ollama's native
# JSON tool-calling but still emit function calls as raw text in a variety of
# XML / JSON formats.  We parse those here so the agent loop can execute them.

_XML_FUNC_RE = re.compile(
    r"<function=(?P<name>\w+)>\s*(?P<body>.*?)\s*</function>",
    re.DOTALL | re.IGNORECASE,
)
_XML_PARAM_RE = re.compile(
    r"<(?P<key>\w+)>\s*(?P<val>.*?)\s*</(?P=key)>",
    re.DOTALL,
)
_TOOL_CALL_JSON_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_BARE_JSON_CALL_RE = re.compile(
    r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"(?:arguments|parameters|args)"\s*:\s*(\{[^{}]*\})\s*\}',
    re.DOTALL,
)


def _parse_text_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract tool-call intent from raw model text.

    Returns a list of ``{"name": str, "arguments": dict}`` dicts, or an
    empty list when no call is found.  The function tries several formats
    used by popular open-weights models in order of specificity.
    """
    calls: List[Dict[str, Any]] = []

    # Format 1: <function=name><param>value</param>…</function>
    for m in _XML_FUNC_RE.finditer(text):
        name = m.group("name")
        body = m.group("body")
        # Try to parse body as JSON first.
        try:
            args = json.loads(body)
            if isinstance(args, dict):
                calls.append({"name": name, "arguments": args})
                continue
        except (json.JSONDecodeError, ValueError):
            pass
        # Fall back to XML param tags.
        params = {k: v for k, v in _XML_PARAM_RE.findall(body)}
        calls.append({"name": name, "arguments": params})

    if calls:
        return calls

    # Format 2: <tool_call>{"name": ..., "arguments": {...}}</tool_call>
    for m in _TOOL_CALL_JSON_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            name = obj.get("name") or obj.get("tool") or obj.get("function")
            args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
            if name:
                calls.append({"name": str(name), "arguments": args if isinstance(args, dict) else {}})
        except (json.JSONDecodeError, ValueError):
            pass

    if calls:
        return calls

    # Format 3: bare {"name": "...", "arguments": {...}}
    for m in _BARE_JSON_CALL_RE.finditer(text):
        try:
            args = json.loads(m.group(2))
            calls.append({"name": m.group(1), "arguments": args if isinstance(args, dict) else {}})
        except (json.JSONDecodeError, ValueError):
            pass

    return calls




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
    completed = False
    try:
        resp = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=[{"role": "user", "content": "Call the ping tool."}],
                tools=probe_tool,
                stream=False,
                # Cap the output: a model that cannot call tools would
                # otherwise ramble for a full generation, which is seconds of
                # waiting before the user sees anything.
                options={"num_predict": TOOL_PROBE_MAX_TOKENS},
            ),
            timeout=TOOL_PROBE_TIMEOUT,
        )
        supported = bool(getattr(resp.message, "tool_calls", None))
        completed = True
    except Exception:
        supported = False

    # Only remember the answer if the probe actually ran. A timeout or a
    # connection error must not be cached as "this model cannot call tools".
    if completed:
        _tool_support_cache[model] = supported
        disk = _load_tool_support_cache()
        disk[model] = supported
        _save_tool_support_cache(disk)
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
    use_native_tools: bool = True,
    advertise_tools: bool = True,
) -> List[Dict[str, Any]]:
    """Turn stored history into the message list Ollama expects."""
    system = cfg.system_prompt or ""
    tool_names = (
        [n for n in cfg.enabled_skills if registry.get_skill(n)] if advertise_tools else []
    )
    if tool_names:
        if use_native_tools:
            # Native tool-calling models just need a reminder.
            system += (
                "\n\nYou have access to these tools: "
                + ", ".join(tool_names)
                + ". Use them when a request needs live data or actions you cannot "
                "perform from memory. Never claim to have used a tool unless a tool "
                "result appears in this conversation."
            )
        else:
            # Text-mode: give explicit XML call syntax the backend can parse.
            system += (
                "\n\nYou have access to these tools: "
                + ", ".join(tool_names)
                + ".\n"
                "Call a tool ONLY when the request needs live data - a web search, "
                "an article lookup, or reading a URL you were given. If the question "
                "can be answered from your own knowledge, just answer it and do not "
                "call a tool. Never call a tool for a greeting or a thank-you.\n"
                "To call a tool, output ONLY the following XML block and nothing else "
                "on that turn — do NOT add any prose before or after the tag:\n"
                "  <function=TOOL_NAME><query>YOUR QUERY HERE</query></function>\n"
                "Replace TOOL_NAME with the exact tool name and YOUR QUERY HERE with "
                "the search term or argument. After the tool result is shown, answer "
                "the user in plain prose. Never invent a tool result."
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

    # A greeting, a thank-you or a sign-off never needs a tool. Skipping the
    # tools for those keeps the probe (and the tool round-trip) out of the
    # way, and stops small models from inventing a call they do not need.
    small_talk = is_small_talk(cfg.prompt)
    tools = None if small_talk else (registry.get_ollama_tools(cfg.enabled_skills) or None)

    use_tools = False
    if tools and cfg.tool_mode != "off":
        if cfg.tool_mode == "on":
            use_tools = True
        else:
            known = cached_tool_support(cfg.model)
            if known is None:
                yield {"type": "status", "stage": "probe", "text": "Checking tool support…"}
                use_tools = await model_supports_tools(client, cfg.model)
                if not use_tools:
                    yield {
                        "type": "status",
                        "stage": "probe",
                        "text": "No native tool calling in this model — tools run in text mode.",
                    }
            else:
                use_tools = known

    # Build context messages AFTER probing so the system prompt can include
    # the correct tool-call syntax for this model's capabilities.
    messages = build_model_messages(
        cfg.history or [],
        cfg,
        use_native_tools=use_tools,
        advertise_tools=not small_talk,
    )

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
                # Reset the text accumulator for the new pass so stale raw
                # function-call markup from the previous iteration is not
                # carried forward.
                full_text = ""

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

            # ---- Text-based tool call interception -------------------------
            # Models that fail the tool-support probe (e.g. MiniCPM, small
            # Llama) sometimes still emit calls as raw XML/JSON text.  Detect
            # those, strip them from the streamed output, execute the skills,
            # and continue the loop so the model can answer using the results.
            # Only when tools were actually offered: with no tool instructions
            # in the prompt, markup like that is just text.
            if not pending and not forced_final and tools and full_text.strip():
                text_calls = _parse_text_tool_calls(full_text)
                # Only act on calls whose skill name we actually know.
                text_calls = [
                    c for c in text_calls if registry.resolve_name(c["name"])
                ]
                if text_calls:
                    # The text the model emitted was the "thinking" portion
                    # before the call, or is just the raw call markup itself.
                    # Strip the call syntax so we don't show it to the user.
                    clean = full_text
                    for pat in (_XML_FUNC_RE, _TOOL_CALL_JSON_RE, _BARE_JSON_CALL_RE):
                        clean = pat.sub("", clean).strip()

                    # The raw markup has already been streamed to the client.
                    # Send a correction chunk that replaces it in place, so
                    # the call syntax never sits in the chat as prose.
                    if clean != full_text:
                        yield {"type": "chunk", "content": clean, "replace": True}
                    full_text = clean

                    for tc in text_calls:
                        pending[tc["name"]] = tc

            # ----------------------------------------------------------------

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
                if use_tools:
                    # Native tool-calling: use the structured tool message format.
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
                else:
                    # Text-mode: Ollama won't accept role:tool without tools in
                    # the request.  Inject as an assistant+user exchange instead.
                    messages.append(
                        {
                            "role": "assistant",
                            "content": f"<function={name}><query>{args.get('query', json.dumps(args))}</query></function>",
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"[Tool result for {name}]\n"
                                + summarize_result(result)
                                + "\n\nNow answer the original question in plain prose using the above result."
                            ),
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