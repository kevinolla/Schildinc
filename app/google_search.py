"""
Google Custom Search JSON API Wrapper
=====================================
Thin wrapper around https://www.googleapis.com/customsearch/v1 used as
the highest-quality input for our Stage 0 email-from-snippet finder.

Google's snippets are far richer than DuckDuckGo's HTML — they routinely
include emails right in the meta-description (e.g. "Contact us at
info@bikecity.nl") which our regex extractor can pick up in <1s without
ever opening the website.

Pricing (May 2026):
  - Free tier: 100 queries/day
  - Paid tier: $5 per 1000 additional queries
  - For 3990 KVK records at 1 query each: ~$20 max one-time cost

Setup required (one-time):
  1. Create a Custom Search Engine at
     https://programmablesearchengine.google.com/
  2. In its setup, toggle "Search the entire web"
  3. Copy the Search Engine ID → GOOGLE_CSE_CX env var
  4. Enable "Custom Search API" in the same Google Cloud project that
     hosts GOOGLE_PLACES_API_KEY (the API key can be reused)
  5. Set GOOGLE_CSE_API_KEY env var (or it falls back to
     GOOGLE_PLACES_API_KEY)

The wrapper degrades gracefully — if neither env var is set, the
caller just gets an empty list and the pipeline falls through to the
existing DDG path.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from app.config import settings


_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


def is_enabled() -> bool:
    """True only when both an API key and a CSE ID are configured."""
    return bool(
        (getattr(settings, "google_cse_api_key", "") or settings.google_places_api_key)
        and getattr(settings, "google_cse_cx", "")
    )


def _api_key() -> str:
    """CSE key falls back to the Places key (same Cloud project usually)."""
    return getattr(settings, "google_cse_api_key", "") or settings.google_places_api_key


def google_cse_search(query: str, num: int = 5, lr: str = "lang_nl") -> list[dict[str, Any]]:
    """
    Run one Google Custom Search query and return parsed result items.

    Each returned dict contains at minimum:
        title       — page title
        snippet     — meta-description excerpt (where emails live)
        link        — full URL of the result
        displayLink — host portion (e.g. www.bikecity.nl)

    Returns [] on any failure (missing key, network error, quota).
    Caller never has to think about exceptions.
    """
    if not is_enabled() or not (query or "").strip():
        return []

    params = {
        "key": _api_key(),
        "cx": settings.google_cse_cx,
        "q": query,
        "num": str(max(1, min(10, num))),
    }
    if lr:
        params["lr"] = lr

    url = f"{_CSE_ENDPOINT}?" + "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        # 429 = rate limit, 403 = quota exceeded or key disabled. Don't crash.
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body = ""
        print(f"[google-cse] HTTP {exc.code} for query={query!r}: {body}")
        return []
    except (URLError, TimeoutError, Exception):
        return []

    return list(data.get("items") or [])


def cse_snippet_text(query: str, num: int = 5) -> str:
    """
    Concatenate title + snippet + link + displayLink from every result
    into a single text blob the email regex can scan in one pass.

    Putting `link` into the blob too is intentional: sometimes Google
    surfaces a "mailto:info@…" link directly in the result metadata,
    and the regex picks that up even when the visible snippet is sparse.
    """
    items = google_cse_search(query, num=num)
    if not items:
        return ""

    parts: list[str] = []
    for item in items:
        for key in ("title", "snippet", "htmlSnippet", "link", "displayLink"):
            val = item.get(key) or ""
            if isinstance(val, str) and val:
                parts.append(val)
        # Sometimes emails hide in pagemap metatags
        pagemap = item.get("pagemap") or {}
        for tag_list in pagemap.values():
            if not isinstance(tag_list, list):
                continue
            for tag in tag_list:
                if not isinstance(tag, dict):
                    continue
                for v in tag.values():
                    if isinstance(v, str) and "@" in v:
                        parts.append(v)
    return " | ".join(parts)
