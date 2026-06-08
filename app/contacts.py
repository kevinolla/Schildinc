"""Unified Contact Hub — identity resolution, backfill, timeline.

A `Contact` is the master identity for a person/company, merging the four
source tables (Customer, KvkCompany, FacebookLead, Prospect). Resolution is
STRICT (same philosophy as KVK matching): merge only on an exact normalized
email, exact normalized phone, or exact company-name + country. No fuzzy
merges — we never want two different shops collapsed into one record.

The backfill is idempotent: re-running it attaches new sources/channels to
existing contacts rather than duplicating them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Activity,
    Contact,
    ContactChannel,
    Customer,
    EmailCampaignRecipient,
    FacebookLead,
    KvkCompany,
    Prospect,
    SuppressionEntry,
)
from app.utils import normalize_email, normalize_text, split_pipe_values


# ── Normalization ───────────────────────────────────────────────────────────


def normalize_phone(value: str | None) -> str:
    """Light E.164-ish normalization: keep a leading + and digits only."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    plus = raw.startswith("+") or raw.startswith("00")
    digits = re.sub(r"[^0-9]", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    if not digits:
        return ""
    return ("+" + digits) if plus else digits


def name_country_key(company_name: str | None, country: str | None) -> str:
    name = normalize_text(company_name)
    if len(name) < 3:
        return ""
    return f"{name}|{(country or '').strip().upper()}"


# ── Source extraction ───────────────────────────────────────────────────────


@dataclass
class SourceRecord:
    """A normalized view of one source row, ready for resolution."""
    source: str                      # customer|kvk|lead|prospect
    company_name: str = ""
    contact_person: str = ""
    city: str = ""
    country_code: str = ""
    sector: str = ""
    tier: str = ""
    website: str = ""
    lifetime_value: float = 0.0
    is_customer: bool = False
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    whatsapps: list[str] = field(default_factory=list)
    instagrams: list[str] = field(default_factory=list)
    linkedins: list[str] = field(default_factory=list)
    customer_id: int | None = None
    kvk_company_id: int | None = None
    facebook_lead_id: int | None = None
    prospect_id: int | None = None


def _customer_to_record(c: Customer) -> SourceRecord:
    emails = [c.customer_email_primary] + split_pipe_values(c.customer_email_variants)
    return SourceRecord(
        source="customer",
        company_name=c.canonical_company_name or "",
        contact_person=c.contact_person or "",
        city=c.city or "",
        country_code=c.country_code or "",
        sector=c.main_sector or "",
        website=c.website or "",
        lifetime_value=float(c.lifetime_amount_paid or 0),
        is_customer=True,
        emails=[e for e in emails if e],
        phones=[c.phone_primary] if c.phone_primary else [],
        customer_id=c.id,
    )


def _kvk_to_record(k: KvkCompany) -> SourceRecord:
    return SourceRecord(
        source="kvk",
        company_name=k.company_name or "",
        contact_person=k.owner_name or "",
        city=k.primary_city or "",
        country_code=k.country_code or "",
        tier=k.bike_shop_tier or "",
        website=k.website or "",
        is_customer=bool(k.already_client_flag),
        emails=[k.email_public] if k.email_public else [],
        phones=[k.phone_public] if k.phone_public else [],
        whatsapps=[k.whatsapp_number] if k.whatsapp_number else [],
        instagrams=[k.instagram_url] if k.instagram_url else [],
        linkedins=[k.linkedin_url] if k.linkedin_url else [],
        kvk_company_id=k.id,
    )


def _lead_to_record(l: FacebookLead) -> SourceRecord:
    return SourceRecord(
        source="lead",
        company_name=l.company_name or l.full_name or "",
        contact_person=l.full_name or "",
        country_code=l.country or "",
        sector=l.main_sector or "",
        emails=[l.email] if l.email else [],
        phones=[l.phone_number] if l.phone_number else [],
        facebook_lead_id=l.id,
    )


def _prospect_to_record(p: Prospect) -> SourceRecord:
    return SourceRecord(
        source="prospect",
        company_name=p.company_name or "",
        city=p.city or "",
        country_code=p.country_code or "",
        tier=p.bike_shop_tier or "",
        website=p.website or "",
        emails=[p.email] if p.email else [],
        phones=[p.phone] if p.phone else [],
        whatsapps=[p.whatsapp_number] if p.whatsapp_number else [],
        instagrams=[p.instagram_url] if p.instagram_url else [],
        linkedins=[p.linkedin_url] if p.linkedin_url else [],
        prospect_id=p.id,
    )


# ── Resolution + merge (with in-run caches for speed) ───────────────────────


class _Resolver:
    def __init__(self, session: Session):
        self.s = session
        self.email_index: dict[str, int] = {}   # normalized email -> contact_id
        self.phone_index: dict[str, int] = {}   # normalized phone -> contact_id
        self.namegeo_index: dict[str, int] = {}  # name|country -> contact_id
        self.suppressed: set[str] = set()

    def warm_caches(self) -> None:
        # Existing channels
        for ct, val, cid in self.s.execute(
            select(ContactChannel.channel_type, ContactChannel.value_normalized, ContactChannel.contact_id)
        ).all():
            if not val:
                continue
            if ct in ("email",):
                self.email_index[val] = cid
            elif ct in ("phone", "whatsapp"):
                self.phone_index.setdefault(val, cid)
        # Existing name+country
        for cid, name, country in self.s.execute(
            select(Contact.id, Contact.company_name, Contact.country_code)
        ).all():
            key = name_country_key(name, country)
            if key:
                self.namegeo_index.setdefault(key, cid)
        # Suppression list
        self.suppressed = {
            e for (e,) in self.s.execute(
                select(SuppressionEntry.email).where(
                    SuppressionEntry.active.is_(True), SuppressionEntry.email != ""
                )
            ).all()
        }

    def find_contact_id(self, rec: SourceRecord) -> int | None:
        for e in rec.emails:
            ne = normalize_email(e)
            if ne and ne in self.email_index:
                return self.email_index[ne]
        for p in rec.phones + rec.whatsapps:
            npn = normalize_phone(p)
            if npn and npn in self.phone_index:
                return self.phone_index[npn]
        key = name_country_key(rec.company_name, rec.country_code)
        if key and key in self.namegeo_index:
            return self.namegeo_index[key]
        return None

    def _add_channel(self, contact: Contact, channel_type: str, value: str, source: str) -> None:
        if not value:
            return
        norm = normalize_email(value) if channel_type == "email" else (
            normalize_phone(value) if channel_type in ("phone", "whatsapp") else value.strip().lower()
        )
        if not norm:
            return
        # dedupe within this contact
        for ch in contact.channels:
            if ch.channel_type == channel_type and ch.value_normalized == norm:
                return
        contact.channels.append(ContactChannel(
            channel_type=channel_type, value=value.strip(), value_normalized=norm,
            source=source, is_primary=False,
        ))
        if channel_type == "email":
            self.email_index.setdefault(norm, contact.id)
        elif channel_type in ("phone", "whatsapp"):
            self.phone_index.setdefault(norm, contact.id)

    def upsert(self, rec: SourceRecord) -> tuple[Contact, bool]:
        cid = self.find_contact_id(rec)
        created = False
        if cid is not None:
            contact = self.s.get(Contact, cid)
        else:
            contact = Contact(created_at=datetime.utcnow())
            self.s.add(contact)
            self.s.flush()  # get id for caches
            created = True

        # Fill / upgrade scalar fields (don't overwrite good data with blanks;
        # customer data wins for value/sector/customer flag).
        if rec.company_name and not contact.company_name:
            contact.company_name = rec.company_name
        if rec.contact_person and not contact.contact_person:
            contact.contact_person = rec.contact_person
        if rec.city and not contact.city:
            contact.city = rec.city
        if rec.country_code and not contact.country_code:
            contact.country_code = rec.country_code
        if rec.sector and not contact.sector:
            contact.sector = rec.sector
        if rec.tier and not contact.tier:
            contact.tier = rec.tier
        if rec.website and not contact.website:
            contact.website = rec.website
        if rec.is_customer:
            contact.is_customer = True
        if rec.lifetime_value and float(rec.lifetime_value) > float(contact.lifetime_value or 0):
            contact.lifetime_value = rec.lifetime_value

        # Source links
        if rec.customer_id and not contact.customer_id:
            contact.customer_id = rec.customer_id
        if rec.kvk_company_id and not contact.kvk_company_id:
            contact.kvk_company_id = rec.kvk_company_id
        if rec.facebook_lead_id and not contact.facebook_lead_id:
            contact.facebook_lead_id = rec.facebook_lead_id
        if rec.prospect_id and not contact.prospect_id:
            contact.prospect_id = rec.prospect_id

        sources = set(filter(None, (contact.source_summary or "").split(",")))
        sources.add(rec.source)
        contact.source_summary = ",".join(sorted(sources))

        # Channels
        for e in rec.emails:
            self._add_channel(contact, "email", e, rec.source)
        for p in rec.phones:
            self._add_channel(contact, "phone", p, rec.source)
        for w in rec.whatsapps:
            self._add_channel(contact, "whatsapp", w, rec.source)
        for ig in rec.instagrams:
            self._add_channel(contact, "instagram", ig, rec.source)
        for li in rec.linkedins:
            self._add_channel(contact, "linkedin", li, rec.source)

        # Primary email/phone + display name + DNC + name/geo cache
        if not contact.primary_email:
            for ch in contact.channels:
                if ch.channel_type == "email":
                    contact.primary_email = ch.value_normalized
                    break
        if not contact.primary_phone:
            for ch in contact.channels:
                if ch.channel_type in ("phone", "whatsapp"):
                    contact.primary_phone = ch.value_normalized
                    break
        contact.display_name = contact.company_name or contact.contact_person or contact.primary_email or f"Contact #{contact.id}"
        if contact.primary_email and contact.primary_email in self.suppressed:
            contact.do_not_contact = True

        key = name_country_key(contact.company_name, contact.country_code)
        if key:
            self.namegeo_index.setdefault(key, contact.id)

        return contact, created


# ── Public backfill ─────────────────────────────────────────────────────────


def backfill_contacts(session: Session, *, batch_commit: int = 500) -> dict:
    """Build/refresh the contacts table from all four source tables.

    Idempotent — safe to run repeatedly. Returns a stats dict.
    """
    resolver = _Resolver(session)
    resolver.warm_caches()

    created = 0
    merged = 0
    processed = 0

    def _run_source(rows, to_record):
        nonlocal created, merged, processed
        for row in rows:
            rec = to_record(row)
            if not (rec.emails or rec.phones or rec.whatsapps or name_country_key(rec.company_name, rec.country_code)):
                continue
            _, was_created = resolver.upsert(rec)
            if was_created:
                created += 1
            else:
                merged += 1
            processed += 1
            if processed % batch_commit == 0:
                session.commit()

    # Order matters: customers first (richest, real clients), then KVK, leads, prospects.
    _run_source(session.scalars(select(Customer)).all(), _customer_to_record)
    _run_source(session.scalars(select(KvkCompany)).all(), _kvk_to_record)
    # All leads (even blank-email ones) — the _run_source guard still requires
    # an email/phone OR a usable name+country, and a blank-email lead can still
    # merge into an existing contact by name+country.
    _run_source(session.scalars(select(FacebookLead)).all(), _lead_to_record)
    _run_source(session.scalars(select(Prospect)).all(), _prospect_to_record)

    session.commit()
    total = session.scalar(select(func.count(Contact.id))) or 0
    return {"processed": processed, "created": created, "merged": merged, "total_contacts": total}


# ── Timeline assembly (for the profile page) ────────────────────────────────


def get_timeline(session: Session, contact: Contact, *, limit: int = 200) -> list[dict]:
    """Combine logged Activity rows + live email-campaign data into one
    reverse-chronological timeline for the contact profile page.
    """
    events: list[dict] = []

    for a in session.scalars(
        select(Activity).where(Activity.contact_id == contact.id)
        .order_by(Activity.occurred_at.desc()).limit(limit)
    ).all():
        events.append({
            "when": a.occurred_at,
            "type": a.activity_type,
            "channel": a.channel,
            "direction": a.direction,
            "title": a.title,
            "body": a.body,
        })

    # Live email engine data keyed by the contact's emails.
    emails = [ch.value_normalized for ch in contact.channels if ch.channel_type == "email"]
    if emails:
        for r in session.scalars(
            select(EmailCampaignRecipient)
            .where(EmailCampaignRecipient.to_email.in_(emails))
            .order_by(EmailCampaignRecipient.id.desc()).limit(limit)
        ).all():
            if r.sent_at:
                events.append({
                    "when": r.sent_at, "type": "email_sent", "channel": "email", "direction": "out",
                    "title": f"Email sent to {r.to_email}", "body": ""})
            if r.first_opened_at:
                events.append({
                    "when": r.first_opened_at, "type": "email_open", "channel": "email", "direction": "in",
                    "title": f"Opened email ({r.open_count}x)", "body": ""})
            if r.first_clicked_at:
                events.append({
                    "when": r.first_clicked_at, "type": "email_click", "channel": "email", "direction": "in",
                    "title": f"Clicked link ({r.click_count}x)", "body": ""})
            if r.unsubscribed_at:
                events.append({
                    "when": r.unsubscribed_at, "type": "unsubscribe", "channel": "email", "direction": "in",
                    "title": "Unsubscribed", "body": ""})

    events.sort(key=lambda e: (e["when"] is not None, e["when"]), reverse=True)
    return events[:limit]


def log_activity(
    session: Session, contact_id: int, activity_type: str, *,
    channel: str = "system", direction: str = "none", title: str = "", body: str = "",
    agent_id: int | None = None, ref_type: str = "", ref_id: int | None = None,
    occurred_at: datetime | None = None, commit: bool = True,
) -> Activity:
    act = Activity(
        contact_id=contact_id, activity_type=activity_type, channel=channel,
        direction=direction, title=title, body=body, agent_id=agent_id,
        ref_type=ref_type, ref_id=ref_id, occurred_at=occurred_at or datetime.utcnow(),
    )
    session.add(act)
    contact = session.get(Contact, contact_id)
    if contact:
        contact.last_activity_at = act.occurred_at
    if commit:
        session.commit()
    return act
