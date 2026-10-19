"""SQLite persistence layer.

All schema evolution is additive and idempotent - `_apply_migrations` checks
``PRAGMA table_info`` before each ``ALTER TABLE`` so an existing
``chat_history.db`` upgrades in place without losing rows.
"""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from backend.config import DB_PATH, DEFAULT_MODEL_OPTIONS, DEFAULT_SYSTEM_PROMPT

SCHEMA_VERSION = 2


def _now() -> str:
    """Timezone-aware UTC timestamp (datetime.utcnow() is deprecated in 3.12)."""
    return datetime.now(timezone.utc).isoformat()


async def _columns(db: aiosqlite.Connection, table: str) -> set:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return {row[1] for row in await cur.fetchall()}


async def _apply_migrations(db: aiosqlite.Connection) -> None:
    """Add columns introduced after v1. Safe to run on every startup."""
    conv_cols = await _columns(db, "conversations")
    new_conv = {
        # NOTE: no DEFAULT for text columns - string defaults would have to be
        # inlined into the DDL, and an apostrophe in the prompt breaks it.
        "system_prompt": "TEXT",
        "options": "TEXT",
        "tool_mode": "TEXT DEFAULT 'auto'",
        "pinned": "INTEGER DEFAULT 0",
        "folder": "TEXT",
    }
    for name, ddl in new_conv.items():
        if name not in conv_cols:
            await db.execute(f"ALTER TABLE conversations ADD COLUMN {name} {ddl}")

    # Backfill defaults for pre-existing rows.
    await db.execute(
        "UPDATE conversations SET system_prompt = ? WHERE system_prompt IS NULL",
        (DEFAULT_SYSTEM_PROMPT,),
    )
    await db.execute(
        "UPDATE conversations SET tool_mode = 'auto' WHERE tool_mode IS NULL"
    )
    await db.execute(
        "UPDATE conversations SET options = ? WHERE options IS NULL",
        (json.dumps(DEFAULT_MODEL_OPTIONS),),
    )

    msg_cols = await _columns(db, "messages")
    new_msg = {
        "model": "TEXT",
        "stats": "TEXT",
        "tool_calls": "TEXT",
        "truncated": "INTEGER DEFAULT 0",
    }
    for name, ddl in new_msg.items():
        if name not in msg_cols:
            await db.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")

    await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                images TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """
        )
        await _apply_migrations(db)
        await db.commit()


# ---------------------------------------------------------------- helpers --
def _row_to_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    item = dict(row)
    for key in ("images", "stats", "tool_calls"):
        raw = item.get(key)
        if raw:
            try:
                item[key] = json.loads(raw)
            except Exception:
                item[key] = [] if key in ("images", "tool_calls") else {}
        elif key in item:
            item[key] = [] if key in ("images", "tool_calls") else None
    if item.get("options"):
        try:
            item["options"] = json.loads(item["options"])
        except Exception:
            item["options"] = None
    item.setdefault("pinned", 0)
    return item


async def get_all_conversations() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, model, created_at, updated_at, system_prompt,
                   options, tool_mode, pinned, folder
            FROM conversations
            ORDER BY pinned DESC, updated_at DESC
            """
        ) as cursor:
            return [_row_to_dict(r) for r in await cursor.fetchall()]


async def get_conversation(conv_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, title, model, created_at, updated_at, system_prompt,
                   options, tool_mode, pinned, folder
            FROM conversations WHERE id = ?
            """,
            (conv_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_dict(row) if row else None


async def create_conversation(
    title: str = "New Chat",
    model: str = "openbmb/minicpm-v4.7:1b",
    system_prompt: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> str:
    conv_id = str(uuid.uuid4())
    now = _now()
    opts = json.dumps(options or DEFAULT_MODEL_OPTIONS)
    prompt = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO conversations
                (id, title, model, created_at, updated_at, system_prompt, options, tool_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'auto')
            """,
            (conv_id, title, model, now, now, prompt, opts),
        )
        await db.commit()
    return conv_id


async def update_conversation(
    conv_id: str,
    title: Optional[str] = None,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    tool_mode: Optional[str] = None,
    pinned: Optional[bool] = None,
    folder: Optional[str] = None,
) -> None:
    sets: List[str] = []
    vals: List[Any] = []

    if title is not None:
        sets.append("title = ?")
        vals.append(title)
    if model is not None:
        sets.append("model = ?")
        vals.append(model)
    if system_prompt is not None:
        sets.append("system_prompt = ?")
        vals.append(system_prompt)
    if options is not None:
        sets.append("options = ?")
        vals.append(json.dumps(options))
    if tool_mode is not None:
        sets.append("tool_mode = ?")
        vals.append(tool_mode)
    if pinned is not None:
        sets.append("pinned = ?")
        vals.append(1 if pinned else 0)
    if folder is not None:
        sets.append("folder = ?")
        vals.append(folder)

    if not sets:
        return

    sets.append("updated_at = ?")
    vals.extend([_now(), conv_id])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", vals)
        await db.commit()


# Backwards-compatible wrappers used by existing callers.
update_conversation_title = lambda conv_id, title: update_conversation(conv_id, title=title)
update_conversation_model = lambda conv_id, model: update_conversation(conv_id, model=model)


async def touch_conversation(conv_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id)
        )
        await db.commit()


async def delete_conversation(conv_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        await db.commit()


# --------------------------------------------------------------- messages --
async def get_messages(conv_id: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, role, content, images, timestamp, model, stats, tool_calls, truncated
            FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC, rowid ASC
            """,
            (conv_id,),
        ) as cursor:
            return [_row_to_dict(r) for r in await cursor.fetchall()]


async def get_message(message_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, conversation_id, role, content, images, timestamp,
                   model, stats, tool_calls, truncated
            FROM messages WHERE id = ?
            """,
            (message_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_dict(row) if row else None


async def add_message(
    conv_id: str,
    role: str,
    content: str,
    images: Optional[List[str]] = None,
    model: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    truncated: bool = False,
) -> str:
    msg_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO messages
                (id, conversation_id, role, content, images, timestamp,
                 model, stats, tool_calls, truncated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg_id,
                conv_id,
                role,
                content,
                json.dumps(images) if images else None,
                _now(),
                model,
                json.dumps(stats) if stats else None,
                json.dumps(tool_calls) if tool_calls else None,
                1 if truncated else 0,
            ),
        )
        await db.commit()
    await touch_conversation(conv_id)
    return msg_id


async def update_message(message_id: str, content: str, images: Optional[List[str]] = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE messages SET content = ?, images = ? WHERE id = ?",
            (content, json.dumps(images) if images else None, message_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_message(message_id: str) -> Optional[str]:
    """Delete a message and return its conversation id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT conversation_id FROM messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            conv_id = row["conversation_id"]
        await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        await db.commit()
    await touch_conversation(conv_id)
    return conv_id


async def delete_messages_after(conv_id: str, timestamp: str) -> int:
    """Truncate a conversation from a given message onwards (used by regenerate/edit)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND timestamp >= ?",
            (conv_id, timestamp),
        )
        await db.commit()
        count = cur.rowcount
    await touch_conversation(conv_id)
    return count


async def branch_conversation(conv_id: str, up_to_message_id: str, title: Optional[str] = None) -> Optional[str]:
    """Copy a conversation up to (but excluding) a message into a new one."""
    source = await get_conversation(conv_id)
    if not source:
        return None

    messages = await get_messages(conv_id)
    cutoff_idx = next((i for i, m in enumerate(messages) if m["id"] == up_to_message_id), None)
    if cutoff_idx is None:
        return None

    new_id = await create_conversation(
        title=title or f"{source['title']} (branch)",
        model=source["model"],
        system_prompt=source.get("system_prompt"),
        options=source.get("options"),
    )
    for msg in messages[:cutoff_idx]:
        await add_message(
            conv_id=new_id,
            role=msg["role"],
            content=msg["content"],
            images=msg.get("images"),
            model=msg.get("model"),
        )
    return new_id


# ------------------------------------------------------------- utilities --
async def referenced_images() -> set:
    """Every upload filename still referenced by a message (for GC)."""
    refs = set()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT images FROM messages WHERE images IS NOT NULL") as cur:
            for (raw,) in await cur.fetchall():
                try:
                    for item in json.loads(raw):
                        name = Path(str(item)).name
                        if name:
                            refs.add(name)
                except Exception:
                    continue
    return refs


async def backup_db(dest: Path) -> None:
    """Snapshot the database (used before destructive operations)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, dest)