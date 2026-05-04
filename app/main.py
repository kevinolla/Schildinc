from __future__ import annotations

import secrets
from datetime import date

import pandas as pd
import stripe
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.discovery import discover_public_contacts_for_prospect
from app.emailing import export_queue_csv, preview_queue_for_day, send_queue_item
from app.google_places import place_to_prospect_record, search_google_places
from app.importers import read_csv_upload, upsert_customers_from_dataframe, upsert_invoices_from_dataframe, upsert_prospects_from_dataframe
from app.jobs import run_daily_queue_build, run_daily_queue_send
from app.matching import apply_matching
from app.models import (
    Customer,
    EmailLog,
    MatchStatus,
    OutreachQueueItem,
    Prospect,
    ProspectActivityLog,
    ProspectState,
    QueueState,
    SuppressionEntry,
    WebhookLog,
)
from app.outreach_templates import build_outreach_bundle
from app.tiering import apply_bike_tier
from app.stripe_sync import sync_stripe_event
from app.utils import build_unsubscribe_token, normalize_domain, normalize_email

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic(auto_error=False)

TIER_FILTERS = ["Good Tier", "Hard to Reach", "Mid Tier", "Low Tier", "Brand Store", "Low Fit", "Unclassified"]
DISCOVERY_FILTERS = ["all", "has_email", "no_email", "has_whatsapp", "has_socials", "high_confidence", "low_confidence", "found", "partial", "no_contacts", "error", "not_started"]


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
    }


def prospect_filters_context(
    *,
    search: str = "",
    match_filter: str = "",
    review_filter: str = "",
    tier_filter: str = "",
    discovery_filter: str = "all",
) -> dict:
    return {
        "search": search,
        "match_filter": match_filter,
        "review_filter": review_filter,
        "tier_filter": tier_filter,
        "discovery_filter": discovery_filter,
        "tier_options": TIER_FILTERS,
        "discovery_options": DISCOVERY_FILTERS,
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
        },
    )


@app.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    customers = db.scalars(select(Customer).order_by(Customer.updated_at.desc()).limit(100)).all()
    return templates.TemplateResponse(
        request,
        "customers.html",
        {"request": request, "customers": customers, "app_name": settings.app_name},
    )


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
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> HTMLResponse:
    query = select(Prospect).options(selectinload(Prospect.matched_customer)).order_by(Prospect.updated_at.desc())
    if search:
        like_term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Prospect.company_name.ilike(like_term),
                Prospect.website.ilike(like_term),
                Prospect.email.ilike(like_term),
                Prospect.whatsapp_number.ilike(like_term),
                Prospect.city.ilike(like_term),
            )
        )
    if match_filter:
        query = query.where(Prospect.match_status == MatchStatus(match_filter))
    if review_filter:
        query = query.where(Prospect.review_status == ProspectState(review_filter))
    if tier_filter:
        query = query.where(Prospect.bike_shop_tier == tier_filter)
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
    elif discovery_filter in {"found", "partial", "no_contacts", "error", "not_started"}:
        query = query.where(Prospect.email_discovery_status == discovery_filter)

    prospects = db.scalars(query.limit(300)).all()
    return templates.TemplateResponse(
        request,
        "prospects.html",
        {
            "request": request,
            "prospects": prospects,
            "app_name": settings.app_name,
            "google_places_enabled": bool(settings.google_places_api_key),
            **prospect_filters_context(
                search=search,
                match_filter=match_filter,
                review_filter=review_filter,
                tier_filter=tier_filter,
                discovery_filter=discovery_filter,
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
    discover_public_contacts_for_prospect(db, prospect)
    db.commit()
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
    for prospect in prospects:
        discover_public_contacts_for_prospect(db, prospect)
    db.commit()
    return redirect_back(request, "/prospects")


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
