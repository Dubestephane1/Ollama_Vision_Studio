"""Read a web page and return its readable text.

A local model cannot open links. This closes the gap between "search for
headlines about X" and "actually read the article about X".
"""

import re
from typing import Any, Dict, Optional

import httpx

from backend.skills.base import BaseSkill, SkillContext, summarize_result

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

MAX_HTML_BYTES = 4 * 1024 * 1024


def html_to_text(html: str) -> str:
    """Crude but dependency-free HTML -> readable text conversion."""
    # Keep the title before we strip tags.
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    title = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", "", title_m.group(1))).strip() if title_m else ""

    html = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer|aside|form)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    # Preserve structure with newlines.
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)

    for a, b in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"), ("&mdash;", "-"), ("&ndash;", "-"),
    ):
        html = html.replace(a, b)

    text = re.sub(r"[ \t]+", " ", html)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()

    if title:
        text = f"{title}\n\n{text}"
    return text


class FetchUrlSkill(BaseSkill):
    name: str = "fetch_url"
    display_name: str = "Web Page Reader"
    enabled_by_default: bool = True
    description: str = (
        "Fetch a web page and return its readable text content. Call this after a "
        "web search when you need the details of a specific article, or whenever "
        "the user gives you a URL to read or summarise."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full http:// or https:// URL to read.",
            }
        },
        "required": ["url"],
    }

    async def execute(
        self, ctx: Optional[SkillContext] = None, url: str = "", **kwargs
    ) -> Dict[str, Any]:
        url = (url or "").strip()
        if not url:
            return {"error": "No URL provided."}
        if not url.startswith(("http://", "https://")):
            if "://" not in url:
                url = "https://" + url
            else:
                return {"error": f"Unsupported URL scheme: {url}"}

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=12.0,
            ) as client:
                res = await client.get(url)

            if res.status_code >= 400:
                return {"error": f"HTTP {res.status_code} fetching {url}"}

            raw = res.content[:MAX_HTML_BYTES]
            ctype = res.headers.get("content-type", "")
            text = html_to_text(raw.decode(res.encoding or "utf-8", errors="replace"))

            if not text:
                return {"error": f"No readable text found at {url} (content-type: {ctype})"}

            return {
                "url": str(res.url),
                "status_code": res.status_code,
                "content_type": ctype,
                "char_count": len(text),
                "truncated": len(raw) >= MAX_HTML_BYTES,
                "content": text,
            }
        except httpx.TimeoutException:
            return {"error": f"Timed out fetching {url}"}
        except Exception as e:
            return {"error": f"Failed fetching {url}: {e}"}