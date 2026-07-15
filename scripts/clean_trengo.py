#!/usr/bin/env python
"""Clean a Trengo ticket export into Meta + Google retargeting audiences.

Rules (per request):
  - REMOVE contacts whose tickets show a transaction: labels reorder / order /
    deal (Deals, Reorder, Weborder, Wait for payment, Wait for tracking,
    Tracking, Reseller).
  - REMOVE contacts whose email already PURCHASED in the CRM (customers with
    lifetime_amount_paid > 0).
  - KEEP only contacts whose tickets carry a pre-purchase label:
    No deal / No Response / Mockup / Quote.
  - SPLIT survivors into two audiences:
      A) QUOTE_NO_PURCHASE  — asked for a quote/mockup but didn't buy.
      B) NODEAL_NORESPONSE  — no deal / no response, no quote yet.
Aggregation is per normalized email (a contact may have many tickets); labels
are unioned across all of a contact's tickets so a single transaction anywhere
removes them. Read-only CRM access; PII written outside the git repo.
"""
from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from datetime import datetime

from sqlalchemy import create_engine, text

SRC = os.environ.get("TRENGO_CSV", r"C:/Users/Kevin/Downloads/export-288351.csv")
OUT = os.environ.get("TRENGO_OUT", r"C:/Users/Kevin/AI Workspace/trengo_audiences")
DB_URL = os.environ.get("AUDIENCE_DB_URL", "")

for sub in ("meta", "google", "analysis"):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)

# Label sets (case-insensitive match).
REMOVE_LABELS = {"deals", "reorder", "weborder", "wait for payment",
                 "wait for tracking", "tracking", "reseller"}
KEEP_LABELS = {"no deal", "no response", "mockup", "quote"}
QUOTE_LABELS = {"quote", "mockup"}          # -> segment A
NODEAL_LABELS = {"no deal", "no response"}   # -> segment B
WARM_CC = {"usa": "US", "france": "FR", "netherlands": "NL", "uk": "GB",
           "canada": "CA", "belgium": "BE", "germany": "DE", "sweden": "SE",
           "europe": "", "australia": "AU", "switzerland": "CH", "denmark": "DK"}
CC_DIAL = {"US": "1", "FR": "33", "NL": "31", "GB": "44", "CA": "1", "BE": "32",
           "DE": "49", "SE": "46", "CH": "41", "DK": "45", "AU": "61"}

EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
PLACEHOLDER = ("test@", "@test", "example.", "noreply", "no-reply", "donotreply")


def norm_email(v):
    v = (v or "").strip().lower()
    return v if v and EMAIL_RE.match(v) and not any(p in v for p in PLACEHOLDER) else ""


def norm_phone(v, cc):
    v = (v or "").strip()
    if not v or v.lower().startswith(("insta-", "wa-", "fb-", "messenger")):
        return ""  # channel IDs, not phone numbers
    had_plus = v.startswith("+") or v.startswith("00")
    digits = re.sub(r"\D", "", v)
    if v.startswith("00"):
        digits = digits[2:]
    if len(digits) < 8 or len(digits) > 15:
        return ""
    if had_plus:
        return "+" + digits
    dial = CC_DIAL.get(cc or "")
    if dial:
        nat = digits[1:] if digits.startswith("0") else digits
        return "+" + dial + nat
    return ""


def split_name(display):
    s = (display or "").strip()
    if not s or "@" in s or re.search(r"\b(b\.?v\.?|gmbh|ltd|inc|nv)\b", s, re.I):
        return "", ""
    parts = s.split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else (parts[0], "")


# Country-code top-level domains -> ISO-2 (fallback when no Warm label).
TLD_CC = {".nl": "NL", ".de": "DE", ".be": "BE", ".fr": "FR", ".co.uk": "GB",
          ".uk": "GB", ".ch": "CH", ".at": "AT", ".se": "SE", ".dk": "DK",
          ".ca": "CA", ".com.au": "AU", ".ie": "IE", ".it": "IT", ".es": "ES"}


def cc_from_email(email):
    dom = email.split("@")[-1] if "@" in email else ""
    for tld, cc in TLD_CC.items():
        if dom.endswith(tld):
            return cc
    return ""


# ── Aggregate Trengo tickets by contact email ───────────────────────────────
contacts = {}       # email -> aggregate
total_rows = 0
rows_no_email = 0
with open(SRC, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        total_rows += 1
        email = norm_email(row.get("Contact email"))
        if not email:
            rows_no_email += 1
            continue
        labels = {l.strip().lower() for l in (row.get("Attached labels") or "").split(",") if l.strip()}
        cc = ""
        tier = ""
        for l in labels:
            if l.startswith("warm "):
                cc = WARM_CC.get(l[5:].strip(), "")
            if l.startswith("lead "):
                tier = l
        created = (row.get("Created at") or "").strip()
        agg = contacts.setdefault(email, {
            "email": email, "labels": set(), "cc": "", "tier": "", "phone": "",
            "last_created": "", "ticket_count": 0})
        agg["labels"] |= labels
        agg["ticket_count"] += 1
        if cc and not agg["cc"]:
            agg["cc"] = cc
        if tier:
            agg["tier"] = tier
        if created > agg["last_created"]:
            agg["last_created"] = created
        ph = norm_phone(row.get("Contact phone"), cc or agg["cc"])
        if ph and not agg["phone"]:
            agg["phone"] = ph

print(f"tickets: {total_rows} | rows without valid email: {rows_no_email} | unique contacts: {len(contacts)}")

# ── CRM: emails that already purchased (read-only) ──────────────────────────
engine = create_engine(DB_URL)
with engine.connect() as c:
    c.execute(text("SET default_transaction_read_only = on"))
    crm_paid = {e.strip().lower() for (e,) in c.execute(text(
        "SELECT customer_email_primary FROM customers WHERE lifetime_amount_paid > 0 "
        "AND COALESCE(customer_email_primary,'')<>''")) if e}
    crm_all = {e.strip().lower() for (e,) in c.execute(text(
        "SELECT customer_email_primary FROM customers WHERE COALESCE(customer_email_primary,'')<>''")) if e}
engine.dispose()
print(f"CRM paid customers: {len(crm_paid)}")

# ── Apply removal + keep rules, then segment ────────────────────────────────
removed_deal = removed_crm = removed_nokeep = 0
seg_quote, seg_nodeal = [], []
audit = []

for email, a in contacts.items():
    labels = a["labels"]
    if labels & REMOVE_LABELS:
        removed_deal += 1
        audit.append((email, "removed_transaction", ";".join(sorted(labels & REMOVE_LABELS)))); continue
    if email in crm_paid:
        removed_crm += 1
        audit.append((email, "removed_crm_purchased", "")); continue
    if not (labels & KEEP_LABELS):
        removed_nokeep += 1
        audit.append((email, "removed_no_keep_label", ";".join(sorted(labels)))); continue
    a["fn"], a["ln"] = "", ""
    a["already_in_crm"] = email in crm_all  # known contact but not a purchaser
    # Country: Warm label first, then email-domain TLD fallback.
    if not a["cc"]:
        a["cc"] = cc_from_email(email)
    if labels & QUOTE_LABELS:
        a["segment"] = "quote_no_purchase"; seg_quote.append(a)
    else:
        a["segment"] = "nodeal_noresponse"; seg_nodeal.append(a)

print(f"removed (transaction/deal): {removed_deal} | removed (CRM purchased): {removed_crm} | "
      f"removed (no keep label): {removed_nokeep}")
print(f"KEPT -> quote_no_purchase: {len(seg_quote)} | nodeal_noresponse: {len(seg_nodeal)}")


# ── Writers ─────────────────────────────────────────────────────────────────
def write_meta(name, rows):
    path = os.path.join(OUT, "meta", name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["email", "phone", "fn", "ln", "ct", "st", "zip", "country", "extern_id"])
        for r in rows:
            w.writerow([r["email"], r["phone"], "", "", "", "", "", r["cc"].lower(), "trengo:" + r["email"]])
    return len(rows)


def write_google(name, rows):
    path = os.path.join(OUT, "google", name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Email", "Phone", "First Name", "Last Name", "Country", "Zip"])
        for r in rows:
            w.writerow([r["email"], r["phone"], "", "", r["cc"], ""])
    return len(rows)


write_meta("META_RT_Quote_No_Purchase.csv", seg_quote)
write_meta("META_RT_NoDeal_NoResponse.csv", seg_nodeal)
write_google("GOOGLE_CM_Quote_No_Purchase.csv", seg_quote)
write_google("GOOGLE_CM_NoDeal_NoResponse.csv", seg_nodeal)

# ── NL + DE geo splits (the two core markets) ───────────────────────────────
def by_cc(rows, cc):
    return [r for r in rows if r["cc"] == cc]

geo_counts = {}
for cc in ("NL", "DE"):
    for seg_name, seg in (("Quote_No_Purchase", seg_quote), ("NoDeal_NoResponse", seg_nodeal)):
        rows = by_cc(seg, cc)
        geo_counts[f"{seg_name}_{cc}"] = len(rows)
        write_meta(f"META_RT_{seg_name}_{cc}.csv", rows)
        write_google(f"GOOGLE_CM_{seg_name}_{cc}.csv", rows)
    # Combined per-country audience (quote + no-deal) = one ready-to-run list.
    combined = by_cc(seg_quote, cc) + by_cc(seg_nodeal, cc)
    geo_counts[f"AllLeads_{cc}"] = len(combined)
    write_meta(f"META_RT_AllLeads_{cc}.csv", combined)
    write_google(f"GOOGLE_CM_AllLeads_{cc}.csv", combined)

# Internal cleaned master (both segments, full context).
with open(os.path.join(OUT, "analysis", "Trengo_Cleaned_Master.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["email", "phone", "segment", "country", "value_tier", "labels", "ticket_count",
                "last_ticket_date", "already_in_crm_not_purchased"])
    for a in seg_quote + seg_nodeal:
        w.writerow([a["email"], a["phone"], a["segment"], a["cc"], a["tier"],
                    ";".join(sorted(a["labels"])), a["ticket_count"], a["last_created"],
                    "yes" if a.get("already_in_crm") else "no"])

with open(os.path.join(OUT, "analysis", "Trengo_Removed_Audit.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["email", "reason", "detail"]); w.writerows(audit)

# Summary + country/tier breakdown.
def breakdown(rows, key):
    d = defaultdict(int)
    for r in rows:
        d[r[key] or "(unknown)"] += 1
    return dict(sorted(d.items(), key=lambda x: -x[1]))


with open(os.path.join(OUT, "Trengo_Summary.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["metric", "value"])
    rows = [
        ("source_tickets", total_rows),
        ("tickets_without_valid_email", rows_no_email),
        ("unique_contacts", len(contacts)),
        ("removed_transaction_deal_order_reorder", removed_deal),
        ("removed_already_purchased_in_crm", removed_crm),
        ("removed_no_keep_label", removed_nokeep),
        ("kept_total", len(seg_quote) + len(seg_nodeal)),
        ("quote_no_purchase", len(seg_quote)),
        ("quote_no_purchase_with_phone", sum(1 for r in seg_quote if r["phone"])),
        ("nodeal_noresponse", len(seg_nodeal)),
        ("nodeal_noresponse_with_phone", sum(1 for r in seg_nodeal if r["phone"])),
        ("quote_country_breakdown", breakdown(seg_quote, "cc")),
        ("nodeal_country_breakdown", breakdown(seg_nodeal, "cc")),
    ]
    for k, v in rows:
        w.writerow([k, v])

print("FILES:")
for p in ("meta/META_RT_Quote_No_Purchase.csv", "meta/META_RT_NoDeal_NoResponse.csv",
          "google/GOOGLE_CM_Quote_No_Purchase.csv", "google/GOOGLE_CM_NoDeal_NoResponse.csv"):
    print("  ", p)
print("quote country:", breakdown(seg_quote, "cc"))
print("NL/DE geo splits:", geo_counts)
print("OUT:", OUT)
