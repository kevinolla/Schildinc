"""Starter email templates for Schild Inc outreach.

Five professional, English, bike-focused templates covering the full
funnel: cold intro, warm (known-detail) intro, cold follow-up, warm
follow-up, and a VIP / multi-location partnership pitch.

Merge fields (resolved per-recipient by app/email_engine.py):
    {{company_name}} {{contact_name}} {{city}} {{country}} {{website}}
    {{sender_name}}  {{reply_to}}     {{unsubscribe_url}}

`{{tracking_pixel}}` and click-tracked links are injected automatically at
send time — templates should NOT include them manually.

Bump STARTER_SEED_VERSION when editing any template below; the seeder
re-writes starter rows whose seed_version is lower (user-created templates
are never touched).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailTemplate

STARTER_SEED_VERSION = 1

# Shared merge fields hint surfaced in the UI.
COMMON_FIELDS = "company_name, contact_name, city, country, website, sender_name, reply_to"


def _wrap(title: str, inner_html: str) -> str:
    """Wrap body content in the Schild Inc branded email shell.

    `inner_html` is the per-template content. {{unsubscribe_url}} is filled
    per recipient; the tracking pixel is appended by the engine.
    """
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f4ef;font-family:Arial,Helvetica,sans-serif;color:#1f2933;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f4ef;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="620" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e7e1d6;">
          <tr>
            <td style="background:#101010;padding:22px 32px;text-align:center;">
              <div style="color:#e4c977;font-size:20px;font-weight:bold;letter-spacing:0.5px;">SCHILD INC</div>
              <div style="color:#cbbf9b;font-size:12px;margin-top:4px;">{title}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px 32px;font-size:15px;line-height:1.7;">
              {inner_html}
            </td>
          </tr>
          <tr>
            <td style="padding:8px 32px 26px 32px;font-size:15px;line-height:1.7;">
              <p style="margin:0;">Best regards,<br><strong>{{{{sender_name}}}}</strong><br>Schild Inc</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 24px 32px;">
              <div style="border-top:1px solid #e7dfd0;padding-top:12px;color:#8a8f98;font-size:12px;line-height:1.6;">
                Schild Inc — premium metal labels &amp; branded accessories.<br>
                You received this because we believe Schild Inc is relevant to {{{{company_name}}}}.
                Replies go to <a href="mailto:{{{{reply_to}}}}" style="color:#946f16;">{{{{reply_to}}}}</a>.<br>
                <a href="{{{{unsubscribe_url}}}}" style="color:#8a8f98;">Unsubscribe</a>
              </div>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


# ── 1. COLD — initial outreach ─────────────────────────────────────────────
COLD_INTRO_HTML = _wrap(
    "Premium branding for bike shops",
    """\
<p style="margin:0 0 14px 0;">Hi {{company_name}} team,</p>
<p style="margin:0 0 14px 0;">I came across your shop and thought Schild Inc could be a great fit. We help bike retailers stand out with <strong>premium metal labels and branded accessories</strong> carrying their own logo.</p>
<p style="margin:0 0 8px 0;">What we can do for you, free of charge to start:</p>
<ul style="margin:0 0 16px 0;padding-left:20px;">
  <li>A first label design using your current logo</li>
  <li>Samples of bike accessories with your branding</li>
  <li>A few relevant project examples</li>
</ul>
<p style="margin:0 0 14px 0;">Our solutions are already trusted by 500+ bike shops across Europe.</p>
<p style="margin:0 0 14px 0;">Would you be open to a couple of samples or a free first design idea?</p>""",
)
COLD_INTRO_TEXT = """\
Hi {{company_name}} team,

I came across your shop and thought Schild Inc could be a great fit. We help bike retailers stand out with premium metal labels and branded accessories carrying their own logo.

What we can do for you, free of charge to start:
- A first label design using your current logo
- Samples of bike accessories with your branding
- A few relevant project examples

Our solutions are already trusted by 500+ bike shops across Europe.

Would you be open to a couple of samples or a free first design idea?

Best regards,
{{sender_name}}
Schild Inc

Replies: {{reply_to}}
Unsubscribe: {{unsubscribe_url}}"""


# ── 2. WARM — known detail / prior touch ───────────────────────────────────
WARM_INTRO_HTML = _wrap(
    "Your shop + Schild labels",
    """\
<p style="margin:0 0 14px 0;">Hi {{company_name}} team,</p>
<p style="margin:0 0 14px 0;">Thanks for your interest in Schild Inc. Since you're based in {{city}}, {{country}}, I wanted to follow up personally.</p>
<p style="margin:0 0 14px 0;">We specialise in <strong>premium metal labels and branded accessories</strong> for bike shops — the kind of finishing touch that makes bikes leaving your workshop look unmistakably yours.</p>
<p style="margin:0 0 8px 0;">As a next step I'd be glad to send:</p>
<ul style="margin:0 0 16px 0;padding-left:20px;">
  <li>A free first label mock-up with your logo</li>
  <li>Pricing for your expected volume</li>
  <li>Physical samples on request</li>
</ul>
<p style="margin:0 0 14px 0;">Shall I put a first design idea together for {{company_name}}?</p>""",
)
WARM_INTRO_TEXT = """\
Hi {{company_name}} team,

Thanks for your interest in Schild Inc. Since you're based in {{city}}, {{country}}, I wanted to follow up personally.

We specialise in premium metal labels and branded accessories for bike shops — the finishing touch that makes bikes leaving your workshop look unmistakably yours.

As a next step I'd be glad to send:
- A free first label mock-up with your logo
- Pricing for your expected volume
- Physical samples on request

Shall I put a first design idea together for {{company_name}}?

Best regards,
{{sender_name}}
Schild Inc

Replies: {{reply_to}}
Unsubscribe: {{unsubscribe_url}}"""


# ── 3. COLD follow-up ───────────────────────────────────────────────────────
COLD_FOLLOWUP_HTML = _wrap(
    "Quick follow-up",
    """\
<p style="margin:0 0 14px 0;">Hi {{company_name}} team,</p>
<p style="margin:0 0 14px 0;">Just a short follow-up on my previous note. I know inboxes get busy.</p>
<p style="margin:0 0 14px 0;">If it's useful, I can send a <strong>free first label design</strong> with your current logo — no obligation, just so you can see how it would look on your bikes and in-store.</p>
<p style="margin:0 0 14px 0;">If now isn't the right time, just let me know and I'll close the loop.</p>""",
)
COLD_FOLLOWUP_TEXT = """\
Hi {{company_name}} team,

Just a short follow-up on my previous note. I know inboxes get busy.

If it's useful, I can send a free first label design with your current logo — no obligation, just so you can see how it would look on your bikes and in-store.

If now isn't the right time, just let me know and I'll close the loop.

Best regards,
{{sender_name}}
Schild Inc

Replies: {{reply_to}}
Unsubscribe: {{unsubscribe_url}}"""


# ── 4. WARM follow-up ───────────────────────────────────────────────────────
WARM_FOLLOWUP_HTML = _wrap(
    "Still interested?",
    """\
<p style="margin:0 0 14px 0;">Hi {{company_name}} team,</p>
<p style="margin:0 0 14px 0;">I wanted to check back in. Plenty of shops like yours use our branded labels to add a premium feel and a bit of extra add-on revenue at the counter.</p>
<p style="margin:0 0 14px 0;">Happy to send a <strong>free mock-up</strong> and a short overview of what other {{country}} retailers are doing. Want me to go ahead?</p>""",
)
WARM_FOLLOWUP_TEXT = """\
Hi {{company_name}} team,

I wanted to check back in. Plenty of shops like yours use our branded labels to add a premium feel and a bit of extra add-on revenue at the counter.

Happy to send a free mock-up and a short overview of what other {{country}} retailers are doing. Want me to go ahead?

Best regards,
{{sender_name}}
Schild Inc

Replies: {{reply_to}}
Unsubscribe: {{unsubscribe_url}}"""


# ── 5. VIP / multi-location partnership ─────────────────────────────────────
VIP_HTML = _wrap(
    "Partnership opportunity",
    """\
<p style="margin:0 0 14px 0;">Hi {{company_name}} team,</p>
<p style="margin:0 0 14px 0;">Given your presence across multiple locations, I'd love to explore a <strong>partnership</strong> rather than a one-off order.</p>
<p style="margin:0 0 8px 0;">For multi-location retailers and buying groups we offer:</p>
<ul style="margin:0 0 16px 0;padding-left:20px;">
  <li>Volume pricing across all your stores</li>
  <li>A dedicated account manager</li>
  <li>Consistent branding on labels &amp; accessories chain-wide</li>
</ul>
<p style="margin:0 0 14px 0;">Could we set up a 15-minute call to see if it's a fit for {{company_name}}?</p>""",
)
VIP_TEXT = """\
Hi {{company_name}} team,

Given your presence across multiple locations, I'd love to explore a partnership rather than a one-off order.

For multi-location retailers and buying groups we offer:
- Volume pricing across all your stores
- A dedicated account manager
- Consistent branding on labels & accessories chain-wide

Could we set up a 15-minute call to see if it's a fit for {{company_name}}?

Best regards,
{{sender_name}}
Schild Inc

Replies: {{reply_to}}
Unsubscribe: {{unsubscribe_url}}"""


STARTER_TEMPLATES = [
    {
        "name": "Cold — Initial Outreach",
        "category": "cold",
        "description": "First touch for net-new bike shops with no prior relationship.",
        "subject": "Premium branded labels for {{company_name}}",
        "body_html": COLD_INTRO_HTML,
        "body_text": COLD_INTRO_TEXT,
    },
    {
        "name": "Warm — Known Lead Intro",
        "category": "warm",
        "description": "For leads who already showed interest or whose details we hold.",
        "subject": "Your shop + Schild labels — {{company_name}}",
        "body_html": WARM_INTRO_HTML,
        "body_text": WARM_INTRO_TEXT,
    },
    {
        "name": "Cold — Follow-up",
        "category": "followup",
        "description": "Gentle nudge ~5 days after a cold intro with no reply.",
        "subject": "Quick question about {{company_name}}",
        "body_html": COLD_FOLLOWUP_HTML,
        "body_text": COLD_FOLLOWUP_TEXT,
    },
    {
        "name": "Warm — Follow-up",
        "category": "followup",
        "description": "Re-engage a warm lead who opened but didn't reply.",
        "subject": "Still interested, {{company_name}}?",
        "body_html": WARM_FOLLOWUP_HTML,
        "body_text": WARM_FOLLOWUP_TEXT,
    },
    {
        "name": "VIP — Multi-location Partnership",
        "category": "vip",
        "description": "For franchises, buying groups, and multi-store retailers.",
        "subject": "Partnership opportunity for {{company_name}}",
        "body_html": VIP_HTML,
        "body_text": VIP_TEXT,
    },
]


def seed_starter_templates(session: Session) -> int:
    """Idempotently insert/update the built-in starter templates.

    Matches on (name, is_starter=True). Re-writes content only when the
    stored seed_version is older than STARTER_SEED_VERSION. Returns the
    number of rows created or updated.
    """
    touched = 0
    for spec in STARTER_TEMPLATES:
        existing = session.scalar(
            select(EmailTemplate).where(
                EmailTemplate.name == spec["name"],
                EmailTemplate.is_starter.is_(True),
            )
        )
        if existing is None:
            session.add(
                EmailTemplate(
                    name=spec["name"],
                    category=spec["category"],
                    description=spec["description"],
                    subject=spec["subject"],
                    body_html=spec["body_html"],
                    body_text=spec["body_text"],
                    merge_fields=COMMON_FIELDS,
                    is_active=True,
                    is_starter=True,
                    seed_version=STARTER_SEED_VERSION,
                )
            )
            touched += 1
        elif existing.seed_version < STARTER_SEED_VERSION:
            existing.category = spec["category"]
            existing.description = spec["description"]
            existing.subject = spec["subject"]
            existing.body_html = spec["body_html"]
            existing.body_text = spec["body_text"]
            existing.merge_fields = COMMON_FIELDS
            existing.seed_version = STARTER_SEED_VERSION
            touched += 1
    if touched:
        session.commit()
    return touched
