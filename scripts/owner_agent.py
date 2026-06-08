#!/usr/bin/env python3
"""
Local Owner-Enrichment Agent (brand-safe, hands-free, background)
================================================================
Finds the OWNER / decision-maker name for KVK companies from PUBLIC search
snippets so cold emails open "Hi Jan," instead of "Hi there,".

How it stays background + CAPTCHA-free for YOU:
  • Runs a HEADLESS browser with a PERSISTENT profile, so the search engine's
    consent cookie sticks and challenges become rare.
  • Queries DuckDuckGo's HTML page in-browser (far fewer challenges than
    Google), with Google as a fallback.
  • It is 100% hands-free: if a challenge ever appears it waits briefly and
    SKIPS the record — it NEVER asks you to solve a CAPTCHA. The record stays
    pending and is retried on a later run.
  • It only reads PUBLIC result snippets — it does NOT log into or scrape
    LinkedIn/Instagram.

Usage:
    source .venv/bin/activate
    python scripts/owner_agent.py --dry-run --max 20            # safe preview (headless)
    python scripts/owner_agent.py --show --dry-run --max 10     # watch it work
    python scripts/owner_agent.py                               # live, background-safe

Flags:
    --dry-run     find names but DON'T write back
    --show        show the browser window (default: headless/background)
    --batch 25    records per fetch
    --max 100     stop after N records (0 = forever)
    --delay 4.0   seconds between searches
    --debug       explain misses
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from base64 import b64encode
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

# ── Config ───────────────────────────────────────────────────────────────────
API_BASE = "https://schild-prospect-engine-production.up.railway.app"
USERNAME = "schild"
PASSWORD = "Schildinc#01"
PROFILE_DIR = os.path.expanduser("~/.cache/schild-owner-agent")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ROLE_WORDS = [
    "Eigenaar", "Owner", "Inhaber", "Propriétaire", "Proprietaire",
    "Founder", "Oprichter", "Co-founder", "Medeoprichter", "Gründer", "Grunder",
    "Directeur", "Director", "Geschäftsführer", "Geschaftsfuhrer", "Gérant", "Gerant",
    "Zaakvoerder", "Managing Director", "CEO", "Bedrijfsleider",
]
_ROLE_ALT = "|".join(re.escape(w) for w in ROLE_WORDS)
_NAME = r"[A-ZÀ-Ý][a-zà-ÿ'\-]+(?:\s+(?:van|de|der|den|von|del|di|le|la|du)\b)*(?:\s+[A-ZÀ-Ý][a-zà-ÿ'\-]+){1,2}"
_NAME_THEN_ROLE = re.compile(rf"({_NAME})\s*[-–—,|]\s*(?:[A-Za-z ]*\b)?({_ROLE_ALT})\b")
_ROLE_THEN_NAME = re.compile(rf"\b({_ROLE_ALT})\b\s*[:\-–—]\s*({_NAME})")
_NAME_STOPWORDS = {"google", "facebook", "instagram", "linkedin", "the", "best",
                   "bike", "shop", "store", "home", "contact", "about", "privacy",
                   "duckduckgo", "bing", "maps", "reviews"}
_CHALLENGE = ["unusual traffic", "/sorry/", "are you a robot", "i'm not a robot",
              "verify you are human", "captcha"]


# ── API helpers ──────────────────────────────────────────────────────────────


def auth_header() -> dict[str, str]:
    return {"Authorization": "Basic " + b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()}


def api_get(path: str, params: dict[str, Any] | None = None, timeout: int = 45) -> Any:
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    with urlopen(Request(url, headers=auth_header()), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path: str, data: dict[str, str], timeout: int = 45) -> Any:
    req = Request(f"{API_BASE}{path}", data=urlencode(data).encode(), method="POST",
                  headers={**auth_header(), "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}", "body": exc.read().decode("utf-8", "replace")[:200]}
    except URLError as exc:
        return {"ok": False, "error": f"URLError: {exc}"}


# ── Owner extraction (pure) ──────────────────────────────────────────────────


def extract_owner(text: str, company_name: str) -> tuple[str, str]:
    if not text:
        return "", ""
    blob = re.sub(r"\s+", " ", text)
    candidates: list[tuple[str, str]] = []
    for m in _NAME_THEN_ROLE.finditer(blob):
        candidates.append((m.group(1).strip(), m.group(2).strip()))
    for m in _ROLE_THEN_NAME.finditer(blob):
        candidates.append((m.group(2).strip(), m.group(1).strip()))
    company_low = (company_name or "").lower()
    best, best_score = None, -1
    for name, role in candidates:
        toks = name.split()
        if len(toks) < 2 or len(toks) > 4:
            continue
        if any(t.lower() in _NAME_STOPWORDS for t in toks):
            continue
        score = 0
        idx = blob.lower().find(name.lower())
        cidx = blob.lower().find(company_low) if company_low else -1
        if idx >= 0 and cidx >= 0 and abs(idx - cidx) < 120:
            score += 3
        if role.lower() in ("eigenaar", "owner", "inhaber", "founder", "oprichter"):
            score += 1
        if score > best_score:
            best_score, best = score, (name, role)
    return best if best else ("", "")


def _social(text: str, pattern: str) -> str:
    m = re.search(pattern, text or "", re.I)
    return m.group(0) if m else ""


# ── In-browser search (headless, persistent profile) ─────────────────────────


def _page_text(page) -> tuple[str, bool]:
    """Return (text+hrefs, is_challenge)."""
    try:
        url = (page.url or "").lower()
        body = page.evaluate("document.body ? document.body.innerText : ''") or ""
        if any(c in url or c in body.lower() for c in _CHALLENGE):
            return "", True
        hrefs = page.evaluate("Array.from(document.querySelectorAll('a')).map(a=>a.href||'').join(' ')") or ""
        return body + " " + hrefs, False
    except Exception:
        return "", False


def fetch_snippets(page, query: str) -> tuple[str, bool]:
    """Try DuckDuckGo HTML first (rarely challenges), then Google. Returns
    (text, challenged) where challenged=True means we were blocked and should skip."""
    challenged = False
    # 1) DuckDuckGo HTML (lightweight, server-rendered)
    try:
        page.goto("https://html.duckduckgo.com/html/?q=" + quote_plus(query),
                  wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(600)
        text, ch = _page_text(page)
        if text and not ch and len(text) > 400:
            return text, False
        challenged = challenged or ch
    except Exception:
        pass
    # 2) Google fallback
    try:
        page.goto("https://www.google.com/search?q=" + quote_plus(query),
                  wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(700)
        text, ch = _page_text(page)
        if text and not ch:
            return text, False
        challenged = challenged or ch
    except Exception:
        pass
    return "", challenged


def search_owner(page, company: str, city: str, debug: bool) -> dict | str:
    roles = "eigenaar OR owner OR inhaber OR founder OR directeur OR oprichter"
    q1 = f'"{company}" {city} ({roles})'.strip()
    text, challenged = fetch_snippets(page, q1)
    if challenged and not text:
        return "challenge"
    name, role = extract_owner(text, company)
    if not name:
        q2 = f'"{company}" {city} linkedin OR instagram'.strip()
        text2, ch2 = fetch_snippets(page, q2)
        if ch2 and not text2 and not text:
            return "challenge"
        name, role = extract_owner(text2, company)
        text = text + " " + text2
    if debug:
        print(f"    q: {q1[:64]} -> name={name!r} role={role!r}", flush=True)
    return {
        "owner_name": name, "owner_role": role,
        "instagram_url": _social(text, r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._\-]+"),
        "linkedin_url": _social(text, r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|company)/[A-Za-z0-9._%\-]+"),
        "source": "public_snippet",
    }


# ── Main loop ────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", action="store_true", help="show the browser (default: headless)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--delay", type=float, default=4.0)
    ap.add_argument("--challenge-cooldown", type=int, default=120)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1

    os.makedirs(PROFILE_DIR, exist_ok=True)
    processed = found = 0
    print(f"Owner agent (headless={not args.show}, dry_run={args.dry_run}). Ctrl-C to stop.\n", flush=True)

    with sync_playwright() as pw:
        # Persistent context = consent cookie sticks => far fewer challenges.
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR, headless=not args.show, locale="nl-NL", user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            while True:
                pending = api_get("/api/enrich/owner/pending", {"limit": args.batch})
                if not pending:
                    print("No pending records. Done.", flush=True)
                    break
                for rec in pending:
                    if args.max and processed >= args.max:
                        print(f"\nReached --max {args.max}. Stopping.", flush=True)
                        return 0
                    cid, company = rec["id"], rec.get("company_name", "")
                    result = search_owner(page, company, rec.get("city", ""), args.debug)
                    if result == "challenge":
                        print(f"[skip] {company}: search challenged — cooling down "
                              f"{args.challenge_cooldown}s (stays pending, no action needed)", flush=True)
                        try:
                            page.wait_for_timeout(args.challenge_cooldown * 1000)
                        except Exception:
                            time.sleep(args.challenge_cooldown)
                        continue
                    processed += 1
                    name = result["owner_name"]
                    if name:
                        found += 1
                        print(f"[{processed}] {company}: {name}"
                              + (f" ({result['owner_role']})" if result["owner_role"] else ""), flush=True)
                    else:
                        print(f"[{processed}] {company}: no owner found", flush=True)
                    if not args.dry_run:
                        resp = api_post("/api/enrich/owner/result", {
                            "company_id": str(cid), "owner_name": name,
                            "owner_role": result["owner_role"],
                            "instagram_url": result["instagram_url"],
                            "linkedin_url": result["linkedin_url"],
                            "source": result["source"],
                        })
                        if not resp.get("ok"):
                            print(f"  ! write failed: {resp}", flush=True)
                    time.sleep(max(0.0, args.delay))
        except KeyboardInterrupt:
            print("\nStopped by user.", flush=True)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    print(f"\nDone. Processed {processed}, owners found {found}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
