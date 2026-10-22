import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from backend.skills.base import BaseSkill, SkillContext

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

NEWS_INTENT = (
    "top news",
    "latest news",
    "breaking news",
    "current events",
    "today's news",
    "headlines",
    "news today",
    "what happened today",
)


def strip_html(html: str) -> str:
    """Remove tags/scripts/styles and collapse whitespace."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    for a, b in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
    ):
        html = html.replace(a, b)
    return re.sub(r"\s+", " ", html).strip()


class WebSearchSkill(BaseSkill):
    name: str = "web_search"
    display_name: str = "Live Web & News Search"
    enabled_by_default: bool = True
    description: str = (
        "Search the live web for current information and news. Returns headlines "
        "with sources and dates, plus a short abstract. Call this for anything "
        "time-sensitive: recent events, current versions, prices, today's news, "
        "or anything that may have changed after your training data."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query or topic. Leave blank for current top global headlines.",
            }
        },
    }

    async def execute(
        self, ctx: Optional[SkillContext] = None, query: str = "", **kwargs
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        lowered = query.lower()
        is_top_news = not query or any(k in lowered for k in NEWS_INTENT)
        effective_query = query or "Top Global Headlines"

        articles: List[Dict[str, str]] = []
        abstract_text = ""
        sources_tried: List[str] = []
        errors: List[str] = []

        headers = {"User-Agent": USER_AGENT}

        try:
            async with httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=8.0
            ) as client:
                # --- Primary: Google News RSS (works for topics and headlines)
                sources_tried.append("Google News RSS")
                if is_top_news:
                    rss_url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
                else:
                    encoded = urllib.parse.quote(query)
                    rss_url = (
                        f"https://news.google.com/rss/search?q={encoded}"
                        "&hl=en-US&gl=US&ceid=US:en"
                    )

                try:
                    res = await client.get(rss_url)
                    if res.status_code == 200:
                        root = ET.fromstring(res.text)
                        for it in root.findall(".//item")[:8]:
                            title = _text(it, "title")
                            link = _text(it, "link")
                            if not title:
                                continue
                            articles.append(
                                {
                                    "title": title,
                                    "source": _text(it, "source") or "News",
                                    "date": _text(it, "pubDate"),
                                    "url": link,
                                }
                            )
                except Exception as e:
                    errors.append(f"Google News RSS: {e}")

                # --- Fallback: DuckDuckGo Instant Answers (best effort)
                if not articles and query and not is_top_news:
                    sources_tried.append("DuckDuckGo")
                    try:
                        ddg = (
                            "https://api.duckduckgo.com/?q="
                            f"{urllib.parse.quote(query)}&format=json&no_html=1"
                        )
                        res = await client.get(ddg)
                        if res.status_code == 200:
                            data = res.json()
                            abstract_text = data.get("AbstractText", "") or ""
                            for topic in data.get("RelatedTopics", [])[:5]:
                                if "Text" in topic:
                                    articles.append(
                                        {
                                            "title": topic["Text"][:160],
                                            "source": "DuckDuckGo",
                                            "date": "",
                                            "url": topic.get("FirstURL", ""),
                                        }
                                    )
                    except Exception as e:
                        errors.append(f"DuckDuckGo: {e}")

        except Exception as e:
            return {"error": f"Failed fetching web search results: {e}"}

        if not articles and not abstract_text:
            return {
                "query": effective_query,
                "type": "headlines" if is_top_news else "search_results",
                "articles": [],
                "abstract": "",
                "error": "No results returned. Sources tried: "
                + ", ".join(sources_tried)
                + (f" | {'; '.join(errors)}" if errors else ""),
            }

        return {
            "query": effective_query,
            "type": "headlines" if is_top_news else "search_results",
            "abstract": abstract_text,
            "articles": articles,
            "result_count": len(articles),
            "sources": sources_tried,
        }


def _text(node, tag: str) -> str:
    """Safely read an RSS child element's text."""
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()