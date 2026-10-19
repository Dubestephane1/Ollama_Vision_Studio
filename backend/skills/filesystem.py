"""Read-only, sandboxed filesystem access.

Scoped to an explicit root directory. Symlinks are resolved and checked so a
path cannot escape the sandbox, and only text-like extensions are readable.
This skill is read-only by design - it can list, search and read, never write.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.skills.base import BaseSkill, SkillContext

# Everything the skill is allowed to reach sits under this directory.
SANDBOX_ROOT = Path(
    os.getenv("SKILLS_FS_ROOT", str(Path.home() / "Documents"))
).expanduser().resolve()

ALLOWED_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".html",
    ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".c",
    ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh",
    ".ps1", ".bat", ".sql", ".swift", ".kt", ".vue", ".svelte",
}

# Directories never descended into.
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".idea", ".vscode", "target", ".cache", ".tox",
}

MAX_FILE_BYTES = 512 * 1024
MAX_RESULTS = 100


class FilesystemSkill(BaseSkill):
    name: str = "filesystem"
    display_name: str = "Sandboxed File Search & Reader"
    description: str = (
        "Search and read text files inside a sandboxed folder on this machine. "
        "Use it to find where something is defined, or to read a file's contents. "
        "Read-only - it cannot create, edit or delete anything."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "search", "read"],
                "description": "list: show files in a directory. search: find files by name. read: return a file's contents.",
            },
            "path": {
                "type": "string",
                "description": "File or directory path, relative to the sandbox root.",
            },
            "pattern": {
                "type": "string",
                "description": "Glob or substring to match when action is 'search', e.g. '*.py' or 'config'.",
            },
        },
        "required": ["action"],
    }
    enabled_by_default = False

    async def execute(
        self,
        ctx: Optional[SkillContext] = None,
        action: str = "list",
        path: str = ".",
        pattern: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        action = (action or "list").strip().lower()
        root = SANDBOX_ROOT

        if not root.exists():
            return {
                "error": f"Sandbox root does not exist: {root}",
                "hint": "Set the SKILLS_FS_ROOT environment variable to change it.",
            }

        try:
            target = self._resolve(root, path or ".")
        except ValueError as e:
            return {"error": str(e)}

        if action == "read":
            return self._read(root, target)
        if action == "search":
            return self._search(root, target, pattern)
        if action == "list":
            return self._list(root, target)

        return {"error": f"Unknown action '{action}'. Use list, search or read."}

    # ------------------------------------------------------------------ #
    def _resolve(self, root: Path, path: str) -> Path:
        """Resolve ``path`` inside the sandbox, refusing to escape it."""
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError(
                f"Path '{path}' resolves outside the sandbox root ({root}). Denied."
            )
        return candidate

    def _list(self, root: Path, target: Path) -> Dict[str, Any]:
        if not target.exists():
            return {"error": f"Not found: {self._rel(root, target)}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {self._rel(root, target)}"}

        entries: List[Dict[str, Any]] = []
        try:
            for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name in SKIP_DIRS or child.name.startswith("."):
                    continue
                try:
                    stat = child.stat()
                    entries.append(
                        {
                            "name": child.name,
                            "type": "dir" if child.is_dir() else "file",
                            "size": stat.st_size,
                        }
                    )
                except OSError:
                    continue
                if len(entries) >= MAX_RESULTS:
                    break
        except PermissionError:
            return {"error": f"Permission denied listing {self._rel(root, target)}"}

        return {
            "path": self._rel(root, target),
            "entry_count": len(entries),
            "entries": entries,
        }

    def _search(self, root: Path, target: Path, pattern: str) -> Dict[str, Any]:
        if not target.exists():
            return {"error": f"Not found: {self._rel(root, target)}"}

        needle = (pattern or "").strip().lower()
        matches: List[Dict[str, Any]] = []

        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                if len(matches) >= MAX_RESULTS:
                    break
                if name.startswith("."):
                    continue
                ext = Path(name).suffix.lower()
                if ext and ext not in ALLOWED_EXTS:
                    continue
                if needle and needle not in name.lower():
                    continue
                full = Path(dirpath) / name
                matches.append(
                    {
                        "path": self._rel(root, full),
                        "name": name,
                        "size": full.stat().st_size if full.exists() else 0,
                    }
                )
        return {
            "root": self._rel(root, target),
            "pattern": pattern,
            "match_count": len(matches),
            "matches": matches,
        }

    def _read(self, root: Path, target: Path) -> Dict[str, Any]:
        if not target.exists():
            return {"error": f"Not found: {self._rel(root, target)}"}
        if target.is_dir():
            return {"error": f"{self._rel(root, target)} is a directory. Use action='list'."}

        ext = target.suffix.lower()
        if ext and ext not in ALLOWED_EXTS:
            return {
                "error": f"Cannot read '{ext}' files (not a text format).",
                "allowed_count": len(ALLOWED_EXTS),
            }

        try:
            size = target.stat().st_size
            if size > MAX_FILE_BYTES:
                return {
                    "error": f"File is {size} bytes, larger than the {MAX_FILE_BYTES} byte limit.",
                    "hint": "Read a specific portion instead.",
                }
            content = target.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            return {"error": f"Permission denied reading {self._rel(root, target)}"}
        except Exception as e:
            return {"error": f"Failed reading file: {e}"}

        return {
            "path": self._rel(root, target),
            "size": size,
            "line_count": content.count("\n") + 1,
            "content": content,
        }

    @staticmethod
    def _rel(root: Path, target: Path) -> str:
        try:
            rel = target.relative_to(root)
            return str(rel) if str(rel) != "." else "."
        except ValueError:
            return str(target)