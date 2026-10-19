"""Skill engine.

A skill is a Python class that exposes a tool the LLM can call. Skills are
auto-discovered from ``backend/skills/`` at startup and are surfaced to Ollama
via the ``tools`` parameter of ``client.chat()``.

Security note: skills are arbitrary Python and run with full interpreter
privileges. Only files you have written to ``backend/skills/`` are loaded, and
only skills the user has enabled are ever exposed to the model or executable
through the API.
"""

import asyncio
import importlib
import inspect
import pkgutil
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Hard ceiling on how long a single skill may run before it is abandoned.
DEFAULT_SKILL_TIMEOUT = 30.0

# Tool results are injected back into the model context. Long results get
# truncated so a single verbose skill cannot blow the context window.
MAX_RESULT_CHARS = 4000


@dataclass
class SkillContext:
    """Ambient information a skill may use if it wants to."""

    conversation_id: Optional[str] = None
    image_paths: List[str] = field(default_factory=list)
    last_user_prompt: str = ""


class BaseSkill(ABC):
    name: str = "base_skill"
    display_name: str = "Base Skill"
    description: str = "Base description of skill"
    parameters: Dict[str, Any] = {}
    enabled_by_default: bool = True

    @abstractmethod
    async def execute(self, ctx: Optional[SkillContext] = None, **kwargs) -> Dict[str, Any]:
        """Execute the skill and return a dictionary result."""
        raise NotImplementedError

    def to_ollama_tool(self) -> Dict[str, Any]:
        """Render this skill in Ollama's function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "parameters": self.parameters,
            "enabled_by_default": self.enabled_by_default,
        }


class SkillRegistry:
    def __init__(self) -> None:
        self.skills: Dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        self.skills[skill.name] = skill

    def get_skill(self, name: str) -> Optional[BaseSkill]:
        return self.skills.get(name)

    def resolve_name(self, name: str) -> Optional[str]:
        """Map a model-supplied tool name to a real skill name.

        Small models frequently invent qualified names like ``wikipedia.search``
        or ``wikipedia:search``. Rather than failing the turn, try to recover the
        real skill. Returns None when nothing plausible matches.
        """
        if not name:
            return None
        if name in self.skills:
            return name

        # Strip a namespace/qualifier: "wikipedia.search" -> "wikipedia".
        head = re.split(r"[.:/\s]", name, maxsplit=1)[0]
        if head in self.skills:
            return head

        # Last resort: a skill whose name the hallucinated one starts with.
        for known in self.skills:
            if name.startswith(known) or known.startswith(name):
                return known

        return None

    def list_skills(self) -> List[Dict[str, Any]]:
        return [skill.to_dict() for skill in self.skills.values()]

    def default_enabled(self) -> List[str]:
        return [s.name for s in self.skills.values() if s.enabled_by_default]

    def get_ollama_tools(self, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Build the tool manifest, restricted to the allowlist ``names``."""
        if names is None:
            selected = list(self.skills.values())
        else:
            allowed = set(names)
            selected = [s for n, s in self.skills.items() if n in allowed]
        return [s.to_ollama_tool() for s in selected]

    async def execute_skill(
        self,
        name: str,
        ctx: Optional[SkillContext] = None,
        timeout: float = DEFAULT_SKILL_TIMEOUT,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        skill = self.get_skill(name)
        if not skill:
            resolved = self.resolve_name(name)
            if resolved:
                skill = self.get_skill(resolved)
            if not skill:
                known = ", ".join(sorted(self.skills)) or "(none)"
                return {
                    "error": f"No tool named '{name}'. Available tools: {known}.",
                    "hint": "Call one of the tools listed above using its exact name.",
                }
        try:
            return await asyncio.wait_for(skill.execute(ctx=ctx, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            return {"error": f"Skill '{skill.name}' timed out after {timeout:g}s."}
        except TypeError as e:
            return {"error": f"Skill '{skill.name}' rejected these arguments: {e}"}
        except Exception as e:
            return {"error": f"Skill '{skill.name}' execution failed: {e}"}


registry = SkillRegistry()


def auto_discover_skills(package_path: str, package_name: str) -> None:
    """Find and register every BaseSkill subclass in the package."""
    for _, module_name, is_pkg in pkgutil.iter_modules([package_path]):
        if is_pkg or module_name in ("base", "__init__"):
            continue
        full_module_name = f"{package_name}.{module_name}"
        try:
            module = importlib.import_module(full_module_name)
        except Exception as e:
            print(f"[skills] failed loading {full_module_name}: {e}")
            continue

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                inspect.isclass(attr)
                and issubclass(attr, BaseSkill)
                and attr is not BaseSkill
            ):
                try:
                    registry.register(attr())
                except Exception as e:
                    print(f"[skills] failed instantiating {attr_name}: {e}")


def summarize_result(result: Any, limit: int = MAX_RESULT_CHARS) -> str:
    """Render a skill result to a compact string for injection into context."""
    import json

    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)

    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"