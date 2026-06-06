#!/usr/bin/env python3
"""
Local Owner-Enrichment Agent (Google-snippet, brand-safe)
=========================================================
Runs on YOUR laptop (residential IP). Finds the OWNER / decision-maker
name for KVK companies from PUBLIC Google result snippets — which routinely
surface lines like:

    "Jan de Vries - Eigenaar - Velo Amsterdam | LinkedIn"
    "Owner at Bike City Rotterdam"

…so cold emails can open "Hi Jan," instead of "Hi there,". Higher reply
rates AND better manners = protects the Schild Inc brand.

IMPORTANT — what this does NOT do:
  • It does NOT log into LinkedIn or Instagram.
  • It does NOT scrape those sites directly.
  • It only reads PUBLIC Google search snippets (the same text you'd see
    typing the query into Chrome). No ToS breach, no account-ban risk.

Usage:
    cd "/Users/kevinolla/AI Project/B2B Prospect tool"
    source .venv/bin/activate
    python scripts/owner_agent.py --dry-run --max 20 --debug   # safe trial
    python scripts/owner_agent.py                              # live, hands-free

Flags:
    --dry-run      find names but DON'T write them back (preview only)
    --batch 25     records per fetch (default 25)
    --max 100      stop after N records (0 = forever)
    --headless     hide the browser
    --debug        explain misses
    --delay 1.5    seconds between searches
"""
from __future__ import annotations

import argparse
import json
import re
import time
from base64 import b64encode
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ── Config ───────────────────────────────────────────────────────────────────
API_BASE = "https://schild-prospect-engine-production.up.railway.app"
USERNAME = "schild"
PASSWORD = "Schildinc#01"

# Owner/role keywords across NL/DE/FR/EN. Order matters for nicer role labels.
ROLE_WORDS = [
    "Eigenaar", "Owner", "Inhaber", "Propriétaire", "Proprietaire",
    "Founder", "Oprichter", "Co-founder", "Medeoprichter", "Gründer", "Grunder",
    "Directeur", "Director", "Geschäftsführer", "Geschaftsfuhrer", "Gérant", "Gerant",
    "Zaakvoerder", "Managing Director", "CEO", "Bedrijfsleider",
]
_ROLE_ALT = "|".join(re.escape(w) for w in ROLE_WORDS)

# A person name: 2–3 capitalized tokens (supports Dutch tussenvoegsels van/de/der).
_NAME = r"[A-ZÀ-Ý][a-zà-ÿ'\-]+(?:\s+(?:van|de|der|den|von|del|di|le|la|du)\b)*(?:\s+[A-ZÀ-Ý][a-zà-ÿ'\-]+){1,2}"

# "Jan de Vries - Eigenaar" / "Jan de Vries — Owner at X" / "Jan de Vries, Founder"
_NAME_THEN_ROLE = re.compile(rf"({_NAME})\s*[-–—,|]\s*(?:[A-Za-z ]*\b)?({_ROLE_ALT})\b")
# "Owner: Jan de Vries" / "Eigenaar - Jan de Vries"
_ROLE_THEN_NAME = re.compile(rf"\b({_ROLE_ALT})\b\s*[:\-–—]\s*({_NAME})")

# Junk tokens that look like names but aren't.
_NAME_STOPWORDS = {"google", "facebook", "instagram", "linkedin", "the", "best",
                   "bike", "shop", "store", "home", "contact", "about"}

_CAPTCHA_URL_FRAGMENTS = ["/sorry/", "consent.google", "consent.youtube"]
_CAPTCHA_TEXT_FRAGMENTS = ["unusual traffic", "i'm not a robot", "before you continue",
                           "verify you're human", "are you a robot"]


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


# ── Owner extraction (pure — unit-testable) ─────────────────────────────────


def extract_owner(text: str, company_name: str) -> tuple[str, str]:
    """Return (owner_name, role) from public snippet text, or ("","").

    Conservative: prefers a match that appears near the company name, rejects
    obvious junk, and normalizes spacing.
    """
    if not text:
        return "", ""
    # Collapse whitespace for cleaner regex matching.
    blob = re.sub(r"\s+", " ", text)

    candidates: list[tuple[str, str]] = []
    for m in _NAME_THEN_ROLE.finditer(blob):
        candidates.append((m.group(1).strip(), m.group(2).strip()))
    for m in _ROLE_THEN_NAME.finditer(blob):
        candidates.append((m.group(2).strip(), m.group(1).strip()))

    company_low = (company_name or "").lower()
    best: tuple[str, str] | None = None
    best_score = -1
    for name, role in candidates:
        toks = name.split()
        if len(toks) < 2 or len(toks) > 4:
            continue
        if any(t.lower() in _NAME_STOPWORDS for t in toks):
            continue
        score = 0
        # Prefer names that sit close to the company name in the text.
        idx = blob.lower().find(name.lower())
        cidx = blob.lower().find(company_low) if company_low else -1
        if idx >= 0 and cidx >= 0 and abs(idx - cidx) < 120:
            score += 3
        if role.lower() in ("eigenaar", "owner", "inhaber", "founder", "oprichter"):
            score += 1
        if score > best_score:
            best_score = score
            best = (name, role)
    if best:
        return best[0], best[1]
    return "", ""


# ── Browser search ───────────────────────────────────────────────────────────


def is_captcha_page(page) -> bool:
    try:
        url = (page.url or "").lower()
        if any(fr in url for fr in _CAPTCHA_URL_FRAGMENTS):
            return True
        body = (page.evaluate("document.body ? document.body.innerText.slice(0,800) : ''") or "").lower()
        return any(fr in body for fr in _CAPTCHA_TEXT_FRAGMENTS)
    except Exception:
        return False


def google_text(page, query: str) -> tuple[str, str]:
    url = "https://www.google.com/search?" + urlencode({"q": query})
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if is_captcha_page(page):
            return "", "captcha"
        try:
            page.wait_for_selector("div#search, div#rso, div#main", timeout=8000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(800)
        except Exception:
            pass
        if is_captcha_page(page):
            return "", "captcha"
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        hrefs = page.evaluate("Array.from(document.querySelectorAll('a')).map(a=>a.href||'').join(' ')") or ""
        return text + " " + hrefs, ""
    except Exception as exc:
        print(f"  ! query error: {exc}")
        return "", "error"


def _social(text: str, pattern: str) -> str:
    m = re.search(pattern, text or "", re.I)
    return m.group(0) if m else ""


def search_owner(page, company: str, city: str, country: str, debug: bool) -> dict | str:
    """Return {owner_name, owner_role, instagram_url, linkedin_url, source} or 'captcha'."""
    roles = "eigenaar OR owner OR inhaber OR founder OR directeur OR oprichter"
    query = f'"{company}" {city} ({roles}) (linkedin OR instagram)'.strip()
    text, status = google_text(page, query)
    if status == "captcha":
        return "captcha"
    name, role = extract_owner(text, company)
    if debug:
        print(f"    query: {query[:70]}…")
        print(f"    -> name={name!r} role={role!r}")
    return {
        "owner_name": name,
        "owner_role": role,
        "instagram_url": _social(text, r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._\-]+"),
        "linkedin_url": _social(text, r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|company)/[A-Za-z0-9._%\-]+"),
        "source": "google_snippet",
    }


# ── Main loop ────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--max", type=int, default=0, help="stop after N records (0=forever)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="find names but don't write back")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--captcha-cooldown", type=int, default=90)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1

    processed = found = 0
    print(f"Owner-enrichment agent starting (dry_run={args.dry_run}). Ctrl-C to stop.\n")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        ctx = browser.new_context(locale="nl-NL", user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        page = ctx.new_page()
        try:
            while True:
                pending = api_get("/api/enrich/owner/pending", {"limit": args.batch})
                if not pending:
                    print("No pending records. Done.")
                    break
                for rec in pending:
                    if args.max and processed >= args.max:
                        print(f"\nReached --max {args.max}. Stopping.")
                        return 0
                    cid, company = rec["id"], rec.get("company_name", "")
                    print(f"[{processed+1}] {company} ({rec.get('city','')})")
                    result = search_owner(page, company, rec.get("city", ""), rec.get("country", ""), args.debug)
                    if result == "captcha":
                        print(f"  ⚠ CAPTCHA — cooling down {args.captcha_cooldown}s, record stays pending")
                        try:
                            page.wait_for_timeout(args.captcha_cooldown * 1000)
                        except Exception:
                            time.sleep(args.captcha_cooldown)
                        continue
                    processed += 1
                    name = result["owner_name"]
                    if name:
                        found += 1
                        print(f"  ✓ {name}" + (f" ({result['owner_role']})" if result['owner_role'] else ""))
                    else:
                        print("  – no owner found")
                    if not args.dry_run:
                        resp = api_post("/api/enrich/owner/result", {
                            "company_id": str(cid),
                            "owner_name": name,
                            "owner_role": result["owner_role"],
                            "instagram_url": result["instagram_url"],
                            "linkedin_url": result["linkedin_url"],
                            "source": result["source"],
                        })
                        if not resp.get("ok"):
                            print(f"  ! write failed: {resp}")
                    time.sleep(max(0.0, args.delay))
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            browser.close()
    print(f"\nDone. Processed {processed}, owners found {found}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
