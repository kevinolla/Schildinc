from __future__ import annotations

import secrets
from datetime import date
from io import BytesIO

import pandas as pd
import stripe
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.emailing import build_queue_for_day, send_queue_item
from app.google_places import place_to_prospect_record, search_google_places
from app.importers import read_csv_upload, upsert_customers_from_dataframe, upsert_invoices_from_dataframe, upsert_prospects_from_dataframe
from app.jobs import run_daily_queue_build
from app.matching import apply_matching
from app.models import Customer, EmailLog, MatchStatus, OutreachQueueItem, Prospect, ProspectState, QueueState, SuppressionEntry, WebhookLog
from app.stripe_sync import sync_stripe_event
from app.utils import build_unsubscribe_token, normalize_domain, normalize_email

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic(auto_error=False)


def require_admin(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    if not settings.admin_password:
        return credentials.username if credentials else "local-dev"

    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    expected_user = settings.admin_username.encode("utf-8")
    expected_pass = settings.admin_password.encode("utf-8")
    provided_user = credentials.username.encode("utf-8")
    provided_pass = credentials.password.encode("utf-8")
    if settings.admin_password and (
        not secrets.compare_digest(expected_user, provided_user)
        or not secrets.compare_digest(expected_pass, provided_pass)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username


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
        "daily_send_limit": settings.daily_send_limit,
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
    request: Request,
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
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin),
) -> RedirectResponse:
    df = read_csv_upload(await file.read())
    summary = upsert_invoices_from_dataframe(db, df)
    db.commit()
    return RedirectResponse(f"/customers?invoice_inserted={summary.inserted}&invoice_updated={summary.updated}", status_code=303)


@app.get("/prospects", response_class=HTMLResponse)
def prospects_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    prospects = db.scalars(
        select(Prospect).options(selectinload(Prospect.matched_customer)).order_by(Prospect.updated_at.desc()).limit(200)
    ).all()
    return templates.TemplateResponse(
        request,
        "prospects.html",
        {"request": request, "prospects": prospects, "app_name": settings.app_name, "google_places_enabled": bool(settings.google_places_api_key)},
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
        prospects = db.scalars(select(Prospect).where(Prospect.source.in_(["google_places", "google_maps_csv"])).order_by(Prospect.updated_at.desc()).limit(limit)).all()
        for prospect in prospects:
            apply_matching(db, prospect)
        db.commit()
        return RedirectResponse(f"/prospects?inserted={summary.inserted}&updated={summary.updated}", status_code=303)
    return RedirectResponse("/prospects?message=no_results", status_code=303)


@app.post("/admin/prospects/{prospect_id}/match")
def rematch_prospect(prospect_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    apply_matching(db, prospect)
    db.commit()
    return RedirectResponse("/prospects", status_code=303)


@app.post("/admin/prospects/{prospect_id}/review")
def review_prospect(
    prospect_id: int,
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
    db.commit()
    return RedirectResponse("/prospects", status_code=303)


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> HTMLResponse:
    items = db.scalars(
        select(OutreachQueueItem).options(selectinload(OutreachQueueItem.prospect)).order_by(OutreachQueueItem.queue_date.desc(), OutreachQueueItem.id.desc()).limit(200)
    ).all()
    return templates.TemplateResponse(
        request,
        "queue.html",
        {"request": request, "items": items, "today": date.today(), "app_name": settings.app_name},
    )


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
    return RedirectResponse(f"/queue?created={created}", status_code=303)


@app.post("/admin/queue/{item_id}/send")
def send_queue(item_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    item = db.scalar(select(OutreachQueueItem).options(selectinload(OutreachQueueItem.prospect)).where(OutreachQueueItem.id == item_id))
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    send_queue_item(db, item)
    db.commit()
    return RedirectResponse("/queue", status_code=303)


@app.post("/admin/queue/{item_id}/skip")
def skip_queue(item_id: int, db: Session = Depends(get_db), _: str = Depends(require_admin)) -> RedirectResponse:
    item = db.get(OutreachQueueItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    item.state = QueueState.skipped
    db.commit()
    return RedirectResponse("/queue", status_code=303)


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
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"request": request, "emails": emails, "webhooks": webhooks, "app_name": settings.app_name},
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
