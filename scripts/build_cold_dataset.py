#!/usr/bin/env python
"""Normalize the KVK list + web-crawled prospects into a clean, platform-format
dataset (Meta + Google column layout). READ-ONLY DB access; PII written outside
the git repo.

COMPLIANCE: this is COLD, scraped B2B data with no consent / relationship.
Uploading it as a Custom Audience / Customer Match *for targeting* violates both
platforms' terms and GDPR. Compliant uses: exclusion/suppression lists, cold
email (the crawler's purpose), or as reference. Files are labelled accordingly.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re

from sqlalchemy import create_engine, text

OUT = os.environ.get("COLD_OUT", r"C:/Users/Kevin/AI Workspace/cold_dataset")
DB_URL = os.environ.get("AUDIENCE_DB_URL", "")
for sub in ("meta", "google", "analysis"):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)

EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
PLACEHOLDER = ("test@", "@test", "example.", "noreply", "no-reply", "donotreply", "info@sentry")
CC_DIAL = {"NL": "31", "BE": "32", "DE": "49", "FR": "33", "GB": "44", "US": "1",
           "CH": "41", "AT": "43", "IT": "39", "ES": "34", "DK": "45", "SE": "46"}
SECTOR_MAP = {"bike": "bike_shop", "candles": "candles", "woodwork": "woodworker",
              "furniture": "furniture_maker", "steelwork": "product_manufacturer",
              "music": "other_qualified_b2b", "fashion": "clothing_accessories",
              "liquor & bottles": "brewery_beverage", "art": "other_qualified_b2b",
              "service": "other_qualified_b2b", "uncategorized": "unknown"}


def norm_email(v):
    v = (v or "").strip().lower()
    return v if v and EMAIL_RE.match(v) and not any(p in v for p in PLACEHOLDER) else ""


def norm_cc(v):
    v = (v or "").strip().upper()
    return v if len(v) == 2 and v.isalpha() and v != "??" else ""


def norm_phone(v, cc):
    v = (v or "").strip()
    if not v or v.lower().startswith(("insta-", "wa-", "fb-")):
        return ""
    plus = v.startswith("+") or v.startswith("00")
    d = re.sub(r"\D", "", v)
    if v.startswith("00"):
        d = d[2:]
    if len(d) < 8 or len(d) > 15:
        return ""
    if plus:
        return "+" + d
    dial = CC_DIAL.get(cc or "")
    if dial:
        return "+" + dial + (d[1:] if d.startswith("0") else d)
    return ""


def norm_sector(v):
    return SECTOR_MAP.get((v or "").strip().lower(), "unknown")


def sha256(v):
    return hashlib.sha256(v.encode()).hexdigest() if v else ""


rows = []
engine = create_engine(DB_URL)
with engine.connect() as c:
    c.execute(text("SET default_transaction_read_only = on"))
    exclude = {e.strip().lower() for (e,) in c.execute(text(
        "SELECT customer_email_primary FROM customers WHERE lifetime_amount_paid>0 "
        "AND COALESCE(customer_email_primary,'')<>''")) if e}
    exclude |= {e.strip().lower() for (e,) in c.execute(text(
        "SELECT email FROM suppression_entries WHERE active IS TRUE")) if e}

    # Web-crawled prospects (has sector)
    for r in c.execute(text("""
        SELECT email, phone, company_name, city, country_code, main_sector, website, website_domain, source
        FROM prospects WHERE source IN ('crawler','google_places','google_maps')
        AND COALESCE(email,'')<>''""")).mappings():
        rows.append({"email": norm_email(r["email"]), "phone_raw": r["phone"],
                     "company": r["company_name"] or "", "city": r["city"] or "",
                     "cc": norm_cc(r["country_code"]), "sector": norm_sector(r["main_sector"]),
                     "website": r["website"] or "", "domain": (r["website_domain"] or "").lower(),
                     "source": "web_crawl"})

    # KVK list (sector backfilled by scripts/backfill_kvk_sector.py, migration 0029)
    for r in c.execute(text("""
        SELECT email_public, phone_public, company_name, primary_city, country_code, website,
               website_domain, main_sector
        FROM kvk_companies WHERE COALESCE(email_public,'')<>''""")).mappings():
        rows.append({"email": norm_email(r["email_public"]), "phone_raw": r["phone_public"],
                     "company": r["company_name"] or "", "city": r["primary_city"] or "",
                     "cc": norm_cc(r["country_code"]) or "NL", "sector": norm_sector(r["main_sector"]),
                     "website": r["website"] or "", "domain": (r["website_domain"] or "").lower(),
                     "source": "kvk"})

engine.dispose()
raw = len(rows)

# Normalize phone, drop invalid email, drop existing customers/suppressed, dedup by email.
seen = {}
dropped_customer = dropped_invalid = 0
for r in rows:
    if not r["email"]:
        dropped_invalid += 1
        continue
    if r["email"] in exclude:
        dropped_customer += 1
        continue
    r["phone"] = norm_phone(r["phone_raw"], r["cc"])
    key = r["email"]
    if key in seen:
        # keep the more complete record (prefer one with sector/phone/domain)
        old = seen[key]
        score = lambda x: sum(bool(x[k]) for k in ("phone", "sector", "domain", "city")) + (1 if x["sector"] != "unknown" else 0)
        if score(r) > score(old):
            seen[key] = r
    else:
        seen[key] = r

clean = list(seen.values())
print(f"raw rows: {raw} | dropped invalid email: {dropped_invalid} | "
      f"dropped existing-customer/suppressed: {dropped_customer} | unique cold contacts: {len(clean)}")


def write_meta(name, rs):
    with open(os.path.join(OUT, "meta", name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["email", "phone", "fn", "ln", "ct", "st", "zip", "country", "extern_id"])
        for r in rs:
            w.writerow([r["email"], r["phone"], "", "", r["city"].lower(), "", "", r["cc"].lower(), "cold:" + r["email"]])
    return len(rs)


def write_google(name, rs):
    with open(os.path.join(OUT, "google", name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Email", "Phone", "First Name", "Last Name", "Country", "Zip"])
        for r in rs:
            w.writerow([r["email"], r["phone"], "", "", r["cc"], ""])
    return len(rs)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "unknown"


# Clear any stale split files from a previous run.
for d in ("meta", "google"):
    for old in os.listdir(os.path.join(OUT, d)):
        os.remove(os.path.join(OUT, d, old))

write_meta("COLD_ALL.csv", clean)
write_google("COLD_ALL.csv", clean)

countries = sorted({r["cc"] for r in clean if r["cc"]})
sectors = sorted({r["sector"] for r in clean})
matrix = {}

# By country
for cc in countries:
    sub = [r for r in clean if r["cc"] == cc]
    if len(sub) >= 1:
        write_meta(f"COLD_country_{cc}.csv", sub)
        write_google(f"COLD_country_{cc}.csv", sub)

# By sector
for sec in sectors:
    sub = [r for r in clean if r["sector"] == sec]
    if len(sub) >= 1:
        write_meta(f"COLD_sector_{slug(sec)}.csv", sub)
        write_google(f"COLD_sector_{slug(sec)}.csv", sub)

# By country x sector (only combinations with >= 20 to avoid noise; rest counted)
MIN_COMBO = 20
skipped_small = 0
for cc in countries:
    for sec in sectors:
        sub = [r for r in clean if r["cc"] == cc and r["sector"] == sec]
        matrix[(cc, sec)] = len(sub)
        if len(sub) >= MIN_COMBO:
            write_meta(f"COLD_{cc}_{slug(sec)}.csv", sub)
            write_google(f"COLD_{cc}_{slug(sec)}.csv", sub)
        elif sub:
            skipped_small += 1

# Full internal master (with hashes + all context, unhashed reference).
with open(os.path.join(OUT, "analysis", "Cold_Dataset_Master.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["email", "email_sha256", "phone_e164", "phone_sha256", "company", "domain",
                "city", "country", "sector", "source"])
    for r in clean:
        w.writerow([r["email"], sha256(r["email"]), r["phone"], sha256(r["phone"]),
                    r["company"], r["domain"], r["city"], r["cc"], r["sector"], r["source"]])

# Breakdown
from collections import Counter
cc_ct = Counter(r["cc"] or "(none)" for r in clean)
src_ct = Counter(r["source"] for r in clean)
sec_ct = Counter(r["sector"] for r in clean)
with open(os.path.join(OUT, "analysis", "Cold_Dataset_Summary.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["metric", "value"])
    w.writerow(["raw_rows_with_email", raw])
    w.writerow(["dropped_invalid_email", dropped_invalid])
    w.writerow(["dropped_existing_customer_or_suppressed", dropped_customer])
    w.writerow(["unique_cold_contacts", len(clean)])
    w.writerow(["with_phone_e164", sum(1 for r in clean if r["phone"])])
    w.writerow(["by_source", dict(src_ct)])
    w.writerow(["by_country_top", dict(cc_ct.most_common(10))])
    w.writerow(["by_sector", dict(sec_ct)])

print("by source:", dict(src_ct))
print("by country (top):", dict(cc_ct.most_common(8)))
print("by sector:", dict(sec_ct))
print("with phone:", sum(1 for r in clean if r["phone"]))
print(f"country x sector combos written (>= {MIN_COMBO}): "
      f"{sum(1 for v in matrix.values() if v >= MIN_COMBO)}; small combos skipped: {skipped_small}")
print("=== country x sector matrix (counts) ===")
hdr = "        " + "".join(f"{c:>6}" for c in countries)
print(hdr)
for sec in sectors:
    line = f"{sec[:8]:8}" + "".join(f"{matrix.get((c, sec), 0):>6}" for c in countries)
    print(line)
# write the matrix to analysis
with open(os.path.join(OUT, "analysis", "Country_Sector_Matrix.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["sector"] + countries)
    for sec in sectors:
        w.writerow([sec] + [matrix.get((c, sec), 0) for c in countries])
print("OUT:", OUT)
