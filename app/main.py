from __future__ import annotations

import asyncio
import csv
import html as html_lib
import io
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from io import StringIO
from threading import Thread
from urllib.parse import quote_plus

import pandas as pd
import stripe
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import SessionLocal, get_db
from app.discovery import discover_contacts_for_kvk_company, discover_public_contacts_for_prospect, ensure_prospect_contacts
from app.klaviyo_sync import push_companies_to_klaviyo, test_klaviyo_connection
from app.kvk_enrichment import (
    enrich_kvk_company_full, find_website_for_kvk_company,
    get_enrichment_progress, run_kvk_bulk_enrichment,
    run_kvk_enrichment_job, start_auto_enrichment_scheduler,
)
from app.emailing import export_queue_csv, preview_queue_for_day, send_queue_item
from app import gmail_sender
from app import email_providers
from app.email_engine import (
    build_recipients,
    process_due_campaigns,
    record_click,
    record_open,
    record_unsubscribe,
    render_for_recipient,
    send_campaign_batch,
    sent_today,
    start_email_sender_scheduler,
)
from app.email_library import seed_starter_templates
from app import contacts as contacts_module
from app import inbox as inbox_module
from app.inbox import seed_inbox_defaults
from app.gmail_inbound import poll_inbound, start_gmail_inbound_scheduler
from app import whatsapp as whatsapp_module
from app import instagram as instagram_module
from app import enrichment_open as enrichment_open_module
from app import crawler as crawler_module
from app.tiering import score_kvk_company_tier, apply_bike_tier
from app import auth as auth_module
from app.auth import require_admin_role
from app.audit import log_audit
from app import reporting as reporting_module
from app.google_places import place_to_prospect_record, search_google_places
from app.importers import (
    prepare_kvk_prospects_dataframe,
    read_csv_upload,
    upsert_customers_from_dataframe,
    upsert_invoices_from_dataframe,
    upsert_kvk_companies_from_dataframe,
    upsert_kvk_establishments_from_dataframe,
    upsert_prospects_from_dataframe,
)
from app.jobs import run_daily_queue_build, run_daily_queue_send
from app.klaviyo import KlaviyoExportError, export_prospects_to_klaviyo
from app.matching import apply_kvk_matching, apply_matching
from app.models import (
    Activity,
    Agent,
    AuditLog,
    CannedReply,
    Contact,
    ContactChannel,
    Conversation,
    CrawlJob,
    Customer,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailEvent,
    EmailLog,
    EmailTemplate,
    FacebookLead,
    Message,
    MessageAttachment,
    Notification,
    GmailAccount,
    KvkCompany,
    KvkEstablishment,
    KvkImportLog,
    MatchStatus,
    OutreachQueueItem,
    Prospect,
    ProspectActivityLog,
    ProspectState,
    QueueState,
    SuppressionEntry,
    WebhookLog,
    WhatsappTemplate,
    EmailSequence,
    SequenceStep,
    SequenceEnrollment,
    SequenceEmail,
)
from app import sequences as sequences_module
from app.outreach_templates import build_outreach_bundle
from app.tiering import apply_bike_tier, score_kvk_company_tier
from app.stripe_sync import sync_stripe_event
from app.utils import build_unsubscribe_token, normalize_domain, normalize_email


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background schedulers — both daemons, idempotent
    start_auto_enrichment_scheduler()
    try:
        crawler_module.start_crawler_scheduler()
    except Exception as exc:  # noqa: BLE001
        print(f"[lifespan] directory crawler not started: {exc}")
    try:
        from app.facebook_leads import start_facebook_leads_scheduler, start_lead_classifier_scheduler
        start_facebook_leads_scheduler()
        start_lead_classifier_scheduler()
    except Exception as exc:
        print(f"[lifespan] FB leads schedulers not started: {exc}")
    # Email engine: seed starter templates + start the campaign sender daemon.
    try:
        seed_session = SessionLocal()
        try:
            seed_starter_templates(seed_session)
            seed_inbox_defaults(seed_session, admin_email=settings.reply_to_email)
            # Seed the 3 baseline sequence templates + default sequence (harmless
            # data; the sequence engine stays OFF until its flag is set).
            try:
                from app.sequence_library import seed_sequence_templates
                seed_sequence_templates(seed_session)
                seed_session.commit()
            except Exception as exc:  # noqa: BLE001
                print(f"[lifespan] sequence seed skipped: {exc}")
        finally:
            seed_session.close()
        start_email_sender_scheduler()
        start_gmail_inbound_scheduler()
        # Sequence scheduler — no-op unless SEQUENCE_ENGINE_ENABLED.
        try:
            from app.sequences import start_sequence_scheduler
            start_sequence_scheduler()
        except Exception as exc:  # noqa: BLE001
            print(f"[lifespan] sequence scheduler not started: {exc}")
    except Exception as exc:
        print(f"[lifespan] Email engine / inbox not started: {exc}")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic(auto_error=False)

TIER_FILTERS = ["Good Tier", "Hard to Reach", "Mid Tier", "Low Tier", "Brand Store", "Low Fit", "Unclassified"]
DISCOVERY_FILTERS = ["all", "has_email", "no_email", "has_whatsapp", "has_socials", "high_confidence", "low_confidence", "found", "partial", "no_contacts", "no_website", "error", "not_started", "running"]
KVK_SOURCE = "kvk_bike_list"
SOURCE_FILTERS = [("all", "All sources"), ("kvk", "KVK list"), ("maps", "Google Maps"), ("crawler", "Directory crawler")]


def prospect_contact_count(prospect: Prospect) -> int:
    return sum(
        1
        for value in [prospect.email, prospect.phone, prospect.whatsapp_number, prospect.instagram_url, prospect.linkedin_url]
        if (value or "").strip()
    )


def prospect_reachability_summary(prospect: Prospect) -> dict[str, str | int | None]:
    channels = prospect_contact_count(prospect)
    status = (prospect.email_discovery_status or "not_started").replace("_", " ")
    source = prospect.email_source_page or ""
    confidence = prospect.email_confidence or 0

    if prospect.email:
        title = "Ready for outreach"
        detail = f"{channels} public contact channel{'s' if channels != 1 else ''} found"
    elif prospect.phone or prospect.whatsapp_number or prospect.instagram_url or prospect.linkedin_url:
        title = "Phone, social, or WhatsApp only"
        detail = f"{channels} public contact channel{'s' if channels != 1 else ''} found"
    elif prospect.email_discovery_status == "running":
        title = "Checking website now"
        detail = "Crawler is scanning the website"
    elif prospect.email_discovery_status == "no_website":
        title = "Website still missing"
        detail = "No company website found yet"
    elif prospect.email_discovery_status == "error":
        title = "Needs retry"
        detail = "Crawler hit an error"
    elif prospect.email_discovery_status == "no_contacts":
        title = "No public contact found"
        detail = "No email or social contact detected"
    else:
        title = "Not checked yet"
        detail = "Run Find contacts to scan the website"

    return {
        "title": title,
        "detail": detail,
        "status": status,
        "source": source,
        "confidence": confidence if prospect.email else None,
        "channels": channels,
    }


def require_admin(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    if not settings.admin_password:
        return credentials.username if credentials else "local-dev"

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Schild Inc CRM MVP"'},
        )

    expected_user = settings.admin_username.encode("utf-8")
    expected_pass = settings.admin_password.encode("utf-8")
    provided_user = credentials.username.encode("utf-8")
    provided_pass = credentials.password.encode("utf-8")
    if settings.admin_password and (
        not secrets.compare_digest(expected_user, provided_user)
        or not secrets.compare_digest(expected_pass, provided_pass)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Schild Inc CRM MVP"'},
        )
    return credentials.username


def redirect_back(request: Request, fallback: str) -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer", fallback), status_code=303)


def with_notice(path: str, message: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}notice={quote_plus(message)}"


def _run_contact_discovery_job(prospect_id: int) -> None:
    db = SessionLocal()
    try:
        prospect = db.get(Prospect, prospect_id)
        if not prospect:
            return
        discover_public_contacts_for_prospect(db, prospect)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        prospect = db.get(Prospect, prospect_id)
        if prospect:
            prospect.email_discovery_status = "error"
            prospect.discovery_error = str(exc)
            db.add(
                ProspectActivityLog(
                    prospect=prospect,
                    action_type="email_discovery",
                    status="error",
                    source_url=prospect.website or prospect.google_maps_url,
                    detail=str(exc),
                )
            )
            db.commit()
    finally:
        db.close()


def _run_contact_discovery_batch_job(prospect_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        for prospect_id in prospect_ids:
            try:
                prospect = db.get(Prospect, prospect_id)
                if not prospect:
                    continue
                discover_public_contacts_for_prospect(db, prospect)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                prospect = db.get(Prospect, prospect_id)
                if not prospect:
                    continue
                prospect.email_discovery_status = "error"
                prospect.discovery_error = str(exc)
                db.add(
                    ProspectActivityLog(
                        prospect=prospect,
                        action_type="email_discovery",
                        status="error",
                        source_url=prospect.website or prospect.google_maps_url,
                        detail=str(exc),
                    )
                )
                db.commit()
    finally:
        db.close()


def _queue_contact_discovery(db: Session, prospect: Prospect) -> None:
    _queue_contact_discovery_batch(db, [prospect])


def _queue_contact_discovery_batch(db: Session, prospects: list[Prospect]) -> None:
    queued_ids: list[int] = []
    for prospect in prospects:
        if prospect.email_discovery_status == "running":
            continue
        prospect.email_discovery_status = "running"
        prospect.discovery_error = ""
        db.add(
            ProspectActivityLog(
                prospect=prospect,
                action_type="email_discovery",
                status="running",
                source_url=prospect.website or prospect.google_maps_url,
                detail="Discovery queued from admin action.",
            )
        )
        queued_ids.append(prospect.id)
    if not queued_ids:
        return
    db.commit()
    Thread(target=_run_contact_discovery_batch_job, args=(queued_ids,), daemon=True).start()


def kvk_dashboard_context(db: Session) -> dict[str, int]:
    kvk_query = select(Prospect).where(Prospect.source == KVK_SOURCE)
    return {
        "total": db.scalar(select(func.count()).select_from(kvk_query.subquery())) or 0,
        "with_website": db.scalar(select(func.count(Prospect.id)).where(Prospect.source == KVK_SOURCE, Prospect.website != "")) or 0,
        "with_email": db.scalar(select(func.count(Prospect.id)).where(Prospect.source == KVK_SOURCE, Prospect.email != "")) or 0,
        "ready": db.scalar(select(func.count(Prospect.id)).where(Prospect.source == KVK_SOURCE, Prospect.email_discovery_status == "found")) or 0,
    }


def build_prospect_query(
    *,
    search: str = "",
    match_filter: str = "",
    review_filter: str = "",
    tier_filter: str = "",
    discovery_filter: str = "all",
    source_filter: str = "",
    include_relationships: bool = True,
):
    query = select(Prospect)
    if include_relationships:
        query = query.options(selectinload(Prospect.matched_customer))
    query = query.order_by(Prospect.updated_at.desc())

    if search:
        like_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Prospect.company_name.ilike(like_term),
                Prospect.website.ilike(like_term),
                Prospect.email.ilike(like_term),
                Prospect.whatsapp_number.ilike(like_term),
                Prospect.city.ilike(like_term),
                Prospect.kvk_number.ilike(like_term),
            )
        )
    if match_filter:
        query = query.where(Prospect.match_status == MatchStatus(match_filter))
    if review_filter:
        query = query.where(Prospect.review_status == ProspectState(review_filter))
    if tier_filter:
        query = query.where(Prospect.bike_shop_tier == tier_filter)
    if source_filter == "kvk":
        query = query.where(Prospect.source == KVK_SOURCE)
    elif source_filter == "maps":
        query = query.where(Prospect.source.in_(["google_places", "google_maps_csv"]))
    elif source_filter == "crawler":
        query = query.where(Prospect.source == "crawler")
    elif source_filter.startswith("crawl_job:"):
        job_ref = source_filter.split(":", 1)[1]
        if job_ref.isdigit():
            query = query.where(Prospect.crawl_job_id == int(job_ref))
    if discovery_filter == "has_email":
        query = query.where(Prospect.email != "")
    elif discovery_filter == "no_email":
        query = query.where(Prospect.email == "")
    elif discovery_filter == "has_whatsapp":
        query = query.where(Prospect.whatsapp_number != "")
    elif discovery_filter == "has_socials":
        query = query.where(or_(Prospect.linkedin_url != "", Prospect.instagram_url != ""))
    elif discovery_filter == "high_confidence":
        query = query.where(Prospect.email_confidence >= 75)
    elif discovery_filter == "low_confidence":
        query = query.where(Prospect.email_confidence < 75)
    elif discovery_filter in {"found", "partial", "no_contacts", "no_website", "error", "not_started", "running"}:
        query = query.where(Prospect.email_discovery_status == discovery_filter)
    return query


def exportable_prospects(
    db: Session,
    *,
    search: str = "",
    match_filter: str = "",
    review_filter: str = "",
    tier_filter: str = "",
    discovery_filter: str = "all",
    source_filter: str = "",
    selected_ids: list[int] | None = None,
    require_email: bool = True,
    exclude_existing_customers: bool = False,
) -> list[Prospect]:
    if selected_ids:
        query = select(Prospect).where(Prospect.id.in_(selected_ids)).order_by(Prospect.company_name.asc())
    else:
        query = build_prospect_query(
            search=search,
            match_filter=match_filter,
            review_filter=review_filter,
            tier_filter=tier_filter,
            discovery_filter=discovery_filter,
            source_filter=source_filter,
            include_relationships=False,
        )
    if require_email:
        query = query.where(Prospect.email != "")
    if exclude_existing_customers:
        query = query.where(Prospect.match_status != MatchStatus.existing_customer)
    return db.scalars(query).all()


def export_prospects_csv_text(prospects: list[Prospect]) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "company_name",
            "email",
            "website",
            "website_domain",
            "phone",
            "whatsapp_number",
            "instagram_url",
            "linkedin_url",
            "city",
            "country_code",
            "bike_shop_tier",
            "outreach_priority",
            "match_status",
            "review_status",
            "kvk_number",
            "kvk_establishment_number",
        ]
    )
    for prospect in prospects:
        writer.writerow(
            [
                prospect.company_name,
                prospect.email,
                prospect.website,
                prospect.website_domain,
                prospect.phone,
                prospect.whatsapp_number,
                prospect.instagram_url,
                prospect.linkedin_url,
                prospect.city,
                prospect.country_code,
                prospect.bike_shop_tier,
                prospect.outreach_priority,
                prospect.match_status.value,
                prospect.review_status.value,
                prospect.kvk_number,
                prospect.kvk_establishment_number,
            ]
        )
    return output.getvalue()


def _kvk_candidates_for_discovery(db: Session, limit: int) -> list[Prospect]:
    return db.scalars(
        select(Prospect)
        .where(
            Prospect.source == KVK_SOURCE,
            Prospect.email_discovery_status.in_(["not_started", "error", "no_website", "no_contacts", "partial"]),
        )
        .order_by(Prospect.updated_at.asc(), Prospect.id.asc())
        .limit(limit)
    ).all()


def _load_recent_touched_prospects(db: Session, source: str, source_references: list[str]) -> list[Prospect]:
    unique_refs = [item for item in dict.fromkeys(source_references) if item]
    if not unique_refs:
        return []
    return db.scalars(
        select(Prospect).where(Prospect.source == source, Prospect.source_reference.in_(unique_refs))
    ).all()


def _build_kvk_import_message(summary, queued_count: int) -> str:
    return (
        f"/prospects?inserted={summary.inserted}"
        f"&updated={summary.updated}"
        f"&kvk_queued={queued_count}"
    )


def dashboard_context(db: Session) -> dict:
    return {
        "customer_count": db.scalar(select(func.count(Customer.id))) or 0,
        "prospect_count": db.scalar(select(func.count(Prospect.id))) or 0,
        "existing_match_count": db.scalar(select(func.count(Prospect.id)).where(Prospect.match_status == MatchStatus.existing_customer)) or 0,
        "new_prospect_count": db.scalar(select(func.count(Prospect.id)).where(Prospect.match_status == MatchStatus.new_prospect)) or 0,
        "pending_review_count": db.scalar(select(func.count(Prospect.id)).where(Prospect.review_status == ProspectState.pending)) or 0,
        "queued_today_count": db.scalar(select(func.count(OutreachQueueItem.id)).where(OutreachQueueItem.queue_date == date.today())) or 0,
        "sent_today_count": db.scalar(
            select(func.count(OutreachQueueItem.id)).where(OutreachQueueItem.queue_date == date.today(), OutreachQueueItem.state == QueueState.sent)
        ) or 0,
        "suppression_count": db.scalar(select(func.count(SuppressionEntry.id)).where(SuppressionEntry.active.is_(True))) or 0,
        "discovered_email_count": db.scalar(select(func.count(Prospect.id)).where(Prospect.email != "")) or 0,
        "high_priority_count": db.scalar(select(func.count(Prospect.id)).where(Prospect.outreach_priority == "High")) or 0,
        "daily_send_limit": settings.daily_send_limit,
        # KVK stats
        "kvk_total": db.scalar(select(func.count(KvkCompany.id))) or 0,
        "kvk_with_website": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.website != "")) or 0,
        "kvk_with_email": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.email_public != "")) or 0,
        "kvk_with_phone": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.phone_public != "")) or 0,
        "kvk_existing_customers": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.already_client_flag.is_(True))) or 0,
        "kvk_good_tier": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.bike_shop_tier == "Good Tier")) or 0,
        "kvk_outreach_ready": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.approved_for_outreach.is_(True), KvkCompany.already_client_flag.is_(False))) or 0,
        "kvk_pending_enrichment": db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.enrichment_status == "pending")) or 0,
    }


def prospect_filters_context(
    *,
    search: str = "",
    match_filter: str = "",
    review_filter: str = "",
    tier_filter: str = "",
    discovery_filter: str = "all",
    source_filter: str = "",
) -> dict:
    return {
        "search": search,
        "match_filter": match_filter,
        "review_filter": review_filter,
        "tier_filter": tier_filter,
        "discovery_filter": discovery_filter,
        "source_filter": source_filter,
        "tier_options": TIER_FILTERS,
        "discovery_options": DISCOVERY_FILTERS,
        "source_options": SOURCE_FILTERS,
        "match_options": [status.value for status in MatchStatus],
        "review_options": [status.value for status in ProspectState],
    }


@app.get("/health")
def healthcheck() -> dict[str, bool]:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "app_name": settings.app_name,
            "request": request,
            "stats": dashboard_context(db),
            "google_places_enabled": bool(settings.google_places_api_key),
        },
    )


def _customers_base_query(
    *,
    search: str = "",
    sector: str = "",
    segment: str = "",
    country: list[str] | None = None,
    sort: str = "ltv_desc",
):
    """
    Build the filtered + sorted base SELECT for customers. Shared
    between the /customers HTML view and the /customers/export.csv
    endpoint so both apply identical filters.

    `country` is a list of ISO-2 codes; multiple values mean IN().
    """
    base = select(Customer)
    if search:
        like = f"%{search.lower()}%"
        base = base.where(
            or_(
                func.lower(Customer.canonical_company_name).like(like),
                func.lower(Customer.customer_email_primary).like(like),
                func.lower(Customer.website_domain_candidate).like(like),
                func.lower(Customer.match_key_domain).like(like),
                func.lower(Customer.city).like(like),
                func.lower(Customer.contact_person).like(like),
            )
        )
    if sector:
        base = base.where(Customer.main_sector == sector)
    if segment in ("B2B", "B2C"):
        base = base.where(Customer.customer_segment == segment)
    if country:
        # Normalize input to uppercase for the IN clause
        codes = [c.strip().upper() for c in country if c.strip()]
        if codes:
            base = base.where(func.upper(Customer.country_code).in_(codes))

    # Sort order — declarative so the export and HTML stay in sync
    if sort == "ltv_asc":
        base = base.order_by(Customer.lifetime_amount_paid.asc().nullslast(), Customer.canonical_company_name)
    elif sort == "country":
        base = base.order_by(Customer.country_code.nullslast(), Customer.lifetime_amount_paid.desc().nullslast())
    elif sort == "company":
        base = base.order_by(Customer.canonical_company_name)
    elif sort == "recent":
        base = base.order_by(Customer.last_invoice_date_utc.desc().nullslast(), Customer.canonical_company_name)
    elif sort == "invoices":
        base = base.order_by(Customer.invoice_count.desc().nullslast(), Customer.lifetime_amount_paid.desc().nullslast())
    else:  # ltv_desc — default
        base = base.order_by(Customer.lifetime_amount_paid.desc().nullslast(), Customer.updated_at.desc())
    return base


@app.get("/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    match: str = "",
    sector: str = "",
    segment: str = "",
    country: list[str] = Query(default=[]),
    sort: str = "ltv_desc",
) -> HTMLResponse:
    """
    Paginated customers list with KVK + Prospect match status per row.

    For each customer on the visible page, we check whether they appear
    in the KVK or Prospect tables — matched by primary email OR by
    canonical_company_name_clean. The result is one of:
      - 'kvk_only'        → already in KVK, no prospect
      - 'prospect_only'   → already in prospects, no KVK
      - 'kvk_and_prospect'→ in both
      - 'new'             → not in either (this customer hasn't been
                            cross-referenced — treat as a new lead)
    Showing this lets the user audit gaps in coverage.
    """
    page = max(1, page)
    per_page = max(10, min(200, per_page))

    base = _customers_base_query(
        search=search, sector=sector, segment=segment, country=country, sort=sort
    )

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    customers = db.scalars(
        base.offset((page - 1) * per_page).limit(per_page)
    ).all()

    # ── Cross-reference batch: one query each to KVK and Prospects ───────
    # Compare on BOTH email and clean company name. Email match is stronger
    # (a unique identifier); name match is the fallback when emails diverge.
    emails = {(c.customer_email_primary or "").lower() for c in customers if c.customer_email_primary}
    names = {(c.canonical_company_name_clean or "").lower() for c in customers if c.canonical_company_name_clean}

    kvk_by_email: dict[str, KvkCompany] = {}
    kvk_by_name: dict[str, KvkCompany] = {}
    prospect_by_email: dict[str, Prospect] = {}
    prospect_by_name: dict[str, Prospect] = {}

    if emails:
        for k in db.scalars(select(KvkCompany).where(func.lower(KvkCompany.email_public).in_(emails))).all():
            kvk_by_email[(k.email_public or "").lower()] = k
        for p in db.scalars(select(Prospect).where(func.lower(Prospect.email).in_(emails))).all():
            prospect_by_email[(p.email or "").lower()] = p
    if names:
        for k in db.scalars(select(KvkCompany).where(func.lower(KvkCompany.canonical_company_name_clean).in_(names))).all():
            kvk_by_name[(k.canonical_company_name_clean or "").lower()] = k
        for p in db.scalars(select(Prospect).where(func.lower(Prospect.canonical_company_name_clean).in_(names))).all():
            prospect_by_name[(p.canonical_company_name_clean or "").lower()] = p

    enriched: list[dict] = []
    for c in customers:
        email_lc = (c.customer_email_primary or "").lower()
        name_lc = (c.canonical_company_name_clean or "").lower()

        kvk = kvk_by_email.get(email_lc) or (kvk_by_name.get(name_lc) if name_lc else None)
        prospect = prospect_by_email.get(email_lc) or (prospect_by_name.get(name_lc) if name_lc else None)

        if kvk and prospect:
            status = "kvk_and_prospect"
        elif kvk:
            status = "kvk_only"
        elif prospect:
            status = "prospect_only"
        else:
            status = "new"

        # Why did we match? Helps the user audit
        why = []
        if kvk and email_lc and (kvk.email_public or "").lower() == email_lc:
            why.append("kvk:email")
        elif kvk and name_lc:
            why.append("kvk:name")
        if prospect and email_lc and (prospect.email or "").lower() == email_lc:
            why.append("prospect:email")
        elif prospect and name_lc:
            why.append("prospect:name")

        enriched.append({
            "customer": c,
            "status": status,
            "kvk": kvk,
            "prospect": prospect,
            "match_reason": ", ".join(why) if why else "no_match",
        })

    # Filter by match status if requested (post-query — small enough to be cheap)
    if match in ("kvk_only", "prospect_only", "kvk_and_prospect", "new"):
        enriched = [row for row in enriched if row["status"] == match]

    # Summary counts + distinct sectors / countries for the filter dropdowns
    customer_total = db.scalar(select(func.count(Customer.id))) or 0
    sector_options = [
        s for (s,) in db.execute(
            select(Customer.main_sector).where(Customer.main_sector != "").distinct().order_by(Customer.main_sector)
        ).all()
    ]
    # Distinct country codes with counts + display names, alphabetical
    # by name so the dropdown reads naturally
    from app.country_codes import COUNTRIES, name_for as _country_name
    raw_country_rows = db.execute(
        select(Customer.country_code, func.count(Customer.id))
        .where(Customer.country_code != "")
        .group_by(Customer.country_code)
    ).all()
    country_options = sorted(
        [{"code": code, "name": _country_name(code), "count": cnt}
         for code, cnt in raw_country_rows],
        key=lambda r: r["name"],
    )

    return templates.TemplateResponse(
        request,
        "customers.html",
        {
            "request": request,
            "rows": enriched,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total": total,
            "customer_total": customer_total,
            "search": search,
            "match": match,
            "sector": sector,
            "segment": segment,
            "country": country,
            "sort": sort,
            "sector_options": sector_options,
            "country_options": country_options,
            "country_names": COUNTRIES,
            "app_name": settings.app_name,
        },
    )


@app.get("/customers/export.csv")
def customers_export_csv(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    search: str = "",
    sector: str = "",
    segment: str = "",
    country: list[str] = Query(default=[]),
    sort: str = "ltv_desc",
) -> StreamingResponse:
    """
    Stream the customers list as CSV with the SAME filters + sort
    order as the HTML view. Useful for handing a segmented audience
    to outreach tools (Klaviyo, mail merge, etc.).
    """
    base = _customers_base_query(
        search=search, sector=sector, segment=segment, country=country, sort=sort
    )

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([
            "company_name", "contact_person", "email", "phone", "website",
            "city", "country_code", "main_sector", "sub_sector", "segment",
            "invoice_count", "lifetime_amount_paid", "last_invoice_date",
            "first_invoice_date", "customer_entity_id",
        ])
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        # Stream in chunks of 1000 so a 10k-customer export doesn't load
        # everything into memory at once
        offset, page_size = 0, 1000
        while True:
            chunk = db.scalars(base.offset(offset).limit(page_size)).all()
            if not chunk:
                break
            for c in chunk:
                w.writerow([
                    c.canonical_company_name,
                    c.contact_person or "",
                    c.customer_email_primary or "",
                    c.phone_primary or "",
                    c.website or c.website_domain_candidate or "",
                    c.city or "",
                    c.country_code or "",
                    c.main_sector or "",
                    c.sub_sector or "",
                    c.customer_segment or "",
                    c.invoice_count or 0,
                    f"{float(c.lifetime_amount_paid or 0):.2f}",
                    c.last_invoice_date_utc.strftime("%Y-%m-%d") if c.last_invoice_date_utc else "",
                    c.first_invoice_date_utc.strftime("%Y-%m-%d") if c.first_invoice_date_utc else "",
                    c.customer_entity_id or "",
                ])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            offset += page_size

    # Build a filename that reflects the filters applied
    slug_parts: list[str] = []
    if sector:   slug_parts.append(f"sector-{sector.lower()}")
    if segment:  slug_parts.append(segment.lower())
    if country:
        codes = "-".join(c.lower() for c in country if c.strip())
        if codes:
            slug_parts.append(f"country-{codes}")
    suffix = "-".join(slug_parts) or "all"
    filename = f"customers-{suffix}-{date.today().isoformat()}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Customer analytics ─────────────────────────────────────────────────────
@app.get("/customers/analytics", response_class=HTMLResponse)
def customers_analytics(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    """
    Aggregate views of the customer base — by country, by sector
    (derived from KVK match or domain TLD), and by LTV bucket.
    Cheap to render: every chart uses one GROUP BY query.
    """
    # ── Country breakdown
    country_rows = db.execute(
        select(
            Customer.country_code,
            func.count(Customer.id).label("n"),
            func.coalesce(func.sum(Customer.lifetime_amount_paid), 0).label("ltv"),
        )
        .group_by(Customer.country_code)
        .order_by(func.count(Customer.id).desc())
    ).all()
    country_breakdown = [
        {"label": r[0] or "(unknown)", "count": r[1], "ltv": float(r[2] or 0)}
        for r in country_rows
    ]

    # ── LTV bucket distribution
    ltv_buckets = [
        ("€0",            0,     0),
        ("€1–500",        0.01,  500),
        ("€500–2k",       500,   2000),
        ("€2k–10k",       2000,  10000),
        ("€10k+",         10000, 99999999),
    ]
    ltv_breakdown = []
    for label, lo, hi in ltv_buckets:
        if lo == 0 and hi == 0:
            cnt = db.scalar(
                select(func.count(Customer.id)).where(
                    func.coalesce(Customer.lifetime_amount_paid, 0) == 0
                )
            ) or 0
            sub = 0.0
        else:
            cnt = db.scalar(
                select(func.count(Customer.id)).where(
                    Customer.lifetime_amount_paid >= lo,
                    Customer.lifetime_amount_paid < hi,
                )
            ) or 0
            sub_raw = db.scalar(
                select(func.coalesce(func.sum(Customer.lifetime_amount_paid), 0)).where(
                    Customer.lifetime_amount_paid >= lo,
                    Customer.lifetime_amount_paid < hi,
                )
            ) or 0
            sub = float(sub_raw)
        ltv_breakdown.append({"label": label, "count": cnt, "ltv": sub})

    # ── Sector: prefer the bike_shop_segment / tier from matched KVK
    # rows. For customers without a KVK match, fall back to a coarse
    # bucket from the email/website TLD ("nl" / "de" / "us" / "other").
    sector_rows = db.execute(
        select(
            KvkCompany.bike_shop_segment,
            func.count(Customer.id).label("n"),
            func.coalesce(func.sum(Customer.lifetime_amount_paid), 0).label("ltv"),
        )
        .join(Customer, Customer.id == KvkCompany.matched_customer_id)
        .where(Customer.already_client_flag.is_(True))
        .group_by(KvkCompany.bike_shop_segment)
        .order_by(func.count(Customer.id).desc())
    ).all()
    kvk_sector_breakdown = [
        {"label": r[0] or "(unsegmented)", "count": r[1], "ltv": float(r[2] or 0)}
        for r in sector_rows
    ]

    # ── Facebook leads — count + industry breakdown if any imported
    fb_total = db.scalar(select(func.count(FacebookLead.id))) or 0
    fb_industry_rows = db.execute(
        select(
            FacebookLead.industry,
            func.count(FacebookLead.id).label("n"),
        )
        .group_by(FacebookLead.industry)
        .order_by(func.count(FacebookLead.id).desc())
        .limit(20)
    ).all() if fb_total else []
    fb_industry_breakdown = [
        {"label": (r[0] or "(unspecified)").replace("_", " ").title(), "count": r[1]}
        for r in fb_industry_rows
    ]
    fb_match_rows = db.execute(
        select(FacebookLead.match_status, func.count(FacebookLead.id))
        .group_by(FacebookLead.match_status)
    ).all() if fb_total else []
    fb_match_breakdown = {r[0] or "new": r[1] for r in fb_match_rows}

    # ── Headline counters
    customer_total = db.scalar(select(func.count(Customer.id))) or 0
    total_ltv = db.scalar(select(func.coalesce(func.sum(Customer.lifetime_amount_paid), 0))) or 0
    median_ltv_proxy = float(total_ltv) / max(1, customer_total)

    return templates.TemplateResponse(
        request,
        "customer_analytics.html",
        {
            "request": request,
            "customer_total": customer_total,
            "total_ltv": float(total_ltv),
            "median_ltv_proxy": median_ltv_proxy,
            "country_breakdown": country_breakdown,
            "ltv_breakdown": ltv_breakdown,
            "kvk_sector_breakdown": kvk_sector_breakdown,
            "fb_total": fb_total,
            "fb_industry_breakdown": fb_industry_breakdown,
            "fb_match_breakdown": fb_match_breakdown,
            "app_name": settings.app_name,
        },
    )


# ── Leads (Facebook + historical CSV) ──────────────────────────────────────
@app.post("/admin/facebook-leads/import")
@app.post("/leads/sync")
def facebook_leads_import(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    """One-click pull from the live Google Sheet, dedupes by fb_lead_id."""
    from app.facebook_leads import import_facebook_leads
    try:
        summary = import_facebook_leads(db)
        flash = (
            f"FB leads sync: {summary['inserted']} new, "
            f"{summary['updated']} updated, "
            f"{summary['existing_customer_matches']} matched customers, "
            f"{summary['known_prospect_matches']} matched KVK"
        )
    except Exception as exc:
        flash = f"FB import failed: {exc}"
    return RedirectResponse(f"/leads?flash={quote_plus(flash)}", status_code=303)


@app.post("/leads/import-csv")
async def leads_import_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    """
    Upload the historical Marketing Lead CSV (or any FB Lead Ads-shaped CSV).
    Streams + batch-commits so 50k+ row files don't blow memory.
    """
    from app.facebook_leads import import_facebook_leads_from_csv
    try:
        raw = await file.read()
        # CSVs from Excel sometimes have a BOM — strip it
        text = raw.decode("utf-8-sig", errors="replace")
        summary = import_facebook_leads_from_csv(
            db, text, source_url=f"upload:{file.filename}", batch_size=500
        )
        flash = (
            f"CSV import ({file.filename}): {summary['inserted']} new, "
            f"{summary['updated']} updated, {summary['skipped']} skipped"
        )
    except Exception as exc:
        flash = f"CSV import failed: {exc}"
    return RedirectResponse(f"/leads?flash={quote_plus(flash)}", status_code=303)


# ── Public web-form ingest ───────────────────────────────────────────────────
# Lets an external site POST a contact-form submission to us. Goes
# straight into facebook_leads (with source_url='webform') and gets
# classified inline. CORS-permissive so any origin can post — but no
# auth required, so a CAPTCHA on the embedding site is recommended
# if abuse becomes a problem.
@app.options("/api/leads/webform")
async def webform_preflight() -> JSONResponse:
    """CORS preflight — needed because the form lives on another origin."""
    return JSONResponse(
        {"ok": True},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "86400",
        },
    )


@app.post("/api/leads/webform")
async def webform_submit(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """
    Accept a lead from any web form. Accepts JSON or form-encoded.

    Required fields: email
    Optional: full_name, company_name, phone_number, country, message,
              source_form (free-text label like 'contact-page'),
              source_site (e.g. 'schildinc.com')

    Returns 200 with classified sector on success.
    """
    from datetime import datetime, timezone
    from uuid import uuid4
    from app.facebook_leads import CURRENT_CLASSIFIER_VERSION
    from app.lead_classifier import classify_lead

    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    # Accept either JSON or form-encoded
    payload: dict = {}
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in ctype:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "invalid_payload"},
            status_code=400,
            headers=cors_headers,
        )

    email = str(payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse(
            {"ok": False, "error": "email_required"},
            status_code=400,
            headers=cors_headers,
        )

    # Build a fb_lead_id for webform leads so the same person submitting
    # twice from the same site collapses to one row (idempotent).
    source_site = str(payload.get("source_site") or "webform")
    source_form = str(payload.get("source_form") or "default")
    fb_lead_id = f"webform:{source_site}:{source_form}:{email}"

    existing = db.scalars(
        select(FacebookLead).where(FacebookLead.fb_lead_id == fb_lead_id).limit(1)
    ).first()
    is_new = existing is None
    lead = existing or FacebookLead(fb_lead_id=fb_lead_id)

    # Capture incoming fields — only overwrite if non-empty (don't wipe
    # historical values on a re-submit that omits a field)
    def _set_if(field: str, value):
        val = str(value or "").strip()
        if val:
            setattr(lead, field, val)

    _set_if("full_name",            payload.get("full_name") or payload.get("name"))
    _set_if("email",                email)
    _set_if("phone_number",         payload.get("phone_number") or payload.get("phone"))
    _set_if("company_name",         payload.get("company_name") or payload.get("company"))
    _set_if("country",              payload.get("country"))
    _set_if("detailed_information", payload.get("message") or payload.get("detail"))
    _set_if("form_name",            source_form)
    _set_if("source_url",           f"webform:{source_site}")
    _set_if("platform",             "webform")

    if is_new:
        lead.created_time_utc = datetime.now(tz=timezone.utc)
        db.add(lead)

    # Classify inline before commit
    lead.main_sector = classify_lead(lead)
    lead.classifier_version = CURRENT_CLASSIFIER_VERSION

    # Cross-reference vs customers + KVK
    try:
        from app.facebook_leads import _classify_lead as _matchref
        lead.match_status = _matchref(db, lead)
    except Exception:
        lead.match_status = "new"

    db.commit()
    return JSONResponse(
        {
            "ok": True,
            "id": lead.id,
            "fb_lead_id": lead.fb_lead_id,
            "main_sector": lead.main_sector,
            "match_status": lead.match_status,
            "created": is_new,
        },
        headers=cors_headers,
    )


# Top-level /leads route — also redirected from legacy /customers/facebook-leads
@app.get("/customers/facebook-leads")
def facebook_leads_legacy_redirect() -> RedirectResponse:
    """Old URL kept alive for bookmarks."""
    return RedirectResponse("/leads", status_code=308)


@app.get("/leads", response_class=HTMLResponse)
def leads_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    page: int = 1,
    per_page: int = 50,
    match: str = "",
    search: str = "",
    sector: str = "",
) -> HTMLResponse:
    """Top-level Leads inbox: every FB lead from the live sheet + historical CSV."""
    page = max(1, page)
    per_page = max(10, min(200, per_page))

    base = select(FacebookLead)
    if search:
        like = f"%{search.lower()}%"
        base = base.where(
            or_(
                func.lower(FacebookLead.full_name).like(like),
                func.lower(FacebookLead.email).like(like),
                func.lower(FacebookLead.company_name).like(like),
                func.lower(FacebookLead.industry).like(like),
            )
        )
    if match in ("new", "existing_customer", "known_prospect"):
        base = base.where(FacebookLead.match_status == match)
    if sector:
        base = base.where(FacebookLead.main_sector == sector)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    leads = db.scalars(
        base.order_by(FacebookLead.created_time_utc.desc().nullslast())
        .offset((page - 1) * per_page).limit(per_page)
    ).all()

    # Per-sector counts across the full leads table — used for filter chips
    sector_counts_rows = db.execute(
        select(FacebookLead.main_sector, func.count(FacebookLead.id))
        .where(FacebookLead.main_sector != "")
        .group_by(FacebookLead.main_sector)
        .order_by(func.count(FacebookLead.id).desc())
    ).all()
    sector_counts = [{"name": s, "count": c} for s, c in sector_counts_rows]

    # How many haven't been classified yet (classifier daemon's backlog)
    unclassified = db.scalar(
        select(func.count(FacebookLead.id)).where(FacebookLead.main_sector == "")
    ) or 0

    return templates.TemplateResponse(
        request,
        "facebook_leads.html",
        {
            "request": request,
            "leads": leads,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "search": search,
            "match": match,
            "sector": sector,
            "sector_counts": sector_counts,
            "unclassified": unclassified,
            "app_name": settings.app_name,
        },
    )


# ── Sync orphan invoice customers (creates Customer rows for invoice
# emails that never made it into the customers table — the user reported
# 1141 but the invoice CSV has 63 additional unique billing emails) ────────
@app.post("/admin/sync-invoice-customers")
def sync_invoice_customers(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    """Backfill customers table from any invoice customer_emails that aren't there yet."""
    from app.utils import normalize_email, normalize_domain
    inserted = 0

    # Fetch orphan invoice emails not yet in customers
    raw = db.execute(text("""
        SELECT
            LOWER(i.customer_email) AS email,
            MIN(COALESCE(NULLIF(i.customer_name_clean, ''), i.customer_name_raw, i.billing_name)) AS name,
            MIN(i.city) AS city,
            MIN(i.country_code) AS country,
            COUNT(*) AS invoice_count,
            COALESCE(SUM(i.amount_paid), 0) AS total_paid
        FROM invoices i
        WHERE i.customer_email IS NOT NULL AND i.customer_email != ''
          AND LOWER(i.customer_email) NOT IN (
              SELECT LOWER(customer_email_primary) FROM customers
              WHERE customer_email_primary IS NOT NULL AND customer_email_primary != ''
          )
        GROUP BY LOWER(i.customer_email)
    """)).fetchall()

    for row in raw:
        email = row[0]
        name = (row[1] or email.split("@", 1)[0]).strip()
        domain = email.split("@", 1)[1] if "@" in email else ""
        entity_id = f"orphan-invoice:{email}"
        # Skip if already exists by entity_id (idempotent)
        if db.scalar(select(Customer.id).where(Customer.customer_entity_id == entity_id)):
            continue
        c = Customer(
            customer_entity_id=entity_id,
            source_system="invoice_orphan_sync",
            canonical_company_name=name or email,
            canonical_company_name_clean=(name or email).lower().strip(),
            customer_email_primary=email,
            email_domain_primary=domain,
            match_key_domain=domain,
            city=row[2] or "",
            country_code=row[3] or "",
            invoice_count=int(row[4] or 0),
            lifetime_amount_paid=float(row[5] or 0),
            lifetime_total_invoiced=float(row[5] or 0),
            already_client_flag=True,
        )
        db.add(c)
        inserted += 1
    db.commit()
    flash = f"Synced {inserted} orphan invoice customers into the customers table."
    return RedirectResponse(f"/customers?flash={quote_plus(flash)}", status_code=303)


@app.post("/admin/import/customers")
async def import_customers(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    df = read_csv_upload(await file.read())
    summary = upsert_customers_from_dataframe(db, df)
    db.commit()
    return RedirectResponse(f"/customers?inserted={summary.inserted}&updated={summary.updated}", status_code=303)


@app.post("/admin/import/customers-rich")
async def import_customers_rich(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    """
    Schild Inc historical customer-DB CSV (one row per order-line).
    Aggregates by customer name into one Customer row each, then
    upserts via Postgres ON CONFLICT — safe to re-run.
    """
    from app.customer_normalizer import import_customers_from_csv
    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig", errors="replace")
        summary = import_customers_from_csv(db, text, batch_size=500)
        flash = f"Customer DB import: {summary['upserted']} customers upserted from {summary['total']} aggregated rows"
    except Exception as exc:
        flash = f"Customer DB import failed: {exc}"
    return RedirectResponse(f"/customers?flash={quote_plus(flash)}", status_code=303)


@app.post("/admin/import/invoices")
async def import_invoices(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    df = read_csv_upload(await file.read())
    summary = upsert_invoices_from_dataframe(db, df)
    db.commit()
    return RedirectResponse(f"/customers?invoice_inserted={summary.inserted}&invoice_updated={summary.updated}", status_code=303)


@app.get("/prospects", response_class=HTMLResponse)
def prospects_page(
    request: Request,
    search: str = "",
    match_filter: str = "",
    review_filter: str = "",
    tier_filter: str = "",
    discovery_filter: str = "all",
    source_filter: str = "",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    query = build_prospect_query(
        search=search,
        match_filter=match_filter,
        review_filter=review_filter,
        tier_filter=tier_filter,
        discovery_filter=discovery_filter,
        source_filter=source_filter,
    )
    prospects = db.scalars(query.limit(300)).all()
    return templates.TemplateResponse(
        request,
        "prospects.html",
        {
            "request": request,
            "prospects": prospects,
            "app_name": settings.app_name,
            "google_places_enabled": bool(settings.google_places_api_key),
            "prospect_contact_count": prospect_contact_count,
            "prospect_reachability_summary": prospect_reachability_summary,
            "kvk_stats": kvk_dashboard_context(db),
            "klaviyo_enabled": bool(settings.klaviyo_private_api_key),
            "klaviyo_default_list_id": settings.klaviyo_default_list_id,
            "klaviyo_default_list_name": settings.klaviyo_default_list_name,
            "flash_message": request.query_params.get("notice", ""),
            **prospect_filters_context(
                search=search,
                match_filter=match_filter,
                review_filter=review_filter,
                tier_filter=tier_filter,
                discovery_filter=discovery_filter,
                source_filter=source_filter,
            ),
        },
    )


@app.get("/prospects/{prospect_id}", response_class=HTMLResponse)
def prospect_detail_page(
    prospect_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    prospect = db.scalar(
        select(Prospect)
        .options(
            selectinload(Prospect.matched_customer),
            selectinload(Prospect.activity_logs),
            selectinload(Prospect.queue_items),
        )
        .where(Prospect.id == prospect_id)
    )
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    bundle = build_outreach_bundle(prospect)
    recent_logs = (
        db.scalars(
            select(ProspectActivityLog)
            .where(ProspectActivityLog.prospect_id == prospect_id)
            .order_by(ProspectActivityLog.created_at.desc())
            .limit(30)
        ).all()
    )
    recent_queue = sorted(prospect.queue_items, key=lambda item: item.created_at, reverse=True)[:10]
    return templates.TemplateResponse(
        request,
        "prospect_detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "prospect": prospect,
            "bundle": bundle,
            "recent_logs": recent_logs,
            "recent_queue": recent_queue,
            "tier_options": TIER_FILTERS,
            "priority_options": ["High", "Medium", "Low", "Very Low", "Manual Review"],
            "review_options": [status.value for status in ProspectState],
        },
    )


@app.post("/admin/import/prospects")
async def import_prospects(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    df = read_csv_upload(await file.read())
    summary = upsert_prospects_from_dataframe(db, df)
    prospects = db.scalars(select(Prospect).order_by(Prospect.id.desc()).limit(summary.inserted + summary.updated)).all()
    for prospect in prospects:
        apply_matching(db, prospect)
        apply_bike_tier(prospect)
    db.commit()
    return RedirectResponse(f"/prospects?inserted={summary.inserted}&updated={summary.updated}", status_code=303)


@app.post("/admin/import/kvk")
async def import_kvk_prospects(
    establishments_file: UploadFile = File(...),
    companies_file: UploadFile = File(...),
    auto_queue_limit: int = Form(50),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    establishments_df = read_csv_upload(await establishments_file.read())
    companies_df = read_csv_upload(await companies_file.read())
    merged_df = prepare_kvk_prospects_dataframe(establishments_df, companies_df)
    summary = upsert_prospects_from_dataframe(db, merged_df, source=KVK_SOURCE)
    touched_prospects = _load_recent_touched_prospects(db, KVK_SOURCE, summary.source_references)
    for prospect in touched_prospects:
        apply_matching(db, prospect)
        apply_bike_tier(prospect)
    db.commit()

    queued_count = 0
    if auto_queue_limit > 0:
        candidates = _kvk_candidates_for_discovery(db, auto_queue_limit)
        if candidates:
            _queue_contact_discovery_batch(db, candidates)
            queued_count = len(candidates)
    return RedirectResponse(_build_kvk_import_message(summary, queued_count), status_code=303)


@app.post("/admin/google-places-search")
def google_places_search(
    query: str = Form(...),
    location: str = Form(""),
    limit: int = Form(10),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    places = search_google_places(query=query, location=location, page_size=limit)
    if places:
        df = pd.DataFrame([place_to_prospect_record(place) for place in places])
        summary = upsert_prospects_from_dataframe(db, df, source="google_places")
        prospects = (
            db.scalars(
                select(Prospect)
                .where(Prospect.source.in_(["google_places", "google_maps_csv"]))
                .order_by(Prospect.updated_at.desc())
                .limit(limit)
            ).all()
        )
        for prospect in prospects:
            apply_matching(db, prospect)
            apply_bike_tier(prospect)
        db.commit()
        return RedirectResponse(f"/prospects?inserted={summary.inserted}&updated={summary.updated}", status_code=303)
    return RedirectResponse("/prospects?message=no_results", status_code=303)


@app.post("/admin/prospects/{prospect_id}/match")
def rematch_prospect(
    prospect_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    apply_matching(db, prospect)
    apply_bike_tier(prospect)
    db.add(
        ProspectActivityLog(
            prospect=prospect,
            action_type="manual_rematch",
            status=prospect.match_status.value,
            source_url=prospect.website or prospect.google_maps_url,
            detail=prospect.match_method or "manual re-match",
        )
    )
    db.commit()
    return redirect_back(request, "/prospects")


@app.post("/admin/prospects/{prospect_id}/discover-email")
def discover_email(
    prospect_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    _queue_contact_discovery(db, prospect)
    return redirect_back(request, f"/prospects/{prospect_id}")


@app.post("/admin/prospects/discover-emails")
def bulk_discover_emails(
    request: Request,
    selected_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    if not selected_ids:
        return redirect_back(request, "/prospects")

    prospects = db.scalars(select(Prospect).where(Prospect.id.in_(selected_ids)).order_by(Prospect.id.asc())).all()
    _queue_contact_discovery_batch(db, prospects)
    return redirect_back(request, "/prospects")


@app.post("/admin/prospects/kvk-discovery")
def kvk_discovery_batch(
    request: Request,
    limit: int = Form(50),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    candidates = _kvk_candidates_for_discovery(db, max(1, min(limit, 250)))
    _queue_contact_discovery_batch(db, candidates)
    return redirect_back(request, "/prospects?source_filter=kvk")


@app.post("/admin/prospects/export.csv")
def export_prospects_csv_route(
    search: str = Form(""),
    match_filter: str = Form(""),
    review_filter: str = Form(""),
    tier_filter: str = Form(""),
    discovery_filter: str = Form("all"),
    source_filter: str = Form(""),
    selected_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> PlainTextResponse:
    prospects = exportable_prospects(
        db,
        search=search,
        match_filter=match_filter,
        review_filter=review_filter,
        tier_filter=tier_filter,
        discovery_filter=discovery_filter,
        source_filter=source_filter,
        selected_ids=selected_ids,
        require_email=True,
        exclude_existing_customers=False,
    )
    csv_text = export_prospects_csv_text(prospects)
    source_label = source_filter or "prospects"
    headers = {"Content-Disposition": f'attachment; filename="schild-{source_label}-emails.csv"'}
    return PlainTextResponse(csv_text, media_type="text/csv; charset=utf-8", headers=headers)


@app.post("/admin/prospects/export/klaviyo")
def export_prospects_klaviyo_route(
    request: Request,
    list_id: str = Form(""),
    list_name: str = Form(""),
    search: str = Form(""),
    match_filter: str = Form(""),
    review_filter: str = Form(""),
    tier_filter: str = Form(""),
    discovery_filter: str = Form("all"),
    source_filter: str = Form(""),
    selected_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    prospects = exportable_prospects(
        db,
        search=search,
        match_filter=match_filter,
        review_filter=review_filter,
        tier_filter=tier_filter,
        discovery_filter=discovery_filter,
        source_filter=source_filter,
        selected_ids=selected_ids,
        require_email=True,
        exclude_existing_customers=True,
    )
    try:
        result = export_prospects_to_klaviyo(prospects, list_id=list_id, list_name=list_name)
    except KlaviyoExportError as exc:
        fallback = "/prospects"
        if source_filter:
            fallback = f"/prospects?source_filter={source_filter}"
        return RedirectResponse(with_notice(fallback, str(exc)), status_code=303)

    notice = f"Klaviyo export queued: {result.exported_count} profiles to list {result.list_id}."
    fallback = "/prospects"
    if source_filter:
        fallback = f"/prospects?source_filter={source_filter}"
    return RedirectResponse(with_notice(fallback, notice), status_code=303)


@app.post("/admin/prospects/{prospect_id}/review")
def review_prospect(
    prospect_id: int,
    request: Request,
    action: str = Form(...),
    db: Session = Depends(get_db),
    username: str = Depends(require_admin),
) -> RedirectResponse:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    if action == "approve":
        prospect.review_status = ProspectState.approved
        prospect.approved_for_outreach = prospect.match_status == MatchStatus.new_prospect
        if prospect.approved_for_outreach:
            ensure_prospect_contacts(db, prospect)
    elif action == "reject":
        prospect.review_status = ProspectState.rejected
        prospect.approved_for_outreach = False
    else:
        prospect.review_status = ProspectState.pending
        prospect.approved_for_outreach = False
    prospect.notes = f"{prospect.notes}\nReviewed by {username} with action={action}".strip()
    db.add(
        ProspectActivityLog(
            prospect=prospect,
            action_type="review",
            status=action,
            source_url=prospect.website or prospect.google_maps_url,
            detail=f"Reviewed by {username}",
        )
    )
    db.commit()
    return redirect_back(request, "/prospects")


@app.post("/admin/prospects/{prospect_id}/override")
def override_prospect(
    prospect_id: int,
    request: Request,
    bike_shop_tier: str = Form(...),
    outreach_priority: str = Form(...),
    headquarters_required: str = Form("false"),
    review_status: str = Form(...),
    custom_use_case: str = Form(""),
    proof_line: str = Form(""),
    notes: str = Form(""),
    approved_for_outreach: str = Form("false"),
    db: Session = Depends(get_db),
    username: str = Depends(require_admin),
) -> RedirectResponse:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    prospect.manual_tier_override = True
    prospect.bike_shop_tier = bike_shop_tier
    prospect.outreach_priority = outreach_priority
    prospect.headquarters_required = headquarters_required == "true"
    prospect.review_status = ProspectState(review_status)
    prospect.custom_use_case = custom_use_case.strip()
    prospect.proof_line = proof_line.strip()
    prospect.notes = notes.strip()
    prospect.approved_for_outreach = approved_for_outreach == "true" and prospect.match_status == MatchStatus.new_prospect
    apply_bike_tier(prospect)
    if prospect.match_status != MatchStatus.new_prospect:
        prospect.approved_for_outreach = False

    db.add(
        ProspectActivityLog(
            prospect=prospect,
            action_type="manual_override",
            status="saved",
            source_url=prospect.website or prospect.google_maps_url,
            detail=f"Updated by {username}",
        )
    )
    db.commit()
    return redirect_back(request, f"/prospects/{prospect_id}")


@app.get("/queue", response_class=HTMLResponse)
def queue_page(
    request: Request,
    queue_date: str = "",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    selected_day = date.fromisoformat(queue_date) if queue_date else date.today()
    items = db.scalars(
        select(OutreachQueueItem)
        .options(selectinload(OutreachQueueItem.prospect))
        .where(OutreachQueueItem.queue_date == selected_day)
        .order_by(OutreachQueueItem.id.desc())
        .limit(300)
    ).all()
    preview_items = preview_queue_for_day(db, selected_day)
    if settings.auto_contact_discovery_enabled:
        db.commit()
    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "request": request,
            "items": items,
            "preview_items": preview_items,
            "today": selected_day,
            "app_name": settings.app_name,
            "campaign_active": settings.campaign_active,
            "daily_send_limit": settings.daily_send_limit,
            "send_window_start": settings.send_window_start,
            "send_window_end": settings.send_window_end,
            "reply_to_email": settings.reply_to_email,
        },
    )


@app.get("/queue/preview", response_class=HTMLResponse)
def queue_preview_page(
    request: Request,
    queue_date: str = "",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    selected_day = date.fromisoformat(queue_date) if queue_date else date.today()
    preview_items = preview_queue_for_day(db, selected_day)
    if settings.auto_contact_discovery_enabled:
        db.commit()
    return templates.TemplateResponse(
        request,
        "queue_preview.html",
        {
            "request": request,
            "today": selected_day,
            "preview_items": preview_items,
            "app_name": settings.app_name,
        },
    )


@app.get("/queue/export.csv")
def queue_export_csv(
    queue_date: str = "",
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> PlainTextResponse:
    selected_day = date.fromisoformat(queue_date) if queue_date else date.today()
    csv_text = export_queue_csv(db, selected_day)
    headers = {"Content-Disposition": f'attachment; filename="schild-queue-{selected_day.isoformat()}.csv"'}
    return PlainTextResponse(csv_text, media_type="text/csv; charset=utf-8", headers=headers)


@app.post("/admin/queue/build")
def build_queue(
    queue_date: str = Form(""),
    limit: int = Form(settings.default_queue_size),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    selected_day = date.fromisoformat(queue_date) if queue_date else date.today()
    created = run_daily_queue_build(db, selected_day, limit)
    db.commit()
    return RedirectResponse(f"/queue?queue_date={selected_day.isoformat()}&created={created}", status_code=303)


@app.post("/admin/queue/send-ready")
def send_ready_queue_route(
    queue_date: str = Form(""),
    limit: int = Form(settings.daily_send_limit),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    selected_day = date.fromisoformat(queue_date) if queue_date else date.today()
    sent = run_daily_queue_send(db, selected_day, limit)
    db.commit()
    return RedirectResponse(f"/queue?queue_date={selected_day.isoformat()}&sent={sent}", status_code=303)


@app.post("/admin/queue/{item_id}/send")
def send_queue(item_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    item = db.scalar(select(OutreachQueueItem).options(selectinload(OutreachQueueItem.prospect)).where(OutreachQueueItem.id == item_id))
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    send_queue_item(db, item)
    db.commit()
    return redirect_back(request, "/queue")


@app.post("/admin/queue/{item_id}/skip")
def skip_queue(item_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    item = db.get(OutreachQueueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    item.state = QueueState.skipped
    db.commit()
    return redirect_back(request, "/queue")


@app.get("/suppression", response_class=HTMLResponse)
def suppression_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    entries = db.scalars(select(SuppressionEntry).order_by(SuppressionEntry.created_at.desc()).limit(200)).all()
    return templates.TemplateResponse(
        request,
        "suppression.html",
        {"request": request, "entries": entries, "app_name": settings.app_name},
    )


@app.post("/admin/suppression")
def add_suppression(
    email: str = Form(""),
    domain: str = Form(""),
    company_name: str = Form(""),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    db.add(
        SuppressionEntry(
            email=normalize_email(email),
            domain=normalize_domain(domain),
            company_name=company_name,
            reason=reason or "manual suppression",
            source="admin",
            active=True,
        )
    )
    db.commit()
    return RedirectResponse("/suppression", status_code=303)


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    emails = db.scalars(select(EmailLog).order_by(EmailLog.created_at.desc()).limit(100)).all()
    webhooks = db.scalars(select(WebhookLog).order_by(WebhookLog.received_at.desc()).limit(100)).all()
    activities = db.scalars(select(ProspectActivityLog).order_by(ProspectActivityLog.created_at.desc()).limit(150)).all()
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"request": request, "emails": emails, "webhooks": webhooks, "activities": activities, "app_name": settings.app_name},
    )


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=settings.stripe_webhook_secret)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook: {exc}") from exc
    sync_stripe_event(db, event)
    db.commit()
    return PlainTextResponse("ok")


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, email: str, db: Session = Depends(get_db)) -> HTMLResponse:
    if token != build_unsubscribe_token(email):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe token")
    db.add(
        SuppressionEntry(
            email=normalize_email(email),
            domain="",
            company_name="",
            reason="recipient unsubscribe",
            source="unsubscribe_link",
            active=True,
        )
    )
    db.commit()
    return HTMLResponse("<h2>Unsubscribed</h2><p>You will not receive future outreach from Schild Inc.</p>")


# ---------------------------------------------------------------------------
# Directory crawler routes (sector x country jobs -> prospects)
# ---------------------------------------------------------------------------


@app.get("/crawler", response_class=HTMLResponse)
def crawler_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    flash: str = "",
) -> HTMLResponse:
    status = crawler_module.get_crawler_status(db)
    return templates.TemplateResponse(
        request,
        "crawler.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "status": status,
            "sectors": crawler_module.available_sectors(),
            "countries": crawler_module.available_countries(),
            "country_cities_json": json.dumps(crawler_module.COUNTRY_CITIES),
            "presets": crawler_module.JOB_PRESETS,
            "flash": flash,
        },
    )


@app.post("/crawler/jobs")
def crawler_job_create(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
    name: str = Form(""),
    sectors: list[str] = Form([]),
    country_code: str = Form(...),
    cities: list[str] = Form([]),
    extra_cities: str = Form(""),
    max_results: int = Form(500),
    extract_emails: str = Form("1"),
) -> RedirectResponse:
    picked = [s for s in sectors if s in crawler_module.SECTOR_SEARCH_TERMS]
    cc = country_code.strip().upper()
    # Country-first: any 2-letter ISO country works via OSM when specific
    # cities are given; a whole-country sweep needs the built-in city grid.
    city_list = crawler_module.parse_cities(",".join([*cities, extra_cities]))
    country_ok = cc in crawler_module.COUNTRY_CITIES or (len(cc) == 2 and cc.isalpha() and city_list)
    if not picked or not country_ok:
        return RedirectResponse(f"/crawler?flash={quote_plus('Pick at least one sector and a supported country (or add cities for other countries)')}", status_code=303)
    label = crawler_module.COUNTRY_LABELS.get(cc, cc)
    cities_csv = ",".join(city_list)
    scope = f"{', '.join(city_list[:3])}{'…' if len(city_list) > 3 else ''}" if city_list else label
    job = CrawlJob(
        name=name.strip() or f"{' + '.join(picked)} — {scope}",
        sectors=",".join(picked),
        country_code=cc,
        cities=cities_csv,
        status="running",
        max_results=max(1, min(5000, max_results)),
        extract_emails=extract_emails == "1",
        queries_total=len(crawler_module.build_query_plan(",".join(picked), cc, cities_csv)),
    )
    db.add(job)
    db.commit()
    log_audit(db, actor=auth_module.actor_label(request, db), action="crawler.job_create",
              target_type="crawl_job", target_id=str(job.id), detail=job.name)
    return RedirectResponse(f"/crawler?flash={quote_plus(f'Job started: {job.name}')}", status_code=303)


@app.post("/crawler/presets")
def crawler_seed_presets(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    """Create the standing target-list jobs (skips names that already exist)."""
    created = 0
    for preset in crawler_module.JOB_PRESETS:
        exists = db.scalar(select(CrawlJob.id).where(CrawlJob.name == preset["name"]).limit(1))
        if exists is not None:
            continue
        db.add(CrawlJob(
            name=preset["name"],
            sectors=preset["sectors"],
            country_code=preset["country_code"],
            status="running",
            queries_total=len(crawler_module.build_query_plan(preset["sectors"], preset["country_code"])),
        ))
        created += 1
    db.commit()
    log_audit(db, actor=auth_module.actor_label(request, db), action="crawler.seed_presets",
              target_type="crawl_job", detail=f"{created} created")
    msg = f"{created} preset job(s) started" if created else "All preset jobs already exist"
    return RedirectResponse(f"/crawler?flash={quote_plus(msg)}", status_code=303)


@app.post("/crawler/jobs/{job_id}/pause")
def crawler_job_pause(
    job_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    job = db.get(CrawlJob, job_id)
    if job is not None and job.status == "running":
        job.status = "paused"
        job.current_activity = "Paused by operator"
        db.commit()
    return RedirectResponse("/crawler", status_code=303)


@app.post("/crawler/jobs/{job_id}/resume")
def crawler_job_resume(
    job_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    job = db.get(CrawlJob, job_id)
    if job is not None and job.status in {"paused", "error", "done"}:
        job.status = "running"
        job.error = ""
        job.finished_at = None
        db.commit()
    return RedirectResponse("/crawler", status_code=303)


@app.post("/crawler/jobs/{job_id}/delete")
def crawler_job_delete(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    job = db.get(CrawlJob, job_id)
    if job is not None:
        # Stop the worker first (it re-checks status between queries), keep the
        # harvested prospects but detach them from the job row.
        job.status = "paused"
        db.commit()
        db.execute(
            Prospect.__table__.update().where(Prospect.crawl_job_id == job_id).values(crawl_job_id=None)
        )
        job_name = job.name
        db.delete(job)
        db.commit()
        log_audit(db, actor=auth_module.actor_label(request, db), action="crawler.job_delete",
                  target_type="crawl_job", target_id=str(job_id), detail=job_name)
    return RedirectResponse(f"/crawler?flash={quote_plus('Job deleted (harvested businesses kept)')}", status_code=303)


@app.get("/api/crawler/status")
def crawler_status_api(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> JSONResponse:
    return JSONResponse(crawler_module.get_crawler_status(db))


@app.get("/crawler/jobs/{job_id}/export.csv")
def crawler_job_export(
    job_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    has_email: str = "",
) -> StreamingResponse:
    job = db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown crawl job")
    q = select(Prospect).where(Prospect.crawl_job_id == job_id).order_by(Prospect.city, Prospect.company_name)
    if has_email == "1":
        q = q.where(Prospect.email != "")
    rows = db.scalars(q).all()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "company_name", "sector", "email", "email_confidence", "phone",
        "website", "city", "country", "address", "google_maps_url", "existing_customer",
    ])
    for p in rows:
        writer.writerow([
            p.company_name, p.main_sector, p.email, p.email_confidence, p.phone,
            p.website, p.city, p.country_code, p.address, p.google_maps_url,
            "yes" if p.match_status == MatchStatus.existing_customer else "no",
        ])
    buffer.seek(0)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in job.name.lower().replace(" ", "-"))
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="crawl-{job_id}-{safe_name}.csv"'},
    )


# ---------------------------------------------------------------------------
# KVK routes
# ---------------------------------------------------------------------------

KVK_TIER_FILTERS = ["Good Tier", "Hard to Reach", "Mid Tier", "Low Tier", "Brand Store", "Low Fit", "Unclassified"]
KVK_ENRICHMENT_FILTERS = ["all", "pending", "running", "discovered", "partial", "no_website", "no_contacts", "error"]
KVK_MATCH_FILTERS = ["all", "no_match", "matched", "unknown"]


@app.get("/kvk", response_class=HTMLResponse)
def kvk_companies_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    search: str = "",
    tier: str = "",
    enrichment: str = "all",
    match: str = "all",
    has_email: str = "",
    has_website: str = "",
    search_status: str = "all",  # all / never / once / twice_plus / tried_no_email
    page: int = 1,
) -> HTMLResponse:
    PAGE_SIZE = 50
    q = select(KvkCompany).order_by(KvkCompany.company_name)

    if search:
        q = q.where(
            or_(
                KvkCompany.company_name.ilike(f"%{search}%"),
                KvkCompany.primary_city.ilike(f"%{search}%"),
                KvkCompany.kvk_number.ilike(f"%{search}%"),
            )
        )
    if tier:
        q = q.where(KvkCompany.bike_shop_tier == tier)
    if enrichment != "all":
        q = q.where(KvkCompany.enrichment_status == enrichment)
    if match != "all":
        q = q.where(KvkCompany.client_match_status == match)
    if has_email == "1":
        q = q.where(KvkCompany.email_public != "")
    elif has_email == "0":
        q = q.where(KvkCompany.email_public == "")
    if has_website == "1":
        q = q.where(KvkCompany.website != "")
    elif has_website == "0":
        q = q.where(KvkCompany.website == "")
    # New: filter by search-attempt status
    if search_status == "never":
        q = q.where(KvkCompany.search_attempts == 0)
    elif search_status == "once":
        q = q.where(KvkCompany.search_attempts == 1)
    elif search_status == "twice_plus":
        q = q.where(KvkCompany.search_attempts >= 2)
    elif search_status == "tried_no_email":
        q = q.where(KvkCompany.search_attempts >= 1).where(KvkCompany.email_public == "")
    elif search_status == "offline":
        # Tried at least once and the agent found ZERO online presence:
        # no website, no email, no phone, no WhatsApp, no IG, no LI.
        # These are physical-only / offline-only businesses — they need
        # door-to-door or phone outreach via the KVK address, not email.
        q = (
            q.where(KvkCompany.search_attempts >= 1)
             .where(KvkCompany.website == "")
             .where(KvkCompany.email_public == "")
             .where(KvkCompany.phone_public == "")
             .where(KvkCompany.whatsapp_number == "")
             .where(KvkCompany.instagram_url == "")
             .where(KvkCompany.linkedin_url == "")
        )

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    companies = db.scalars(q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    recent_imports = db.scalars(
        select(KvkImportLog).order_by(KvkImportLog.started_at.desc()).limit(5)
    ).all()

    # Cheap counters across the WHOLE dataset for the filter chips at top
    search_counts = {
        "never":          db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.search_attempts == 0)) or 0,
        "once":           db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.search_attempts == 1)) or 0,
        "twice_plus":     db.scalar(select(func.count(KvkCompany.id)).where(KvkCompany.search_attempts >= 2)) or 0,
        "tried_no_email": db.scalar(
            select(func.count(KvkCompany.id))
            .where(KvkCompany.search_attempts >= 1)
            .where(KvkCompany.email_public == "")
        ) or 0,
        "offline":        db.scalar(
            select(func.count(KvkCompany.id))
            .where(KvkCompany.search_attempts >= 1)
            .where(KvkCompany.website == "")
            .where(KvkCompany.email_public == "")
            .where(KvkCompany.phone_public == "")
            .where(KvkCompany.whatsapp_number == "")
            .where(KvkCompany.instagram_url == "")
            .where(KvkCompany.linkedin_url == "")
        ) or 0,
    }

    return templates.TemplateResponse("kvk_companies.html", {
        "request": request,
        "app_name": settings.app_name,
        "companies": companies,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "tier": tier,
        "enrichment": enrichment,
        "match": match,
        "has_email": has_email,
        "has_website": has_website,
        "search_status": search_status,
        "search_counts": search_counts,
        "tier_options": KVK_TIER_FILTERS,
        "enrichment_options": KVK_ENRICHMENT_FILTERS,
        "match_options": KVK_MATCH_FILTERS,
        "recent_imports": recent_imports,
    })


@app.post("/kvk/import-companies")
async def kvk_import_companies(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    contents = await file.read()
    df = read_csv_upload(contents)
    summary = upsert_kvk_companies_from_dataframe(db, df, file_name=file.filename or "upload")
    db.commit()
    # Run matching immediately after import
    Thread(target=_run_post_import_matching, daemon=True).start()
    return RedirectResponse(
        f"/kvk?flash=Geïmporteerd%3A+{summary.inserted}+nieuw%2C+{summary.updated}+bijgewerkt.+Verrijking+start+automatisch.",
        status_code=303,
    )


@app.post("/kvk/import-establishments")
async def kvk_import_establishments(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    contents = await file.read()
    df = read_csv_upload(contents)
    summary = upsert_kvk_establishments_from_dataframe(db, df, file_name=file.filename or "upload")
    db.commit()
    return RedirectResponse(
        f"/kvk?flash=Vestigingen+geïmporteerd%3A+{summary.inserted}+nieuw%2C+{summary.updated}+bijgewerkt%2C+{summary.failed}+mislukt",
        status_code=303,
    )


@app.post("/kvk/run-matching")
def kvk_run_matching_bulk(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    companies = db.scalars(select(KvkCompany).where(KvkCompany.client_match_status == "unknown")).all()
    count = 0
    for company in companies:
        apply_kvk_matching(db, company)
        count += 1
        if count % 100 == 0:
            db.commit()
    db.commit()
    return RedirectResponse(f"/kvk?flash={count}+bedrijven+gematcht", status_code=303)


@app.post("/kvk/{company_id}/run-matching")
def kvk_run_matching_single(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    company = db.get(KvkCompany, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    apply_kvk_matching(db, company)
    db.commit()
    return RedirectResponse(f"/kvk/{company_id}", status_code=303)


@app.post("/kvk/{company_id}/enrich")
def kvk_enrich_single(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    company = db.get(KvkCompany, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    company.enrichment_status = "searching" if not company.website else "running"
    db.commit()
    Thread(target=run_kvk_enrichment_job, args=(company_id,), daemon=True).start()
    return RedirectResponse(f"/kvk/{company_id}?flash=Contactgegevens+worden+opgezocht", status_code=303)


@app.post("/kvk/{company_id}/find-website")
def kvk_find_website(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    """Search for a website using business name + city (Places API or DuckDuckGo)."""
    company = db.get(KvkCompany, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")

    result = find_website_for_kvk_company(company)
    if result.get("website"):
        website = result["website"]
        if not website.startswith(("http://", "https://")):
            website = f"https://{website}"
        company.website = website
        company.website_domain = normalize_domain(website)
        if result.get("phone") and not company.phone_public:
            company.phone_public = result["phone"]
            company.phone_source_url = result.get("source", "search")
            company.phone_confidence = result.get("confidence", "medium")
        db.commit()
        flash = f"Website+gevonden+via+{result.get('source','search')}%3A+{website[:60]}"
    else:
        flash = "Geen+website+gevonden+via+zoekactie"

    return RedirectResponse(f"/kvk/{company_id}?flash={flash}", status_code=303)


@app.post("/kvk/bulk-enrich")
async def kvk_bulk_enrich(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    form = await request.form()
    ids_raw = form.get("company_ids", "")
    if ids_raw:
        company_ids = [int(i) for i in str(ids_raw).split(",") if i.strip().isdigit()]
    else:
        # Enrich up to 30 pending — includes those without websites (will search first)
        company_ids = [
            c.id for c in db.scalars(
                select(KvkCompany)
                .where(KvkCompany.enrichment_status.in_(["pending", "no_website", "error"]))
                .where(KvkCompany.already_client_flag.is_(False))
                .limit(30)
            ).all()
        ]
    for cid in company_ids:
        c = db.get(KvkCompany, cid)
        if c and c.enrichment_status not in ("running", "searching"):
            c.enrichment_status = "searching" if not c.website else "running"
    db.commit()
    Thread(target=run_kvk_bulk_enrichment, args=(company_ids,), daemon=True).start()
    return RedirectResponse(f"/kvk?flash={len(company_ids)}+bedrijven+worden+verrijkt", status_code=303)


@app.post("/kvk/{company_id}/approve")
def kvk_approve_outreach(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    company = db.get(KvkCompany, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    if company.already_client_flag:
        return RedirectResponse(f"/kvk/{company_id}?flash=Bestaande+klant%3A+outreach+niet+toegestaan", status_code=303)
    if not company.email_public:
        return RedirectResponse(f"/kvk/{company_id}?flash=Geen+e-mailadres+beschikbaar", status_code=303)
    company.approved_for_outreach = not company.approved_for_outreach
    db.commit()
    label = "goedgekeurd" if company.approved_for_outreach else "ingetrokken"
    return RedirectResponse(f"/kvk/{company_id}?flash=Outreach+{label}", status_code=303)


@app.post("/kvk/{company_id}/re-tier")
def kvk_retier_single(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    company = db.get(KvkCompany, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    decision = score_kvk_company_tier(company)
    company.bike_shop_tier = decision.bike_shop_tier
    company.bike_shop_segment = decision.bike_shop_segment
    company.outreach_priority = decision.outreach_priority
    company.headquarters_required = decision.headquarters_required
    company.franchise_or_buying_group = decision.franchise_or_buying_group
    company.tier_reason = decision.tier_reason
    company.recommended_sales_angle = decision.recommended_sales_angle
    company.recommended_contact_type = decision.recommended_contact_type
    db.commit()
    return RedirectResponse(f"/kvk/{company_id}?flash=Tier+herberekend%3A+{decision.bike_shop_tier}", status_code=303)


# IMPORTANT: this static-path GET MUST come BEFORE the catch-all
# /kvk/{company_id} below, otherwise FastAPI tries to parse
# "export.csv" as an int and returns 422.
@app.get("/kvk/export.csv")
def kvk_export_csv(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    tier: str = "",
    has_email: str = "",
    match: str = "",
    confidence: str = "",
) -> StreamingResponse:
    q = select(KvkCompany).order_by(KvkCompany.company_name)
    # Treat 'all' / 'any' as no-op — they're the dropdown's default UI label,
    # not an actual filter value. Same treatment for empty string.
    NO_OP = {"", "all", "any"}
    if tier and tier not in NO_OP:
        q = q.where(KvkCompany.bike_shop_tier == tier)
    if has_email == "1":
        q = q.where(KvkCompany.email_public != "")
    elif has_email == "0":
        q = q.where(KvkCompany.email_public == "")
    if match and match not in NO_OP:
        q = q.where(KvkCompany.client_match_status == match)
    if confidence and confidence not in NO_OP:
        q = q.where(KvkCompany.email_confidence == confidence)
    companies = db.scalars(q).all()

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([
            "kvk_number", "company_name", "city", "postal_code", "address",
            "website", "email", "phone", "whatsapp", "instagram", "linkedin",
            "bike_shop_tier", "outreach_priority",
            "already_client", "client_match_status",
            "enrichment_status", "email_confidence", "email_source",
            "approved_for_outreach",
        ])
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for c in companies:
            w.writerow([
                c.kvk_number or "",
                c.company_name or "",
                c.primary_city or "",
                c.primary_postal_code or "",
                c.primary_address or "",
                c.website or "",
                c.email_public or "",
                c.phone_public or "",
                c.whatsapp_number or "",
                c.instagram_url or "",
                c.linkedin_url or "",
                c.bike_shop_tier or "",
                c.outreach_priority or "",
                "ja" if c.already_client_flag else "nee",
                c.client_match_status or "",
                c.enrichment_status or "",
                c.email_confidence or "",
                c.email_source_url or "",
                "ja" if c.approved_for_outreach else "nee",
            ])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    # Reflect active filters in the filename (skip no-op values)
    slug_parts: list[str] = []
    if tier and tier not in NO_OP:
        slug_parts.append("tier-" + tier.lower().replace(" ", "-"))
    if has_email == "1":
        slug_parts.append("email-yes")
    elif has_email == "0":
        slug_parts.append("email-no")
    if match and match not in NO_OP:
        slug_parts.append("match-" + match)
    if confidence and confidence not in NO_OP:
        slug_parts.append("conf-" + confidence)
    suffix = "-".join(slug_parts) or "all"
    filename = f"kvk-{suffix}-{date.today().isoformat()}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/kvk/{company_id}", response_class=HTMLResponse)
def kvk_company_detail(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    flash: str = "",
) -> HTMLResponse:
    company = db.scalar(
        select(KvkCompany)
        .where(KvkCompany.id == company_id)
        .options(selectinload(KvkCompany.matched_customer), selectinload(KvkCompany.establishments))
    )
    if not company:
        raise HTTPException(status_code=404, detail="Bedrijf niet gevonden")
    return templates.TemplateResponse("kvk_company_detail.html", {
        "request": request,
        "app_name": settings.app_name,
        "company": company,
        "flash": flash,
    })


# -- Post-import background matching --
def _run_post_import_matching() -> None:
    db = SessionLocal()
    try:
        companies = db.scalars(
            select(KvkCompany).where(KvkCompany.client_match_status == "unknown")
        ).all()
        for company in companies:
            apply_kvk_matching(db, company)
        db.commit()
    finally:
        db.close()


# -- Manual email override --
@app.post("/kvk/{company_id}/set-email")
async def kvk_set_email(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    form = await request.form()
    email = normalize_email(str(form.get("email", "")))
    if not email or "@" not in email:
        return RedirectResponse(f"/kvk/{company_id}?flash=Ongeldig+e-mailadres", status_code=303)
    company = db.get(KvkCompany, company_id)
    if not company:
        raise HTTPException(status_code=404)
    company.email_public = email
    company.email_source_url = "manual_override"
    company.email_confidence = "manual"
    if company.enrichment_status not in ("discovered",):
        company.enrichment_status = "partial"
    apply_kvk_matching(db, company)
    db.commit()
    return RedirectResponse(f"/kvk/{company_id}?flash=E-mail+opgeslagen%3A+{email}", status_code=303)


# -- Inline email update from list (AJAX) --
@app.post("/kvk/{company_id}/set-email-inline")
async def kvk_set_email_inline(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> JSONResponse:
    form = await request.form()
    email = normalize_email(str(form.get("email", "")))
    if not email or "@" not in email:
        return JSONResponse({"ok": False, "error": "Ongeldig e-mailadres"}, status_code=400)
    company = db.get(KvkCompany, company_id)
    if not company:
        return JSONResponse({"ok": False, "error": "Niet gevonden"}, status_code=404)
    company.email_public = email
    company.email_source_url = "manual_override"
    company.email_confidence = "manual"
    if company.enrichment_status not in ("discovered",):
        company.enrichment_status = "partial"
    apply_kvk_matching(db, company)
    db.commit()
    return JSONResponse({"ok": True, "email": email})


@app.post("/kvk/{company_id}/verify-email")
async def kvk_verify_email(
    company_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> JSONResponse:
    """One-click verify: mark this email as human-checked (confidence='verified')."""
    company = db.get(KvkCompany, company_id)
    if not company or not (company.email_public or "").strip():
        return JSONResponse({"ok": False, "error": "no_email"}, status_code=400)
    company.email_confidence = "verified"
    company.approved_for_outreach = True
    company.enrichment_status = "discovered"
    db.commit()
    return JSONResponse({"ok": True, "email": company.email_public, "confidence": "verified"})


@app.post("/kvk/{company_id}/reject-email")
async def kvk_reject_email(
    company_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> JSONResponse:
    """One-click reject: clear a wrong email so the record goes back to pending."""
    company = db.get(KvkCompany, company_id)
    if not company:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    company.email_public = ""
    company.email_confidence = ""
    company.email_source_url = ""
    company.approved_for_outreach = False
    company.enrichment_status = "pending"
    apply_kvk_matching(db, company)
    db.commit()
    return JSONResponse({"ok": True})


# -- Enrichment progress API (for dashboard live counter) --
@app.get("/api/kvk/progress")
def kvk_progress(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    return JSONResponse(get_enrichment_progress(db))


# ── Local browser-agent endpoints ────────────────────────────────────────────
# These two endpoints let a Playwright script running on the user's laptop
# (residential IP, can actually scrape Google) drive enrichment from
# outside Railway. The script polls /agent/pending for a batch, runs
# Google searches in a real browser, then POSTs each result back to
# /agent/result. See scripts/email_agent.py.
@app.get("/api/kvk/agent/pending")
def kvk_agent_pending(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    limit: int = 25,
    max_attempts: int = 2,
) -> JSONResponse:
    """
    Return records the agent should search next. Two-axis prioritization:

      1. enrichment_status — skip records that are already done
         ('discovered'/'partial') or marked as 'no_contacts'
      2. search_attempts — fewest first, so we exhaust never-searched
         records before retrying records that already failed once
         or twice. Records with attempts >= max_attempts (default 2)
         are excluded entirely, so we don't spin forever on hopeless
         records.

    Sort tie-breaker is `id` so the order is stable across calls.
    """
    limit = max(1, min(100, limit))
    rows = db.scalars(
        select(KvkCompany)
        .where(KvkCompany.email_public == "")
        .where(KvkCompany.already_client_flag.is_(False))
        .where(
            KvkCompany.enrichment_status.notin_(
                ["discovered", "partial", "no_contacts"]
            )
        )
        .where(KvkCompany.search_attempts < max_attempts)
        .order_by(KvkCompany.search_attempts.asc(), KvkCompany.id.asc())
        .limit(limit)
    ).all()
    return JSONResponse([
        {
            "id": r.id,
            "company_name": r.company_name,
            "city": r.primary_city or "",
            "postal_code": r.primary_postal_code or "",
            "address": r.primary_address or "",
            "current_website": r.website or "",
            "current_status": r.enrichment_status,
            "search_attempts": r.search_attempts,
        }
        for r in rows
    ])


@app.post("/api/kvk/agent/result")
def kvk_agent_result(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    company_id: int = Form(...),
    email: str = Form(""),
    website: str = Form(""),
    phone: str = Form(""),
    whatsapp_number: str = Form(""),
    whatsapp_url: str = Form(""),
    instagram_url: str = Form(""),
    linkedin_url: str = Form(""),
    source: str = Form("browser_agent"),
    confidence: str = Form("high"),
    note: str = Form(""),
) -> JSONResponse:
    """
    Save email + any social/phone contacts discovered by the local
    browser agent. Empty `email` is allowed and used to mark
    "checked but nothing found" so we don't keep handing the same
    record back to it. If the agent found ANY contact channel
    (phone/WhatsApp/Instagram/LinkedIn even with no email) the record
    flips to 'partial' instead of 'no_contacts'.
    """
    company = db.get(KvkCompany, company_id)
    if not company:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    company.last_enrichment_attempt_at = datetime.now(timezone.utc)
    # Every agent post counts as one attempt — used by /agent/pending
    # to prioritize records with fewer attempts first.
    company.search_attempts = (company.search_attempts or 0) + 1

    has_email = bool(email and "@" in email)
    has_any_contact = bool(
        has_email or phone or whatsapp_number or whatsapp_url or instagram_url or linkedin_url
    )

    if has_email:
        company.email_public = email.strip().lower()
        company.email_source_url = source
        company.email_confidence = confidence or "high"
        if website and not company.website:
            company.website = website
            from app.utils import normalize_domain as _nd
            company.website_domain = _nd(website)
        if not company.website_domain:
            company.website_domain = email.split("@", 1)[1]

    # Social / phone — only overwrite when empty so a manual entry is
    # never clobbered by an automated find.
    if phone and not (company.phone_public or "").strip():
        company.phone_public = phone.strip()
        company.phone_source_url = source
        company.phone_confidence = confidence or "high"
    if whatsapp_number and not (company.whatsapp_number or "").strip():
        company.whatsapp_number = whatsapp_number.strip()
    if whatsapp_url and not (company.whatsapp_url or "").strip():
        company.whatsapp_url = whatsapp_url.strip()
    if instagram_url and not (company.instagram_url or "").strip():
        company.instagram_url = instagram_url.strip()
    if linkedin_url and not (company.linkedin_url or "").strip():
        company.linkedin_url = linkedin_url.strip()

    if note:
        company.notes = ((company.notes or "") + " | agent: " + note).lstrip(" |")

    if has_email:
        company.enrichment_status = "discovered"
        from app.matching import apply_kvk_matching
        apply_kvk_matching(db, company)
    elif has_any_contact:
        # Found phone / socials but no email — keep it active for later
        # email finds, don't flip to no_contacts
        if company.enrichment_status not in ("discovered", "no_website"):
            company.enrichment_status = "partial"
    else:
        if company.enrichment_status not in ("discovered", "no_website"):
            company.enrichment_status = "no_contacts"

    db.commit()
    return JSONResponse({
        "ok": True,
        "id": company.id,
        "email": company.email_public,
        "phone": company.phone_public,
        "whatsapp": company.whatsapp_number,
        "instagram": company.instagram_url,
        "linkedin": company.linkedin_url,
        "status": company.enrichment_status,
    })


# ── Owner / decision-maker enrichment agent (Google-snippet) ────────────────


@app.get("/api/enrich/owner/pending")
def owner_enrich_pending(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    limit: int = 25,
    max_attempts: int = 2,
) -> JSONResponse:
    """Records needing an owner name. Prioritizes never-searched first.

    Only hands out records that aren't already clients and still need a name
    (owner_status='pending'), capped by attempts so we don't loop forever.
    """
    limit = max(1, min(100, limit))
    rows = db.scalars(
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(KvkCompany.owner_status == "pending")
        .where(KvkCompany.owner_search_attempts < max_attempts)
        .order_by(KvkCompany.owner_search_attempts.asc(), KvkCompany.id.asc())
        .limit(limit)
    ).all()
    return JSONResponse([
        {
            "id": r.id,
            "company_name": r.company_name,
            "city": r.primary_city or "",
            "country": r.country_code or "",
            "website": r.website or "",
            "instagram_url": r.instagram_url or "",
            "linkedin_url": r.linkedin_url or "",
            "owner_search_attempts": r.owner_search_attempts,
        }
        for r in rows
    ])


@app.post("/api/enrich/owner/result")
def owner_enrich_result(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    company_id: int = Form(...),
    owner_name: str = Form(""),
    owner_role: str = Form(""),
    instagram_url: str = Form(""),
    linkedin_url: str = Form(""),
    source: str = Form(""),
) -> JSONResponse:
    """Save an owner name found from PUBLIC search snippets. Empty owner_name
    marks 'checked, nothing found' so the record isn't handed back forever.
    Propagates the name to the linked Contact so campaigns personalize.
    """
    company = db.get(KvkCompany, company_id)
    if not company:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    company.owner_search_attempts = (company.owner_search_attempts or 0) + 1

    name = " ".join((owner_name or "").split()).strip()
    if name:
        company.owner_name = name
        company.owner_role = (owner_role or "").strip()
        company.owner_source = (source or "")[:500]
        company.owner_status = "found"
        if instagram_url and not (company.instagram_url or "").strip():
            company.instagram_url = instagram_url.strip()
        if linkedin_url and not (company.linkedin_url or "").strip():
            company.linkedin_url = linkedin_url.strip()
        # Propagate to the unified Contact (so the inbox + campaigns greet by name).
        contact = db.scalar(select(Contact).where(Contact.kvk_company_id == company.id))
        if contact and not (contact.contact_person or "").strip():
            contact.contact_person = name
    elif company.owner_search_attempts >= 2:
        company.owner_status = "none"

    db.commit()
    return JSONResponse({
        "ok": True, "id": company.id, "owner_name": company.owner_name,
        "owner_role": company.owner_role, "owner_status": company.owner_status,
    })


# (Old kvk_export_csv route moved up before /kvk/{company_id} —
# leaving it duplicated here was the source of the int_parsing error.)


# -- Klaviyo push --
@app.post("/kvk/push-klaviyo")
async def kvk_push_klaviyo(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    form = await request.form()
    ids_raw = str(form.get("company_ids", ""))
    tier_filter = str(form.get("tier", ""))

    q = select(KvkCompany).where(
        KvkCompany.email_public != "",
        KvkCompany.already_client_flag.is_(False),
    )
    if ids_raw:
        ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        q = q.where(KvkCompany.id.in_(ids))
    elif tier_filter:
        q = q.where(KvkCompany.bike_shop_tier == tier_filter)

    companies = db.scalars(q).all()
    if not companies:
        return RedirectResponse("/kvk?flash=Geen+bedrijven+met+e-mail+gevonden", status_code=303)

    try:
        success, failed, errors = push_companies_to_klaviyo(companies)
        msg = f"{success}+profielen+naar+Klaviyo+gestuurd"
        if failed:
            msg += f"%2C+{failed}+mislukt"
    except Exception as exc:
        msg = f"Klaviyo+fout%3A+{str(exc)[:80]}"

    return RedirectResponse(f"/kvk?flash={msg}", status_code=303)


# -- Klaviyo connection test --
@app.get("/api/klaviyo/test")
def klaviyo_test(_: str = Depends(require_admin)) -> JSONResponse:
    return JSONResponse(test_klaviyo_connection())


# ===========================================================================
# Email engine — campaigns, templates, Gmail OAuth, open/click tracking
# ===========================================================================

# 1x1 transparent GIF for open tracking.
_TRACKING_PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01"
    b"\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

EMAIL_CATEGORIES = ["cold", "warm", "followup", "vip", "custom"]


def _kick_campaign_send(campaign_id: int) -> None:
    """Run one send batch in a background thread for instant start."""
    def _run() -> None:
        session = SessionLocal()
        try:
            campaign = session.get(EmailCampaign, campaign_id)
            if campaign and campaign.status == "sending":
                send_campaign_batch(session, campaign)
        except Exception as exc:  # noqa: BLE001
            print(f"[email] kick send error: {exc}")
        finally:
            session.close()

    Thread(target=_run, daemon=True, name=f"email-kick-{campaign_id}").start()


def _resolve_audience_ids(
    db: Session,
    audience_type: str,
    *,
    ids_csv: str = "",
    tier: str = "",
    sector: str = "",
    country: str = "",
    crawl_job_id: int = 0,
    limit: int = 500,
) -> dict[str, list[int]]:
    """Return {kvk_ids|lead_ids|customer_ids|prospect_ids: [...]} for a campaign.

    Only includes records with an email. KVK and crawled prospects exclude
    existing clients. Explicit ids_csv takes precedence over filters.
    """
    explicit = [int(i) for i in ids_csv.split(",") if i.strip().isdigit()] if ids_csv else []
    limit = max(1, min(5000, limit))

    if audience_type == "kvk":
        q = select(KvkCompany.id).where(
            KvkCompany.email_public != "",
            KvkCompany.already_client_flag.is_(False),
        )
        if explicit:
            q = q.where(KvkCompany.id.in_(explicit))
        if tier:
            q = q.where(KvkCompany.bike_shop_tier == tier)
        if country:
            q = q.where(func.upper(KvkCompany.country_code) == country.upper())
        ids = [r for (r,) in db.execute(q.limit(limit)).all()]
        return {"kvk_ids": ids}

    if audience_type == "lead":
        q = select(FacebookLead.id).where(FacebookLead.email != "")
        if explicit:
            q = q.where(FacebookLead.id.in_(explicit))
        if sector:
            q = q.where(FacebookLead.main_sector == sector)
        if country:
            q = q.where(func.upper(FacebookLead.country) == country.upper())
        ids = [r for (r,) in db.execute(q.limit(limit)).all()]
        return {"lead_ids": ids}

    if audience_type == "customer":
        q = select(Customer.id).where(Customer.customer_email_primary != "")
        if explicit:
            q = q.where(Customer.id.in_(explicit))
        if sector:
            q = q.where(func.lower(Customer.main_sector) == sector.lower())
        if country:
            q = q.where(func.upper(Customer.country_code) == country.upper())
        ids = [r for (r,) in db.execute(q.limit(limit)).all()]
        return {"customer_ids": ids}

    if audience_type == "prospect":
        # Crawled directory prospects. Excludes rows already matched to an
        # existing customer; suppression is re-checked in build_recipients.
        q = select(Prospect.id).where(
            Prospect.email != "",
            Prospect.match_status != MatchStatus.existing_customer,
        )
        if explicit:
            q = q.where(Prospect.id.in_(explicit))
        if crawl_job_id:
            q = q.where(Prospect.crawl_job_id == crawl_job_id)
        if sector:
            q = q.where(Prospect.main_sector == sector)
        if country:
            q = q.where(func.upper(Prospect.country_code) == country.upper())
        ids = [r for (r,) in db.execute(q.limit(limit)).all()]
        return {"prospect_ids": ids}

    return {}


@app.get("/emails", response_class=HTMLResponse)
def emails_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    flash: str = "",
) -> HTMLResponse:
    gmail = gmail_sender.connection_status(db)
    campaigns = db.scalars(
        select(EmailCampaign).order_by(EmailCampaign.created_at.desc()).limit(100)
    ).all()
    template_rows = db.scalars(
        select(EmailTemplate)
        .where(EmailTemplate.is_active.is_(True))
        .order_by(EmailTemplate.category, EmailTemplate.name)
    ).all()
    # Aggregate metrics for the dashboard cards (Klaviyo/Instantly style).
    agg = db.execute(select(
        func.coalesce(func.sum(EmailCampaign.sent_count), 0),
        func.coalesce(func.sum(EmailCampaign.open_count), 0),
        func.coalesce(func.sum(EmailCampaign.click_count), 0),
        func.coalesce(func.sum(EmailCampaign.unsubscribe_count), 0),
    )).one()
    total_sent, total_open, total_click, total_unsub = (int(x) for x in agg)
    metrics = {
        "total_sent": total_sent,
        "open_rate": round(100 * total_open / total_sent, 1) if total_sent else 0.0,
        "click_rate": round(100 * total_click / total_sent, 1) if total_sent else 0.0,
        "unsub_rate": round(100 * total_unsub / total_sent, 1) if total_sent else 0.0,
        "active": sum(1 for c in campaigns if c.status in ("sending", "scheduled")),
        "total_campaigns": len(campaigns),
    }
    return templates.TemplateResponse(
        request,
        "emails.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "gmail": gmail,
            "campaigns": campaigns,
            "templates_list": template_rows,
            "sent_today": sent_today(db),
            "daily_limit": settings.gmail_daily_limit,
            "metrics": metrics,
            "flash": flash,
        },
    )


# ── Gmail OAuth ─────────────────────────────────────────────────────────────


@app.get("/emails/gmail/connect")
def gmail_connect(_: str = Depends(require_admin)) -> RedirectResponse:
    state = secrets.token_urlsafe(16)
    try:
        url = gmail_sender.build_authorization_url(state)
    except gmail_sender.GmailConfigError as exc:
        return RedirectResponse(f"/emails?flash={quote_plus(str(exc))}", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/emails/gmail/callback")
def gmail_callback(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    code: str = "",
    state: str = "",
    error: str = "",
) -> RedirectResponse:
    if error or not code:
        return RedirectResponse(
            f"/emails?flash={quote_plus('Gmail authorization failed: ' + (error or 'no code'))}",
            status_code=303,
        )
    try:
        account = gmail_sender.exchange_code(db, code)
        msg = f"Gmail connected as {account.account_email}"
    except Exception as exc:  # noqa: BLE001
        msg = f"Gmail connect error: {str(exc)[:160]}"
    return RedirectResponse(f"/emails?flash={quote_plus(msg)}", status_code=303)


@app.post("/emails/gmail/disconnect")
def gmail_disconnect(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    gmail_sender.disconnect(db)
    return RedirectResponse(f"/emails?flash={quote_plus('Gmail disconnected')}", status_code=303)


# ── Templates ───────────────────────────────────────────────────────────────


@app.get("/emails/templates", response_class=HTMLResponse)
def email_templates_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    edit: int = 0,
    flash: str = "",
) -> HTMLResponse:
    rows = db.scalars(
        select(EmailTemplate).order_by(EmailTemplate.category, EmailTemplate.name)
    ).all()
    editing = db.get(EmailTemplate, edit) if edit else None
    return templates.TemplateResponse(
        request,
        "email_templates.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "templates_list": rows,
            "editing": editing,
            "categories": EMAIL_CATEGORIES,
            "flash": flash,
        },
    )


@app.post("/emails/templates/save")
def email_template_save(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    template_id: int = Form(0),
    name: str = Form(...),
    category: str = Form("custom"),
    description: str = Form(""),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
) -> RedirectResponse:
    tpl = db.get(EmailTemplate, template_id) if template_id else None
    if tpl is None:
        tpl = EmailTemplate(is_starter=False)
        db.add(tpl)
    tpl.name = name.strip()
    tpl.category = category if category in EMAIL_CATEGORIES else "custom"
    tpl.description = description
    tpl.subject = subject
    tpl.body_html = body_html
    tpl.body_text = body_text
    db.commit()
    return RedirectResponse(f"/emails/templates?flash={quote_plus('Template saved')}", status_code=303)


@app.post("/emails/templates/{tpl_id}/delete")
def email_template_delete(
    tpl_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    tpl = db.get(EmailTemplate, tpl_id)
    if tpl and not tpl.is_starter:
        db.delete(tpl)
        db.commit()
        msg = "Template deleted"
    elif tpl:
        tpl.is_active = False
        db.commit()
        msg = "Starter template hidden"
    else:
        msg = "Template not found"
    return RedirectResponse(f"/emails/templates?flash={quote_plus(msg)}", status_code=303)


@app.get("/emails/templates/{tpl_id}/preview", response_class=HTMLResponse)
def email_template_preview(
    tpl_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> HTMLResponse:
    """Render a template with realistic sample data so it can be reviewed /
    shown to stakeholders as a finished email (no raw {{merge}} tokens)."""
    tpl = db.get(EmailTemplate, tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    sample = {
        "company_name": "Voorbeeld Fietsen Amsterdam",
        "city": "Amsterdam", "country": "NL", "website": "voorbeeldfietsen.nl",
        # Personalization slots — filled so stakeholders see the layered version.
        "first_line": "Mooie winkel — en jullie Gazelle-collectie ziet er sterk uit.",
        "angle_block": "Veel van onze klanten zetten de labels juist op hun A-merk fietsen.",
        "cta_block": "Zal ik vrijblijvend een gratis ontwerp met jullie logo maken?",
    }
    import json as _json
    preview_campaign = EmailCampaign(
        subject=tpl.subject, body_html=tpl.body_html, body_text=tpl.body_text,
        sender_name=settings.gmail_sender_name, reply_to=settings.reply_to_email,
    )
    preview_recipient = EmailCampaignRecipient(
        company_name=sample["company_name"], contact_name="",
        merge_data=_json.dumps(sample), tracking_token="preview",
    )
    subject, html_body, text_body = render_for_recipient(preview_campaign, preview_recipient)
    return templates.TemplateResponse("email_template_preview.html", {
        "request": request, "tpl": tpl, "subject": subject,
        "html_body": html_body, "text_body": text_body, "sample": sample,
    })


# ── Campaign builder ────────────────────────────────────────────────────────


@app.get("/emails/campaigns/new", response_class=HTMLResponse)
def campaign_new_form(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    audience: str = "kvk",
    ids: str = "",
    template_id: int = 0,
    crawl_job_id: int = 0,
) -> HTMLResponse:
    template_rows = db.scalars(
        select(EmailTemplate)
        .where(EmailTemplate.is_active.is_(True))
        .order_by(EmailTemplate.category, EmailTemplate.name)
    ).all()
    gmail = gmail_sender.connection_status(db)
    sectors = [s for (s,) in db.execute(
        select(FacebookLead.main_sector)
        .where(FacebookLead.main_sector != "")
        .group_by(FacebookLead.main_sector)
        .order_by(FacebookLead.main_sector)
    ).all()]
    crawl_jobs = db.scalars(select(CrawlJob).order_by(CrawlJob.id.desc())).all()
    return templates.TemplateResponse(
        request,
        "email_campaign_new.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "templates_list": template_rows,
            "gmail": gmail,
            "audience": audience,
            "preset_ids": ids,
            "preset_template_id": template_id,
            "tiers": KVK_TIER_FILTERS,
            "sectors": sectors,
            "crawl_jobs": crawl_jobs,
            "preset_crawl_job_id": crawl_job_id,
            "default_sender_alias": settings.gmail_send_as,
            "default_sender_name": settings.gmail_sender_name,
            "default_reply_to": settings.reply_to_email,
        },
    )


@app.post("/emails/campaigns/create")
def campaign_create(
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
    name: str = Form(...),
    template_id: int = Form(...),
    audience_type: str = Form("kvk"),
    lead_temperature: str = Form("cold"),
    sender_alias: str = Form(""),
    sender_name: str = Form(""),
    reply_to: str = Form(""),
    ids_csv: str = Form(""),
    tier: str = Form(""),
    sector: str = Form(""),
    country: str = Form(""),
    crawl_job_id: int = Form(0),
    limit: int = Form(500),
) -> RedirectResponse:
    tpl = db.get(EmailTemplate, template_id)
    if tpl is None:
        return RedirectResponse(f"/emails?flash={quote_plus('Pick a template first')}", status_code=303)

    campaign = EmailCampaign(
        name=name.strip() or "Untitled campaign",
        template_id=tpl.id,
        subject=tpl.subject,
        body_html=tpl.body_html,
        body_text=tpl.body_text,
        audience_type=audience_type,
        lead_temperature=lead_temperature,
        status="draft",
        # DESIGN_V2 safety default: a new campaign starts in dry-run (render-only,
        # cannot send real mail) until the operator turns it off. Override with
        # CAMPAIGN_DRY_RUN_DEFAULT=false to restore the old behaviour.
        dry_run=settings.campaign_dry_run_default,
        sender_alias=sender_alias.strip() or settings.gmail_send_as,
        sender_name=sender_name.strip() or settings.gmail_sender_name,
        reply_to=reply_to.strip() or settings.reply_to_email,
        created_by=user,
    )
    db.add(campaign)
    db.commit()

    audience = _resolve_audience_ids(
        db, audience_type, ids_csv=ids_csv, tier=tier, sector=sector, country=country,
        crawl_job_id=crawl_job_id, limit=limit,
    )
    stats = build_recipients(db, campaign, **audience)
    msg = (
        f"Campaign '{campaign.name}' created with {stats['total']} recipients "
        f"(skipped {stats['skipped_no_email']} no-email, "
        f"{stats['skipped_suppressed']} suppressed, {stats['skipped_duplicate']} duplicate)."
    )
    return RedirectResponse(f"/emails/campaigns/{campaign.id}?flash={quote_plus(msg)}", status_code=303)


@app.get("/emails/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    flash: str = "",
) -> HTMLResponse:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recipients = db.scalars(
        select(EmailCampaignRecipient)
        .where(EmailCampaignRecipient.campaign_id == campaign_id)
        .order_by(EmailCampaignRecipient.id.asc())
        .limit(500)
    ).all()
    status_counts = dict(db.execute(
        select(EmailCampaignRecipient.status, func.count(EmailCampaignRecipient.id))
        .where(EmailCampaignRecipient.campaign_id == campaign_id)
        .group_by(EmailCampaignRecipient.status)
    ).all())
    gmail = gmail_sender.connection_status(db)
    return templates.TemplateResponse(
        request,
        "email_campaign_detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "campaign": campaign,
            "recipients": recipients,
            "status_counts": status_counts,
            "gmail": gmail,
            "flash": flash,
        },
    )


@app.post("/emails/campaigns/{campaign_id}/send")
def campaign_send(
    campaign_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.total_recipients == 0:
        return RedirectResponse(
            f"/emails/campaigns/{campaign_id}?flash={quote_plus('No recipients to send to')}",
            status_code=303,
        )
    # DESIGN_V2 dry-run keystone: a dry-run campaign renders previews but never
    # sends. Handled BEFORE the sender check so previews work with no provider
    # connected at all. send_campaign_batch short-circuits to preview-only.
    if getattr(campaign, "dry_run", False):
        result = send_campaign_batch(db, campaign)
        msg = (
            f"Dry-run: previewed {result.get('previewed', 0)} email(s) — NOTHING was sent. "
            "Turn off Dry-run to send for real."
        )
        return RedirectResponse(f"/emails/campaigns/{campaign_id}?flash={quote_plus(msg)}", status_code=303)
    # Only the Gmail-API transport needs a connected Gmail account. When a
    # provider (Resend/Brevo/SMTP) is configured, sending goes through the
    # provider abstraction and no Gmail connection is required.
    _provider = (getattr(settings, "mail_provider", "console") or "console").lower()
    _use_provider = _provider in {"resend", "brevo", "smtp", "gmail_smtp"}
    if not _use_provider and gmail_sender.get_active_account(db) is None:
        return RedirectResponse(
            f"/emails/campaigns/{campaign_id}?flash={quote_plus('Connect Gmail (or set MAIL_PROVIDER) before sending')}",
            status_code=303,
        )
    campaign.status = "sending"
    campaign.started_at = campaign.started_at or datetime.utcnow()
    db.commit()
    _kick_campaign_send(campaign.id)
    msg = "Sending started — emails go out in the background (throttled). Refresh for live stats."
    return RedirectResponse(f"/emails/campaigns/{campaign_id}?flash={quote_plus(msg)}", status_code=303)


@app.post("/emails/campaigns/{campaign_id}/dry-run")
def campaign_toggle_dry_run(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
    enabled: str = Form(""),
) -> RedirectResponse:
    """Turn a campaign's dry-run flag on/off (DESIGN_V2 keystone control).

    Cannot be toggled while a campaign is actively sending — pause it first.
    """
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status == "sending":
        return RedirectResponse(
            f"/emails/campaigns/{campaign_id}?flash={quote_plus('Pause the campaign before changing dry-run')}",
            status_code=303,
        )
    new_value = enabled.strip().lower() in {"1", "true", "yes", "on"}
    campaign.dry_run = new_value
    db.commit()
    try:
        log_audit(
            db, actor=user, action="campaign.dry_run",
            target_type="email_campaign", target_id=str(campaign.id),
            detail=f"dry_run set to {new_value}",
        )
    except Exception:  # noqa: BLE001 - audit must never block the action
        pass
    msg = "Dry-run ON — sends are previews only." if new_value else "Dry-run OFF — this campaign can now send real mail."
    return RedirectResponse(f"/emails/campaigns/{campaign_id}?flash={quote_plus(msg)}", status_code=303)


# ===========================================================================
# DESIGN_V2 Phase 3B — Cold email SEQUENCE engine UI (gated; off by default)
# ===========================================================================

@app.get("/sequences", response_class=HTMLResponse)
def sequences_page(
    request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin), flash: str = ""
) -> HTMLResponse:
    seq = sequences_module.get_default_sequence(db)
    steps = []
    if seq is not None:
        steps = db.scalars(
            select(SequenceStep).where(SequenceStep.sequence_id == seq.id).order_by(SequenceStep.step_number)
        ).all()
    status_counts = dict(db.execute(
        select(SequenceEnrollment.sequence_status, func.count(SequenceEnrollment.id))
        .group_by(SequenceEnrollment.sequence_status)
    ).all())
    enrollments = db.scalars(
        select(SequenceEnrollment).order_by(SequenceEnrollment.updated_at.desc()).limit(100)
    ).all()
    return templates.TemplateResponse("sequences.html", {
        "request": request, "sequence": seq, "steps": steps,
        "status_counts": status_counts, "enrollments": enrollments,
        "engine_enabled": sequences_module.engine_enabled(),
        "dry_run_default": bool(getattr(settings, "campaign_dry_run_default", True)),
        "flash": flash,
    })


@app.post("/sequences/enroll-batch")
def sequences_enroll_batch(
    db: Session = Depends(get_db), user: str = Depends(require_admin), limit: int = Form(25)
) -> RedirectResponse:
    """Enroll up to `limit` eligible KVK leads (not client, has email, not
    suppressed, not already enrolled) into the default sequence. Enrollment is
    just data — nothing sends until the engine + dry-run are configured."""
    seq = sequences_module.get_default_sequence(db)
    if seq is None:
        return RedirectResponse(f"/sequences?flash={quote_plus('No default sequence seeded yet')}", status_code=303)
    limit = max(1, min(500, limit))
    already = set(db.scalars(
        select(SequenceEnrollment.subject_id).where(
            SequenceEnrollment.sequence_id == seq.id, SequenceEnrollment.subject_type == "kvk"
        )
    ).all())
    rows = db.scalars(
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(KvkCompany.email_public != "")
        .order_by(KvkCompany.id.asc())
        .limit(limit * 3)
    ).all()
    enrolled = 0
    for co in rows:
        if enrolled >= limit:
            break
        if co.id in already:
            continue
        merge = {"company_name": co.company_name or "", "city": co.primary_city or "",
                 "country": co.country_code or "NL", "website": co.website or ""}
        e = sequences_module.enroll(
            db, sequence=seq, subject_type="kvk", subject_id=co.id,
            to_email=co.email_public or "", company_name=co.company_name or "",
            merge_context=merge, created_by=user,
        )
        if e is not None and e.current_step == 0 and e.id:
            enrolled += 1
    db.commit()
    msg = f"Enrolled {enrolled} lead(s). They will send on the weekly cadence once the engine is enabled."
    return RedirectResponse(f"/sequences?flash={quote_plus(msg)}", status_code=303)


@app.post("/sequences/enrollments/{enrollment_id}/{action}")
def sequences_enrollment_action(
    enrollment_id: int, action: str, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    e = db.get(SequenceEnrollment, enrollment_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    if action == "pause" and e.sequence_status == "active":
        e.sequence_status = "paused"
    elif action == "resume" and e.sequence_status == "paused":
        e.sequence_status = "active"
    elif action == "stop":
        sequences_module.stop_enrollment(db, e, "manual_stop")
    db.commit()
    return RedirectResponse(f"/sequences?flash={quote_plus('Updated enrollment ' + str(enrollment_id))}", status_code=303)


@app.get("/sequences/enrollments/{enrollment_id}", response_class=HTMLResponse)
def sequence_enrollment_detail(
    enrollment_id: int, request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> HTMLResponse:
    e = db.get(SequenceEnrollment, enrollment_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    emails = db.scalars(
        select(SequenceEmail).where(SequenceEmail.enrollment_id == enrollment_id).order_by(SequenceEmail.step_number)
    ).all()
    return templates.TemplateResponse("sequence_enrollment.html", {
        "request": request, "enrollment": e, "emails": emails,
    })


@app.post("/emails/campaigns/{campaign_id}/pause")
def campaign_pause(
    campaign_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign and campaign.status == "sending":
        campaign.status = "paused"
        db.commit()
    return RedirectResponse(f"/emails/campaigns/{campaign_id}?flash={quote_plus('Campaign paused')}", status_code=303)


@app.post("/emails/campaigns/{campaign_id}/schedule")
def campaign_schedule(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    scheduled_at: str = Form(""),
) -> RedirectResponse:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    try:
        # Expecting HTML datetime-local format: YYYY-MM-DDTHH:MM
        dt = datetime.fromisoformat(scheduled_at)
        campaign.scheduled_at = dt
        campaign.status = "scheduled"
        db.commit()
        msg = f"Scheduled for {dt.isoformat(sep=' ', timespec='minutes')}"
    except Exception:
        msg = "Invalid date/time"
    return RedirectResponse(f"/emails/campaigns/{campaign_id}?flash={quote_plus(msg)}", status_code=303)


@app.post("/emails/campaigns/{campaign_id}/test")
def campaign_test_send(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    test_email: str = Form(...),
) -> RedirectResponse:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    # Build an in-memory recipient (not persisted) to reuse the renderer.
    sample = EmailCampaignRecipient(
        campaign_id=campaign.id,
        to_email=normalize_email(test_email),
        company_name="Sample Bike Shop",
        contact_name="there",
        merge_data='{"company_name": "Sample Bike Shop", "city": "Amsterdam", "country": "NL", "website": "example.com"}',
        tracking_token=f"test-{secrets.token_urlsafe(10)}",
    )
    subject, html_body, text_body = render_for_recipient(campaign, sample)
    _provider = (getattr(settings, "mail_provider", "console") or "console").lower()
    if _provider in {"resend", "brevo", "smtp", "gmail_smtp"}:
        result = email_providers.send(
            to_email=sample.to_email,
            subject=f"[TEST] {subject}",
            html=html_body,
            text=text_body,
            from_name=campaign.sender_name,
            from_alias=campaign.sender_alias,
            reply_to=campaign.reply_to,
            session=db,
        )
    else:
        result = gmail_sender.send_message(
            db,
            to_email=sample.to_email,
            subject=f"[TEST] {subject}",
            body_html=html_body,
            body_text=text_body,
            from_alias=campaign.sender_alias,
            from_name=campaign.sender_name,
            reply_to=campaign.reply_to,
        )
    msg = f"Test sent to {sample.to_email}" if result.ok else f"Test failed: {result.error[:160]}"
    return RedirectResponse(f"/emails/campaigns/{campaign_id}?flash={quote_plus(msg)}", status_code=303)


@app.post("/emails/campaigns/{campaign_id}/delete")
def campaign_delete(
    campaign_id: int, request: Request, db: Session = Depends(get_db),
    _: str = Depends(require_admin), __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign:
        log_audit(db, actor=auth_module.actor_label(request, db), action="campaign.delete",
                  target_type="campaign", target_id=campaign_id, detail=campaign.name, commit=False)
        db.delete(campaign)
        db.commit()
    return RedirectResponse(f"/emails?flash={quote_plus('Campaign deleted')}", status_code=303)


# ── Public tracking endpoints (NO auth — recipients hit these) ──────────────


@app.get("/e/o/{token}.gif")
def track_open(token: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    try:
        record_open(
            db, token,
            user_agent=request.headers.get("user-agent", ""),
            ip=request.client.host if request.client else "",
        )
    except Exception:
        pass
    return StreamingResponse(
        io.BytesIO(_TRACKING_PIXEL),
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, private", "Pragma": "no-cache"},
    )


@app.get("/e/c/{token}")
def track_click(token: str, request: Request, u: str = "", db: Session = Depends(get_db)) -> RedirectResponse:
    target = u or settings.app_base_url
    if not (target.startswith("http://") or target.startswith("https://")):
        target = settings.app_base_url
    try:
        record_click(
            db, token, target,
            user_agent=request.headers.get("user-agent", ""),
            ip=request.client.host if request.client else "",
        )
    except Exception:
        pass
    return RedirectResponse(target, status_code=302)


@app.get("/e/u/{token}", response_class=HTMLResponse)
def track_unsubscribe(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    recipient = record_unsubscribe(db, token)
    email = recipient.to_email if recipient else "your address"
    return HTMLResponse(
        f"""<!doctype html><html><body style="font-family:Arial,sans-serif;max-width:520px;margin:60px auto;text-align:center;color:#1f2933;">
        <h2>You're unsubscribed</h2>
        <p>{email} has been removed from Schild Inc outreach. You won't receive further emails from us.</p>
        <p style="color:#8a8f98;font-size:13px;">If this was a mistake, reply to any previous email and we'll add you back.</p>
        </body></html>"""
    )


@app.post("/e/u/{token}")
def track_unsubscribe_oneclick(token: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    """RFC 8058 List-Unsubscribe-Post one-click endpoint."""
    record_unsubscribe(db, token)
    return PlainTextResponse("unsubscribed", status_code=200)


# ===========================================================================
# In-house CRM — Phase 1: Unified Contact Hub
# ===========================================================================


def _contacts_query(search: str, sector: str, country: str, source: str, has_email: str, customers_only: str, cold: str = ""):
    q = select(Contact)
    if cold == "1":
        # Cold-outreach pool: KVK + Google-Maps prospects only — never existing
        # customers, never form-submitted leads.
        q = q.where(
            (Contact.source_summary.like("%kvk%") | Contact.source_summary.like("%prospect%")),
            Contact.is_customer.is_(False),
            ~Contact.source_summary.like("%lead%"),
            ~Contact.source_summary.like("%customer%"),
        )
    if search:
        like = f"%{search.lower()}%"
        q = q.where(
            or_(
                func.lower(Contact.display_name).like(like),
                func.lower(Contact.company_name).like(like),
                func.lower(Contact.primary_email).like(like),
                func.lower(Contact.primary_phone).like(like),
                func.lower(Contact.contact_person).like(like),
            )
        )
    if sector:
        q = q.where(func.lower(Contact.sector) == sector.lower())
    if country:
        q = q.where(func.upper(Contact.country_code) == country.upper())
    if source:
        q = q.where(Contact.source_summary.like(f"%{source}%"))
    if has_email == "1":
        q = q.where(Contact.primary_email != "")
    elif has_email == "0":
        q = q.where(Contact.primary_email == "")
    if customers_only == "1":
        q = q.where(Contact.is_customer.is_(True))
    return q


@app.get("/contacts/export.csv")
def contacts_export(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    search: str = "",
    sector: str = "",
    country: str = "",
    source: str = "",
    has_email: str = "",
    customers_only: str = "",
    cold: str = "",
) -> StreamingResponse:
    rows = db.scalars(
        _contacts_query(search, sector, country, source, has_email, customers_only, cold)
        .order_by(Contact.lifetime_value.desc(), Contact.id.asc())
    ).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "display_name", "company_name", "contact_person", "email",
                     "phone", "city", "country", "sector", "tier", "is_customer",
                     "lifetime_value", "sources", "do_not_contact"])
    for c in rows:
        writer.writerow([c.id, c.display_name, c.company_name, c.contact_person,
                         c.primary_email, c.primary_phone, c.city, c.country_code,
                         c.sector, c.tier, c.is_customer, c.lifetime_value,
                         c.source_summary, c.do_not_contact])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


@app.post("/contacts/backfill")
def contacts_backfill(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    """Build/refresh contacts from customers + KVK + leads + prospects (background)."""
    def _run() -> None:
        session = SessionLocal()
        try:
            stats = contacts_module.backfill_contacts(session)
            print(f"[contacts] backfill done: {stats}")
        except Exception as exc:  # noqa: BLE001
            print(f"[contacts] backfill error: {exc}")
        finally:
            session.close()

    Thread(target=_run, daemon=True, name="contacts-backfill").start()
    msg = "Contact backfill started in the background. Refresh in a minute to see contacts."
    return RedirectResponse(f"/contacts?flash={quote_plus(msg)}", status_code=303)


@app.get("/contacts", response_class=HTMLResponse)
def contacts_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    sector: str = "",
    country: str = "",
    source: str = "",
    has_email: str = "",
    customers_only: str = "",
    cold: str = "",
    flash: str = "",
) -> HTMLResponse:
    page = max(1, page)
    per_page = max(10, min(200, per_page))
    base = _contacts_query(search, sector, country, source, has_email, customers_only, cold)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = db.scalars(
        base.order_by(Contact.last_activity_at.desc().nullslast(), Contact.lifetime_value.desc(), Contact.id.asc())
        .offset((page - 1) * per_page).limit(per_page)
    ).all()
    grand_total = db.scalar(select(func.count(Contact.id))) or 0
    customer_count = db.scalar(select(func.count(Contact.id)).where(Contact.is_customer.is_(True))) or 0
    sectors = [s for (s,) in db.execute(
        select(Contact.sector).where(Contact.sector != "").group_by(Contact.sector).order_by(Contact.sector)
    ).all()]
    return templates.TemplateResponse(
        request,
        "contacts.html",
        {
            "request": request, "app_name": settings.app_name,
            "contacts": rows, "page": page, "per_page": per_page, "total": total,
            "total_pages": total_pages, "grand_total": grand_total,
            "customer_count": customer_count, "sectors": sectors,
            "search": search, "sector": sector, "country": country, "source": source,
            "has_email": has_email, "customers_only": customers_only, "cold": cold, "flash": flash,
        },
    )


@app.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail(
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    flash: str = "",
) -> HTMLResponse:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    timeline = contacts_module.get_timeline(db, contact)
    wa_status = whatsapp_module.status()
    wa_templates = db.scalars(
        select(WhatsappTemplate).where(WhatsappTemplate.is_active.is_(True)).order_by(WhatsappTemplate.name)
    ).all() if wa_status["configured"] else []

    # LinkedIn manual-outreach helper: profile URL + a ready-to-paste opener.
    linkedin_url = next(
        (c.value for c in contact.channels if c.channel_type == "linkedin" and c.value), ""
    )
    first_name = (contact.contact_person or "").split()[0] if contact.contact_person else "there"
    company = contact.company_name or contact.display_name
    linkedin_opener = (
        f"Hi {first_name}, I came across {company} and wanted to reach out. "
        f"Schild Inc makes premium metal labels and branded accessories for bike shops "
        f"— would a couple of free design samples be useful? No pressure either way."
    )
    # Cold-outreach eligibility: KVK/Maps prospects only, never customers or form leads.
    src = contact.source_summary or ""
    is_cold_eligible = (("kvk" in src or "prospect" in src)
                        and not contact.is_customer
                        and "lead" not in src and "customer" not in src)

    return templates.TemplateResponse(
        request,
        "contact_detail.html",
        {
            "request": request, "app_name": settings.app_name,
            "contact": contact, "channels": contact.channels, "timeline": timeline,
            "wa_status": wa_status, "wa_templates": wa_templates,
            "linkedin_url": linkedin_url, "linkedin_opener": linkedin_opener,
            "is_cold_eligible": is_cold_eligible,
            "flash": flash,
        },
    )


@app.post("/contacts/{contact_id}/note")
def contact_add_note(
    contact_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
    note: str = Form(...),
) -> RedirectResponse:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    contacts_module.log_activity(
        db, contact_id, "note", channel="system", title=f"Note by {user}", body=note,
    )
    return RedirectResponse(f"/contacts/{contact_id}?flash={quote_plus('Note added')}", status_code=303)


@app.post("/contacts/{contact_id}/linkedin-log")
def contact_linkedin_log(
    contact_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
    message: str = Form(""),
) -> RedirectResponse:
    """Log a manual LinkedIn outreach to the timeline (you send it by hand on
    LinkedIn — this is compliant tracking, no automation)."""
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    contacts_module.log_activity(
        db, contact_id, "linkedin_out", channel="linkedin", direction="out",
        title=f"LinkedIn message sent (by {user})", body=message[:1000],
    )
    return RedirectResponse(f"/contacts/{contact_id}?flash={quote_plus('Logged LinkedIn outreach')}", status_code=303)


# ===========================================================================
# In-house CRM — Phase 2: Shared Inbox (Trengo-style) + two-way email
# ===========================================================================


def _current_agent(db: Session, agent_id: int | None = None) -> Agent | None:
    if agent_id:
        a = db.get(Agent, agent_id)
        if a:
            return a
    return db.scalar(select(Agent).where(Agent.is_active.is_(True)).order_by(Agent.id.asc()))


def _text_to_html(text: str) -> str:
    return html_lib.escape(text or "").replace("\n", "<br>")


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    view: str = "new",        # new|assigned|closed|spam|mine|favorites|all
    channel: str = "",
    label: str = "",
    team: str = "",
    search: str = "",
    conv: int = 0,
    flash: str = "",
) -> HTMLResponse:
    me = auth_module.current_agent(request, db)
    mine_id = me.id if me else None
    team_ids = inbox_module.team_agent_ids(db, team) if team else None
    if view == "mentions":
        conv_ids = inbox_module.mention_conversation_ids(db, mine_id) if mine_id else []
        seen, ordered = set(), []
        for ci in conv_ids:
            if ci not in seen:
                seen.add(ci)
                c_obj = db.get(Conversation, ci)
                if c_obj:
                    ordered.append(c_obj)
        conversations = ordered
        if mine_id:
            inbox_module.mark_mentions_read(db, mine_id)
    else:
        conversations = inbox_module.list_conversations(
            db, view=view, channel=channel, label=label,
            team_agent_ids=team_ids, mine_id=mine_id, search=search,
        )
    selected = db.get(Conversation, conv) if conv else (conversations[0] if conversations else None)
    messages = []
    contact = None
    attachments_by_msg: dict[int, list] = {}
    if selected is not None:
        inbox_module.mark_read(db, selected)
        messages = db.scalars(
            select(Message).where(Message.conversation_id == selected.id).order_by(Message.occurred_at.asc(), Message.id.asc())
        ).all()
        contact = selected.contact
        msg_ids = [m.id for m in messages]
        if msg_ids:
            for att in db.scalars(select(MessageAttachment).where(MessageAttachment.message_id.in_(msg_ids))).all():
                attachments_by_msg.setdefault(att.message_id, []).append(att)
    agents = db.scalars(select(Agent).where(Agent.is_active.is_(True)).order_by(Agent.name)).all()
    canned = db.scalars(select(CannedReply).where(CannedReply.is_active.is_(True)).order_by(CannedReply.title)).all()
    gmail = gmail_sender.connection_status(db)
    wa_templates = db.scalars(select(WhatsappTemplate).where(WhatsappTemplate.is_active.is_(True)).order_by(WhatsappTemplate.name)).all()
    wa_window_open = (
        whatsapp_module.within_service_window(db, selected)
        if (selected is not None and selected.channel == "whatsapp") else False
    )
    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "request": request, "app_name": settings.app_name,
            "conversations": conversations, "selected": selected, "messages": messages,
            "contact": contact, "agents": agents, "canned": canned,
            "attachments_by_msg": attachments_by_msg,
            "counts": inbox_module.rail_counts(db, mine_id), "gmail": gmail,
            "mention_count": inbox_module.unread_mention_count(db, mine_id),
            "labels": inbox_module.list_labels(db), "teams": inbox_module.list_teams(db),
            "wa_status": whatsapp_module.status(), "wa_templates": wa_templates,
            "wa_window_open": wa_window_open,
            "view": view, "channel": channel, "label": label, "team": team, "search": search,
            "flash": flash,
        },
    )


@app.post("/inbox/{conv_id}/spam")
def inbox_mark_spam(
    conv_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv:
        inbox_module.set_status(db, conv, "spam")
    return RedirectResponse(f"/inbox?flash={quote_plus('Marked as spam')}", status_code=303)


@app.post("/inbox/{conv_id}/favorite")
def inbox_toggle_favorite(
    conv_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    fav = inbox_module.toggle_favorite(db, conv) if conv else False
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Added to favorites' if fav else 'Removed from favorites')}", status_code=303)


@app.get("/inbox/attachment/{att_id}")
def inbox_attachment(att_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)):
    """Stream an inbound email attachment, fetched on-demand from Gmail."""
    att = db.get(MessageAttachment, att_id)
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        import base64 as _b64
        service, _acct = gmail_sender.get_gmail_service(db)
        data = service.users().messages().attachments().get(
            userId="me", messageId=att.gmail_message_id, id=att.gmail_attachment_id
        ).execute()
        raw = _b64.urlsafe_b64decode(data.get("data", "").encode("utf-8"))
    except gmail_sender.GmailNotConnected:
        raise HTTPException(status_code=409, detail="Gmail not connected")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not fetch attachment: {str(exc)[:120]}")
    safe_name = (att.filename or "attachment").replace('"', "")
    return StreamingResponse(
        io.BytesIO(raw),
        media_type=att.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@app.post("/inbox/poll")
def inbox_poll(db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    if gmail_sender.get_active_account(db) is None:
        return RedirectResponse(f"/inbox?flash={quote_plus('Connect Gmail first (on the Email Campaigns page).')}", status_code=303)

    def _run() -> None:
        session = SessionLocal()
        try:
            stats = poll_inbound(session)
            print(f"[inbox] manual poll: {stats}")
        except Exception as exc:  # noqa: BLE001
            print(f"[inbox] manual poll error: {exc}")
        finally:
            session.close()

    Thread(target=_run, daemon=True, name="inbox-manual-poll").start()
    return RedirectResponse(f"/inbox?flash={quote_plus('Checking for new replies… refresh in a few seconds.')}", status_code=303)


@app.post("/inbox/{conv_id}/reply")
def inbox_reply(
    conv_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    body_text: str = Form(...),
    agent_id: int = Form(0),
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Prefer the logged-in agent for attribution; fall back to the dropdown.
    agent = auth_module.current_agent(request, db) or _current_agent(db, agent_id)

    # WhatsApp reply (free-form text only inside the 24h service window).
    if conv.channel == "whatsapp":
        to_phone = conv.contact_phone or (conv.contact.primary_phone if conv.contact else "")
        if not to_phone:
            return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('No WhatsApp number on this conversation.')}", status_code=303)
        if not whatsapp_module.within_service_window(db, conv):
            return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Outside the 24h window — send an approved template instead.')}", status_code=303)
        result = whatsapp_module.send_text(to_phone, body_text)
        inbox_module.add_outbound_message(
            db, conv, agent=agent, from_addr=settings.whatsapp_phone_number_id, to_addr=to_phone,
            subject="WhatsApp", body_text=body_text, body_html="",
            external_message_id=result.message_id, external_thread_id=conv.external_thread_id,
            status="sent" if result.ok else "failed", error="" if result.ok else result.error,
            channel="whatsapp",
        )
        msg = "WhatsApp sent" if result.ok else f"Send failed: {result.error[:140]}"
        return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus(msg)}", status_code=303)

    # Instagram reply (only inside the 24h window; Meta blocks cold/late DMs).
    if conv.channel == "instagram":
        igsid = conv.external_thread_id
        if not igsid:
            return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('No Instagram recipient on this conversation.')}", status_code=303)
        if not instagram_module.within_service_window(db, conv):
            return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Outside the 24h Instagram window — Meta does not allow a reply now.')}", status_code=303)
        result = instagram_module.send_text(igsid, body_text)
        inbox_module.add_outbound_message(
            db, conv, agent=agent, from_addr=settings.instagram_account_id, to_addr=igsid,
            subject="Instagram", body_text=body_text, body_html="",
            external_message_id=result.message_id, external_thread_id=igsid,
            status="sent" if result.ok else "failed", error="" if result.ok else result.error,
            channel="instagram",
        )
        msg = "Instagram reply sent" if result.ok else f"Send failed: {result.error[:140]}"
        return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus(msg)}", status_code=303)

    to_email = conv.contact_email or (conv.contact.primary_email if conv.contact else "")
    if not to_email:
        return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('No recipient email on this conversation.')}", status_code=303)

    subject = conv.subject if conv.subject.lower().startswith("re:") else f"Re: {conv.subject}"
    last_in = db.scalar(
        select(Message).where(Message.conversation_id == conv.id, Message.direction == "in")
        .order_by(Message.id.desc())
    )
    in_reply_to = last_in.external_message_id if last_in else ""

    result = gmail_sender.send_message(
        db,
        to_email=to_email,
        subject=subject,
        body_html=_text_to_html(body_text),
        body_text=body_text,
        from_alias=settings.gmail_send_as,
        from_name=settings.gmail_sender_name,
        reply_to=settings.reply_to_email,
        thread_id=conv.external_thread_id,
        in_reply_to=in_reply_to,
    )
    inbox_module.add_outbound_message(
        db, conv, agent=agent, from_addr=settings.gmail_send_as, to_addr=to_email,
        subject=subject, body_text=body_text, body_html=_text_to_html(body_text),
        external_message_id=result.message_id, external_thread_id=conv.external_thread_id,
        status="sent" if result.ok else "failed", error="" if result.ok else result.error,
    )
    msg = "Reply sent" if result.ok else f"Send failed: {result.error[:140]}"
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus(msg)}", status_code=303)


@app.post("/inbox/{conv_id}/note")
def inbox_note(
    conv_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    body: str = Form(...),
    agent_id: int = Form(0),
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    agent = auth_module.current_agent(request, db) or _current_agent(db, agent_id)
    note_msg = inbox_module.add_internal_note(db, conv, agent=agent, body=body)
    mentioned = inbox_module.create_mentions(db, conv, note_msg, body, agent)
    flash = "Internal note added" + (f" · {mentioned} teammate(s) mentioned" if mentioned else "")
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus(flash)}", status_code=303)


@app.post("/inbox/{conv_id}/assign")
def inbox_assign(
    conv_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    agent_id: int = Form(0),
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    inbox_module.assign(db, conv, agent_id or None)
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Assignment updated')}", status_code=303)


@app.post("/inbox/{conv_id}/status")
def inbox_status(
    conv_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    status: str = Form(...),
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    inbox_module.set_status(db, conv, status)
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Status: ' + status)}", status_code=303)


@app.post("/inbox/{conv_id}/labels")
def inbox_labels(
    conv_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    labels: str = Form(""),
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    inbox_module.set_labels(db, conv, labels)
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Labels updated')}", status_code=303)


# ── Inbox settings: agents + canned replies ─────────────────────────────────


@app.get("/inbox/settings", response_class=HTMLResponse)
def inbox_settings(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    flash: str = "",
) -> HTMLResponse:
    agents = db.scalars(select(Agent).order_by(Agent.id.asc())).all()
    canned = db.scalars(select(CannedReply).order_by(CannedReply.category, CannedReply.title)).all()
    wa_templates = db.scalars(select(WhatsappTemplate).order_by(WhatsappTemplate.name)).all()
    return templates.TemplateResponse(
        request,
        "inbox_settings.html",
        {
            "request": request, "app_name": settings.app_name,
            "agents": agents, "canned": canned, "flash": flash,
            "wa_status": whatsapp_module.status(), "wa_templates": wa_templates,
        },
    )


@app.post("/inbox/settings/agents")
def inbox_add_agent(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form("agent"),
    team: str = Form(""),
    password: str = Form(""),
) -> RedirectResponse:
    norm = normalize_email(email)
    existing = db.scalar(select(Agent).where(Agent.email == norm))
    if existing:
        existing.name = name.strip()
        existing.role = role if role in ("admin", "agent") else "agent"
        existing.team = team.strip()
        existing.is_active = True
        if password.strip():
            existing.password_hash = auth_module.hash_password(password.strip())
    else:
        db.add(Agent(
            name=name.strip(), email=norm,
            role=role if role in ("admin", "agent") else "agent", team=team.strip(), is_active=True,
            password_hash=auth_module.hash_password(password.strip()) if password.strip() else "",
        ))
    log_audit(db, actor=auth_module.actor_label(request, db), action="agent.save",
              target_type="agent", target_id=norm, detail=f"role={role}", commit=False)
    db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('Teammate saved')}", status_code=303)


@app.post("/inbox/settings/agents/{agent_id}/password")
def inbox_set_agent_password(
    agent_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
    password: str = Form(...),
) -> RedirectResponse:
    agent = db.get(Agent, agent_id)
    if agent and password.strip():
        agent.password_hash = auth_module.hash_password(password.strip())
        log_audit(db, actor=auth_module.actor_label(request, db), action="agent.set_password",
                  target_type="agent", target_id=agent.email, commit=False)
        db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('Password set for ' + (agent.name if agent else 'agent'))}", status_code=303)


@app.post("/inbox/settings/agents/{agent_id}/delete")
def inbox_remove_agent(
    agent_id: int, request: Request, db: Session = Depends(get_db),
    _: str = Depends(require_admin), __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    agent = db.get(Agent, agent_id)
    if agent:
        agent.is_active = False
        log_audit(db, actor=auth_module.actor_label(request, db), action="agent.deactivate",
                  target_type="agent", target_id=agent.email, commit=False)
        db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('Teammate deactivated')}", status_code=303)


@app.post("/inbox/settings/canned")
def inbox_add_canned(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    title: str = Form(...),
    category: str = Form("general"),
    body: str = Form(...),
    canned_id: int = Form(0),
) -> RedirectResponse:
    cr = db.get(CannedReply, canned_id) if canned_id else None
    if cr is None:
        cr = CannedReply(is_starter=False)
        db.add(cr)
    cr.title = title.strip()
    cr.category = category.strip() or "general"
    cr.body = body
    cr.is_active = True
    db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('Canned reply saved')}", status_code=303)


@app.post("/inbox/settings/canned/{canned_id}/delete")
def inbox_remove_canned(
    canned_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)
) -> RedirectResponse:
    cr = db.get(CannedReply, canned_id)
    if cr:
        if cr.is_starter:
            cr.is_active = False
        else:
            db.delete(cr)
        db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('Canned reply removed')}", status_code=303)


# ===========================================================================
# In-house CRM — Phase 3: WhatsApp (Meta Cloud API)
# ===========================================================================


@app.get("/webhooks/whatsapp")
def whatsapp_webhook_verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta webhook verification handshake (public, no auth)."""
    result = whatsapp_module.verify_webhook(mode, token, challenge)
    if result is None:
        raise HTTPException(status_code=403, detail="verification failed")
    return PlainTextResponse(result)


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook_receive(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    """Inbound WhatsApp messages + delivery statuses (public, signature-verified)."""
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    if settings.whatsapp_app_secret and not whatsapp_module.verify_signature(raw, signature):
        raise HTTPException(status_code=403, detail="bad signature")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return PlainTextResponse("ok")
    try:
        whatsapp_module.process_webhook(db, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[whatsapp] webhook processing error: {exc}")
    # Always 200 quickly so Meta doesn't retry/disable the webhook.
    return PlainTextResponse("ok")


@app.get("/webhooks/instagram")
def instagram_webhook_verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta webhook verification handshake for Instagram (public, no auth)."""
    result = instagram_module.verify_webhook(mode, token, challenge)
    if result is None:
        raise HTTPException(status_code=403, detail="verification failed")
    return PlainTextResponse(result)


@app.post("/webhooks/instagram")
async def instagram_webhook_receive(request: Request, db: Session = Depends(get_db)) -> PlainTextResponse:
    """Inbound Instagram DMs (public, signature-verified). Threads into /inbox."""
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    if settings.instagram_app_secret and not instagram_module.verify_signature(raw, signature):
        raise HTTPException(status_code=403, detail="bad signature")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return PlainTextResponse("ok")
    try:
        instagram_module.process_webhook(db, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[instagram] webhook processing error: {exc}")
    return PlainTextResponse("ok")


@app.post("/contacts/{contact_id}/whatsapp")
def contact_start_whatsapp(
    contact_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    template_id: int = Form(...),
    params: str = Form(""),
) -> RedirectResponse:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    phone = contact.primary_phone
    if not phone:
        ch = db.scalar(
            select(ContactChannel).where(
                ContactChannel.contact_id == contact.id,
                ContactChannel.channel_type.in_(["whatsapp", "phone"]),
            )
        )
        phone = ch.value_normalized if ch else ""
    if not phone:
        return RedirectResponse(f"/contacts/{contact_id}?flash={quote_plus('No phone/WhatsApp number on this contact.')}", status_code=303)
    tpl = db.get(WhatsappTemplate, template_id)
    if tpl is None:
        return RedirectResponse(f"/contacts/{contact_id}?flash={quote_plus('Pick an approved template to open a WhatsApp chat.')}", status_code=303)
    conv = inbox_module.get_or_create_whatsapp_conversation(
        db, contact=contact, phone=phone, external_thread_id=whatsapp_module.to_wa_number(phone),
    )
    body_params = [p.strip() for p in params.split("|") if p.strip()] or None
    result = whatsapp_module.send_template(phone, tpl.name, tpl.language, body_params)
    inbox_module.add_outbound_message(
        db, conv, agent=None, from_addr=settings.whatsapp_phone_number_id, to_addr=phone,
        subject="WhatsApp", body_text=f"[template: {tpl.name}] {tpl.body_preview}", body_html="",
        external_message_id=result.message_id, external_thread_id=conv.external_thread_id,
        status="sent" if result.ok else "failed", error="" if result.ok else result.error, channel="whatsapp",
    )
    msg = "WhatsApp started" if result.ok else f"Send failed: {result.error[:140]}"
    return RedirectResponse(f"/inbox?conv={conv.id}&flash={quote_plus(msg)}", status_code=303)


@app.post("/inbox/settings/wa-template")
def add_wa_template(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
    name: str = Form(...),
    language: str = Form("en"),
    category: str = Form(""),
    body_preview: str = Form(""),
    param_count: int = Form(0),
) -> RedirectResponse:
    existing = db.scalar(select(WhatsappTemplate).where(WhatsappTemplate.name == name.strip(), WhatsappTemplate.language == language.strip()))
    if existing:
        existing.category = category
        existing.body_preview = body_preview
        existing.param_count = param_count
        existing.is_active = True
    else:
        db.add(WhatsappTemplate(
            name=name.strip(), language=language.strip() or "en", category=category,
            body_preview=body_preview, param_count=param_count, is_active=True,
        ))
    db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('WhatsApp template saved')}", status_code=303)


@app.post("/inbox/settings/wa-template/{tpl_id}/delete")
def delete_wa_template(
    tpl_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
) -> RedirectResponse:
    tpl = db.get(WhatsappTemplate, tpl_id)
    if tpl:
        db.delete(tpl)
        db.commit()
    return RedirectResponse(f"/inbox/settings?flash={quote_plus('WhatsApp template removed')}", status_code=303)


# NOTE: declared AFTER the static /inbox/settings/* routes above so the literal
# "settings" path can't be captured as {conv_id} (FastAPI matches in order).
@app.post("/inbox/{conv_id}/wa-template")
def inbox_wa_template(
    conv_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    template_id: int = Form(...),
    params: str = Form(""),
    agent_id: int = Form(0),
) -> RedirectResponse:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.channel != "whatsapp":
        return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Not a WhatsApp conversation.')}", status_code=303)
    tpl = db.get(WhatsappTemplate, template_id)
    if tpl is None:
        return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus('Pick a template.')}", status_code=303)
    to_phone = conv.contact_phone or (conv.contact.primary_phone if conv.contact else "")
    body_params = [p.strip() for p in params.split("|") if p.strip()] or None
    result = whatsapp_module.send_template(to_phone, tpl.name, tpl.language, body_params)
    inbox_module.add_outbound_message(
        db, conv, agent=_current_agent(db, agent_id), from_addr=settings.whatsapp_phone_number_id,
        to_addr=to_phone, subject="WhatsApp", body_text=f"[template: {tpl.name}] {tpl.body_preview}",
        body_html="", external_message_id=result.message_id, external_thread_id=conv.external_thread_id,
        status="sent" if result.ok else "failed", error="" if result.ok else result.error, channel="whatsapp",
    )
    msg = "Template sent" if result.ok else f"Send failed: {result.error[:140]}"
    return RedirectResponse(f"/inbox?conv={conv_id}&flash={quote_plus(msg)}", status_code=303)


# ===========================================================================
# In-house CRM — Phase 6: agent login (roles), reporting, audit, real-time
# ===========================================================================


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin), flash: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request, "login.html",
        {"request": request, "app_name": settings.app_name,
         "current": auth_module.current_agent(request, db), "flash": flash},
    )


@app.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    email: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    agent = auth_module.authenticate(db, email, password)
    if agent is None:
        return RedirectResponse(f"/login?flash={quote_plus('Invalid email or password.')}", status_code=303)
    agent.last_login_at = datetime.utcnow()
    log_audit(db, actor=agent.name, action="agent.login", agent_id=agent.id, target_type="agent", target_id=agent.email)
    resp = RedirectResponse("/inbox", status_code=303)
    resp.set_cookie(
        auth_module.COOKIE_NAME, auth_module.make_session_token(agent.id),
        max_age=settings.session_ttl_hours * 3600, httponly=True, samesite="lax",
    )
    return resp


@app.get("/logout")
def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth_module.COOKIE_NAME)
    return resp


@app.get("/api/me")
def api_me(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> JSONResponse:
    agent = auth_module.current_agent(request, db)
    counts = reporting_module.live_counts(db)
    return JSONResponse({
        "agent": agent.name if agent else "",
        "role": agent.role if agent else "owner",
        "is_admin": auth_module.is_admin(request, db),
        "unread": counts["unread"], "open": counts["open"],
        "mentions": inbox_module.unread_mention_count(db, agent.id if agent else None),
    })


@app.get("/api/stream")
async def api_stream(request: Request, _: str = Depends(require_admin)):
    """Server-Sent Events: pushes live inbox counts when they change."""
    async def event_gen():
        last = None
        # Cap lifetime so connections recycle (proxies, redeploys).
        for _ in range(450):  # ~1h at 8s
            if await request.is_disconnected():
                break
            try:
                counts = await asyncio.to_thread(_live_counts_safe)
            except Exception:
                counts = {}
            data = json.dumps(counts)
            if data != last:
                yield f"data: {data}\n\n"
                last = data
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(8)

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })


def _live_counts_safe() -> dict:
    session = SessionLocal()
    try:
        return reporting_module.live_counts(session)
    finally:
        session.close()


@app.get("/setup", response_class=HTMLResponse)
def setup_center(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    """One-stop setup dashboard — live status + copy-paste values + buttons."""
    gmail = gmail_sender.connection_status(db)
    wa = whatsapp_module.status()
    ig = instagram_module.status()
    contact_count = db.scalar(select(func.count(Contact.id))) or 0
    cold_count = db.scalar(
        select(func.count(Contact.id)).where(
            (Contact.source_summary.like("%kvk%") | Contact.source_summary.like("%prospect%")),
            Contact.is_customer.is_(False),
            ~Contact.source_summary.like("%lead%"),
            ~Contact.source_summary.like("%customer%"),
        )
    ) or 0
    agent_count = db.scalar(select(func.count(Agent.id))) or 0
    address_set = bool(settings.company_address) and "<" not in settings.company_address

    # Email (Gmail) checklist
    email_steps = [
        {"label": "OAuth client set in Railway (GMAIL_CLIENT_ID + SECRET)", "done": gmail["configured"]},
        {"label": "Gmail account connected", "done": gmail["connected"]},
        {"label": f"Send-as alias verified ({gmail.get('default_send_as','')})",
         "done": gmail["connected"] and (gmail.get("default_send_as", "") in gmail.get("send_as_aliases", []))},
        {"label": "Company address set (legal footer)", "done": address_set},
    ]
    # Instagram checklist
    ig_steps = [
        {"label": "INSTAGRAM_ACCOUNT_ID + ACCESS_TOKEN set", "done": ig["configured"]},
        {"label": "Verify token set", "done": ig["verify_token_set"]},
        {"label": "App secret set (signature check)", "done": ig["has_app_secret"]},
        {"label": "Webhook registered in Meta (subscribe to 'messages')", "done": False, "manual": True},
    ]
    # Contacts checklist
    contacts_steps = [
        {"label": f"Contacts built ({contact_count:,} total, {cold_count:,} cold-eligible)", "done": contact_count > 0},
    ]

    def pct(steps):
        auto = [s for s in steps if not s.get("manual")]
        return int(100 * sum(1 for s in auto if s["done"]) / max(1, len(auto)))

    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request, "app_name": settings.app_name,
            "gmail": gmail, "wa": wa, "ig": ig,
            "email_steps": email_steps, "ig_steps": ig_steps, "contacts_steps": contacts_steps,
            "email_pct": pct(email_steps), "ig_pct": pct(ig_steps), "contacts_pct": pct(contacts_steps),
            "contact_count": contact_count, "cold_count": cold_count, "agent_count": agent_count,
            "redirect_uri": gmail.get("redirect_uri", ""),
        },
    )


@app.get("/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    days: int = 30,
) -> HTMLResponse:
    days = max(1, min(365, days))
    report = reporting_module.build_report(db, days=days)
    return templates.TemplateResponse(
        request, "reports.html",
        {"request": request, "app_name": settings.app_name, "report": report, "days": days},
    )


@app.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
    __: bool = Depends(require_admin_role),
) -> HTMLResponse:
    entries = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(300)).all()
    return templates.TemplateResponse(
        request, "audit.html",
        {"request": request, "app_name": settings.app_name, "entries": entries},
    )


# ===========================================================================
# Review workflow (non-Google discovery) — Discovery / Match / Tier / Ready
# ===========================================================================

LOW_FIT_TIERS = {"Low Fit", "Brand Store"}


def _discovery_queue_q():
    return (
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(KvkCompany.email_public == "")
        .where(KvkCompany.enrichment_status.in_(["pending", "needs_review", "no_website", "partial"]))
        .order_by(KvkCompany.website_confidence.asc(), KvkCompany.search_attempts.asc(), KvkCompany.id.asc())
    )


def _match_queue_q():
    return (
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(KvkCompany.match_confidence.in_(["high", "medium"]))
        .order_by(KvkCompany.match_confidence.asc(), KvkCompany.id.asc())
    )


def _tier_queue_q():
    return (
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(or_(KvkCompany.bike_shop_tier == "", KvkCompany.bike_shop_tier == "Unclassified"))
        .order_by(KvkCompany.id.asc())
    )


def _ready_queue_q():
    return (
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(KvkCompany.email_public != "")
        .where(KvkCompany.approved_for_outreach.is_(False))
        .where(KvkCompany.bike_shop_tier.notin_(list(LOW_FIT_TIERS)))
        .order_by(KvkCompany.website_confidence.desc(), KvkCompany.id.asc())
    )


def _count(db, q):
    return db.scalar(select(func.count()).select_from(q.subquery())) or 0


@app.get("/review", response_class=HTMLResponse)
def review_hub(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "review_hub.html",
        {
            "request": request, "app_name": settings.app_name,
            "engine": getattr(settings, "discovery_engine", "open"),
            "searxng_set": bool(getattr(settings, "searxng_url", "")),
            "discovery_count": _count(db, _discovery_queue_q()),
            "match_count": _count(db, _match_queue_q()),
            "tier_count": _count(db, _tier_queue_q()),
            "ready_count": _count(db, _ready_queue_q()),
        },
    )


# ── Discovery queue ──────────────────────────────────────────────────────────


@app.get("/review/discovery", response_class=HTMLResponse)
def review_discovery(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin),
                     flash: str = "", page: int = 1) -> HTMLResponse:
    per = 50
    rows = db.scalars(_discovery_queue_q().offset((max(1, page) - 1) * per).limit(per)).all()
    return templates.TemplateResponse(
        request, "review_discovery.html",
        {"request": request, "app_name": settings.app_name, "rows": rows, "page": max(1, page),
         "total": _count(db, _discovery_queue_q()), "flash": flash,
         "engine": getattr(settings, "discovery_engine", "open"),
         "searxng_set": bool(getattr(settings, "searxng_url", ""))},
    )


@app.post("/review/discovery/run-batch")
def review_discovery_run_batch(db: Session = Depends(get_db), _: str = Depends(require_admin), limit: int = Form(25)) -> RedirectResponse:
    if not enrichment_open_module.open_engine_active():
        return RedirectResponse(f"/review/discovery?flash={quote_plus('DISCOVERY_ENGINE is not open — set it to open to use this.')}", status_code=303)

    def _run():
        s = SessionLocal()
        try:
            print("[review] open discovery batch:", enrichment_open_module.run_open_discovery_batch(s, limit=max(1, min(200, limit))))
        except Exception as exc:  # noqa: BLE001
            print(f"[review] discovery batch error: {exc}")
        finally:
            s.close()
    Thread(target=_run, daemon=True, name="review-discovery-batch").start()
    return RedirectResponse(f"/review/discovery?flash={quote_plus('Discovery started in the background. Refresh in a minute.')}", status_code=303)


@app.post("/review/discovery/{cid}/run")
def review_discovery_run_one(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        try:
            enrichment_open_module.run_open_discovery_for_company(db, company)
            msg = f"Discovery ran: {company.enrichment_status}"
        except Exception as exc:  # noqa: BLE001
            msg = f"Discovery error: {str(exc)[:120]}"
    else:
        msg = "Not found"
    return RedirectResponse(f"/review/discovery?flash={quote_plus(msg)}", status_code=303)


@app.post("/review/discovery/{cid}/confirm")
def review_discovery_confirm(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        company.enrichment_status = "discovered"
        db.commit()
    return RedirectResponse(f"/review/discovery?flash={quote_plus('Marked as discovered')}", status_code=303)


@app.post("/review/discovery/{cid}/clear-website")
def review_discovery_clear(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        company.website = ""
        company.website_domain = ""
        company.website_confidence = 0
        company.enrichment_status = "pending"
        db.commit()
    return RedirectResponse(f"/review/discovery?flash={quote_plus('Website cleared — will be re-discovered')}", status_code=303)


# ── Match review (possible existing customers) ──────────────────────────────


@app.get("/review/match", response_class=HTMLResponse)
def review_match(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin), flash: str = "") -> HTMLResponse:
    rows = db.scalars(_match_queue_q().limit(200)).all()
    # Resolve matched customer names for display.
    cust_ids = [r.matched_customer_id for r in rows if r.matched_customer_id]
    customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(cust_ids))).all()} if cust_ids else {}
    return templates.TemplateResponse(
        request, "review_match.html",
        {"request": request, "app_name": settings.app_name, "rows": rows, "customers": customers,
         "total": _count(db, _match_queue_q()), "flash": flash},
    )


@app.post("/review/match/scan")
def review_match_scan(db: Session = Depends(get_db), _: str = Depends(require_admin), limit: int = Form(300)) -> RedirectResponse:
    def _run():
        s = SessionLocal()
        try:
            print("[review] suppression scan:", enrichment_open_module.scan_possible_customers(s, limit=max(1, min(2000, limit))))
        except Exception as exc:  # noqa: BLE001
            print(f"[review] suppression scan error: {exc}")
        finally:
            s.close()
    Thread(target=_run, daemon=True, name="review-match-scan").start()
    return RedirectResponse(f"/review/match?flash={quote_plus('Scanning for possible customers in the background. Refresh shortly.')}", status_code=303)


@app.post("/review/match/{cid}/confirm")
def review_match_confirm(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        company.already_client_flag = True
        company.client_match_status = "matched"
        db.commit()
    return RedirectResponse(f"/review/match?flash={quote_plus('Confirmed as existing customer — suppressed from outreach')}", status_code=303)


@app.post("/review/match/{cid}/dismiss")
def review_match_dismiss(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        company.match_confidence = "dismissed"
        db.commit()
    return RedirectResponse(f"/review/match?flash={quote_plus('Dismissed — not a customer')}", status_code=303)


# ── Tier review ──────────────────────────────────────────────────────────────


@app.get("/review/tier", response_class=HTMLResponse)
def review_tier(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin), flash: str = "") -> HTMLResponse:
    rows = db.scalars(_tier_queue_q().limit(100)).all()
    return templates.TemplateResponse(
        request, "review_tier.html",
        {"request": request, "app_name": settings.app_name, "rows": rows, "tiers": KVK_TIER_FILTERS,
         "total": _count(db, _tier_queue_q()), "flash": flash},
    )


@app.post("/review/tier/{cid}/set")
def review_tier_set(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin), tier: str = Form(...)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company and tier in KVK_TIER_FILTERS:
        company.bike_shop_tier = tier
        db.commit()
    return RedirectResponse(f"/review/tier?flash={quote_plus('Tier set: ' + tier)}", status_code=303)


@app.post("/review/tier/{cid}/auto")
def review_tier_auto(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        decision = score_kvk_company_tier(company)
        company.bike_shop_tier = decision.bike_shop_tier
        company.outreach_priority = decision.outreach_priority
        company.tier_reason = decision.tier_reason
        db.commit()
        msg = f"Auto-classified: {decision.bike_shop_tier}"
    else:
        msg = "Not found"
    return RedirectResponse(f"/review/tier?flash={quote_plus(msg)}", status_code=303)


# ── Outreach-ready queue ────────────────────────────────────────────────────


@app.get("/review/ready", response_class=HTMLResponse)
def review_ready(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin), flash: str = "") -> HTMLResponse:
    rows = db.scalars(_ready_queue_q().limit(100)).all()
    return templates.TemplateResponse(
        request, "review_ready.html",
        {"request": request, "app_name": settings.app_name, "rows": rows,
         "total": _count(db, _ready_queue_q()), "flash": flash},
    )


@app.post("/review/ready/{cid}/approve")
def review_ready_approve(cid: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    company = db.get(KvkCompany, cid)
    if company:
        company.approved_for_outreach = True
        db.commit()
    return RedirectResponse(f"/review/ready?flash={quote_plus('Approved for outreach')}", status_code=303)
