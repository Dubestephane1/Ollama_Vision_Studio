import os
import platform
import sys
from typing import Any, Dict, Optional

from backend.skills.base import BaseSkill, SkillContext
from backend.config import OLLAMA_HOST


class SystemInfoSkill(BaseSkill):
    name: str = "system_info"
    display_name: str = "Host System & Ollama Status"
    description: str = (
        "Report host machine details: OS, CPU, Python version, Ollama service "
        "reachability and which models are installed. Call this when the user asks "
        "about the machine, the environment, or which models are available."
    )
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}
    enabled_by_default: bool = False

    async def execute(
        self, ctx: Optional[SkillContext] = None, **kwargs
    ) -> Dict[str, Any]:
        import httpx

        ollama_status = "unreachable"
        models_available = []
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{OLLAMA_HOST}/api/tags")
                if res.status_code == 200:
                    data = res.json()
                    ollama_status = "online"
                    models_available = [m["name"] for m in data.get("models", [])]
        except Exception as e:
            ollama_status = f"offline ({e})"

        return {
            "os": f"{platform.system()} {platform.release()}",
            "architecture": platform.machine(),
            "python_version": sys.version.split()[0],
            "cpu_cores": os.cpu_count(),
            "ollama_host": OLLAMA_HOST,
            "ollama_status": ollama_status,
            "ollama_models": models_available[:10],
            "ollama_model_count": len(models_available),
        }