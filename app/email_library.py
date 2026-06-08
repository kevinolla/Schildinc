"""Starter email templates for Schild Inc outreach.

Professional, brand-safe, GDPR/CAN-SPAM-conscious cold + warm templates that
represent Schild Inc with good manners and protect sender reputation:

  • Personalized greeting via {{greeting_name}} (owner first name → company →
    "there") so a cold email never reads "Hi ,".
  • Short, consultative, low-pressure copy with one clear soft CTA.
  • Real signature (name + title + company + site) and a compliant footer with
    the company's physical address, reply-to, and one-click unsubscribe.
  • Lean HTML, few links — better deliverability, less spam-folder risk.

Merge fields (resolved per-recipient by app/email_engine.py):
    {{greeting_name}} {{first_name}} {{company_name}} {{contact_name}}
    {{city}} {{country}} {{website}} {{sender_name}} {{sender_title}}
    {{reply_to}} {{company_legal_name}} {{company_address}} {{company_phone}}
    {{company_website}} {{unsubscribe_url}}

`{{unsubscribe_url}}` is filled per recipient; the open pixel + click tracking
are injected automatically at send time — do NOT add them manually.

Bump STARTER_SEED_VERSION when editing any template; the seeder re-writes
starter rows whose seed_version is lower (user-created templates untouched).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailTemplate

STARTER_SEED_VERSION = 2

COMMON_FIELDS = (
    "greeting_name, first_name, company_name, contact_name, city, country, "
    "website, sender_name, sender_title, reply_to, company_address, "
    "company_phone, company_website"
)


def _wrap(preheader: str, inner_html: str) -> str:
    """Wrap body content in the Schild Inc branded shell with a professional
    signature and a compliant footer. Keep it lean for deliverability.
    """
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f4ef;font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1f2933;">
    <span style="display:none!important;opacity:0;color:#f5f4ef;height:0;overflow:hidden;">{preheader}</span>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f4ef;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e7e1d6;">
          <tr>
            <td style="background:#101010;padding:18px 32px;">
              <span style="color:#e4c977;font-size:18px;font-weight:bold;letter-spacing:0.5px;">SCHILD INC</span>
              <span style="color:#9b9483;font-size:12px;float:right;padding-top:5px;">Premium metal labels &amp; branded accessories</span>
            </td>
          </tr>
          <tr>
            <td style="padding:26px 32px 6px 32px;font-size:15px;line-height:1.65;">
              {inner_html}
            </td>
          </tr>
          <tr>
            <td style="padding:6px 32px 22px 32px;font-size:15px;line-height:1.55;color:#1f2933;">
              Kind regards,<br>
              <strong>{{{{sender_name}}}}</strong><br>
              <span style="color:#5b6470;">{{{{sender_title}}}} · Schild Inc</span><br>
              <a href="{{{{company_website}}}}" style="color:#946f16;text-decoration:none;">{{{{company_website}}}}</a>
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 22px 32px;">
              <div style="border-top:1px solid #e7dfd0;padding-top:12px;color:#9aa0a6;font-size:12px;line-height:1.55;">
                {{{{company_legal_name}}}} · {{{{company_address}}}}<br>
                You received this email because we believe Schild Inc may be relevant to {{{{company_name}}}}.
                If it isn't, we're sorry for the interruption — you can
                <a href="{{{{unsubscribe_url}}}}" style="color:#9aa0a6;">unsubscribe in one click</a>
                and we won't contact you again. Replies reach a real person at
                <a href="mailto:{{{{reply_to}}}}" style="color:#9aa0a6;">{{{{reply_to}}}}</a>.
              </div>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


def _text(body: str) -> str:
    """Plain-text twin with the same compliant signature/footer."""
    return (
        body.rstrip()
        + "\n\nKind regards,\n{{sender_name}}\n{{sender_title}}, Schild Inc\n{{company_website}}\n\n"
        + "--\n{{company_legal_name}} · {{company_address}}\n"
        + "You received this because we believe Schild Inc may be relevant to {{company_name}}. "
        + "Not relevant? Unsubscribe: {{unsubscribe_url}} — replies go to {{reply_to}}."
    )


# ── 1. COLD — initial outreach (consultative, low-pressure) ─────────────────
COLD_INTRO_HTML = _wrap(
    "A premium finishing touch for the bikes leaving your workshop",
    """\
<p style="margin:0 0 14px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 14px 0;">I'll keep this short. I'm with Schild Inc — we make <strong>premium metal labels and branded accessories</strong> for bike shops, so the bikes leaving your workshop carry your name, not just the manufacturer's.</p>
<p style="margin:0 0 14px 0;">A lot of shops use them as a subtle quality signal in-store and as a small extra at the counter. If it's useful, I'd be glad to send a <strong>free first label design with your logo</strong> — no cost, no commitment, just so you can see how it looks.</p>
<p style="margin:0 0 4px 0;">Would that be worth a look for {{company_name}}?</p>""",
)
COLD_INTRO_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "I'll keep this short. I'm with Schild Inc — we make premium metal labels and "
    "branded accessories for bike shops, so the bikes leaving your workshop carry your "
    "name, not just the manufacturer's.\n\n"
    "A lot of shops use them as a subtle quality signal in-store and as a small extra at "
    "the counter. If it's useful, I'd be glad to send a free first label design with your "
    "logo — no cost, no commitment, just so you can see how it looks.\n\n"
    "Would that be worth a look for {{company_name}}?"
)


# ── 2. WARM — known/interested lead ─────────────────────────────────────────
WARM_INTRO_HTML = _wrap(
    "Following up on your interest in Schild Inc",
    """\
<p style="margin:0 0 14px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 14px 0;">Thanks for showing interest in Schild Inc — I wanted to follow up personally.</p>
<p style="margin:0 0 14px 0;">We help bike retailers like {{company_name}} stand out with <strong>premium metal labels and branded accessories</strong>. As a no-obligation next step I can send a <strong>free label mock-up with your logo</strong>, along with pricing for the volume you'd expect.</p>
<p style="margin:0 0 4px 0;">Shall I put a first design together for you?</p>""",
)
WARM_INTRO_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "Thanks for showing interest in Schild Inc — I wanted to follow up personally.\n\n"
    "We help bike retailers like {{company_name}} stand out with premium metal labels and "
    "branded accessories. As a no-obligation next step I can send a free label mock-up with "
    "your logo, along with pricing for the volume you'd expect.\n\n"
    "Shall I put a first design together for you?"
)


# ── 3. COLD follow-up (polite, easy out) ────────────────────────────────────
COLD_FOLLOWUP_HTML = _wrap(
    "A quick follow-up — and an easy way to say no",
    """\
<p style="margin:0 0 14px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 14px 0;">Just floating my earlier note back to the top — inboxes get busy, and I don't want to be a nuisance.</p>
<p style="margin:0 0 14px 0;">The offer still stands: a <strong>free first label design</strong> with your logo, no strings attached. If the timing isn't right, a one-line "not now" is completely fine and I'll leave it there.</p>""",
)
COLD_FOLLOWUP_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "Just floating my earlier note back to the top — inboxes get busy, and I don't want to "
    "be a nuisance.\n\n"
    "The offer still stands: a free first label design with your logo, no strings attached. "
    "If the timing isn't right, a one-line \"not now\" is completely fine and I'll leave it there."
)


# ── 4. WARM follow-up ───────────────────────────────────────────────────────
WARM_FOLLOWUP_HTML = _wrap(
    "Still happy to send that mock-up whenever you're ready",
    """\
<p style="margin:0 0 14px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 14px 0;">Circling back in case this slipped through. Plenty of shops in {{country}} use our branded labels to add a premium feel and a little extra at the counter.</p>
<p style="margin:0 0 4px 0;">Happy to send a <strong>free mock-up</strong> and a short overview whenever suits you — just say the word.</p>""",
)
WARM_FOLLOWUP_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "Circling back in case this slipped through. Plenty of shops in {{country}} use our "
    "branded labels to add a premium feel and a little extra at the counter.\n\n"
    "Happy to send a free mock-up and a short overview whenever suits you — just say the word."
)


# ── 5. VIP / multi-location partnership ─────────────────────────────────────
VIP_HTML = _wrap(
    "A partnership idea for multi-location retailers",
    """\
<p style="margin:0 0 14px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 14px 0;">Given {{company_name}}'s presence across multiple locations, I'd love to explore a <strong>partnership</strong> rather than a one-off order.</p>
<p style="margin:0 0 8px 0;">For multi-store retailers and buying groups we offer:</p>
<ul style="margin:0 0 16px 0;padding-left:20px;">
  <li>Volume pricing across all your stores</li>
  <li>A dedicated account contact</li>
  <li>Consistent branding on labels &amp; accessories chain-wide</li>
</ul>
<p style="margin:0 0 4px 0;">Would a short 15-minute call be worth your time? I'm happy to work around your schedule.</p>""",
)
VIP_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "Given {{company_name}}'s presence across multiple locations, I'd love to explore a "
    "partnership rather than a one-off order.\n\n"
    "For multi-store retailers and buying groups we offer:\n"
    "- Volume pricing across all your stores\n"
    "- A dedicated account contact\n"
    "- Consistent branding on labels & accessories chain-wide\n\n"
    "Would a short 15-minute call be worth your time? I'm happy to work around your schedule."
)


STARTER_TEMPLATES = [
    {
        "name": "Cold — Initial Outreach",
        "category": "cold",
        "description": "First touch for net-new bike shops. Consultative, low-pressure, owner-personalized.",
        "subject": "A premium finishing touch for {{company_name}}",
        "body_html": COLD_INTRO_HTML,
        "body_text": COLD_INTRO_TEXT,
    },
    {
        "name": "Warm — Known Lead Intro",
        "category": "warm",
        "description": "For leads who showed interest or whose details we hold.",
        "subject": "Following up — a free label mock-up for {{company_name}}",
        "body_html": WARM_INTRO_HTML,
        "body_text": WARM_INTRO_TEXT,
    },
    {
        "name": "Cold — Follow-up",
        "category": "followup",
        "description": "Polite nudge ~5 days after a cold intro, with an easy opt-out.",
        "subject": "Quick follow-up, {{greeting_name}}",
        "body_html": COLD_FOLLOWUP_HTML,
        "body_text": COLD_FOLLOWUP_TEXT,
    },
    {
        "name": "Warm — Follow-up",
        "category": "followup",
        "description": "Re-engage a warm lead who opened but didn't reply.",
        "subject": "Still happy to send that mock-up, {{greeting_name}}",
        "body_html": WARM_FOLLOWUP_HTML,
        "body_text": WARM_FOLLOWUP_TEXT,
    },
    {
        "name": "VIP — Multi-location Partnership",
        "category": "vip",
        "description": "For franchises, buying groups, and multi-store retailers.",
        "subject": "Partnership idea for {{company_name}}",
        "body_html": VIP_HTML,
        "body_text": VIP_TEXT,
    },
]


def seed_starter_templates(session: Session) -> int:
    """Idempotently insert/update the built-in starter templates.

    Matches on (name, is_starter=True). Re-writes content only when the stored
    seed_version is older than STARTER_SEED_VERSION. Returns rows touched.
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
                    name=spec["name"], category=spec["category"], description=spec["description"],
                    subject=spec["subject"], body_html=spec["body_html"], body_text=spec["body_text"],
                    merge_fields=COMMON_FIELDS, is_active=True, is_starter=True,
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
