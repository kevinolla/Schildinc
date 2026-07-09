# Multi-domain email + website redirects

You can now send campaigns from three brand identities, chosen per campaign on
the "New Campaign" screen under **Send from**:

| Identity | From address | Replies go to | Status |
|---|---|---|---|
| Schild Inc | `sales@schildinc.com` | `sales@schildinc.com` | ✅ verified (already sending) |
| Schild Labels | `sales@schildlabels.com` | `sales@schildlabels.com` | ⚠ needs Resend + DNS setup |
| Schild Inc (NL) | `verkoop@schildinc.nl` | `verkoop@schildinc.nl` | ⚠ needs Resend + DNS setup |

The app **blocks sending** from a domain until you mark it verified (see step 4),
so a half-configured domain can never quietly send un-authenticated mail and
hurt your reputation.

Addresses are overridable without a deploy — e.g. set `SEND_SCHILDLABELS_COM_FROM=info@schildlabels.com`
in Railway. Full env list at the bottom.

---

## Part A — Add each new domain to Resend (do this twice: schildlabels.com, schildinc.nl)

Your API key is send-only (a good thing), so this is done in the dashboard.

1. Go to **https://resend.com/domains** → **Add Domain**.
2. Enter `schildlabels.com` (then repeat for `schildinc.nl`). Pick region
   **EU (eu-west-1)** — closest to your recipients in NL/DE.
3. Resend shows a **DNS records** table. Add each row at your DNS host
   (hostnet.nl for both domains). The records are all on the `send.` subdomain
   and a DKIM key — they do **not** touch your existing MX, so your current
   inbox on hostnet keeps working. The records are:

   | Type | Name / Host | Value | Notes |
   |---|---|---|---|
   | **MX** | `send` | `feedback-smtp.eu-west-1.amazonses.com` (priority 10) | bounce handling |
   | **TXT** | `send` | `v=spf1 include:amazonses.com ~all` | SPF for the send subdomain |
   | **TXT** | `resend._domainkey` | `p=MIGfMA0…` (long key — **copy from the dashboard**) | DKIM signature |
   | **TXT** | `_dmarc` | `v=DMARC1; p=none;` | recommended; only add if you don't already have a `_dmarc` record |

   > At **hostnet.nl**: log in → *Domeinnamen* → pick the domain → *DNS beheren*.
   > For the "Name/Host", hostnet usually wants just the subdomain part
   > (`send`, `resend._domainkey`, `_dmarc`) — it appends the domain for you.
   > Set TTL to the lowest option (e.g. 300) while verifying.

4. Back in Resend, click **Verify**. It goes green in 5–30 min (DNS
   propagation). Once green, tell the app it's allowed to send from it — in
   Railway set/append:
   ```
   SEND_VERIFIED_DOMAINS=schildinc.com,schildlabels.com,schildinc.nl
   ```
   (Add only the domains that are actually green. The "verify in Resend" badge
   on the campaign form disappears for each domain you list here.)

5. Send yourself a test: New Campaign → pick 1 recipient (your own address) →
   choose the new **Send from** identity → **Send test**. Confirm it lands in
   the inbox (not spam) and the From/Reply-To read correctly.

### Important: keep your existing inbox working
`schildlabels.com` and `schildinc.nl` currently receive mail via hostnet
(`mailfilter.hostnet.nl`). The Resend records above only add an MX on the
**`send.` subdomain** and a DKIM TXT — they never change your root MX, so
incoming mail to `verkoop@schildinc.nl` etc. is unaffected. Make sure someone
actually monitors those mailboxes, since replies will land there.

---

## Part B — Redirect schildlabels.com and schildinc.nl → schildinc.com

Goal: anyone visiting `schildlabels.com` or `schildinc.nl` in a browser lands on
`https://schildinc.com`. This is independent of email (email uses the `send.`
subdomain; the redirect uses the root `@` and `www`).

Pick **one** of these. Cloudflare is the most reliable (free, real HTTPS,
proper 301). Choose it if you're comfortable changing nameservers.

### Option 1 — Cloudflare (recommended, free, HTTPS 301)
1. Create a free Cloudflare account → **Add site** `schildlabels.com`.
2. Cloudflare imports your existing DNS. **Verify the Resend `send` records +
   your hostnet MX all came across** before continuing (so email keeps working).
3. Change the domain's nameservers at hostnet to the two Cloudflare
   nameservers Cloudflare shows you. Wait for "Active" (up to a few hours).
4. Ensure there's a proxied DNS record for the root so the redirect has
   something to attach to: add an **A** record `@` → `192.0.2.1` (a dummy IP;
   Cloudflare only needs it "proxied" 🟠 to run the rule) and an **A** record
   `www` → `192.0.2.1`, both proxied.
5. **Rules → Redirect Rules → Create rule**:
   - When: *Hostname* `equals` `schildlabels.com` OR `www.schildlabels.com`
   - Then: *Dynamic redirect* → `concat("https://schildinc.com", http.request.uri.path)`
   - Status **301**, **Preserve query string** on.
6. Repeat all steps for `schildinc.nl`.

### Option 2 — hostnet.nl built-in URL forwarding (no nameserver change)
Keeps DNS at hostnet — simplest if it supports HTTPS forwarding.
1. hostnet control panel → the domain → look for *URL doorsturen* /
   *Webforward* / *Redirect*.
2. Forward `schildlabels.com` **and** `www.schildlabels.com` → `https://schildinc.com`,
   type **permanent (301)**.
3. Repeat for `schildinc.nl`.
4. Test in an incognito window. If hostnet's forwarder doesn't serve valid
   HTTPS on the bare domain (some don't), use Option 1 or 3 instead.

### Option 3 — Redirect through this app (Railway)
The app already knows how to 301 these hosts to schildinc.com (a middleware
checks the `Host` header). To use it, point the domains at Railway:
1. Railway → the `schild-prospect-engine` service → **Settings → Networking →
   Custom Domain** → add `schildlabels.com`, `www.schildlabels.com`,
   `schildinc.nl`, `www.schildinc.nl`. Railway shows a CNAME target.
2. At hostnet, add **CNAME** `@`/`www` → the Railway target (or an ALIAS/ANAME
   for the root if hostnet supports it). Railway auto-issues HTTPS.
3. Set `REDIRECT_HOSTS=schildlabels.com,schildinc.nl` in Railway (already the
   default — see below). Any request to those hosts 301s to
   `https://schildinc.com` + the original path.
   > Trade-off: this routes brand-domain web traffic through the app container.
   > Fine for a redirect, but Option 1 keeps it off your app entirely.

---

## Environment variables (all optional — sensible defaults shipped)

| Var | Default | Purpose |
|---|---|---|
| `SEND_VERIFIED_DOMAINS` | `schildinc.com` | Comma list of Resend-verified domains allowed to send. Add each domain once it's green. |
| `SEND_SCHILDINC_COM_FROM` / `_NAME` / `_REPLY_TO` | `sales@schildinc.com` / `Schild Inc` / `sales@schildinc.com` | Override the primary identity |
| `SEND_SCHILDLABELS_COM_FROM` / `_NAME` / `_REPLY_TO` | `sales@schildlabels.com` / `Schild Labels` / `sales@schildlabels.com` | Override the Labels identity |
| `SEND_SCHILDINC_NL_FROM` / `_NAME` / `_REPLY_TO` | `verkoop@schildinc.nl` / `Schild Inc` / `verkoop@schildinc.nl` | Override the NL identity |
| `REDIRECT_HOSTS` | `schildlabels.com,schildinc.nl` | Hosts the app 301-redirects to schildinc.com (Option 3 only) |
| `REDIRECT_TARGET` | `https://schildinc.com` | Where those hosts redirect to |

Reply-To safety: the send layer only accepts a Reply-To on a Schild-owned
domain (schildinc.com / schildlabels.com / schildinc.nl). Anything else is
forced back to `sales@schildinc.com`, so a mis-built campaign can't leak
replies to an outside address.
