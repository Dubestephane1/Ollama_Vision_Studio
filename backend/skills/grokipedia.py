"""Grokipedia lookup – an open-source, unbiased alternative to Wikipedia.

Fetches articles directly from https://grokipedia.com/page/<Title>, which
is server-side rendered and readable without JavaScript.  Falls back to a
DuckDuckGo site-search when the exact title is not known.
"""

import re
import urllib.parse
from typing import Any, Dict, Optional

import httpx

from backend.skills.base import BaseSkill, SkillContext

BASE_URL = "https://grokipedia.com"
PAGE_URL = f"{BASE_URL}/page/"
SEARCH_URL = f"{BASE_URL}/search?q="

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

MAX_CONTENT_CHARS = 3000


def _html_to_text(html: str) -> str:
    """Minimal dependency-free HTML -> readable text conversion."""
    # Grab <title> before stripping tags.
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = (
        re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", "", title_m.group(1))).strip()
        if title_m
        else ""
    )
    # Remove non-content blocks.
    html = re.sub(
        r"(?is)<(script|style|noscript|svg|head|nav|footer|aside|form)[^>]*>.*?</\1>",
        " ",
        html,
    )
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    # Preserve structure.
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    # Entity decode.
    for a, b in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
        ("&mdash;", "-"), ("&ndash;", "-"),
    ):
        html = html.replace(a, b)
    text = re.sub(r"[ \t]+", " ", html)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if title:
        text = f"{title}\n\n{text}"
    return text


def _title_from_query(query: str) -> str:
    """Convert a search query to a Grokipedia-style title (Title_Case_Underscored)."""
    return "_".join(w.capitalize() for w in query.strip().split())


class GrokipediaSkill(BaseSkill):
    name: str = "grokipedia"
    display_name: str = "Grokipedia Lookup"
    enabled_by_default: bool = True
    description: str = (
        "Look up an article on Grokipedia (grokipedia.com) — an open-source, "
        "unbiased knowledge base — and return a summary. Use this for questions "
        "about people, places, companies, historical events, science, or any "
        "factual 'who/what/when/where' question."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Article title or search term, e.g. 'Ada Lovelace' or "
                    "'Photosynthesis'."
                ),
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        ctx: Optional[SkillContext] = None,
        query: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"error": "No search term provided."}

        headers = {"User-Agent": USER_AGENT}

        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=12.0
            ) as client:
                # --- 1. Try a direct title lookup (most reliable path) ------
                title_slug = _title_from_query(query)
                page_url = f"{PAGE_URL}{urllib.parse.quote(title_slug)}"
                try:
                    res = await client.get(page_url)
                    if res.status_code == 200:
                        text = _html_to_text(
                            res.content.decode(res.encoding or "utf-8", errors="replace")
                        )
                        if text and len(text) > 200:
                            snippet = text[:MAX_CONTENT_CHARS]
                            return {
                                "query": query,
                                "found": True,
                                "title": title_slug.replace("_", " "),
                                "url": str(res.url),
                                "summary": snippet,
                                "char_count": len(text),
                                "source": "Grokipedia",
                            }
                except httpx.TimeoutException:
                    return {"error": "Timed out fetching Grokipedia article."}
                except Exception:
                    pass  # Fall through to DuckDuckGo fallback.

                # --- 2. Fallback: DuckDuckGo instant answer for grokipedia.com
                try:
                    ddg_url = (
                        "https://api.duckduckgo.com/?q="
                        f"site:grokipedia.com+{urllib.parse.quote(query)}"
                        "&format=json&no_html=1&skip_disambig=1"
                    )
                    ddg_res = await client.get(ddg_url)
                    if ddg_res.status_code == 200:
                        data = ddg_res.json()
                        abstract = data.get("AbstractText", "") or ""
                        abstract_url = data.get("AbstractURL", "") or ""
                        if abstract:
                            return {
                                "query": query,
                                "found": True,
                                "title": data.get("Heading", query),
                                "url": abstract_url or page_url,
                                "summary": abstract[:MAX_CONTENT_CHARS],
                                "source": "Grokipedia (via DuckDuckGo)",
                            }

                        # Try related topics that link to grokipedia.com.
                        for topic in data.get("RelatedTopics", [])[:3]:
                            first_url = topic.get("FirstURL", "")
                            if "grokipedia.com/page/" in first_url:
                                try:
                                    art_res = await client.get(first_url)
                                    if art_res.status_code == 200:
                                        art_text = _html_to_text(
                                            art_res.content.decode(
                                                art_res.encoding or "utf-8",
                                                errors="replace",
                                            )
                                        )
                                        if art_text and len(art_text) > 200:
                                            return {
                                                "query": query,
                                                "found": True,
                                                "title": first_url.split("/page/")[-1].replace("_", " "),
                                                "url": str(art_res.url),
                                                "summary": art_text[:MAX_CONTENT_CHARS],
                                                "source": "Grokipedia",
                                            }
                                except Exception:
                                    continue
                except Exception as e:
                    return {"error": f"Grokipedia search failed: {e}"}

        except Exception as e:
            return {"error": f"Grokipedia lookup failed: {e}"}

        return {
            "query": query,
            "found": False,
            "message": (
                f"No Grokipedia article found for '{query}'. "
                f"You can browse manually at {SEARCH_URL}{urllib.parse.quote(query)}"
            ),
            "search_url": f"{SEARCH_URL}{urllib.parse.quote(query)}",
            "source": "Grokipedia",
        }
