# Schild Inc KVK Prospecting Engine - Refined Requirements

## Project Goal

Extend the existing Railway B2B prospecting app to support KVK bike company enrichment, enabling Schild Inc to:
1. Import normalized KVK company and establishment data (~4k companies, ~4.2k locations)
2. Discover missing website, email, and phone data using Google Maps + website scraping
3. Match against existing customers to avoid duplicate outreach
4. Score prospects by bike shop tier and outreach priority
5. Provide an easy, non-technical admin UI for business owners
6. Enable controlled, manual-first email sending with templates

**Key constraint:** Keep all existing customer, matching, and outreach features working. Build incrementally and safely.

---

## Data Overview

**Incoming CSV structure (already normalized):**
- `normalized_kvk_bike_companies.csv` (3,990 rows) — company-level aggregation
- `normalized_kvk_bike_establishments.csv` (4,240 rows) — location-level records

**Available fields:**
- Company/establishment identifiers: `kvk_number`, `company_name`, `canonical_company_name_clean`, `search_company_name`
- Address: `primary_city`, `postal_code`, `visiting_street`, `visiting_house_number`, etc.
- Enrichment status: `enrichment_status` (pending → needs discovery)
- Pre-built search queries: `google_maps_query`, `contact_search_query`
- Customer matching: `already_client_flag`, `client_match_status` (unknown → needs matching)
- Target enrichment fields: `website`, `website_domain`, `email_public`, `phone_public` (mostly empty)
- Confidence fields: `email_confidence`, `phone_confidence`, `email_source_url`, `phone_source_url`

---

## Phase 1: Foundation (Week 1-2)
**Goal:** Import KVK data, set up database models, enable basic UI

### 1.1 Database Models & Schema

**New tables:**

```sql
kvk_companies:
  id (PK)
  source_system, source_file, company_entity_id
  kvk_number (unique)
  company_name, canonical_company_name_clean, search_company_name
  main_activity_code, main_activity_description
  date_of_establishment
  country_code, province_code
  establishments_count
  primary_establishment_number, primary_city, primary_postal_code, primary_address
  
  -- Contact discovery
  website, website_domain, email_public, phone_public
  email_source_url, phone_source_url
  email_confidence, phone_confidence
  
  -- Enrichment tracking
  enrichment_status (pending|maps_search_in_progress|maps_match_found|website_discovery_in_progress|discovered|partial|error)
  google_maps_query, contact_search_query
  
  -- Customer matching
  already_client_flag, client_match_status
  matched_customer_id (FK → customers.id)
  match_confidence (high|medium|low)
  best_match_reason (domain|email|name_city|fuzzy|kvk_number)
  
  -- Tiering
  bike_shop_tier (Good Tier|Hard to Reach|Mid Tier|Low Tier|Brand Store|Low Fit|Unclassified)
  bike_shop_segment, outreach_priority
  tier_reason
  headquarters_required
  franchise_or_buying_group
  recommended_sales_angle, recommended_contact_type
  
  -- Outreach control
  approved_for_outreach (default: false)
  
  -- Import tracking
  created_at, updated_at, last_enrichment_attempt_at

kvk_establishments:
  id (PK)
  source_system, source_file, record_id
  kvk_number (FK)
  establishment_number (unique with kvk_number)
  company_name, canonical_company_name_clean, search_company_name
  main_activity_code, main_activity_description
  date_of_establishment
  country_code, province_code, non_mailing_indicator
  
  -- Addresses (visiting vs postal)
  visiting_street, visiting_house_number, visiting_house_letter, visiting_location_addition
  visiting_postal_code, visiting_city, visiting_municipality_code, visiting_municipality_name
  postal_street, postal_house_number, postal_house_letter, postal_location_addition
  postal_postal_code, postal_city, postal_municipality_code, postal_municipality_name
  full_visiting_address, full_postal_address
  
  -- Contact discovery (same as companies)
  website, website_domain, email_public, phone_public
  email_source_url, phone_source_url
  email_confidence, phone_confidence
  
  -- Enrichment tracking
  enrichment_status, google_maps_query, contact_search_query
  
  -- Customer matching
  already_client_flag, client_match_status
  matched_customer_id (FK)
  
  -- Notes
  notes
  
  -- Timestamps
  created_at, updated_at

kvk_import_log:
  id (PK)
  import_batch_id (uuid)
  file_name, file_size, row_count
  successful_inserts, failed_rows
  error_csv_path (if errors)
  started_at, completed_at, status (in_progress|success|failed)
  notes
```

### 1.2 CSV Import Endpoint & Service

**API endpoint:** `POST /admin/kvk/import`

**Service:** `app/importers.py` → add `import_kvk_companies()` and `import_kvk_establishments()`

**Behavior:**
- Accept CSV upload
- Validate required columns against schema (auto-detect from CSV headers)
- Upsert by `kvk_number` (companies) or `(kvk_number, establishment_number)` (establishments)
- Log import statistics
- Generate error CSV for failed rows (show which rows, why they failed)
- Return summary: imported X, updated Y, failed Z

**UI:** Add "KVK Import" page with file upload, progress bar, summary

---

### 1.3 Database Models (SQLAlchemy)

Add to `app/models.py`:

```python
class KvkCompany(Base):
    __tablename__ = "kvk_companies"
    
    id = Column(Integer, primary_key=True)
    kvk_number = Column(String, unique=True, index=True)
    company_name = Column(String)
    canonical_company_name_clean = Column(String, index=True)
    search_company_name = Column(String)
    
    # Company details
    main_activity_code = Column(Integer)
    main_activity_description = Column(String)
    date_of_establishment = Column(Date)
    country_code = Column(String, default="NL")
    province_code = Column(String)
    establishments_count = Column(Integer, default=1)
    primary_city = Column(String, index=True)
    primary_postal_code = Column(String)
    primary_address = Column(String)
    
    # Contact fields (enrichment targets)
    website = Column(String)
    website_domain = Column(String, index=True)
    email_public = Column(String, index=True)
    phone_public = Column(String)
    email_source_url = Column(String)
    phone_source_url = Column(String)
    email_confidence = Column(String)  # high|medium|low
    phone_confidence = Column(String)
    
    # Enrichment tracking
    enrichment_status = Column(String, default="pending", index=True)
    google_maps_query = Column(String)
    contact_search_query = Column(String)
    last_enrichment_attempt_at = Column(DateTime)
    
    # Customer matching
    already_client_flag = Column(Boolean, default=False, index=True)
    client_match_status = Column(String, default="unknown")
    matched_customer_id = Column(Integer, ForeignKey("customers.id"))
    match_confidence = Column(String)
    best_match_reason = Column(String)
    
    # Tiering (Phase 2)
    bike_shop_tier = Column(String, default="Unclassified")
    bike_shop_segment = Column(String)
    outreach_priority = Column(String)
    tier_reason = Column(String)
    headquarters_required = Column(Boolean, default=False)
    franchise_or_buying_group = Column(String)
    recommended_sales_angle = Column(String)
    recommended_contact_type = Column(String)
    
    # Outreach control
    approved_for_outreach = Column(Boolean, default=False, index=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    matched_customer = relationship("Customer")
```

Similar for `KvkEstablishment`.

### 1.4 Alembic Migration

Create migration:
```bash
alembic revision --autogenerate -m "Add KVK companies and establishments tables"
```

---

## Phase 2: Customer Matching (Week 2-3)
**Goal:** Suppress existing customers, mark them in UI

### 2.1 Matching Service

Add to `app/matching.py`:

```python
def match_kvk_company_to_customer(company: KvkCompany, session: Session) -> tuple[Customer | None, str, str]:
    """
    Match KVK company to existing customer using priority:
    1. website domain exact match
    2. primary email exact match
    3. canonical name + city + country
    4. fuzzy name match in same city/country
    """
    
    # 1. Domain match (strongest signal)
    if company.website_domain:
        customer = session.query(Customer).filter_by(website_domain=company.website_domain).first()
        if customer:
            return customer, "high", "domain"
    
    # 2. Email match
    if company.email_public:
        customer = session.query(Customer).filter_by(email=company.email_public).first()
        if customer:
            return customer, "high", "email"
    
    # 3. Name + city + country
    candidates = session.query(Customer).filter(
        Customer.canonical_company_name_clean == company.canonical_company_name_clean,
        Customer.city == company.primary_city,
        Customer.country_code == company.country_code,
    ).all()
    if candidates:
        return candidates[0], "medium", "name_city_country"
    
    # 4. Fuzzy match
    customers = session.query(Customer).filter(
        Customer.country_code == company.country_code,
        Customer.city == company.primary_city,
    ).all()
    
    if customers:
        best_match = rapidfuzz_best_match(
            company.canonical_company_name_clean,
            [c.canonical_company_name_clean for c in customers],
            score_cutoff=85,
        )
        if best_match:
            return best_match, "medium", "fuzzy_name"
    
    return None, "none", "no_match"

def apply_kvk_matching(session: Session):
    """Run matching for all KVK companies with client_match_status = unknown"""
    unmatched = session.query(KvkCompany).filter_by(client_match_status="unknown").all()
    
    for company in unmatched:
        customer, confidence, reason = match_kvk_company_to_customer(company, session)
        if customer:
            company.already_client_flag = True
            company.matched_customer_id = customer.id
            company.match_confidence = confidence
            company.best_match_reason = reason
            company.client_match_status = "matched"
        else:
            company.client_match_status = "no_match"
        session.commit()
```

### 2.2 Admin UI Changes

**Add filters to KVK list:**
- Already customer / Not customer
- Has website / no website
- Has email / no email
- Match confidence: High / Medium / Low / No Match

**Add badges in table:**
- 🔴 "Existing Customer" (red badge) with matched customer name on hover

---

## Phase 3: Website Discovery & Enrichment (Week 3-4)
**Goal:** Find website/email/phone using Google Maps + website scraping

### 3.1 Google Maps / Business Name Search

Add to `app/google_places.py`:

**Behavior:** Use company name + city + pre-built `google_maps_query` to find likely website and phone via Places API OR local search.

```python
async def find_website_from_maps(company: KvkCompany) -> dict:
    """
    Search Google Maps for company using pre-built query.
    Return: {website, phone, place_id, confidence}
    """
    # Use google_maps_query like "Kooistra en Kuiper Heeg fietswinkel"
    results = await search_google_places(company.google_maps_query)
    
    if results:
        best = results[0]  # Top result
        return {
            "website": best.get("website"),
            "phone": best.get("formatted_phone_number"),
            "place_id": best.get("place_id"),
            "maps_name": best.get("name"),
            "confidence": "high" if best.get("rating") > 4.0 else "medium",
        }
    return {}
```

### 3.2 Website Contact Discovery

Reuse existing `app/discovery.py` logic:
- Visit homepage
- Check likely pages: contact, about, contact-us, impressum
- Extract visible email + phone
- Rank and return best candidate

### 3.3 Enrichment Job

Add background job:

```python
async def enrich_kvk_company_contacts(company_id: int):
    """
    1. Find website from Maps if missing
    2. Run website contact discovery
    3. Store source + confidence
    4. Update enrichment_status
    """
    company = session.get(KvkCompany, company_id)
    
    try:
        # Step 1: Find website if missing
        if not company.website:
            maps_result = await find_website_from_maps(company)
            if maps_result.get("website"):
                company.website = maps_result["website"]
                company.website_domain = extract_domain(maps_result["website"])
                company.phone_public = maps_result.get("phone")
                company.email_source_url = "google_maps"
                company.phone_source_url = "google_maps"
        
        # Step 2: Run website discovery if we have a website
        if company.website:
            company.enrichment_status = "website_discovery_in_progress"
            session.commit()
            
            discovery = await discover_public_contacts_for_prospect(
                company_name=company.company_name,
                website=company.website,
                country=company.country_code,
                city=company.primary_city,
            )
            
            if discovery.get("email"):
                company.email_public = discovery["email"]
                company.email_source_url = discovery["source_page"]
                company.email_confidence = discovery["confidence"]
            
            if discovery.get("phone"):
                company.phone_public = discovery["phone"]
                company.phone_source_url = discovery["source_page"]
                company.phone_confidence = discovery["confidence"]
        
        company.enrichment_status = "discovered" if company.email_public or company.phone_public else "partial"
        company.last_enrichment_attempt_at = datetime.utcnow()
        session.commit()
        
    except Exception as e:
        company.enrichment_status = "error"
        company.notes = str(e)
        session.commit()
```

### 3.4 Admin UI

**Add "KVK Enrichment" page:**
- Show KVK companies pending enrichment
- Filters: pending, in_progress, discovered, error
- Bulk action: "Discover Contacts for Selected"
- Progress indicator during job
- Show discovered website/email/phone with source + confidence

---

## Phase 4: Bike Shop Tiering (Week 4)
**Goal:** Score prospects, suggest outreach priority

### 4.1 Tiering Service

Add to `app/tiering.py`:

```python
def score_kvk_company_bike_tier(company: KvkCompany) -> dict:
    """
    Heuristics-based scoring. Return:
    {tier, segment, outreach_priority, headquarters_required, 
     franchise_or_buying_group, recommended_sales_angle, recommended_contact_type, reason}
    """
    
    score = 0
    signals = []
    
    # Keyword heuristics (check company_name, website)
    text = (company.company_name + " " + (company.website or "")).lower()
    
    if any(kw in text for kw in ["mantel", "bike totaal", "azor", "chain", "group"]):
        return {
            "tier": "Hard to Reach",
            "signals": ["Chain/group indicators"],
            "outreach_priority": "Medium",
            "headquarters_required": True,
            "recommended_contact_type": "Head Office",
            "recommended_sales_angle": "Central purchasing, rollout, brand consistency",
        }
    
    if any(kw in text for kw in ["giant", "cube", "trek", "brand store"]):
        return {
            "tier": "Brand Store",
            "signals": ["Single-brand store indicators"],
            "outreach_priority": "Low",
            "headquarters_required": True,
            "recommended_contact_type": "Brand HQ",
            "recommended_sales_angle": "HQ partnership only",
        }
    
    if any(kw in text for kw in ["premium", "service", "professional", "a-brand"]):
        return {
            "tier": "Good Tier",
            "signals": ["Premium/professional indicators"],
            "outreach_priority": "High",
            "headquarters_required": False,
            "recommended_contact_type": "Owner/Manager",
            "recommended_sales_angle": "Premium branding, professional look, add-on sales",
        }
    
    if any(kw in text for kw in ["second-hand", "used", "tweedehands", "goedkoop", "cheap"]):
        return {
            "tier": "Mid Tier",
            "signals": ["Second-hand/price-driven indicators"],
            "outreach_priority": "Low",
            "recommended_contact_type": "Owner",
            "recommended_sales_angle": "Simple practical branding only",
        }
    
    if any(kw in text for kw in ["repair", "reparatie", "one-man", "lokaal"]):
        return {
            "tier": "Low Tier",
            "signals": ["Repair-focused/local indicators"],
            "outreach_priority": "Very Low",
            "recommended_contact_type": "Owner",
            "recommended_sales_angle": "N/A",
        }
    
    # Default
    return {
        "tier": "Unclassified",
        "signals": ["No clear signals"],
        "outreach_priority": "Unknown",
        "headquarters_required": False,
        "recommended_contact_type": "Owner",
        "recommended_sales_angle": "Review manually",
    }

def apply_kvk_bike_tier(session: Session):
    """Apply tiering to all KVK companies"""
    companies = session.query(KvkCompany).filter_by(bike_shop_tier="Unclassified").all()
    
    for company in companies:
        result = score_kvk_company_bike_tier(company)
        company.bike_shop_tier = result["tier"]
        company.outreach_priority = result["outreach_priority"]
        company.tier_reason = "; ".join(result["signals"])
        company.headquarters_required = result.get("headquarters_required", False)
        company.recommended_sales_angle = result["recommended_sales_angle"]
        company.recommended_contact_type = result["recommended_contact_type"]
        session.commit()
```

### 4.2 Admin UI

**Add filters:**
- Good Tier / Hard to Reach / Mid Tier / Low Tier / Brand Store / Low Fit

**Show in table:**
- Tier badge with color
- Outreach priority label
- Recommended contact type on hover

---

## Phase 5: Manual Email Sending (Week 5)
**Goal:** Test + manual send with templates

### 5.1 Email Templates

Create `app/email_templates.py`:

```python
TEMPLATES = {
    "main": """Beste {{company_name}} team,

Ik kwam jullie website tegen en dacht dat Schild Inc interessant kan zijn voor jullie fietsenwinkel.

Wij helpen fietsenwinkels met:
- premium metalen labels met eigen logo
- bike accessoires met eigen logo
- een professionelere uitstraling in de winkel en op de fiets

Onze oplossingen worden al gebruikt door meer dan 500 fietsenwinkels, waaronder BikeTotaal, Azor, VMG en Gazelle.

Wat we vrijblijvend kunnen doen:
- een gratis eerste labelontwerp met jullie huidige logo
- voorbeelden van bike accessoires met eigen logo
- een paar relevante projectvoorbeelden

Staat jullie open voor een paar voorbeelden of een eerste gratis ontwerpidee?

Met vriendelijke groet,
{{sender_name}}
Schild Inc""",
    
    "followup": """Beste {{company_name}} team,

Ik stuur nog even een korte follow-up op mijn vorige mail.

Wij helpen fietsenwinkels met premium metalen labels en bike accessoires met eigen logo.

Als jullie willen, kan ik vrijblijvend een paar voorbeelden sturen of een eerste gratis labelontwerpidee delen voor jullie winkel.

Staat jullie daarvoor open?

Met vriendelijke groet,
{{sender_name}}
Schild Inc""",
}

SUBJECTS = {
    "main": "Idee voor jullie fietsenwinkel branding",
    "followup": "Even checken - labels en accessoires met jullie logo",
}
```

### 5.2 Manual Send Endpoint

Add `POST /admin/manual-send`:

```python
@app.post("/admin/manual-send")
async def manual_send_email(
    credentials: HTTPBasicCredentials = Depends(require_admin),
    recipient_email: str = Form(...),
    company_name: str = Form(...),
    template: str = Form("main"),  # "main" or "followup"
    subject: str = Form(...),
    body: str = Form(...),
    send_now: bool = Form(False),  # if False, save as draft
    session: Session = Depends(get_db),
):
    """
    Manual send with preview/draft option.
    """
    
    # Validate email
    if not is_valid_email(recipient_email):
        raise HTTPException(status_code=400, detail="Invalid email")
    
    # Check if existing customer
    customer = session.query(Customer).filter_by(email=recipient_email).first()
    if customer and not Form("override_existing"):
        return {"warning": f"This email matches existing customer: {customer.company_name}. Override to proceed."}
    
    # Create outreach message record
    msg = OutreachMessage(
        recipient_email=recipient_email,
        company_name=company_name,
        template_used=template,
        subject=subject,
        body=body,
        status="draft" if not send_now else "pending",
        manual_send=True,
        sent_by=credentials.username,
        created_at=datetime.utcnow(),
    )
    session.add(msg)
    session.commit()
    
    if send_now:
        # Send via email provider
        await send_email(recipient_email, subject, body)
        msg.status = "sent"
        msg.sent_at = datetime.utcnow()
        session.commit()
    
    return {"status": "ok", "message_id": msg.id}
```

### 5.3 Admin UI

**Add "Send Test Email" page:**
- Form: recipient email, company name, template selection
- Live preview of rendered subject + body
- Buttons: "Save as Draft", "Send Test to Self", "Send Now"
- Show confirmation with warnings (existing customer, etc.)

---

## Phase 6: UI/UX Redesign (Week 6)
**Goal:** Make the app business-owner friendly

### 6.1 Design Goals

- Clean, modern, minimal clutter
- Card-based dashboard
- Easy bulk actions
- Readable tables with sticky headers, search, filters
- Color-coded badges (existing customer, tier, confidence)
- Mobile-friendly
- Less "developer tool", more "business software"

### 6.2 Pages to Redesign

1. **Dashboard**
   - Cards: Total KVK imported, Enriched with website, Enriched with email, Existing customers detected, New prospects, Good Tier count, Ready to send
   - Quick actions: Import KVK, Enrich Contacts, Review Matches, Approve Outreach
   - Recent activity log

2. **KVK Import**
   - Drag-drop upload area
   - Progress indicator during import
   - Summary: X inserted, Y updated, Z failed
   - Link to error CSV if any

3. **KVK Enrichment Queue**
   - Table: Company name, City, Website (if found), Email (if found), Phone (if found), Status, Actions
   - Filters: pending, in_progress, discovered, error
   - Bulk action: "Discover Contacts for Selected"
   - Show source + confidence on hover

4. **Customer Match Review**
   - Highlight: "This looks like existing customer: [Match Reason]"
   - Allow manual review/override
   - Approve/reject bulk

5. **Outreach-Ready Queue**
   - Table: Company name, Email, Tier, Priority, Approved?, Actions
   - Filters: approved, not approved, ready to send, needs review
   - Bulk action: "Approve Selected for Outreach"

6. **Company Detail Page**
   - All fields visible
   - Edit enrichment data manually if needed
   - Manual send email button
   - Show matching details
   - Show all contact sources + confidence

### 6.3 UI Components

- Color scheme: Professional (blue/gray, green for success)
- Badges: Red (existing customer), Green (approved), Yellow (needs review), Gray (pending)
- Table: Sticky header, sortable, filterable, bulk select
- Forms: Clear labels, inline validation, helpful tooltips

---

## Phase 7: Email Provider Support (Week 7)
**Goal:** Support Gmail SMTP + generic SMTP

### 7.1 Email Provider Abstraction

Add to `app/email_provider.py`:

```python
class EmailProvider(ABC):
    @abstractmethod
    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: str = None,
        from_name: str = None,
        reply_to: str = None,
    ) -> dict:
        pass

class GmailSmtpProvider(EmailProvider):
    def __init__(self, smtp_user, smtp_password):
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
    
    async def send(self, to_email, subject, body, **kwargs):
        # Use smtplib to connect to smtp.gmail.com:587
        # Requires app-specific password if 2FA enabled

class GenericSmtpProvider(EmailProvider):
    def __init__(self, host, port, user, password, use_tls=True):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
    
    async def send(self, to_email, subject, body, **kwargs):
        # Generic SMTP send

def get_email_provider() -> EmailProvider:
    if settings.EMAIL_PROVIDER == "gmail":
        return GmailSmtpProvider(...)
    elif settings.EMAIL_PROVIDER == "smtp":
        return GenericSmtpProvider(...)
```

### 7.2 Environment Variables

Add to `.env.example`:

```
EMAIL_PROVIDER=smtp  # or "gmail" or "resend"
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-specific-password
SMTP_FROM_EMAIL=noreply@schildinc.com
SMTP_FROM_NAME=Schild Inc Team
OUTREACH_REPLY_TO=sales@schildinc.com
```

---

## Phase 8: Final Testing & Deployment (Week 8)

- End-to-end testing: KVK import → enrichment → matching → approval → send
- Load test: 4k companies enrichment pipeline
- UI/UX testing with non-technical user
- Railway deployment + monitoring
- Documentation update

---

## Deliverables by Phase

### Phase 1
- ✅ SQLAlchemy models (KvkCompany, KvkEstablishment, KvkImportLog)
- ✅ Alembic migration
- ✅ CSV import service
- ✅ Admin UI: KVK Import page

### Phase 2
- ✅ Matching service (4-priority logic)
- ✅ Matching bulk job
- ✅ Admin filters + badges

### Phase 3
- ✅ Google Maps search service
- ✅ Website discovery job (async)
- ✅ Admin UI: Enrichment queue + bulk action

### Phase 4
- ✅ Tiering heuristics service
- ✅ Tiering bulk job
- ✅ Admin UI: Tier badges + filters

### Phase 5
- ✅ Email templates (main, followup)
- ✅ Manual send endpoint
- ✅ Admin UI: Manual Send page with preview

### Phase 6
- ✅ Redesigned dashboard
- ✅ Redesigned tables (all pages)
- ✅ Company detail page
- ✅ New UI components + styling

### Phase 7
- ✅ Email provider abstraction
- ✅ Gmail SMTP support
- ✅ Generic SMTP support
- ✅ Test connection button in admin

### Phase 8
- ✅ End-to-end testing
- ✅ Documentation
- ✅ Railway deployment
- ✅ README updated

---

## Key Implementation Notes

1. **Keep existing features:** Do not modify Customer, Prospect, OutreachQueue, or matching logic. Extend only.

2. **Incremental enrichment:** Run as background jobs. Don't block UI. Store all sources + confidence.

3. **Manual first, automated second:** Manual send/approval comes first. Automated queue uses same infrastructure.

4. **Safety rails:** Always warn before sending to existing customer. Always log. Never auto-message socials.

5. **Reuse existing code:** Leverage existing `discovery.py`, `matching.py`, `emailing.py` patterns.

6. **Database transactions:** Use `session.commit()` carefully. Batch inserts for large imports.

7. **Field naming:** Use exact column names from CSV (e.g., `email_source_url` not `email_source_page`).

---

## Testing Strategy

- Unit tests: Matching, tiering, enrichment logic
- Integration tests: CSV import, job execution, email sending
- E2E: Import 4k companies → enrich → match → approve → send workflow
- UI smoke tests: All admin pages load, tables display, bulk actions work

---

## Monitoring & Logging

- Track enrichment jobs: success rate, avg time per company
- Track email sends: success/failure, provider responses
- Track customer matches: false positive rate, override frequency
- Alert on: enrichment job failures, email send failures, database errors

---

## Future Enhancements (Post-MVP)

- Webhook handling (reply tracking)
- A/B test email templates
- Automatic tier adjustments based on enrichment data
- LinkedIn/Instagram profile enrichment
- Competitor database import
- Lead scoring model
- Multi-user admin with role-based access
- Bulk CSV export with all enriched data
