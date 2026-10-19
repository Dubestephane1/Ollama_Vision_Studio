"""Wikipedia lookup - fast, structured, and reliable for a local model."""

import os
import re
from typing import Any, Dict, List, Optional

import httpx

from backend.skills.base import BaseSkill, SkillContext

API = "https://en.wikipedia.org/w/api.php"

# Wikimedia's robot policy rejects agents without a descriptive name and a
# contact URL - it also rejects spoofed browser user-agents. Override with
# WIKIPEDIA_USER_AGENT if you run this at higher volume.
USER_AGENT = os.getenv(
    "WIKIPEDIA_USER_AGENT",
    "OllamaVisionStudio/2.0 (https://github.com/local/ollama-vision-studio)",
)


class WikipediaSkill(BaseSkill):
    name: str = "wikipedia"
    display_name: str = "Wikipedia Lookup"
    description: str = (
        "Look up an article on Wikipedia and return a summary. Call this for "
        "questions about people, places, companies, historical events, science, "
        "or any factual 'who/what/when/where' question."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Article title or search term, e.g. 'Ada Lovelace' or 'Photosynthesis'.",
            },
            "language": {
                "type": "string",
                "description": "Optional language code, e.g. 'de' or 'fr'. Defaults to English.",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        ctx: Optional[SkillContext] = None,
        query: str = "",
        language: str = "en",
        **kwargs,
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"error": "No search term provided."}
        lang = (language or "en").strip().lower()[:2] or "en"

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT}, timeout=10.0
            ) as client:
                # Full-text search -> best matching article title.
                search_res = await client.get(
                    API,
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srlimit": 1,
                        "format": "json",
                    },
                    headers={"Accept-Language": lang},
                )
                if search_res.status_code != 200:
                    return {"error": f"Wikipedia search failed (HTTP {search_res.status_code})"}

                hits = search_res.json().get("query", {}).get("search", [])
                if not hits:
                    return {
                        "query": query,
                        "found": False,
                        "message": f"No Wikipedia article matches '{query}'.",
                    }

                title = hits[0]["title"]

                # Article extracts: lead section plus up to 4 short sections.
                sum_res = await client.get(
                    API,
                    params={
                        "action": "query",
                        "prop": "extracts|info",
                        "titles": title,
                        "explaintext": 1,
                        "exintro": 1,
                        "inprop": "url",
                        "format": "json",
                    },
                    headers={"Accept-Language": lang},
                )
                pages = sum_res.json().get("query", {}).get("pages", {})
                page = next(iter(pages.values()), {}) if pages else {}
                summary = page.get("extract", "").strip()

                # Full text for a little more depth.
                full_res = await client.get(
                    API,
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "titles": title,
                        "explaintext": 1,
                        "exchars": 2500,
                        "format": "json",
                    },
                    headers={"Accept-Language": lang},
                )
                fpages = full_res.json().get("query", {}).get("pages", {})
                fpage = next(iter(fpages.values()), {}) if fpages else {}
                full_text = fpage.get("extract", "").strip()

                return {
                    "query": query,
                    "found": True,
                    "title": title,
                    "url": page.get("fullurl", f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"),
                    "summary": summary or full_text[:1500],
                    "full_text": full_text,
                    "char_count": len(full_text),
                }
        except httpx.TimeoutException:
            return {"error": "Timed out contacting Wikipedia."}
        except Exception as e:
            return {"error": f"Wikipedia lookup failed: {e}"}