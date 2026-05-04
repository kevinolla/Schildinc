from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from app.config import settings
from app.matching import apply_matching
from app.models import Prospect, ProspectActivityLog
from app.tiering import apply_bike_tier
from app.utils import email_domain, normalize_domain, normalize_email, normalize_text

LIKELY_PATH_KEYWORDS = [
    "contact",
    "customer-service",
    "contact-us",
    "service",
    "support",
    "clubhouses",
    "locations",
    "stores",
    "store",
    "showroom",
    "about",
    "about-us",
    "over-ons",
    "impressum",
    "legal",
    "privacy",
    "terms",
    "faq",
    "customer-care",
    "team",
    "shop",
    "repair",
    "reparatie",
    "verhuur",
    "winkel",
    "company",
    "solutions",
]
MAX_CRAWL_PAGES = 8
MAX_QUEUE_LINKS = 24
RAW_FETCH_TIMEOUT_SECONDS = 6
DISCOVERY_USER_AGENT = "Mozilla/5.0 (compatible; SchildIncProspectCrawler/2.0; +https://schildinc.com)"
MAX_BROWSER_PAGES = 3
REJECT_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "support-ticket"}
VENDOR_DOMAINS = {"2moso.com", "shopify.com", "mailchimp.com", "klaviyo.com", "zendesk.com", "salesforce.com"}
GENERIC_LOCAL_PARTS = {"info", "sales", "contact", "hello", "service", "support", "team", "mail", "office", "shop"}
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
SOCIAL_URL_PATTERN = re.compile(r"https?://(?:www\.)?(instagram\.com/[^\s\"'<>]+|linkedin\.com/[^\s\"'<>]+)", re.I)
MAILTO_PATTERN = re.compile(r"mailto:([^\"'<>?\s]+)", re.I)
TEL_PATTERN = re.compile(r"tel:([^\"'<>?\s]+)", re.I)
JSON_EMAIL_PATTERN = re.compile(r'"email"\s*:\s*"([^"]+@[^"]+)"', re.I)
WHATSAPP_URL_PATTERN = re.compile(
    r"(https?://(?:wa\.me/\+?\d[\d-]{5,}|api\.whatsapp\.com/send\?[^\"'\s<>]+)|whatsapp://send\?phone=\+?\d[\d-]{5,})",
    re.I,
)
WHATSAPP_JSON_NUMBER_PATTERN = re.compile(r'"(?:number|phone|whatsapp|wa_number)"\s*:\s*"(\+?\d[\d\s()./-]{7,}\d)"', re.I)
VISIBLE_WHATSAPP_PATTERN = re.compile(r"(?:whatsapp|whats app)[^+\d]{0,24}(\+?\d[\d\s()./-]{6,}\d)", re.I)
VISIBLE_PHONE_PATTERN = re.compile(r"(?:telephone|phone|tel\.?|telefoon|mobile|call us)[^+\d]{0,36}(\+?\d[\d\s()./-]{7,}\d)", re.I)
INSTAGRAM_HANDLE_PATTERN = re.compile(r"(?:instagram|insta)[^@\n\r]{0,48}@([A-Z0-9._]{2,30})", re.I)
OBFUSCATED_EMAIL_PATTERN = re.compile(
    r"([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)| at )\s*([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)| dot )\s*([A-Z]{2,})",
    re.I,
)
STAR_OBFUSCATED_EMAIL_PATTERN = re.compile(r"([A-Z0-9._%+-]+)\s*\*\s*([A-Z0-9.-]+)\s*\.\s*([A-Z]{2,})", re.I)
SLASH_OBFUSCATED_EMAIL_PATTERN = re.compile(r"([A-Z0-9._%+-]+)\s*//\s*([A-Z0-9.-]+)\s*/\s*([A-Z]{2,})", re.I)


@dataclass
class DiscoveryEmailCandidate:
    email: str
    source_page: str
    confidence: int


@dataclass
class DiscoveryResult:
    status: str
    email: str
    source_page: str
    confidence: int
    emails_found: list[str]
    pages_scanned: list[str]
    phone_number: str
    whatsapp_number: str
    whatsapp_url: str
    linkedin_url: str
    instagram_url: str
    summary: str
    highlights: list[str]
    error: str = ""


def discover_public_contacts_for_prospect(session: Session, prospect: Prospect) -> DiscoveryResult:
    base_url = prospect.website or ""
    if not base_url:
        result = DiscoveryResult(
            "no_website",
            "",
            "",
            0,
            [],
            [],
            prospect.phone or "",
            prospect.whatsapp_number or "",
            prospect.whatsapp_url or "",
            prospect.linkedin_url or "",
            prospect.instagram_url or "",
            "",
            [],
            "",
        )
        _apply_discovery_result(session, prospect, result)
        return result

    website = ensure_http_url(base_url)
    domain = normalize_domain(website)
    visited: list[str] = []
    email_candidates: list[DiscoveryEmailCandidate] = []
    phone_numbers: list[str] = []
    whatsapp_numbers: list[str] = []
    whatsapp_links: list[str] = []
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    snippets: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(ignore_https_errors=True)
            page.set_default_timeout(settings.playwright_timeout_ms)

            pages_to_visit = _seed_pages_to_visit(website, prospect.company_name, prospect.city)

            while pages_to_visit and len(visited) < MAX_CRAWL_PAGES:
                target = pages_to_visit.pop(0)
                if target in visited:
                    continue
                if normalize_domain(target) != domain:
                    continue
                page_info: dict | None = None
                raw_page_info: dict | None = None
                normalized_target = target

                try:
                    raw_doc = _fetch_raw_html(target)
                    normalized_target = raw_doc["url"] or normalized_target
                    raw_page_info = _extract_page_info_from_html(raw_doc["html"], normalized_target, prospect.company_name, prospect.city)
                except Exception:
                    raw_page_info = None

                should_use_browser = _should_use_browser_for_page(raw_page_info, visited, normalized_target)
                if should_use_browser:
                    try:
                        page.goto(target, wait_until="domcontentloaded")
                        try:
                            page.wait_for_load_state("networkidle", timeout=2500)
                        except PlaywrightTimeoutError:
                            pass
                        page_info = _extract_visible_page_info(page, page.url or normalized_target, prospect.company_name, prospect.city)
                    except PlaywrightError:
                        page_info = None

                merged_info = _merge_page_info(page_info, raw_page_info)
                if not merged_info:
                    continue

                effective_url = (page.url if page_info and page.url else normalized_target) or target
                visited.append(effective_url)
                email_candidates.extend(_rank_email_candidates(merged_info["emails"], effective_url, domain, prospect.company_name))
                phone_numbers.extend(merged_info["phone_numbers"])
                whatsapp_numbers.extend(merged_info["whatsapp_numbers"])
                whatsapp_links.extend(merged_info["whatsapp_urls"])
                linkedin_links.extend(merged_info["linkedin"])
                instagram_links.extend(merged_info["instagram"])
                snippets.extend(merged_info["snippets"])

                current_best = max(
                    email_candidates,
                    key=lambda item: item.confidence,
                    default=None,
                )
                if current_best and current_best.confidence >= 92 and (
                    len(visited) >= 2 or merged_info["linkedin"] or merged_info["instagram"] or merged_info["whatsapp_numbers"]
                ):
                    break
                pages_to_visit = _prepend_priority_links(pages_to_visit, merged_info["internal_links"], visited)

            browser.close()
    except Exception as exc:  # noqa: BLE001
        result = DiscoveryResult("error", "", "", 0, [], [], "", "", "", "", "", "", [], str(exc))
        _apply_discovery_result(session, prospect, result)
        return result

    best = max(email_candidates, key=lambda item: item.confidence, default=None)
    best_phone_number = _pick_best_phone_number(phone_numbers, prospect.phone)
    best_whatsapp_number = _pick_best_whatsapp_number(whatsapp_numbers, prospect.phone)
    best_whatsapp_url = _pick_best_whatsapp_url(whatsapp_links, best_whatsapp_number)
    summary = _summarize_snippets(snippets)
    highlights = _highlights_from_snippets(snippets)
    has_any_contact = bool(best or best_phone_number or best_whatsapp_number or linkedin_links or instagram_links)
    result = DiscoveryResult(
        status="found" if best else ("partial" if has_any_contact else "no_contacts"),
        email=best.email if best else "",
        source_page=best.source_page if best else "",
        confidence=best.confidence if best else 0,
        emails_found=_dedupe([item.email for item in sorted(email_candidates, key=lambda item: (-item.confidence, item.email))]),
        pages_scanned=visited,
        phone_number=best_phone_number,
        whatsapp_number=best_whatsapp_number,
        whatsapp_url=best_whatsapp_url,
        linkedin_url=_pick_social_link(linkedin_links),
        instagram_url=_pick_social_link(instagram_links),
        summary=summary,
        highlights=highlights,
    )
    _apply_discovery_result(session, prospect, result)
    return result


def prospect_needs_contact_refresh(prospect: Prospect) -> bool:
    if not settings.auto_contact_discovery_enabled:
        return False
    if not (prospect.website or "").strip():
        return False
    if prospect.email_discovery_status in {"not_started", "error", "no_website"}:
        return True
    if not any(
        [
            (prospect.email or "").strip(),
            (prospect.whatsapp_number or "").strip(),
            (prospect.instagram_url or "").strip(),
            (prospect.linkedin_url or "").strip(),
        ]
    ):
        return True
    if not (prospect.pages_scanned or "").strip():
        return True
    if not (prospect.emails_found or "").strip() and not (prospect.email or "").strip():
        return True

    latest = _latest_discovery_at(prospect)
    if latest is None:
        return True
    updated = _coerce_utc(prospect.updated_at)
    if updated and updated > latest:
        return True
    if prospect.email_discovery_status in {"partial", "no_contacts"}:
        now = datetime.now(timezone.utc)
        age_days = max(0, (now - latest).days)
        if age_days >= settings.auto_contact_refresh_days:
            return True
    return False


def ensure_prospect_contacts(session: Session, prospect: Prospect, force: bool = False) -> bool:
    if force or prospect_needs_contact_refresh(prospect):
        discover_public_contacts_for_prospect(session, prospect)
        return True
    return False


def _apply_discovery_result(session: Session, prospect: Prospect, result: DiscoveryResult) -> None:
    effective_status = result.status
    if result.email or (prospect.email or "").strip():
        effective_status = "found"
    elif result.phone_number or result.whatsapp_number or result.linkedin_url or result.instagram_url:
        effective_status = "partial"

    prospect.email_discovery_status = effective_status
    prospect.discovery_error = result.error
    prospect.website_summary = result.summary or prospect.website_summary
    prospect.discovery_highlights = "\n".join(result.highlights[:4])
    prospect.emails_found = "|".join(result.emails_found[:20])
    prospect.pages_scanned = "|".join(result.pages_scanned[:20])
    if result.phone_number and not (prospect.phone or "").strip():
        prospect.phone = result.phone_number
    if result.whatsapp_number:
        prospect.whatsapp_number = result.whatsapp_number
    if result.whatsapp_url:
        prospect.whatsapp_url = result.whatsapp_url
    if result.linkedin_url:
        prospect.linkedin_url = result.linkedin_url
    if result.instagram_url:
        prospect.instagram_url = result.instagram_url
    if result.linkedin_url or result.instagram_url or result.whatsapp_number:
        prospect.social_discovered_at = datetime.utcnow()

    if result.email:
        prospect.email = result.email
        prospect.email_domain = email_domain(result.email)
        prospect.email_source_page = result.source_page
        prospect.email_confidence = result.confidence
        prospect.email_discovered_at = datetime.utcnow()
    else:
        prospect.email_source_page = result.source_page
        prospect.email_confidence = 0

    apply_matching(session, prospect)
    apply_bike_tier(prospect)
    session.add(
        ProspectActivityLog(
            prospect=prospect,
            action_type="email_discovery",
            status=effective_status,
            source_url=result.source_page or prospect.website,
            detail=result.error or _build_discovery_log_detail(result),
        )
    )


def _latest_discovery_at(prospect: Prospect) -> datetime | None:
    candidates = [
        _coerce_utc(prospect.email_discovered_at),
        _coerce_utc(prospect.social_discovered_at),
    ]
    present = [item for item in candidates if item is not None]
    return max(present) if present else None


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _extract_visible_page_info(page, current_url: str, company_name: str = "", city: str = "") -> dict:
    links = page.locator("a").evaluate_all(
        """els => els
            .map(el => ({
              href: el.href || '',
              text: (el.innerText || '').trim(),
              label: (el.getAttribute('aria-label') || '').trim(),
              title: (el.getAttribute('title') || '').trim()
            }))"""
    )
    try:
        body_text = page.locator("body").inner_text()
    except PlaywrightError:
        body_text = ""
    try:
        raw_html = page.content()
    except PlaywrightError:
        raw_html = ""
    headings = page.locator("h1, h2, h3").all_inner_texts()
    footer_bits = _collect_visible_section_text(page, "footer, [class*='footer'], [id*='footer']")
    contact_bits = _collect_visible_section_text(page, "[class*='contact'], [id*='contact'], [class*='about'], [id*='about']")
    meta_text = _read_meta_description(page)
    structured_data = _extract_structured_data(page)
    raw_source = _extract_public_source_data(raw_html)
    readable_text = _extract_readable_text(raw_html)
    combined_text = " ".join([body_text, readable_text, *headings, *footer_bits, *contact_bits, meta_text])
    emails = {normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(combined_text)}
    emails.update(_extract_obfuscated_visible_emails(combined_text))
    emails.update(structured_data["emails"])
    emails.update(raw_source["emails"])
    for item in links:
        href = item.get("href", "") or ""
        text = " ".join([item.get("text", "") or "", item.get("label", "") or "", item.get("title", "") or ""]).strip()
        if href.startswith("mailto:"):
            emails.add(normalize_email(href.replace("mailto:", "").split("?")[0]))
        if text and EMAIL_PATTERN.search(text):
            emails.update(normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(text))

    internal_links = []
    phone_numbers = []
    whatsapp_numbers = []
    whatsapp_urls = []
    social_linkedin = []
    social_instagram = []
    raw_internal_links = []
    for item in links:
        href = item.get("href", "") or ""
        anchor_text = item.get("text", "") or ""
        label_text = " ".join([item.get("text", "") or "", item.get("label", "") or "", item.get("title", "") or ""]).lower()
        href_lower = href.lower()
        if _is_whatsapp_url(href):
            whatsapp_urls.append(href)
            parsed_number = _extract_whatsapp_number_from_url(href)
            if parsed_number:
                whatsapp_numbers.append(parsed_number)
        elif href.startswith("tel:"):
            phone_number = _normalize_phone_like_value(href.replace("tel:", ""))
            if phone_number:
                phone_numbers.append(phone_number)
        elif "linkedin.com" in href_lower or "linkedin" in label_text:
            social_linkedin.append(href)
        elif "instagram.com" in href_lower or "instagram" in label_text:
            social_instagram.append(href)
        else:
            normalized_link = _normalize_internal_link(href, current_url)
            if normalized_link:
                raw_internal_links.append({"url": normalized_link, "anchor_text": anchor_text})
        if "whatsapp" in label_text and item.get("text"):
            whatsapp_text_number = _normalize_phone_like_value(item.get("text", ""))
            if whatsapp_text_number:
                whatsapp_numbers.append(whatsapp_text_number)
    internal_links = _prioritize_internal_links(raw_internal_links, current_url, company_name, city)
    phone_numbers.extend(_extract_visible_phone_numbers(combined_text))
    phone_numbers.extend(raw_source["phone_numbers"])
    whatsapp_numbers.extend(_extract_visible_whatsapp_numbers(combined_text))
    whatsapp_numbers.extend(raw_source["whatsapp_numbers"])
    whatsapp_urls.extend(raw_source["whatsapp"])
    social_linkedin.extend(structured_data["linkedin"])
    social_instagram.extend(structured_data["instagram"])
    social_linkedin.extend(raw_source["linkedin"])
    social_instagram.extend(raw_source["instagram"])
    social_instagram.extend(_extract_visible_instagram_handles(combined_text))

    snippets = [clean_snippet(text) for text in headings if clean_snippet(text)]
    snippets.extend(clean_snippet(line) for line in readable_text.split(". ")[:8] if clean_snippet(line))
    snippets.extend(clean_snippet(line) for line in footer_bits[:3] if clean_snippet(line))
    if meta_text:
        snippets.append(clean_snippet(meta_text))

    return {
        "emails": sorted(emails),
        "phone_numbers": _dedupe([item for item in phone_numbers if item]),
        "whatsapp_numbers": _dedupe([item for item in whatsapp_numbers if item]),
        "whatsapp_urls": _dedupe([item for item in whatsapp_urls if item]),
        "internal_links": internal_links,
        "linkedin": _dedupe(social_linkedin),
        "instagram": _dedupe(social_instagram),
        "snippets": _dedupe([item for item in snippets if item]),
    }


def _rank_email_candidates(emails: list[str], source_page: str, website_domain: str, company_name: str) -> list[DiscoveryEmailCandidate]:
    results: list[DiscoveryEmailCandidate] = []
    company_tokens = {token for token in normalize_text(company_name).split() if len(token) > 2}
    for email in emails:
        if not _looks_valid_business_email(email, website_domain, company_tokens):
            continue
        local = email.split("@", 1)[0]
        external_domain = email_domain(email)
        score = 30
        if external_domain == website_domain:
            score += 35
        elif _looks_related_business_domain(external_domain, website_domain, company_tokens):
            score += 18
        elif local in GENERIC_LOCAL_PARTS or any(local.startswith(prefix) for prefix in GENERIC_LOCAL_PARTS):
            score += 8
        else:
            score -= 10

        if local in GENERIC_LOCAL_PARTS:
            score += 30
        elif any(local.startswith(prefix) for prefix in GENERIC_LOCAL_PARTS):
            score += 24
        elif "." in local or "-" in local:
            score += 10

        if any(part in source_page.lower() for part in LIKELY_PATH_KEYWORDS):
            score += 8

        results.append(DiscoveryEmailCandidate(email=email, source_page=source_page, confidence=max(0, min(score, 100))))
    return results


def _looks_valid_business_email(email: str, website_domain: str, company_tokens: set[str]) -> bool:
    if not email or "@" not in email:
        return False
    local, domain = email.split("@", 1)
    if local in REJECT_LOCAL_PARTS or any(token in local for token in REJECT_LOCAL_PARTS):
        return False
    if any(local.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")):
        return False
    if domain in VENDOR_DOMAINS and domain != website_domain:
        return False
    if "example." in domain or "test." in domain:
        return False
    if domain != website_domain and not _looks_related_business_domain(domain, website_domain, company_tokens):
        if local not in GENERIC_LOCAL_PARTS and not any(local.startswith(prefix) for prefix in GENERIC_LOCAL_PARTS):
            return False
    return True


def _build_likely_urls(website: str) -> list[str]:
    parsed = urlparse(website)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [urljoin(root + "/", path) for path in LIKELY_PATH_KEYWORDS]


def _build_contextual_likely_urls(website: str, company_name: str = "", city: str = "") -> list[str]:
    parsed = urlparse(website)
    root = f"{parsed.scheme}://{parsed.netloc}"
    city_slug = _slugify_path_part(city)
    company_slug = _slugify_path_part(company_name)
    candidates = []
    if city_slug:
        candidates.extend(
            [
                f"clubhouses/{city_slug}",
                f"locations/{city_slug}",
                f"stores/{city_slug}",
                f"store/{city_slug}",
                f"showroom/{city_slug}",
                f"winkel/{city_slug}",
            ]
        )
    if company_slug:
        candidates.extend([f"locations/{company_slug}", f"stores/{company_slug}"])
    candidates.extend(
        [
            "contact",
            "contact-us",
            "customer-service",
            "service",
            "support",
            "about",
            "over-ons",
            "locations",
            "stores",
            "clubhouses",
        ]
    )
    return [urljoin(root + "/", path) for path in candidates]


def _seed_pages_to_visit(website: str, company_name: str = "", city: str = "") -> list[str]:
    ordered = [website]
    for link in _build_contextual_likely_urls(website, company_name, city):
        if link not in ordered:
            ordered.append(link)
    return ordered


def _prepend_priority_links(queue: list[str], links: list[str], visited: list[str]) -> list[str]:
    priority_links = [href for href in links if href and href not in visited and href not in queue]
    available_slots = max(0, MAX_QUEUE_LINKS - len(queue))
    priority_links = priority_links[:available_slots]
    return priority_links + queue


def _normalize_internal_link(href: str, current_url: str) -> str:
    if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
        return ""
    current = urlparse(current_url)
    try:
        target = urlparse(urljoin(current_url, href))
    except ValueError:
        return ""
    if target.netloc and normalize_domain(target.netloc) != normalize_domain(current.netloc):
        return ""
    if re.search(r"\.(pdf|jpg|jpeg|png|gif|svg|webp|zip|js|css|woff|woff2|ttf|eot|ico)$", target.path, re.I):
        return ""
    junk_haystack = f"{target.path.lower()}?{target.query.lower()}"
    if any(
        token in junk_haystack
        for token in [
            "/wp-content/",
            "/_next/static/",
            "/static/chunks/",
            "add-to-cart",
            "/cart",
            "/checkout",
            "/my-account",
            "wishlist",
            "orderby=",
            "min_price=",
            "max_price=",
            "dgwt_wcas=",
        ]
    ):
        return ""
    target = target._replace(fragment="", query=target.query)
    return target.geturl()


def _prioritize_internal_links(links: list[dict[str, str]], base_url: str, company_name: str = "", city: str = "") -> list[str]:
    base = _normalize_internal_link(base_url, base_url)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    priorities = [
        ("customer-service", 28),
        ("customer service", 28),
        ("contact", 24),
        ("telephone", 20),
        ("email", 20),
        ("service", 18),
        ("support", 18),
        ("clubhouse", 18),
        ("clubhouses", 18),
        ("location", 16),
        ("locations", 16),
        ("store", 16),
        ("stores", 16),
        ("showroom", 16),
        ("shop finder", 14),
        ("shop-finder", 14),
        ("about", 9),
        ("over", 9),
        ("team", 8),
        ("repair", 7),
        ("reparatie", 7),
        ("verhuur", 7),
        ("winkel", 7),
        ("company", 6),
        ("faq", 4),
    ]
    city_tokens = {token for token in normalize_text(city).split() if len(token) > 2}
    company_tokens = {token for token in normalize_text(company_name).split() if len(token) > 3}
    generic_company_tokens = {"bike", "bikes", "fiets", "fietsen", "rental", "rent", "tours", "store", "shop", "amsterdam"}
    company_tokens = company_tokens - generic_company_tokens
    for link in links:
        url = str(link.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        haystack = f"{url.lower()} {(link.get('anchor_text') or '').lower()}"
        score = 0
        for keyword, points in priorities:
            if keyword in haystack:
                score += points
        if city_tokens and city_tokens & _link_tokens(haystack):
            score += 22
        if company_tokens and company_tokens & _link_tokens(haystack):
            score += 10
        if url.rstrip("/") == str(base).rstrip("/"):
            score -= 20
        if score > 0:
            scored.append((score, url))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in scored[:MAX_QUEUE_LINKS]]


def _pick_social_link(links: list[str]) -> str:
    cleaned = _dedupe([_clean_social_url(link) for link in links if link])
    for link in cleaned:
        if "/company/" in link or "/business/" in link:
            return link
    return cleaned[0] if cleaned else ""


def _clean_social_url(value: str) -> str:
    link = unescape(str(value or "")).replace("\\/", "/").strip()
    return link.rstrip(").,;\"'<>\\")


def _slugify_path_part(value: str) -> str:
    parts = [part for part in re.split(r"[^a-z0-9]+", normalize_text(value)) if part]
    return "-".join(parts[:4])


def _link_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", normalize_text(value)) if len(token) > 2}


def _collect_visible_section_text(page, selector: str) -> list[str]:
    try:
        return page.locator(selector).evaluate_all(
            """els => els
                .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
                .map(el => (el.innerText || '').trim())
                .filter(Boolean)
                .slice(0, 8)"""
        )
    except PlaywrightError:
        return []


def _read_meta_description(page) -> str:
    try:
        value = page.locator("meta[name='description']").first.get_attribute("content")
        return (value or "").strip()
    except PlaywrightError:
        return ""


def _extract_structured_data(page) -> dict[str, list[str]]:
    try:
        raw_scripts = page.locator("script[type='application/ld+json']").evaluate_all(
            """els => els.map(el => el.textContent || '').filter(Boolean)"""
        )
    except PlaywrightError:
        raw_scripts = []

    emails: list[str] = []
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    for raw in raw_scripts:
        for payload in _iter_json_ld_payloads(raw):
            _walk_schema_value(payload, emails, linkedin_links, instagram_links)
    return {
        "emails": _dedupe([normalize_email(item) for item in emails if item]),
        "linkedin": _dedupe(linkedin_links),
        "instagram": _dedupe(instagram_links),
    }


def _iter_json_ld_payloads(raw: str) -> list[object]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _walk_schema_value(value: object, emails: list[str], linkedin_links: list[str], instagram_links: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_name = str(key).lower()
            if key_name == "email" and isinstance(item, str):
                emails.append(item.replace("mailto:", "").strip())
            elif key_name == "sameas":
                if isinstance(item, list):
                    for link in item:
                        _collect_social_from_string(link, linkedin_links, instagram_links)
                elif isinstance(item, str):
                    _collect_social_from_string(item, linkedin_links, instagram_links)
            else:
                _walk_schema_value(item, emails, linkedin_links, instagram_links)
    elif isinstance(value, list):
        for item in value:
            _walk_schema_value(item, emails, linkedin_links, instagram_links)


def _collect_social_from_string(value: object, linkedin_links: list[str], instagram_links: list[str]) -> None:
    link = _clean_social_url(str(value or "").strip())
    lower = link.lower()
    if "linkedin.com" in lower:
        linkedin_links.append(link)
    elif "instagram.com" in lower:
        instagram_links.append(link)


def _extract_public_source_data(raw_html: str) -> dict[str, list[str]]:
    text = raw_html or ""
    normalized_source = unescape(text).replace("\\/", "/")
    emails = [normalize_email(unquote(match.group(1))) for match in MAILTO_PATTERN.finditer(text)]
    emails.extend(normalize_email(match.group(1)) for match in JSON_EMAIL_PATTERN.finditer(text))
    emails.extend(_extract_obfuscated_visible_emails(unquote(normalized_source)))
    emails.extend(_extract_symbol_obfuscated_emails(normalized_source))
    emails.extend(_extract_cloudflare_protected_emails(text))
    emails.extend(normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(normalized_source))

    phone_numbers = [_normalize_phone_like_value(unquote(match.group(1))) for match in TEL_PATTERN.finditer(normalized_source)]
    whatsapp_links = [match.group(1) for match in WHATSAPP_URL_PATTERN.finditer(normalized_source)]
    whatsapp_numbers = [_extract_whatsapp_number_from_url(link) for link in whatsapp_links]
    if "whatsapp" in normalized_source.lower() or "wa.me" in normalized_source.lower():
        whatsapp_numbers.extend(_normalize_phone_like_value(match.group(1)) for match in WHATSAPP_JSON_NUMBER_PATTERN.finditer(normalized_source))
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    for match in SOCIAL_URL_PATTERN.finditer(normalized_source):
        href = _clean_social_url(match.group(0))
        if "linkedin.com" in href.lower():
            linkedin_links.append(href)
        elif "instagram.com" in href.lower():
            instagram_links.append(href)
    return {
        "emails": _dedupe([item for item in emails if item]),
        "phone_numbers": _dedupe([item for item in phone_numbers if item]),
        "whatsapp": _dedupe([item for item in whatsapp_links if item]),
        "whatsapp_numbers": _dedupe([item for item in whatsapp_numbers if item]),
        "linkedin": _dedupe(linkedin_links),
        "instagram": _dedupe(instagram_links),
    }


def _fetch_raw_html(target_url: str) -> dict[str, str]:
    request = Request(
        ensure_http_url(target_url),
        headers={
            "User-Agent": DISCOVERY_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=RAW_FETCH_TIMEOUT_SECONDS) as response:
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            raise URLError(f"Non-HTML response: {content_type}")
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")
        return {"url": response.geturl() or target_url, "html": html}


def _extract_page_info_from_html(raw_html: str, current_url: str, company_name: str = "", city: str = "") -> dict:
    readable_text = _extract_readable_text(raw_html)
    raw_source = _extract_public_source_data(raw_html)
    combined_text = " ".join(
        [
            readable_text,
            extract_title_from_html(raw_html),
            extract_meta_description_from_html(raw_html),
            " ".join(extract_open_graph_descriptions_from_html(raw_html)),
        ]
    )
    emails = {normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(combined_text)}
    emails.update(_extract_obfuscated_visible_emails(readable_text))
    emails.update(_extract_symbol_obfuscated_emails(combined_text))
    emails.update(raw_source["emails"])
    title = extract_title_from_html(raw_html)
    meta_text = extract_meta_description_from_html(raw_html)
    og_meta = extract_open_graph_descriptions_from_html(raw_html)
    for source_text in [title, meta_text, *og_meta]:
        if source_text:
            emails.update(normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(source_text))
            emails.update(_extract_obfuscated_visible_emails(source_text))
    raw_internal_links = _extract_internal_links_from_html(raw_html, current_url)
    raw_internal_links.extend(_extract_internal_urls_from_source(raw_html, current_url))
    internal_links = _prioritize_internal_links(raw_internal_links, current_url, company_name, city)
    phone_numbers = _dedupe(raw_source["phone_numbers"] + _extract_visible_phone_numbers(combined_text))
    headings = extract_headings_from_html(raw_html)
    snippets = [clean_snippet(item) for item in [title, meta_text, *og_meta, *headings] if clean_snippet(item)]
    snippets.extend(clean_snippet(line) for line in readable_text.split(". ")[:8] if clean_snippet(line))
    return {
        "emails": sorted(emails),
        "phone_numbers": phone_numbers,
        "whatsapp_numbers": _dedupe(raw_source["whatsapp_numbers"]),
        "whatsapp_urls": _dedupe(raw_source["whatsapp"]),
        "internal_links": internal_links,
        "linkedin": _dedupe(raw_source["linkedin"]),
        "instagram": _dedupe(raw_source["instagram"] + _extract_visible_instagram_handles(combined_text)),
        "snippets": _dedupe([item for item in snippets if item]),
    }


def _merge_page_info(primary: dict | None, fallback: dict | None) -> dict | None:
    if not primary and not fallback:
        return None
    primary = primary or {}
    fallback = fallback or {}
    return {
        "emails": _dedupe(list(primary.get("emails", [])) + list(fallback.get("emails", []))),
        "phone_numbers": _dedupe(list(primary.get("phone_numbers", [])) + list(fallback.get("phone_numbers", []))),
        "whatsapp_numbers": _dedupe(list(primary.get("whatsapp_numbers", [])) + list(fallback.get("whatsapp_numbers", []))),
        "whatsapp_urls": _dedupe(list(primary.get("whatsapp_urls", [])) + list(fallback.get("whatsapp_urls", []))),
        "internal_links": _dedupe(list(primary.get("internal_links", [])) + list(fallback.get("internal_links", []))),
        "linkedin": _dedupe(list(primary.get("linkedin", [])) + list(fallback.get("linkedin", []))),
        "instagram": _dedupe(list(primary.get("instagram", [])) + list(fallback.get("instagram", []))),
        "snippets": _dedupe(list(primary.get("snippets", [])) + list(fallback.get("snippets", []))),
    }


def _summarize_snippets(snippets: list[str]) -> str:
    useful = [item for item in snippets if len(item) > 20]
    return " ".join(useful[:2]).strip()


def _highlights_from_snippets(snippets: list[str]) -> list[str]:
    return [item for item in snippets if len(item) > 14][:4]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _extract_readable_text(html: str) -> str:
    raw = str(html or "")
    without_scripts = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    without_scripts = re.sub(r"<style[\s\S]*?</style>", " ", without_scripts, flags=re.I)
    without_scripts = re.sub(r"<noscript[\s\S]*?</noscript>", " ", without_scripts, flags=re.I)
    expanded_breaks = re.sub(r"<br\s*/?>", "\n", without_scripts, flags=re.I)
    expanded_breaks = re.sub(r"</(p|li|div|span|a|h1|h2|h3|h4|h5|h6|footer|section|article|ul|ol)>", " ", expanded_breaks, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", expanded_breaks)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return " ".join(text.split()).strip()


def extract_title_from_html(html: str) -> str:
    match = re.search(r"<title[^>]*>([\s\S]*?)</title>", html or "", re.I)
    return clean_snippet(unescape(re.sub(r"<[^>]+>", " ", match.group(1)))) if match else ""


def extract_meta_description_from_html(html: str) -> str:
    match = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"'][^>]*>", html or "", re.I)
    return clean_snippet(unescape(match.group(1))) if match else ""


def extract_open_graph_descriptions_from_html(html: str) -> list[str]:
    matches = re.finditer(
        r"<meta[^>]+(?:property|name)=[\"'](?:og:description|twitter:description)[\"'][^>]+content=[\"']([^\"']+)[\"'][^>]*>",
        html or "",
        re.I,
    )
    return [clean_snippet(unescape(match.group(1))) for match in matches if clean_snippet(unescape(match.group(1)))]


def extract_headings_from_html(html: str) -> list[str]:
    matches = re.finditer(r"<(h1|h2|h3)[^>]*>([\s\S]*?)</\1>", html or "", re.I)
    return [clean_snippet(unescape(re.sub(r"<[^>]+>", " ", match.group(2)))) for match in matches if clean_snippet(unescape(re.sub(r"<[^>]+>", " ", match.group(2))))][:8]


def _extract_internal_links_from_html(html: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>", html or "", re.I):
        href = clean_snippet(unescape(match.group(1) or ""))
        anchor_text = clean_snippet(unescape(re.sub(r"<[^>]+>", " ", match.group(2) or "")))
        normalized = _normalize_internal_link(href, base_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        links.append({"url": normalized, "anchor_text": anchor_text})
    return links


def _extract_internal_urls_from_source(html: str, base_url: str) -> list[dict[str, str]]:
    normalized_source = unescape(str(html or "")).replace("\\/", "/")
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"https?://[^\s\"'<>\\]+", normalized_source, re.I):
        raw_url = match.group(0).rstrip(").,;]")
        normalized = _normalize_internal_link(raw_url, base_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        links.append({"url": normalized, "anchor_text": ""})
    return links


def _extract_obfuscated_visible_emails(text: str) -> list[str]:
    results: list[str] = []
    for match in OBFUSCATED_EMAIL_PATTERN.finditer(text or ""):
        email = normalize_email(f"{match.group(1)}@{match.group(2)}.{match.group(3)}")
        if email:
            results.append(email)
    return results


def _extract_symbol_obfuscated_emails(text: str) -> list[str]:
    results: list[str] = []
    for pattern in [STAR_OBFUSCATED_EMAIL_PATTERN, SLASH_OBFUSCATED_EMAIL_PATTERN]:
        for match in pattern.finditer(text or ""):
            email = normalize_email(f"{match.group(1)}@{match.group(2)}.{match.group(3)}")
            if email:
                results.append(email)
    return results


def _extract_cloudflare_protected_emails(text: str) -> list[str]:
    results: list[str] = []
    for match in re.finditer(r'data-cfemail=[\"\']([0-9a-fA-F]+)[\"\']', text or "", re.I):
        decoded = _decode_cloudflare_email(match.group(1))
        if decoded:
            results.append(decoded)
    for match in re.finditer(r'aria-label=[\"\']([^\"\']+@[^\"\']+)[\"\']', text or "", re.I):
        email = normalize_email(unescape(match.group(1)))
        if email:
            results.append(email)
    return results


def _decode_cloudflare_email(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) < 4 or len(raw) % 2:
        return ""
    try:
        key = int(raw[:2], 16)
        chars = [chr(int(raw[index : index + 2], 16) ^ key) for index in range(2, len(raw), 2)]
    except ValueError:
        return ""
    return normalize_email("".join(chars))


def _should_use_browser_for_page(raw_page_info: dict | None, visited: list[str], target_url: str = "") -> bool:
    if len(visited) >= MAX_BROWSER_PAGES:
        return False
    if not raw_page_info:
        return True
    high_intent_page = any(
        token in str(target_url or "").lower()
        for token in [
            "contact",
            "customer-service",
            "service",
            "support",
            "clubhouse",
            "location",
            "store",
            "showroom",
        ]
    )
    if high_intent_page and not raw_page_info.get("emails"):
        return True
    has_contacts = any(
        [
            raw_page_info.get("emails"),
            raw_page_info.get("phone_numbers"),
            raw_page_info.get("whatsapp_numbers"),
            raw_page_info.get("linkedin"),
            raw_page_info.get("instagram"),
        ]
    )
    if not has_contacts:
        return True
    return False


def _domain_tokens(value: str) -> set[str]:
    tokens = {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(token) > 2}
    return {token for token in tokens if token not in {"www", "shop", "store", "fiets", "bike", "bikes", "fietsen", "com", "net", "org", "nl", "de", "eu"}}


def _looks_related_business_domain(domain: str, website_domain: str, company_tokens: set[str]) -> bool:
    if domain == website_domain:
        return True
    domain_tokens = _domain_tokens(domain)
    website_tokens = _domain_tokens(website_domain)
    if domain_tokens & website_tokens:
        return True
    if company_tokens and domain_tokens & company_tokens:
        return True
    return False


def _is_whatsapp_url(value: str) -> bool:
    lower = str(value or "").lower()
    return "wa.me/" in lower or "api.whatsapp.com/" in lower or lower.startswith("whatsapp://")


def _extract_whatsapp_number_from_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    raw = ""
    if "wa.me" in parsed.netloc:
        raw = parsed.path.strip("/")
    elif "api.whatsapp.com" in parsed.netloc or parsed.scheme == "whatsapp":
        raw = parse_qs(parsed.query).get("phone", [""])[0]
    return _normalize_phone_like_value(raw)


def _extract_visible_whatsapp_numbers(text: str) -> list[str]:
    results: list[str] = []
    for match in VISIBLE_WHATSAPP_PATTERN.finditer(text or ""):
        phone = _normalize_phone_like_value(match.group(1))
        if phone:
            results.append(phone)
    return results


def _extract_visible_phone_numbers(text: str) -> list[str]:
    results: list[str] = []
    for match in VISIBLE_PHONE_PATTERN.finditer(text or ""):
        phone = _normalize_phone_like_value(match.group(1))
        if phone:
            results.append(phone)
    return _dedupe(results)


def _extract_visible_instagram_handles(text: str) -> list[str]:
    links: list[str] = []
    rejected = {"instagram", "insta", "open", "profile", "social", "follow"}
    for match in INSTAGRAM_HANDLE_PATTERN.finditer(text or ""):
        handle = (match.group(1) or "").strip("._- ").lower()
        if not handle or handle in rejected:
            continue
        links.append(f"https://www.instagram.com/{handle}/")
    return _dedupe(links)


def _normalize_phone_like_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = "".join(char for char in raw if char.isdigit())
    if len(digits) < 8:
        return ""
    return f"+{digits}" if keep_plus else digits


def _pick_best_whatsapp_number(candidates: list[str], fallback_phone: str) -> str:
    for number in candidates:
        if number:
            return number
    return _normalize_phone_like_value(fallback_phone) if "whatsapp" in str(fallback_phone or "").lower() else ""


def _pick_best_phone_number(candidates: list[str], fallback_phone: str) -> str:
    normalized_candidates = _dedupe([_normalize_phone_like_value(item) for item in candidates if item])
    for number in normalized_candidates:
        return number
    return _normalize_phone_like_value(fallback_phone)


def _pick_best_whatsapp_url(candidates: list[str], number: str) -> str:
    for value in candidates:
        if value:
            return value
    if number:
        return f"https://wa.me/{number.lstrip('+')}"
    return ""


def _build_discovery_log_detail(result: DiscoveryResult) -> str:
    channels = []
    if result.email:
        channels.append(f"email={result.email}")
    if result.phone_number:
        channels.append(f"phone={result.phone_number}")
    if result.whatsapp_number:
        channels.append(f"whatsapp={result.whatsapp_number}")
    if result.instagram_url:
        channels.append("instagram")
    if result.linkedin_url:
        channels.append("linkedin")
    return ", ".join(channels) if channels else "No public contact channels found."


def ensure_http_url(value: str) -> str:
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def clean_snippet(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > 180:
        text = text[:177].rsplit(" ", 1)[0] + "..."
    return text
