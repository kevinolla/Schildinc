#!/usr/bin/env python
"""Schild Inc — CRM audience export pipeline (Meta Ads + Google Ads).

READ-ONLY against the CRM. Writes PII output files OUTSIDE the git repo so they
can never be committed. Does not modify or delete any source record.

Sources used (profiled 2026-07-10):
    customers (3256)         -> completed customers (lifetime_amount_paid>0)
    invoices  (1780)         -> order validation (aggregates already on customers)
    facebook_leads (8745)    -> inbound Meta Lead Ads leads (email+phone+consent)
    prospects (13426)        -> cold crawler/maps businesses (NO consent)
    kvk_companies (3990)     -> cold Dutch businesses (NO consent)
    suppression_entries (2)  -> unsubscribes
    contacts (11527)         -> do_not_contact flag (0 set today)

Honest scope: the CRM has NO quotes/deals pipeline, NO web-event tracking
(form-start/checkout/sample/design/logo/pricing-page), and NO ad-click IDs
(GCLID/GBRAID/WBRAID/FBCLID). Audiences that depend on those are reported as
"not buildable — missing source data" rather than fabricated.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

DB_URL = os.environ.get("AUDIENCE_DB_URL", "")  # read-only CRM connection string
OUT = os.environ.get("AUDIENCE_OUT_DIR", "C:/Users/Kevin/AI Workspace/audience_exports")
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()

for sub in ("meta", "google", "crm_analysis", "audit"):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)

# ── Normalization ───────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
PLACEHOLDER = ("test@", "example.com", "example.org", "noreply", "no-reply",
               "donotreply", "@test", "asdf", "none@", "n/a", "na@", "xxx@")

COUNTRY_MAP = {
    "netherlands": "NL", "nederland": "NL", "the netherlands": "NL", "holland": "NL", "nl": "NL",
    "belgium": "BE", "belgie": "BE", "belgië": "BE", "belgique": "BE", "be": "BE",
    "germany": "DE", "deutschland": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB", "gb": "GB",
    "united states": "US", "usa": "US", "united states of america": "US", "us": "US",
    "switzerland": "CH", "ch": "CH", "austria": "AT", "at": "AT", "italy": "IT", "it": "IT",
    "spain": "ES", "es": "ES", "denmark": "DK", "dk": "DK", "sweden": "SE", "se": "SE",
    "norway": "NO", "no": "NO", "ireland": "IE", "ie": "IE", "canada": "CA", "ca": "CA",
    "luxembourg": "LU", "lu": "LU", "poland": "PL", "pl": "PL", "portugal": "PT", "pt": "PT",
    "finland": "FI", "fi": "FI",
}
CC_DIAL = {"NL": "31", "BE": "32", "DE": "49", "FR": "33", "GB": "44", "US": "1", "CH": "41",
           "AT": "43", "IT": "39", "ES": "34", "DK": "45", "SE": "46", "NO": "47", "IE": "353",
           "CA": "1", "LU": "352", "PL": "48", "PT": "351", "FI": "358"}
LANG_BY_CC = {"NL": "nl", "BE": "nl", "DE": "de", "AT": "de", "CH": "de", "FR": "fr",
              "GB": "en", "US": "en", "IE": "en", "CA": "en"}

SECTOR_MAP = {
    "bike": "bike_shop", "candles": "candles", "woodwork": "woodworker",
    "furniture": "furniture_maker", "steelwork": "product_manufacturer",
    "music": "other_qualified_b2b", "fashion": "clothing_accessories",
    "liquor & bottles": "brewery_beverage", "art": "other_qualified_b2b",
    "service": "other_qualified_b2b", "uncategorized": "unknown",
}


def norm_email(v):
    v = (v or "").strip().lower()
    if not v or not EMAIL_RE.match(v):
        return ""
    if any(p in v for p in PLACEHOLDER):
        return ""
    return v


def norm_country(v):
    v = (v or "").strip()
    if not v or v == "?":
        return ""
    key = v.lower()
    if key in COUNTRY_MAP:
        return COUNTRY_MAP[key]
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return ""


def norm_phone(v, cc):
    """E.164 best-effort. Returns '' when it can't be normalized reliably."""
    v = (v or "").strip()
    if not v:
        return ""
    had_plus = v.strip().startswith("+") or v.strip().startswith("00")
    digits = re.sub(r"\D", "", v)
    if v.strip().startswith("00"):
        digits = digits[2:]
    if len(digits) < 7 or len(digits) > 15:
        return ""
    dial = CC_DIAL.get(cc or "")
    if had_plus:
        return "+" + digits
    if dial:
        if digits.startswith(dial):
            return "+" + digits
        # strip a national trunk 0 then prepend country code
        nat = digits[1:] if digits.startswith("0") else digits
        return "+" + dial + nat
    return ""  # no country context -> cannot reliably normalize


def norm_sector(v):
    return SECTOR_MAP.get((v or "").strip().lower(), "unknown")


def split_name(full, contact_person=""):
    s = (contact_person or full or "").strip()
    if not s:
        return "", ""
    # Avoid using a company name as a person name: drop legal suffixes.
    if re.search(r"\b(b\.?v\.?|gmbh|ltd|inc|nv|vof|sarl|sas|e\.?k\.?)\b", s, re.I):
        return "", ""
    parts = s.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def sha256(v):
    return hashlib.sha256(v.encode("utf-8")).hexdigest() if v else ""


def parse_postal(full_address):
    """Extract a postal code from a comma-joined address like
    'Keizersdijk 48, RAAMSDONKSVEER, 4941 GG, NL'. Returns '' if none found."""
    if not full_address:
        return ""
    parts = [p.strip() for p in full_address.split(",")]
    # A postal segment contains a digit and is short; the country code (last) does not.
    for seg in reversed(parts):
        if seg and any(ch.isdigit() for ch in seg) and len(seg) <= 10:
            return seg.upper().replace(" ", "")
    return ""


def clamp_days(dt):
    """Whole days since dt, clamped: future/invalid dates -> None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    d = (NOW - dt).days
    return d if d >= 0 else None  # future date = corrupt -> None


# ── Load (read-only) ────────────────────────────────────────────────────────
engine = create_engine(DB_URL)
records = []          # unified normalized records
dup_audit = []        # dropped duplicates
warnings = []

with engine.connect() as c:
    c.execute(text("SET default_transaction_read_only = on"))

    # Suppression set (emails).
    supp_emails = {norm_email(r[0]) for r in c.execute(text(
        "SELECT email FROM suppression_entries WHERE active IS TRUE")) if norm_email(r[0])}
    dnc_contacts = {norm_email(r[0]) for r in c.execute(text(
        "SELECT primary_email FROM contacts WHERE do_not_contact IS TRUE")) if norm_email(r[0])}
    supp_emails |= dnc_contacts

    # Phone fallback map from the contacts hub (more coverage than customers.phone_primary).
    contact_phone = {}
    for cid, ph in c.execute(text(
            "SELECT customer_id, primary_phone FROM contacts "
            "WHERE customer_id IS NOT NULL AND COALESCE(primary_phone,'')<>''")):
        contact_phone.setdefault(cid, ph)

    # CUSTOMERS
    for r in c.execute(text("""
        SELECT id, customer_email_primary, phone_primary, contact_person, canonical_company_name,
               website, city, state, country_code, main_sector, sub_sector, customer_segment,
               lifetime_amount_paid, invoice_count, first_invoice_date_utc, last_invoice_date_utc,
               first_paid_at_utc, last_paid_at_utc, email_domain_primary, customer_entity_id, full_address
        FROM customers""")).mappings():
        cc = norm_country(r["country_code"])
        email = norm_email(r["customer_email_primary"])
        paid = float(r["lifetime_amount_paid"] or 0)
        fn, ln = split_name("", r["contact_person"])
        last_paid = r["last_paid_at_utc"] or r["last_invoice_date_utc"]
        phone_src = r["phone_primary"] or contact_phone.get(r["id"], "")
        postal = parse_postal(r["full_address"])
        records.append({
            "src": "customer", "src_id": r["id"], "crm_contact_id": f"cust:{r['id']}",
            "crm_company_id": r["customer_entity_id"] or "", "crm_deal_id": "",
            "email": email, "phone_raw": phone_src, "cc": cc,
            "first_name": fn, "last_name": ln,
            "company_name": r["canonical_company_name"] or "",
            "company_domain": (r["email_domain_primary"] or "").lower(),
            "website": r["website"] or "", "city": r["city"] or "", "state": r["state"] or "",
            "postal": postal, "sector": norm_sector(r["main_sector"]), "sector_raw": r["main_sector"] or "",
            "segment": (r["customer_segment"] or "").upper(),
            "paid": paid, "order_count": int(r["invoice_count"] or 0),
            "first_dt": r["first_paid_at_utc"] or r["first_invoice_date_utc"],
            "last_dt": last_paid, "purchase_dt": r["first_paid_at_utc"], "last_order_dt": last_paid,
            "consent": True,        # existing paying customer = lawful basis (B2B relationship)
            "lead_source": "crm_customer", "campaign_source": "", "landing": "",
            "lead_status": "", "deal_status": "won" if paid > 0 else "", "quote_status": "",
            "meta_lead_id": "", "ad_click_ids": {},
        })

    # FACEBOOK LEADS (inbound Meta Lead Ads)
    for r in c.execute(text("""
        SELECT id, fb_lead_id, email, phone_number, full_name, company_name, country,
               main_sector, sub_sector, lead_status, progress, match_status, matched_customer_id,
               email_marketing_consent, campaign_name, ad_name, form_name, created_time_utc,
               total_order_amount, customer_segmentation
        FROM facebook_leads""")).mappings():
        cc = norm_country(r["country"])
        email = norm_email(r["email"])
        fn, ln = split_name(r["full_name"], "")
        consent_raw = (r["email_marketing_consent"] or "").strip().lower()
        consent = consent_raw in ("true", "yes", "1", "opt_in", "consented")
        is_cust = r["match_status"] == "existing_customer" or r["matched_customer_id"] is not None
        records.append({
            "src": "fb_lead", "src_id": r["id"], "crm_contact_id": f"fb:{r['id']}",
            "crm_company_id": "", "crm_deal_id": "",
            "email": email, "phone_raw": r["phone_number"], "cc": cc,
            "first_name": fn, "last_name": ln, "company_name": r["company_name"] or "",
            "company_domain": (email.split("@")[-1] if email else ""),
            "website": "", "city": "", "state": "", "postal": "",
            "sector": norm_sector(r["main_sector"]), "sector_raw": r["main_sector"] or "",
            "segment": (r["customer_segmentation"] or "").upper(),
            "paid": 0.0, "order_count": 0,
            "first_dt": r["created_time_utc"], "last_dt": r["created_time_utc"],
            "purchase_dt": None, "last_order_dt": None,
            # Lawful basis: submitted a Meta lead form to Schild. Explicit consent flag preferred.
            "consent": bool(consent) or True, "consent_explicit": bool(consent),
            "already_customer": bool(is_cust),
            "lead_source": "meta_lead_ads", "campaign_source": r["campaign_name"] or "",
            "landing": r["form_name"] or "", "lead_status": (r["lead_status"] or ""),
            "progress": (r["progress"] or ""), "deal_status": "", "quote_status": "",
            "meta_lead_id": r["fb_lead_id"] or "", "ad_click_ids": {},
        })

    # PROSPECTS (cold — NO consent, ad-ineligible)
    for r in c.execute(text("""
        SELECT id, email, phone, company_name, website, website_domain, city, country_code,
               main_sector, source, match_status, updated_at, created_at
        FROM prospects""")).mappings():
        records.append({
            "src": "prospect", "src_id": r["id"], "crm_contact_id": f"pros:{r['id']}",
            "crm_company_id": "", "crm_deal_id": "",
            "email": norm_email(r["email"]), "phone_raw": r["phone"],
            "cc": norm_country(r["country_code"]), "first_name": "", "last_name": "",
            "company_name": r["company_name"] or "", "company_domain": (r["website_domain"] or "").lower(),
            "website": r["website"] or "", "city": r["city"] or "", "state": "", "postal": "",
            "sector": norm_sector(r["main_sector"]), "sector_raw": r["main_sector"] or "",
            "segment": "", "paid": 0.0, "order_count": 0,
            "first_dt": r["created_at"], "last_dt": r["updated_at"],
            "purchase_dt": None, "last_order_dt": None,
            "consent": False, "lead_source": "cold_crawler", "campaign_source": "",
            "landing": "", "lead_status": "", "deal_status": "", "quote_status": "",
            "already_customer": str(r["match_status"]) == "existing_customer",
            "meta_lead_id": "", "ad_click_ids": {},
        })

    # KVK (cold Dutch — NO consent, ad-ineligible)
    for r in c.execute(text("""
        SELECT id, email_public, phone_public, company_name, website, website_domain, primary_city,
               country_code, already_client_flag, updated_at, created_at
        FROM kvk_companies""")).mappings():
        records.append({
            "src": "kvk", "src_id": r["id"], "crm_contact_id": f"kvk:{r['id']}",
            "crm_company_id": "", "crm_deal_id": "",
            "email": norm_email(r["email_public"]), "phone_raw": r["phone_public"],
            "cc": norm_country(r["country_code"]) or "NL", "first_name": "", "last_name": "",
            "company_name": r["company_name"] or "", "company_domain": (r["website_domain"] or "").lower(),
            "website": r["website"] or "", "city": r["primary_city"] or "", "state": "", "postal": "",
            "sector": "unknown", "sector_raw": "", "segment": "", "paid": 0.0, "order_count": 0,
            "first_dt": r["created_at"], "last_dt": r["updated_at"],
            "purchase_dt": None, "last_order_dt": None,
            "consent": False, "lead_source": "cold_kvk", "campaign_source": "",
            "landing": "", "lead_status": "", "deal_status": "", "quote_status": "",
            "already_customer": bool(r["already_client_flag"]),
            "meta_lead_id": "", "ad_click_ids": {},
        })

print(f"loaded {len(records)} raw records; {len(supp_emails)} suppressed emails")

# ── Normalize phone + derived fields ────────────────────────────────────────
for r in records:
    r["phone"] = norm_phone(r.get("phone_raw"), r.get("cc"))
    r["language"] = LANG_BY_CC.get(r["cc"], "other" if r["cc"] else "unknown")
    r["product_interest"] = "unknown"  # not tracked in CRM (documented gap)
    r["is_suppressed_email"] = bool(r["email"]) and r["email"] in supp_emails

# ── Deduplicate (priority: contact_id > email > phone > domain+name) ────────
def completeness(r):
    return sum(bool(r[k]) for k in ("email", "phone", "first_name", "last_name",
                                    "company_name", "cc", "city")) + (2 if r["paid"] > 0 else 0)

STAGE_RANK = {"customer": 0, "open_quote": 1, "high_intent_lead": 2, "middle_funnel": 3,
              "engaged_lead": 4, "cold_prospect": 5, "lost": 6, "no_fit": 7, "suppressed": 8}


def dedup_key(r):
    if r["email"]:
        return ("email", r["email"])
    if r["phone"]:
        return ("phone", r["phone"])
    if r["company_domain"] and (r["first_name"] or r["company_name"]):
        return ("domain", r["company_domain"] + "|" + (r["first_name"] or r["company_name"]).lower())
    if r["company_name"] and r["cc"]:
        return ("name_cc", r["company_name"].lower() + "|" + r["cc"])
    return ("id", r["crm_contact_id"])


# Source priority so a customer beats a lead beats a prospect on the same identity.
SRC_RANK = {"customer": 0, "fb_lead": 1, "prospect": 2, "kvk": 3}
groups = defaultdict(list)
for r in records:
    groups[dedup_key(r)].append(r)

deduped = []
for key, rs in groups.items():
    if len(rs) == 1:
        deduped.append(rs[0]); continue
    rs.sort(key=lambda r: (SRC_RANK.get(r["src"], 9), -completeness(r), -r["paid"]))
    keep = rs[0]
    # Merge: preserve most-advanced order history + suppression + recency across dups.
    keep["order_count"] = max(x["order_count"] for x in rs)
    keep["paid"] = max(x["paid"] for x in rs)
    keep["is_suppressed_email"] = any(x["is_suppressed_email"] for x in rs)
    keep["already_customer"] = any(x.get("already_customer") for x in rs)
    for x in rs[1:]:
        dup_audit.append({"kept": keep["crm_contact_id"], "dropped": x["crm_contact_id"],
                          "match_type": key[0], "match_value": key[1], "dropped_source": x["src"]})
    deduped.append(keep)

print(f"deduped {len(records)} -> {len(deduped)} ({len(dup_audit)} duplicates dropped)")

# ── Assign funnel stage ─────────────────────────────────────────────────────
EMPLOYEE_DOMAINS = ("schildinc.com", "schildinc.nl", "schildlabels.com")


def no_fit(r):
    if r["segment"] == "B2C":
        return True
    if r["email"] and r["email"].split("@")[-1] in ("gmail.com", "hotmail.com", "yahoo.com",
                                                     "outlook.com", "icloud.com") and r["src"] in ("prospect", "kvk"):
        return False  # free webmail on a business listing is common in NL; not auto no-fit
    return False


for r in deduped:
    # SUPPRESSED = genuine opt-out / employee / test only. Records that merely
    # lack a usable email+phone are NOT "suppressed" (they never opted out) —
    # they keep their natural stage and are auto-excluded from uploads because
    # eligibility requires an identifier.
    dom = r["email"].split("@")[-1] if r["email"] else ""
    reason = ""
    if r["is_suppressed_email"]:
        reason = "marketing_opt_out"
    elif dom in EMPLOYEE_DOMAINS:
        reason = "employee_internal"
    elif r["email"] and any(p in r["email"] for p in ("test@", "@test", "example.")):
        reason = "test_record"
    if reason:
        r["funnel_stage"] = "suppressed"; r["suppression_reason"] = reason; continue
    r["suppression_reason"] = "" if (r["email"] or r["phone"]) else "no_usable_identifier"

    if no_fit(r):
        r["funnel_stage"] = "no_fit"; continue
    if r["src"] == "customer" and r["paid"] > 0:
        r["funnel_stage"] = "customer"; continue
    if r["src"] == "customer":
        # in customers table but zero paid -> known client, treat as customer (exclude from acquisition)
        r["funnel_stage"] = "customer"; continue
    if r.get("already_customer"):
        r["funnel_stage"] = "customer"; continue
    if r["src"] == "fb_lead":
        # Submitted a Meta lead form = form submission = high intent inbound lead.
        r["funnel_stage"] = "high_intent_lead"; continue
    if r["src"] in ("prospect", "kvk"):
        r["funnel_stage"] = "cold_prospect"; continue
    r["funnel_stage"] = "engaged_lead"

# ── Recency / value / scores ────────────────────────────────────────────────
paid_vals = sorted(r["paid"] for r in deduped if r["funnel_stage"] == "customer" and r["paid"] > 0)


def percentile_of(v):
    if not paid_vals or v <= 0:
        return 0
    below = sum(1 for x in paid_vals if x <= v)
    return round(100 * below / len(paid_vals))


p75 = paid_vals[int(0.75 * len(paid_vals))] if paid_vals else 0
p90 = paid_vals[int(0.90 * len(paid_vals))] if paid_vals else 0

INTENT = {"customer": 100, "high_intent_lead": 60, "engaged_lead": 25,
          "cold_prospect": 5, "open_quote": 80, "middle_funnel": 35,
          "lost": 10, "no_fit": 0, "suppressed": 0}

for r in deduped:
    r["days_since_first"] = clamp_days(r["first_dt"])
    r["days_since_last"] = clamp_days(r["last_dt"])
    r["days_since_purchase"] = clamp_days(r["purchase_dt"])
    r["days_since_last_order"] = clamp_days(r["last_order_dt"])
    r["avg_order_value"] = round(r["paid"] / r["order_count"], 2) if r["order_count"] else 0
    r["value_percentile"] = percentile_of(r["paid"]) if r["funnel_stage"] == "customer" else 0
    r["intent_score"] = INTENT.get(r["funnel_stage"], 0)
    # engagement: customers/leads with recent activity score higher (capped)
    eng = 0
    if r["funnel_stage"] == "customer":
        eng = 40
    elif r["funnel_stage"] == "high_intent_lead":
        eng = 25
    elif r["funnel_stage"] == "cold_prospect":
        eng = 5
    if r["days_since_last"] is not None and r["days_since_last"] <= 90:
        eng += 10
    r["engagement_score"] = min(eng, 100)
    r["data_completeness_score"] = round(100 * completeness(r) / 11)
    # Eligibility: only records with a lawful basis (customers + own leads) AND an identifier
    has_id = bool(r["email"] or r["phone"])
    lawful = r["funnel_stage"] in ("customer", "high_intent_lead", "open_quote", "engaged_lead")
    ok = has_id and lawful and r["funnel_stage"] not in ("suppressed", "no_fit") and not r["is_suppressed_email"]
    r["meta_eligible"] = bool(ok and r["consent"])
    r["google_eligible"] = bool(ok and r["consent"])

engine.dispose()

# ── Writers ─────────────────────────────────────────────────────────────────
files_created = []


def is_bike(r):
    return r["sector"] in ("bike_shop", "bicycle_manufacturer", "bike_repair", "bike_rental") \
        or r["sector_raw"] == "Bike"


def is_furniture(r):
    return r["sector"] in ("furniture_maker", "woodworker", "interior_company") \
        or r["sector_raw"] in ("Furniture", "Woodwork")


def write_meta(name, rows):
    path = os.path.join(OUT, "meta", name)
    rows = [r for r in rows if r["meta_eligible"]]
    # de-dup identity within a single file
    seen, out = set(), []
    for r in rows:
        k = r["email"] or r["phone"]
        if k and k not in seen:
            seen.add(k); out.append(r)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["email", "phone", "fn", "ln", "ct", "st", "zip", "country", "extern_id"])
        for r in out:
            w.writerow([r["email"], r["phone"], r["first_name"].lower(), r["last_name"].lower(),
                        r["city"].lower(), r["state"].lower(), r["postal"], r["cc"].lower(),
                        r["crm_contact_id"]])
    files_created.append(("meta/" + name, len(out)))
    return len(out)


def write_google(name, rows):
    path = os.path.join(OUT, "google", name)
    rows = [r for r in rows if r["google_eligible"]]
    seen, out = set(), []
    for r in rows:
        k = r["email"] or r["phone"]
        if k and k not in seen:
            seen.add(k); out.append(r)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Email", "Phone", "First Name", "Last Name", "Country", "Zip"])
        for r in out:
            w.writerow([r["email"], r["phone"], r["first_name"], r["last_name"], r["cc"], r["postal"]])
    files_created.append(("google/" + name, len(out)))
    return len(out)


customers = [r for r in deduped if r["funnel_stage"] == "customer"]
paid_customers = [r for r in customers if r["paid"] > 0]
hi_leads = [r for r in deduped if r["funnel_stage"] == "high_intent_lead"]

top25 = [r for r in paid_customers if r["paid"] >= p75]
top10 = [r for r in paid_customers if r["paid"] >= p90]
repeat = [r for r in paid_customers if r["order_count"] >= 2]
bike_c = [r for r in customers if is_bike(r)]
furn_c = [r for r in customers if is_furniture(r)]


def reorder(lo, hi):
    return [r for r in paid_customers
            if r["days_since_last_order"] is not None and lo <= r["days_since_last_order"] <= hi]


reorder_90_180 = reorder(90, 180)
reorder_181_365 = reorder(181, 365)

# Inbound-lead recency proxy for "open quote" (NO formal quote object in CRM).
lead_0_30 = [r for r in hi_leads if r["days_since_last"] is not None and r["days_since_last"] <= 30]
lead_31_90 = [r for r in hi_leads if r["days_since_last"] is not None and 31 <= r["days_since_last"] <= 90]

# ---- Meta files ----
write_meta("META_EXCLUDE_All_Customers.csv", customers)
excl = [r for r in deduped if r["funnel_stage"] in ("no_fit", "suppressed", "lost")]
# exclusion files are allowed to include suppressed/no-fit (they are used to EXCLUDE, not target)
def write_exclusion(platform, name, rows):
    path = os.path.join(OUT, platform, name)
    seen, out = set(), []
    for r in rows:
        k = r["email"] or r["phone"]
        if k and k not in seen:
            seen.add(k); out.append(r)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if platform == "meta":
            w.writerow(["email", "phone", "fn", "ln", "ct", "st", "zip", "country", "extern_id"])
            for r in out:
                w.writerow([r["email"], r["phone"], r["first_name"].lower(), r["last_name"].lower(),
                            r["city"].lower(), r["state"].lower(), r["postal"], r["cc"].lower(), r["crm_contact_id"]])
        else:
            w.writerow(["Email", "Phone", "First Name", "Last Name", "Country", "Zip"])
            for r in out:
                w.writerow([r["email"], r["phone"], r["first_name"], r["last_name"], r["cc"], r["postal"]])
    files_created.append((f"{platform}/" + name, len(out)))
    return len(out)

write_exclusion("meta", "META_EXCLUDE_NoFit_Lost_OptOut.csv", excl)
write_meta("META_LAL_All_Customers.csv", customers)
write_meta("META_LAL_High_Value_Customers.csv", top25)
write_meta("META_LAL_Top_10_Value_Customers.csv", top10)
write_meta("META_LAL_Repeat_Customers.csv", repeat)
write_meta("META_LAL_Bike_Customers.csv", bike_c)
write_meta("META_LAL_Furniture_Customers.csv", furn_c)
write_meta("META_RT_InboundLead_0_30D.csv", lead_0_30)
write_meta("META_RT_InboundLead_31_90D.csv", lead_31_90)
write_meta("META_REORDER_Customers_90_180D.csv", reorder_90_180)
write_meta("META_REORDER_Customers_181_365D.csv", reorder_181_365)

# ---- Merged / activatable audiences (the too-small lists combined) ----
def _dedup_rows(rows):
    seen, out = set(), []
    for r in rows:
        k = r["email"] or r["phone"]
        if k and k not in seen:
            seen.add(k); out.append(r)
    return out

sector_customers = _dedup_rows(bike_c + furn_c)
leads_0_90 = _dedup_rows(lead_0_30 + lead_31_90)
reorder_90_365 = _dedup_rows(reorder_90_180 + reorder_181_365)

write_meta("META_LAL_Sector_Customers_Bike_Furniture.csv", sector_customers)
write_meta("META_RT_InboundLead_0_90D.csv", leads_0_90)
write_meta("META_REORDER_Customers_90_365D.csv", reorder_90_365)

# ---- Google files ----
write_exclusion("google", "GOOGLE_EXCLUDE_All_Customers.csv", customers)
write_google("GOOGLE_CM_Sector_Customers_Bike_Furniture.csv", sector_customers)
write_google("GOOGLE_CM_Inbound_Leads_0_90D.csv", leads_0_90)
write_google("GOOGLE_CM_Reorder_90_365D.csv", reorder_90_365)
write_exclusion("google", "GOOGLE_EXCLUDE_NoFit_OptOut.csv",
                [r for r in deduped if r["funnel_stage"] in ("no_fit", "suppressed")])
write_google("GOOGLE_CM_All_Customers.csv", customers)
write_google("GOOGLE_CM_High_Value_Customers.csv", top25)
write_google("GOOGLE_CM_Top_10_Value_Customers.csv", top10)
write_google("GOOGLE_CM_Repeat_Customers.csv", repeat)
write_google("GOOGLE_CM_Bike_Customers.csv", bike_c)
write_google("GOOGLE_CM_Furniture_Customers.csv", furn_c)
write_google("GOOGLE_CM_Inbound_Leads_0_30D.csv", lead_0_30)
write_google("GOOGLE_CM_Inbound_Leads_31_90D.csv", lead_31_90)
write_google("GOOGLE_CM_Reorder_90_180D.csv", reorder_90_180)
write_google("GOOGLE_CM_Reorder_181_365D.csv", reorder_181_365)

# ---- Internal analysis (full columns, unhashed reference) ----
with open(os.path.join(OUT, "crm_analysis", "CRM_Internal_All_Records.csv"), "w", newline="", encoding="utf-8") as f:
    cols = ["crm_contact_id", "crm_company_id", "crm_deal_id", "normalized_email", "normalized_phone",
            "email_sha256", "phone_sha256", "company_name", "company_domain", "country", "language",
            "sector", "product_interest", "funnel_stage", "lead_status", "deal_status", "quote_status",
            "lead_source", "campaign_source", "original_landing_page", "first_interaction_date",
            "last_activity_date", "quote_request_date", "quote_sent_date", "purchase_date",
            "last_order_date", "days_since_last_activity", "days_since_quote_request",
            "days_since_last_order", "completed_order_count", "total_completed_order_value",
            "average_order_value", "customer_value_percentile", "engagement_score", "intent_score",
            "data_completeness_score", "marketing_consent", "marketing_opt_out", "suppression_reason",
            "meta_audience_eligible", "google_customer_match_eligible"]
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
    for r in deduped:
        w.writerow({
            "crm_contact_id": r["crm_contact_id"], "crm_company_id": r["crm_company_id"], "crm_deal_id": "",
            "normalized_email": r["email"], "normalized_phone": r["phone"],
            "email_sha256": sha256(r["email"]), "phone_sha256": sha256(r["phone"]),
            "company_name": r["company_name"], "company_domain": r["company_domain"], "country": r["cc"],
            "language": r["language"], "sector": r["sector"], "product_interest": r["product_interest"],
            "funnel_stage": r["funnel_stage"], "lead_status": r.get("lead_status", ""),
            "deal_status": r.get("deal_status", ""), "quote_status": "", "lead_source": r["lead_source"],
            "campaign_source": r["campaign_source"], "original_landing_page": r.get("landing", ""),
            "first_interaction_date": r["first_dt"].date().isoformat() if r["first_dt"] else "",
            "last_activity_date": r["last_dt"].date().isoformat() if r["last_dt"] else "",
            "quote_request_date": "", "quote_sent_date": "",
            "purchase_date": r["purchase_dt"].date().isoformat() if r["purchase_dt"] else "",
            "last_order_date": r["last_order_dt"].date().isoformat() if r["last_order_dt"] else "",
            "days_since_last_activity": r["days_since_last"], "days_since_quote_request": "",
            "days_since_last_order": r["days_since_last_order"], "completed_order_count": r["order_count"],
            "total_completed_order_value": round(r["paid"], 2), "average_order_value": r["avg_order_value"],
            "customer_value_percentile": r["value_percentile"], "engagement_score": r["engagement_score"],
            "intent_score": r["intent_score"], "data_completeness_score": r["data_completeness_score"],
            "marketing_consent": "yes" if r["consent"] else "no",
            "marketing_opt_out": "yes" if r["is_suppressed_email"] else "no",
            "suppression_reason": r["suppression_reason"],
            "meta_audience_eligible": "yes" if r["meta_eligible"] else "no",
            "google_customer_match_eligible": "yes" if r["google_eligible"] else "no",
        })

# ---- Audits ----
with open(os.path.join(OUT, "audit", "Duplicate_Audit.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["kept", "dropped", "match_type", "match_value", "dropped_source"])
    w.writeheader(); w.writerows(dup_audit)

with open(os.path.join(OUT, "audit", "Suppressed_Records_Audit.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["crm_contact_id", "email", "company_name", "suppression_reason", "source"])
    for r in deduped:
        if r["funnel_stage"] == "suppressed":
            w.writerow([r["crm_contact_id"], r["email"], r["company_name"], r["suppression_reason"], r["src"]])

# ---- Summary + quality reports ----
by_stage = defaultdict(int)
for r in deduped:
    by_stage[r["funnel_stage"]] += 1

audiences = [
    ("META_EXCLUDE_All_Customers", "customer", "meta", "exclusion"),
    ("META_LAL_All_Customers", "customer", "meta", "lookalike_seed"),
    ("META_LAL_High_Value_Customers", "customer", "meta", "lookalike_seed"),
    ("META_LAL_Top_10_Value_Customers", "customer", "meta", "lookalike_seed"),
    ("META_LAL_Repeat_Customers", "customer", "meta", "lookalike_seed"),
    ("META_LAL_Bike_Customers", "customer", "meta", "lookalike_seed"),
    ("META_LAL_Furniture_Customers", "customer", "meta", "lookalike_seed"),
    ("META_RT_InboundLead_0_30D", "high_intent_lead", "meta", "retargeting"),
    ("META_RT_InboundLead_31_90D", "high_intent_lead", "meta", "retargeting"),
    ("META_REORDER_Customers_90_180D", "customer", "meta", "reorder"),
    ("META_REORDER_Customers_181_365D", "customer", "meta", "reorder"),
]
count_by_file = dict(files_created)
with open(os.path.join(OUT, "crm_analysis", "CRM_Audience_Summary.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["audience_name", "funnel_stage", "record_count", "valid_email", "valid_phone",
                "both_email_phone", "avg_intent_score", "avg_customer_value", "recommended_platform",
                "recommended_use"])

    def stats(rows):
        ve = sum(1 for r in rows if r["email"]); vp = sum(1 for r in rows if r["phone"])
        both = sum(1 for r in rows if r["email"] and r["phone"])
        ai = round(sum(r["intent_score"] for r in rows) / len(rows), 1) if rows else 0
        av = round(sum(r["paid"] for r in rows) / len(rows), 2) if rows else 0
        return ve, vp, both, ai, av

    named = {
        "META_LAL_All_Customers": customers, "META_LAL_High_Value_Customers": top25,
        "META_LAL_Top_10_Value_Customers": top10, "META_LAL_Repeat_Customers": repeat,
        "META_LAL_Bike_Customers": bike_c, "META_LAL_Furniture_Customers": furn_c,
        "META_RT_InboundLead_0_30D": lead_0_30, "META_RT_InboundLead_31_90D": lead_31_90,
        "META_REORDER_Customers_90_180D": reorder_90_180, "META_REORDER_Customers_181_365D": reorder_181_365,
        "META_EXCLUDE_All_Customers": customers,
    }
    for name, stage, plat, use in audiences:
        rows = named.get(name, [])
        ve, vp, both, ai, av = stats(rows)
        w.writerow([name, stage, len(rows), ve, vp, both, ai, av, plat, use])

valid_emails = sum(1 for r in deduped if r["email"])
invalid_emails = sum(1 for r in records if not norm_email(r.get("email") if False else r.get("email")))  # placeholder
invalid_email_ct = sum(1 for r in records if (r.get("email_raw") if False else 0))
with open(os.path.join(OUT, "crm_analysis", "CRM_Audience_Quality_Report.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    small = [(n, c) for n, c in files_created if c < 100 and "EXCLUDE" not in n]
    rows = [
        ("total_source_records", len(records)),
        ("unique_contacts_after_dedup", len(deduped)),
        ("duplicates_removed", len(dup_audit)),
        ("valid_emails", valid_emails),
        ("records_without_email", sum(1 for r in deduped if not r["email"])),
        ("valid_phones_e164", sum(1 for r in deduped if r["phone"])),
        ("records_without_phone", sum(1 for r in deduped if not r["phone"])),
        ("missing_country", sum(1 for r in deduped if not r["cc"])),
        ("missing_sector", sum(1 for r in deduped if r["sector"] == "unknown")),
        ("marketing_opt_outs", sum(1 for r in deduped if r["is_suppressed_email"])),
        ("suppressed_records", by_stage["suppressed"]),
        ("no_fit_records", by_stage["no_fit"]),
        ("customers", by_stage["customer"]),
        ("high_intent_leads", by_stage["high_intent_lead"]),
        ("cold_prospects", by_stage["cold_prospect"]),
        ("meta_eligible_records", sum(1 for r in deduped if r["meta_eligible"])),
        ("google_eligible_records", sum(1 for r in deduped if r["google_eligible"])),
        ("audience_files_created", len(files_created)),
        ("files_under_100_records", "; ".join(f"{n}={c}" for n, c in small) or "none"),
        ("not_buildable_missing_data",
         "open_quote/quote_open_RT (no quote object); form_starter/checkout/sample/design/logo "
         "(no web-event tracking); engaged_lead video/ad engager (no ad-engagement export); "
         "GCLID/GBRAID/WBRAID/FBCLID (not captured)"),
    ]
    for k, v in rows:
        w.writerow([k, v])

# stage counts to stdout for the run log
print("funnel stages:", dict(by_stage))
print("customers", len(customers), "paid", len(paid_customers), "top25", len(top25),
      "top10", len(top10), "repeat", len(repeat), "bike", len(bike_c), "furn", len(furn_c))
print("reorder 90-180", len(reorder_90_180), "181-365", len(reorder_181_365))
print("leads 0-30", len(lead_0_30), "31-90", len(lead_31_90))
print("meta eligible", sum(1 for r in deduped if r["meta_eligible"]))
print(f"p75={p75} p90={p90}")
print("FILES:")
for n, ct in files_created:
    print(f"  {n:48} {ct}")
print(f"OUTPUT DIR: {OUT}")
