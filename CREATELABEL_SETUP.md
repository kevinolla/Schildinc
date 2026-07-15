# createlabel.com — cold-email sending domain + redirect to schildinc.com

`createlabel.com` is your dedicated **cold-outreach** domain. Sending cold email from it (instead of
schildinc.com) keeps your main brand domain's reputation clean if a cold campaign ever gets spam
complaints. It's already set as the **default "Send from"** identity in the app (shows a "verify in
Resend" badge until you finish the steps below).

Current DNS (NameBright): parked A-record + `v=spf1 -all` (blocks all mail). Two jobs below — make it
send via Resend, and make the website redirect to schildinc.com.

---

## Part A — Make createlabel.com send via Resend

1. **Resend** → https://resend.com/domains → **Add Domain** → `createlabel.com`, region **EU (eu-west-1)**.
2. Resend shows a DNS table. Add these at **NameBright** (Manage Domain → DNS / Advanced DNS):

   | Type | Host / Name | Value | Notes |
   |---|---|---|---|
   | **MX** | `send` | `feedback-smtp.eu-west-1.amazonses.com` (priority 10) | bounce handling |
   | **TXT** | `send` | `v=spf1 include:amazonses.com ~all` | SPF for the send subdomain |
   | **TXT** | `resend._domainkey` | `p=MIGf…` (long key — **copy from the Resend dashboard**) | DKIM |
   | **TXT** | `_dmarc` | `v=DMARC1; p=none;` | recommended |

3. **Fix the root SPF.** The domain currently has `v=spf1 -all` on the root (`@`) — that hard-fails
   mail claiming to be from `@createlabel.com`. **Replace it** with:
   ```
   v=spf1 include:amazonses.com ~all
   ```
   (Resend signs with DKIM and aligns DMARC, but a clean root SPF avoids edge-case failures.)
4. Back in Resend, click **Verify** (green in 5–30 min after DNS propagates).
5. Tell the app it's allowed to send — in Railway set/append:
   ```
   SEND_VERIFIED_DOMAINS=schildinc.com,createlabel.com
   ```
6. **Set up reply handling.** The app sends from `ruben@createlabel.com` and replies go there. Since
   createlabel.com has no inbox yet, either:
   - add **email forwarding** at NameBright: `ruben@createlabel.com` → your Trengo address / a monitored
     mailbox, **or**
   - override the reply-to in Railway: `SEND_CREATELABEL_COM_REPLY_TO=sales@schildinc.com`
     (any Schild-owned address is accepted; replies then land in your existing inbox).
   You can also change the From address with `SEND_CREATELABEL_COM_FROM` / `_NAME`.
7. Send yourself a test from the campaign screen (pick 1 recipient = your own email, "Send from" =
   createlabel.com) and confirm it lands in the inbox, not spam.

> Warm-up: a brand-new sending domain should ramp gradually — ~20–40/day the first week, then raise.
> The app's daily cap (`GMAIL_DAILY_LIMIT`, default 80) already throttles this.

---

## Part B — Redirect createlabel.com → schildinc.com (website visits)

This is independent of email (email uses the `send.` subdomain; the redirect uses the root `@` + `www`).
Pick one:

### Option 1 — NameBright URL forwarding (simplest)
1. NameBright → the domain → look for **URL Forwarding / Web Forwarding / Redirect**.
2. Forward `createlabel.com` **and** `www.createlabel.com` → `https://schildinc.com`, type **permanent (301)**.
3. Test in an incognito window. If NameBright's forwarder doesn't serve valid HTTPS on the bare domain,
   use Option 2.

### Option 2 — Cloudflare (free, reliable HTTPS 301)
1. Add `createlabel.com` to a free Cloudflare account; it imports your DNS. **Keep the Resend `send`
   records + DKIM** when reviewing.
2. Change nameservers at NameBright to the two Cloudflare ones. Wait for "Active".
3. Add proxied A records `@` and `www` → `192.0.2.1` (dummy IP, orange-cloud on).
4. **Rules → Redirect Rules → Create:** when Hostname equals `createlabel.com` or `www.createlabel.com`
   → Dynamic redirect → `concat("https://schildinc.com", http.request.uri.path)`, status **301**,
   preserve query string.

### Option 3 — Redirect through this app (already coded)
The app already 301-redirects `createlabel.com` → schildinc.com (`REDIRECT_HOSTS` includes it). To use
it, point createlabel.com's `@`/`www` at the Railway app (Railway → service → Networking → Custom
Domain gives a CNAME target). Trade-off: routes web traffic through the app — fine for a redirect, but
Option 1/2 keeps it off your app entirely.

---

## Summary of what's already done in the app
- `createlabel.com` is the **first/default** sending identity (`app/sending_domains.py`).
- It's in `REDIRECT_HOSTS` so Option 3 works out of the box.
- Reply-to allowlist accepts createlabel.com.
- **You** still need: the Resend DNS + verify (Part A) and the redirect (Part B), and to add
  createlabel.com to `SEND_VERIFIED_DOMAINS` once green.
