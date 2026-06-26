"""Regression-lock + dry-run tests for the live send loop.

These tests pin the two promises of the DESIGN_V2 foundation phase:
  1. A dry_run campaign renders previews but NEVER touches a sender or the
     suppression path (short-circuits at the top of send_campaign_batch).
  2. The live (dry_run=False) path is unchanged: it ALWAYS re-checks suppression
     via the emailing.is_suppressed choke point and then calls the sender; a
     suppressed recipient is never sent.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

from app import email_engine
from app.models import EmailCampaign, EmailCampaignRecipient


def _make_campaign(db, *, dry_run: bool):
    campaign = EmailCampaign(
        name="Test Batch",
        status="sending",
        dry_run=dry_run,
        subject="Hi {{company_name}}",
        body_html="<p>Hello {{company_name}}</p>",
        body_text="Hello {{company_name}}",
        sender_alias="sales@schildinc.com",
        sender_name="Schild Inc",
        reply_to="sales@schildinc.com",
    )
    db.add(campaign)
    db.flush()
    recipient = EmailCampaignRecipient(
        campaign_id=campaign.id,
        to_email="info@shop.nl",
        company_name="Shop BV",
        contact_name="",
        merge_data='{"company_name": "Shop BV"}',
        tracking_token="tok-test-1",
        status="pending",
    )
    db.add(recipient)
    db.commit()
    return campaign, recipient


def test_dry_run_renders_but_never_sends(db_session, monkeypatch):
    sender = MagicMock()
    suppression = MagicMock(return_value=(False, ""))
    provider = MagicMock()
    monkeypatch.setattr(email_engine, "send_message", sender)
    monkeypatch.setattr(email_engine, "is_suppressed", suppression)
    import app.email_providers as email_providers
    monkeypatch.setattr(email_providers, "send", provider)

    campaign, recipient = _make_campaign(db_session, dry_run=True)
    result = email_engine.send_campaign_batch(db_session, campaign)
    db_session.refresh(recipient)
    db_session.refresh(campaign)

    assert result["dry_run"] is True
    assert result["sent"] == 0
    sender.assert_not_called()           # no Gmail send
    provider.assert_not_called()         # no provider send
    suppression.assert_not_called()      # short-circuits before suppression
    assert recipient.status == "pending"          # nothing actually sent
    assert "Shop BV" in recipient.dry_run_preview_html  # preview rendered
    assert campaign.status == "draft"             # left the sending loop


def test_live_send_checks_suppression_then_sends(db_session, monkeypatch):
    monkeypatch.setattr(email_engine, "get_active_account", lambda s: object())
    monkeypatch.setattr(email_engine.time, "sleep", lambda *a, **k: None)
    suppression = MagicMock(return_value=(False, ""))
    monkeypatch.setattr(email_engine, "is_suppressed", suppression)
    ok = types.SimpleNamespace(ok=True, message_id="m1", provider="gmail", transient=False, error="")
    sender = MagicMock(return_value=ok)
    monkeypatch.setattr(email_engine, "send_message", sender)

    campaign, recipient = _make_campaign(db_session, dry_run=False)
    result = email_engine.send_campaign_batch(db_session, campaign)
    db_session.refresh(recipient)

    suppression.assert_called_once()     # the emailing.is_suppressed choke point
    sender.assert_called_once()
    assert recipient.status == "sent"
    assert result["sent"] == 1


def test_live_send_skips_suppressed_recipient(db_session, monkeypatch):
    monkeypatch.setattr(email_engine, "get_active_account", lambda s: object())
    monkeypatch.setattr(email_engine.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(email_engine, "is_suppressed", MagicMock(return_value=(True, "on list")))
    sender = MagicMock()
    monkeypatch.setattr(email_engine, "send_message", sender)

    campaign, recipient = _make_campaign(db_session, dry_run=False)
    email_engine.send_campaign_batch(db_session, campaign)
    db_session.refresh(recipient)

    sender.assert_not_called()           # suppressed -> never sent
    assert recipient.status == "suppressed"
