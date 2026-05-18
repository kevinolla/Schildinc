"""
Customer DB normalizer
======================
Schild Inc's historical customer CSV is one row per *order line*. To
feed it into the customers table (which is one row per customer) we
need to aggregate.

Per unique customer name:
  - Sum every `Total Order Value` across rows → lifetime_amount_paid
  - Count rows → invoice_count
  - Earliest invoice year → first_invoice_date_utc
  - Latest invoice year → last_invoice_date_utc
  - First non-empty Email contact, Phone, Website, Country
  - Mode (most common) Main Sector / Sub Sector / B2B-B2C
  - First non-empty Contact person
  - Aggregated delivery address (first non-empty)

The result is upserted into `customers` by canonical company name (via
`customer_entity_id`).
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.country_codes import to_iso2
from app.models import Customer
from app.utils import normalize_domain, normalize_email, normalize_text


def _clean_str(v: str | None) -> str:
    """Strip + collapse internal whitespace."""
    return " ".join((v or "").split()).strip()


def _parse_amount(raw: str | None) -> float:
    """
    Parse `Total Order Value`. Strings like '265', '1,234.56', '€500',
    '500.00', '500,00' (European comma) all need to work.
    """
    if not raw:
        return 0.0
    s = re.sub(r"[^\d.,\-]", "", raw)
    if not s:
        return 0.0
    if "," in s and "." in s:
        # both → comma is thousands sep (e.g. '1,234.56')
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        # only comma → European decimal (e.g. '500,00')
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_year(raw: str | None) -> int | None:
    if not raw:
        return None
    s = raw.strip()
    # Accept '2024', '24', '2024.0', etc.
    m = re.search(r"\d{4}", s)
    if m:
        y = int(m.group(0))
        if 1990 <= y <= 2100:
            return y
    m = re.search(r"^\d{1,2}$", s)
    if m:
        n = int(m.group(0))
        if 0 <= n <= 99:
            return 2000 + n
    return None


def _date_from_year_month_day(year: int | None, month: str = "", day: str = "") -> datetime | None:
    if year is None:
        return None
    try:
        m = int(re.sub(r"[^\d]", "", month or "0") or "0") or 1
        d = int(re.sub(r"[^\d]", "", day or "0") or "0") or 1
        m = max(1, min(12, m))
        d = max(1, min(28, d))  # safe day clamp so Feb 31 doesn't error
        return datetime(year, m, d, tzinfo=timezone.utc)
    except Exception:
        return None


def _pick_first_nonempty(values: list[str]) -> str:
    for v in values:
        v = _clean_str(v)
        if v:
            return v
    return ""


def _mode_nonempty(values: list[str]) -> str:
    """Most-common non-empty value. Ties broken by first occurrence."""
    cleaned = [_clean_str(v) for v in values if _clean_str(v)]
    if not cleaned:
        return ""
    return Counter(cleaned).most_common(1)[0][0]


def _extract_city_country(address: str) -> tuple[str, str]:
    """
    Parse the multiline delivery address. Last non-empty line is the
    country; line before is usually city + postal.
    """
    if not address:
        return "", ""
    lines = [ln.strip() for ln in address.splitlines() if ln.strip()]
    if not lines:
        return "", ""
    country = lines[-1]
    # City line — heuristic: 2nd-to-last or 3rd-to-last
    city = ""
    if len(lines) >= 3:
        # Often the line above country
        candidate = lines[-2]
        # Strip postal codes like '6604 LR' or '1234 AB'
        candidate = re.sub(r"\b\d{4}\s*[A-Z]{0,2}\b", "", candidate).strip()
        if candidate:
            city = candidate
    return city[:80], country[:80]


def normalize_customer_csv(csv_text: str) -> list[dict[str, Any]]:
    """
    Parse the CSV and return one dict per unique customer name —
    ready for ON CONFLICT bulk upsert.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    # Group raw rows by canonical company name
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for raw in reader:
        name = _clean_str(raw.get("Customer name"))
        if not name:
            continue
        grouped[name].append(raw)

    out: list[dict[str, Any]] = []
    for name, rows in grouped.items():
        clean_name = normalize_text(name)
        # Build per-customer aggregate
        total = sum(_parse_amount(r.get("Total Order Value")) for r in rows)
        invoice_count = len(rows)

        # Year range from any year-style column
        years: list[int] = []
        for r in rows:
            for col in ("Year", "Inv year"):
                y = _parse_year(r.get(col))
                if y:
                    years.append(y)
        first_year = min(years) if years else None
        last_year = max(years) if years else None

        # Date precision from invoice month/day when last_year matches
        last_invoice_date = None
        for r in rows:
            y = _parse_year(r.get("Inv year"))
            if y == last_year:
                d = _date_from_year_month_day(y, r.get("Inv mnt", ""), r.get("Inv day", ""))
                if d and (last_invoice_date is None or d > last_invoice_date):
                    last_invoice_date = d
        if last_invoice_date is None and last_year is not None:
            last_invoice_date = _date_from_year_month_day(last_year, "12", "31")
        first_invoice_date = _date_from_year_month_day(first_year, "1", "1") if first_year else None

        # First non-empty contact data across all rows
        email = _pick_first_nonempty([r.get("Email contact", "") for r in rows])
        phone = _pick_first_nonempty([r.get("Phonenumber contact", "") for r in rows])
        website = _pick_first_nonempty([r.get("Website", "") for r in rows])
        contact_person = _pick_first_nonempty([r.get("Contact person", "") for r in rows])
        delivery_addr = _pick_first_nonempty([r.get("Delivery addresss:", "") for r in rows])
        country_raw = _pick_first_nonempty([r.get("Country", "") for r in rows])

        # Mode for categorical fields (some rows may be blank)
        main_sector = _mode_nonempty([r.get("Main Sector", "") for r in rows])
        sub_sector = _mode_nonempty([r.get("Sub Sector", "") for r in rows])
        segment = _mode_nonempty([r.get("B2C/B2B", "") for r in rows]).upper().replace("/", "")

        # Use the first non-empty Customer ID across rows, fall back to slug of name
        cust_id = _pick_first_nonempty([r.get("Customer ID", "") for r in rows])
        if not cust_id:
            cust_id = re.sub(r"[^a-z0-9]+", "-", clean_name).strip("-")[:80] or f"unnamed-{hash(name) & 0xffff:x}"

        # Extract city/country if not given in dedicated column
        city, addr_country = _extract_city_country(delivery_addr)
        country = country_raw or addr_country
        # Canonicalize to ISO-2 via the alias registry — handles
        # 'Netherlands', 'NL', 'NLD', 'NET' etc. uniformly
        country_code = to_iso2(country)

        # Email + domain
        norm_email = normalize_email(email) if email else ""
        email_domain = norm_email.split("@", 1)[1] if "@" in norm_email else ""
        web_domain = normalize_domain(website) if website else ""

        out.append({
            "customer_entity_id":           cust_id[:120],
            "source_system":                "schild_customer_db_csv",
            "canonical_company_name":       name[:200],
            "canonical_company_name_clean": clean_name[:200],
            "canonical_name_geo_key":       f"{clean_name}|{normalize_text(city)}"[:200],
            "match_key_primary":            clean_name[:200],
            "match_key_domain":             (email_domain or web_domain)[:120],
            "customer_email_primary":       norm_email[:200],
            "email_domain_primary":         email_domain[:120],
            "website_domain_candidate":     web_domain[:120],
            "city":                         city,
            "state":                        "",
            "country_code":                 country_code,
            "full_address":                 delivery_addr[:500],
            "billing_names_seen":           name[:300],
            "invoice_count":                invoice_count,
            "lifetime_amount_paid":         total,
            "lifetime_total_invoiced":      total,
            "first_invoice_date_utc":       first_invoice_date,
            "last_invoice_date_utc":        last_invoice_date,
            "first_paid_at_utc":            first_invoice_date,
            "last_paid_at_utc":             last_invoice_date,
            "already_client_flag":          True,
            "client_source":                "schild_customer_db",
            # Rich annotations (alembic 0010)
            "main_sector":                  main_sector[:80],
            "sub_sector":                   sub_sector[:80],
            "customer_segment":             segment[:10],
            "contact_person":               contact_person[:120],
            "phone_primary":                phone[:60],
            "website":                      website[:300],
        })

    return out


def import_customers_from_csv(
    db: Session,
    csv_text: str,
    *,
    batch_size: int = 500,
    progress_print: bool = False,
) -> dict[str, int]:
    """
    Upsert every aggregated customer into `customers` using Postgres
    ON CONFLICT (customer_entity_id) DO UPDATE.

    Why ON CONFLICT instead of select-then-insert: same reason as the
    FB leads importer — atomic, race-free, bulk-safe.
    """
    records = normalize_customer_csv(csv_text)

    # Within-batch dedup: two distinct customer NAMES can slug to the
    # same `customer_entity_id` (e.g. "Azor Bike BV" and "Azor-Bike BV").
    # ON CONFLICT can only fire once per ID per command, so collapse
    # duplicates here, keeping the highest-LTV record (most data).
    by_id: dict[str, dict[str, Any]] = {}
    for rec in records:
        cid = rec["customer_entity_id"]
        prev = by_id.get(cid)
        if prev is None or rec["lifetime_amount_paid"] > prev["lifetime_amount_paid"]:
            by_id[cid] = rec
    records = list(by_id.values())
    total = len(records)
    if progress_print:
        print(f"[customer-import] CSV aggregated to {total} unique customers")
    if not records:
        return {"upserted": 0, "skipped": 0, "total": 0}

    UPDATE_COLS = [
        "source_system", "canonical_company_name", "canonical_company_name_clean",
        "canonical_name_geo_key", "match_key_primary", "match_key_domain",
        "customer_email_primary", "email_domain_primary", "website_domain_candidate",
        "city", "state", "country_code", "full_address", "billing_names_seen",
        "invoice_count", "lifetime_amount_paid", "lifetime_total_invoiced",
        "first_invoice_date_utc", "last_invoice_date_utc",
        "first_paid_at_utc", "last_paid_at_utc",
        "already_client_flag", "client_source",
        "main_sector", "sub_sector", "customer_segment",
        "contact_person", "phone_primary", "website",
    ]

    upserted = 0
    for offset in range(0, total, batch_size):
        chunk = records[offset : offset + batch_size]
        stmt = pg_insert(Customer).values(chunk)
        excluded = stmt.excluded
        do_update = {col: getattr(excluded, col) for col in UPDATE_COLS}
        do_update["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["customer_entity_id"], set_=do_update
        )
        db.execute(stmt)
        db.commit()
        upserted += len(chunk)
        if progress_print:
            print(f"[customer-import] upserted {upserted}/{total}")

    return {"upserted": upserted, "skipped": 0, "total": total}
