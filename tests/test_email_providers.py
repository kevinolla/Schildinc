"""Unit tests for app/email_providers.py.

These tests are PURE — no real network, no real SMTP, no DB infra. Every
transport's actual wire call (httpx.post / smtplib.SMTP / gmail_sender.send_message)
is monkeypatched, so the suite runs fully offline.

What we assert:
  - Reply-To is ALWAYS forced to settings.reply_to_email, even when the caller
    passes a hostile reply_to, across every provider.
  - Provider selection follows settings.mail_provider.
  - An unconfigured provider returns ok=False (no crash).
  - The gmail (API) provider routes through gmail_sender.send_message and maps
    its result.
  - Unknown/empty provider falls back to console (ok=True).
  - transient is set only for retryable failures (timeouts / 5xx / 429).
"""
from __future__ import annotations

import sys
import types
from dataclasses import replace

import pytest

import app.email_providers as ep
from app.config import settings as real_settings


# ── settings monkeypatch helper ──────────────────────────────────────────────
#
# settings is a frozen dataclass, so we build a replaced copy per test and patch
# it onto the module under test. getattr(...) reads in the module pick it up.


@pytest.fixture
def patch_settings(monkeypatch):
    """Return a function that applies setting overrides for the duration of a test.

    Usage:  patch_settings(mail_provider="resend", resend_api_key="x")
    """

    def _apply(**overrides):
        # Brevo / gmail_smtp settings are added by the integrator later; the real
        # dataclass may not have them yet. dataclasses.replace would reject
        # unknown fields, so for those we attach via a lightweight shim object
        # that mirrors the real settings plus any extras.
        known = {f for f in real_settings.__dataclass_fields__}  # type: ignore[attr-defined]
        base_overrides = {k: v for k, v in overrides.items() if k in known}
        extra_overrides = {k: v for k, v in overrides.items() if k not in known}

        new_settings = replace(real_settings, **base_overrides)

        if extra_overrides:
            # Wrap in a simple namespace that proxies attribute access to the
            # frozen dataclass but overlays the extra (future) settings.
            class _SettingsProxy:
                def __getattr__(self, name):
                    if name in extra_overrides:
                        return extra_overrides[name]
                    return getattr(new_settings, name)

            patched = _SettingsProxy()
        else:
            patched = new_settings

        monkeypatch.setattr(ep, "settings", patched)
        return patched

    return _apply


# ── Recording fakes for each transport ───────────────────────────────────────


class _Recorder:
    """Captures the last call's kwargs/payload for assertions."""

    def __init__(self):
        self.payload = None
        self.kwargs = None
        self.calls = 0


@pytest.fixture
def fake_httpx(monkeypatch):
    """Install a fake ``httpx`` module so _send_resend never hits the network."""
    rec = _Recorder()

    class _Resp:
        def __init__(self, status_code=200, json_body=None, text=""):
            self.status_code = status_code
            self._json = json_body if json_body is not None else {"id": "resend-123"}
            self.text = text or "ok"

        def json(self):
            return self._json

    def make_module(*, status_code=200, raise_exc=None):
        mod = types.ModuleType("httpx")

        def _post(url, json=None, headers=None, timeout=None):
            rec.calls += 1
            rec.payload = json
            rec.kwargs = {"url": url, "headers": headers, "timeout": timeout}
            if raise_exc is not None:
                raise raise_exc
            return _Resp(status_code=status_code)

        mod.post = _post  # type: ignore[attr-defined]
        return mod

    def install(*, status_code=200, raise_exc=None):
        monkeypatch.setitem(
            sys.modules, "httpx", make_module(status_code=status_code, raise_exc=raise_exc)
        )
        return rec

    return install


@pytest.fixture
def fake_smtp(monkeypatch):
    """Install a fake ``smtplib`` module so SMTP providers never open a socket."""
    rec = _Recorder()

    def make_module(*, raise_on=None):
        mod = types.ModuleType("smtplib")

        # Provide the exception classes the code references.
        class SMTPException(Exception):
            pass

        class SMTPConnectError(SMTPException):
            pass

        class SMTPServerDisconnected(SMTPException):
            pass

        class _SMTP:
            def __init__(self, host, port, timeout=None):
                rec.calls += 1
                rec.kwargs = {"host": host, "port": port, "timeout": timeout}
                rec.payload = {"login": None, "messages": []}
                if raise_on == "connect":
                    raise SMTPConnectError("could not connect")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self):
                pass

            def login(self, user, password):
                rec.payload["login"] = (user, password)

            def send_message(self, message):
                rec.payload["messages"].append(message)
                if raise_on == "send":
                    raise SMTPException("550 mailbox unavailable")

        mod.SMTP = _SMTP  # type: ignore[attr-defined]
        mod.SMTPException = SMTPException  # type: ignore[attr-defined]
        mod.SMTPConnectError = SMTPConnectError  # type: ignore[attr-defined]
        mod.SMTPServerDisconnected = SMTPServerDisconnected  # type: ignore[attr-defined]
        return mod

    def install(*, raise_on=None):
        monkeypatch.setitem(sys.modules, "smtplib", make_module(raise_on=raise_on))
        return rec

    return install


@pytest.fixture
def fake_gmail(monkeypatch):
    """Install a fake ``app.gmail_sender`` module for the gmail (API) provider."""
    rec = _Recorder()

    class GmailNotConnected(Exception):
        pass

    class _GmailSendResult:
        def __init__(self, ok=True, message_id="gmail-msg-1", error="", transient=False):
            self.ok = ok
            self.message_id = message_id
            self.error = error
            self.transient = transient

    def make_module(*, result=None, raise_not_connected=False):
        mod = types.ModuleType("app.gmail_sender")
        mod.GmailNotConnected = GmailNotConnected  # type: ignore[attr-defined]

        def _send_message(session, **kwargs):
            rec.calls += 1
            rec.kwargs = kwargs
            if raise_not_connected:
                raise GmailNotConnected("No Gmail account connected.")
            return result or _GmailSendResult()

        mod.send_message = _send_message  # type: ignore[attr-defined]
        return mod

    def install(*, result=None, raise_not_connected=False):
        monkeypatch.setitem(
            sys.modules,
            "app.gmail_sender",
            make_module(result=result, raise_not_connected=raise_not_connected),
        )
        return rec, _GmailSendResult

    return install


# ── Reply-To forcing (the critical invariant) ────────────────────────────────

EVIL = "evil@attacker.example"
FORCED = "sales@schildinc.com"


def test_reply_to_forced_resend(patch_settings, fake_httpx):
    patch_settings(mail_provider="resend", resend_api_key="key", reply_to_email=FORCED)
    rec = fake_httpx(status_code=200)
    res = ep.send("to@x.com", "Subj", "<b>hi</b>", "hi", reply_to=EVIL)
    assert res.ok is True
    assert res.provider == "resend"
    # The payload Reply-To must be the forced address, NOT the caller's evil one.
    assert rec.payload["reply_to"] == FORCED
    assert rec.payload["reply_to"] != EVIL


def test_reply_to_forced_smtp(patch_settings, fake_smtp):
    patch_settings(mail_provider="smtp", smtp_host="mail.x.com", reply_to_email=FORCED)
    rec = fake_smtp()
    res = ep.send("to@x.com", "Subj", "<b>hi</b>", "hi", reply_to=EVIL)
    assert res.ok is True
    sent = rec.payload["messages"][0]
    assert sent["Reply-To"] == FORCED
    assert sent["Reply-To"] != EVIL


def test_reply_to_forced_brevo(patch_settings, fake_smtp):
    patch_settings(
        mail_provider="brevo",
        brevo_smtp_user="u",
        brevo_smtp_key="k",
        reply_to_email=FORCED,
    )
    rec = fake_smtp()
    res = ep.send("to@x.com", "Subj", "<b>hi</b>", "hi", reply_to=EVIL)
    assert res.ok is True
    assert res.provider == "brevo"
    assert rec.payload["messages"][0]["Reply-To"] == FORCED


def test_reply_to_forced_gmail_smtp(patch_settings, fake_smtp):
    patch_settings(
        mail_provider="gmail_smtp",
        gmail_smtp_user="me@gmail.com",
        gmail_smtp_app_password="apppw",
        reply_to_email=FORCED,
    )
    rec = fake_smtp()
    res = ep.send("to@x.com", "Subj", "<b>hi</b>", "hi", reply_to=EVIL)
    assert res.ok is True
    assert res.provider == "gmail_smtp"
    assert rec.payload["messages"][0]["Reply-To"] == FORCED


def test_reply_to_forced_gmail_api(patch_settings, fake_gmail):
    patch_settings(mail_provider="gmail", reply_to_email=FORCED)
    rec, _ = fake_gmail()
    res = ep.send("to@x.com", "Subj", "<b>hi</b>", "hi", reply_to=EVIL, session=object())
    assert res.ok is True
    assert res.provider == "gmail"
    # gmail_sender.send_message must have been called with the FORCED reply_to.
    assert rec.kwargs["reply_to"] == FORCED
    assert rec.kwargs["reply_to"] != EVIL


def test_reply_to_falls_back_when_setting_blank(patch_settings, fake_httpx):
    # Even if reply_to_email is blank, the hard constant must be used.
    patch_settings(mail_provider="resend", resend_api_key="key", reply_to_email="")
    rec = fake_httpx(status_code=200)
    ep.send("to@x.com", "Subj", "<b>hi</b>", "hi", reply_to=EVIL)
    assert rec.payload["reply_to"] == FORCED


# ── Provider selection ───────────────────────────────────────────────────────


def test_selection_resend(patch_settings, fake_httpx):
    patch_settings(mail_provider="resend", resend_api_key="key")
    fake_httpx(status_code=200)
    assert ep.send("to@x.com", "S", "<b>h</b>").provider == "resend"


def test_selection_smtp(patch_settings, fake_smtp):
    patch_settings(mail_provider="smtp", smtp_host="mail.x.com")
    fake_smtp()
    assert ep.send("to@x.com", "S", "<b>h</b>").provider == "smtp"


def test_selection_brevo(patch_settings, fake_smtp):
    patch_settings(mail_provider="brevo", brevo_smtp_user="u", brevo_smtp_key="k")
    fake_smtp()
    assert ep.send("to@x.com", "S", "<b>h</b>").provider == "brevo"


def test_selection_case_insensitive(patch_settings, fake_httpx):
    patch_settings(mail_provider="ReSeNd", resend_api_key="key")
    fake_httpx(status_code=200)
    assert ep.send("to@x.com", "S", "<b>h</b>").provider == "resend"


def test_selection_unknown_falls_back_to_console(patch_settings):
    patch_settings(mail_provider="does-not-exist")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is True
    assert res.provider == "console"


def test_selection_console_explicit(patch_settings):
    patch_settings(mail_provider="console")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is True
    assert res.provider == "console"


# ── Unconfigured providers return ok=False (no crash) ────────────────────────


def test_resend_unconfigured(patch_settings):
    patch_settings(mail_provider="resend", resend_api_key="")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.provider == "resend"
    assert res.transient is False
    assert "RESEND_API_KEY" in res.error


def test_smtp_unconfigured(patch_settings):
    patch_settings(mail_provider="smtp", smtp_host="")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.provider == "smtp"
    assert res.transient is False


def test_brevo_unconfigured(patch_settings):
    patch_settings(mail_provider="brevo", brevo_smtp_user="", brevo_smtp_key="")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.provider == "brevo"


def test_gmail_smtp_unconfigured(patch_settings):
    patch_settings(mail_provider="gmail_smtp", gmail_smtp_user="", gmail_smtp_app_password="")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.provider == "gmail_smtp"


def test_gmail_api_no_session(patch_settings):
    patch_settings(mail_provider="gmail")
    res = ep.send("to@x.com", "S", "<b>h</b>")  # no session passed
    assert res.ok is False
    assert res.provider == "gmail"
    assert res.transient is False


def test_gmail_api_not_connected(patch_settings, fake_gmail):
    patch_settings(mail_provider="gmail")
    fake_gmail(raise_not_connected=True)
    res = ep.send("to@x.com", "S", "<b>h</b>", session=object())
    assert res.ok is False
    assert res.provider == "gmail"
    assert res.transient is False  # not connected is a permanent (config) failure


# ── transient classification ─────────────────────────────────────────────────


def test_resend_5xx_is_transient(patch_settings, fake_httpx):
    patch_settings(mail_provider="resend", resend_api_key="key")
    fake_httpx(status_code=503)
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.transient is True


def test_resend_429_is_transient(patch_settings, fake_httpx):
    patch_settings(mail_provider="resend", resend_api_key="key")
    fake_httpx(status_code=429)
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.transient is True


def test_resend_400_is_permanent(patch_settings, fake_httpx):
    patch_settings(mail_provider="resend", resend_api_key="key")
    fake_httpx(status_code=400)
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.transient is False


def test_resend_timeout_is_transient(patch_settings, fake_httpx):
    patch_settings(mail_provider="resend", resend_api_key="key")
    fake_httpx(raise_exc=Exception("Read timeout while connecting"))
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.transient is True


def test_smtp_connect_error_is_transient(patch_settings, fake_smtp):
    patch_settings(mail_provider="smtp", smtp_host="mail.x.com")
    fake_smtp(raise_on="connect")
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.transient is True


def test_smtp_5xx_send_error_is_permanent(patch_settings, fake_smtp):
    patch_settings(mail_provider="smtp", smtp_host="mail.x.com")
    fake_smtp(raise_on="send")  # raises "550 mailbox unavailable"
    res = ep.send("to@x.com", "S", "<b>h</b>")
    assert res.ok is False
    assert res.transient is False


# ── gmail (API) result mapping ───────────────────────────────────────────────


def test_gmail_api_maps_success(patch_settings, fake_gmail):
    patch_settings(mail_provider="gmail")
    rec, result_cls = fake_gmail(result=None)  # default ok result
    res = ep.send("to@x.com", "S", "<b>h</b>", session=object())
    assert res.ok is True
    assert res.provider == "gmail"
    assert res.message_id == "gmail-msg-1"
    assert rec.calls == 1


def test_gmail_api_maps_transient_failure(patch_settings, fake_gmail):
    patch_settings(mail_provider="gmail")
    _, result_cls = fake_gmail(
        result=None, raise_not_connected=False
    )
    # Build a failing transient result and reinstall.
    failing = result_cls(ok=False, message_id="", error="rate limit", transient=True)
    rec, _ = fake_gmail(result=failing)
    res = ep.send("to@x.com", "S", "<b>h</b>", session=object())
    assert res.ok is False
    assert res.provider == "gmail"
    assert res.transient is True


# ── default selected_provider ────────────────────────────────────────────────


def test_selected_provider_default(monkeypatch):
    # When mail_provider is missing entirely, default is "gmail".
    class _Empty:
        pass

    monkeypatch.setattr(ep, "settings", _Empty())
    assert ep.selected_provider() == "gmail"
