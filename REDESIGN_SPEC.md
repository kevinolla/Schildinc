# Schildinc B2B Engine — Final Architecture & Design Recommendation

You built for a team of five, then found yourself operating solo with Trengo owning the inbox. The tool drifted. This document is the cut list, the redesign, and the migration — pick this up and execute in three weeks.

Core thesis: **the app has three populations and one output.** KVK is the cold pool. Customers is the exclusion set. Leads is the inbound triage. Campaigns is where they converge. Everything else is drift. Trengo owns replies. Klaviyo, WhatsApp, Instagram, and the shared inbox all die.

---

# 1. Feature scope — what stays, what dies

## Stays (the five, and their essential support)

| Feature | Surface | Why it earns its slot |
|---|---|---|
| **KVK search** | `/search` + record detail | Highest-frequency task. Must be one-click from anywhere. |
| **Auto crawler** | `/crawler` dashboard + review queue | Always-on daemon. Users watch it, don't operate it — deserves a dashboard, not a settings toggle. |
| **Customers** | `/customers` read-mostly list | Explicit exclusion set. Framed as reference, not CRM. |
| **Leads** | `/leads` inbound triage | Different mental model from Customers (warm, classified, needs action). Merging muddies both. |
| **Campaigns** | `/campaigns` wizard + detail | The output. Four-step wizard, live preview, dry-run gates every send. |
| Suppression | `/settings/suppression` | Required for cold-mail compliance. Behind Settings, not top-nav. |
| Gmail OAuth + tracking pixels | Under Campaigns | Load-bearing for Feature 5. |
| Stripe webhook | Backend only | Keeps `customers` fresh without manual imports. |

## Dies (with one-line justification)

- **Shared inbox (`/inbox`)** — Trengo owns replies. Two inboxes = neither gets checked.
- **Contact Hub (`/contacts`)** — Blurs the KVK/Customer/Lead distinction we're deliberately keeping.
- **WhatsApp + Instagram webhooks** — Trengo owns them; the code here has never sent a message in anger.
- **LinkedIn manual helper** — Zero automation, zero volume, negative maintenance ROI.
- **Legacy `/prospects` (Google Places, 68 rows)** — Superseded by KVK crawler. Merge usable rows once, drop table.
- **Old Dutch `/queue`** — Duplicated by `/campaigns` with worse suppression handling.
- **Klaviyo push** — You send campaigns yourself now. Klaviyo push = second source of truth.
- **Multi-step sequences** — Not in the five. Replace with post-send "Follow up non-openers" button in Campaign detail.
- **Split review queues (discovery / match / tier)** — Users think "records that need my eyes," not internal reasons. Collapse into one queue.
- **Per-agent login + roles + audit UI** — You're solo. Keep the audit *log* behind Settings; delete the agents table.
- **Gmail two-way polling (`gmail.readonly`)** — Replies go to Trengo. Delete the poll loop.
- **Brave/Bing search fallbacks** — SearXNG is the default. Dead code with live env vars.
- **Canned replies, WhatsApp templates, notifications table** — Die with the inbox.
- **Personalization LLM + fact extraction + lead scoring** — Gated OFF today. Replace with rule-based `{{opener}}` snippets in Campaigns.
- **`/reports` and `/setup` pages** — Reports collapse into Campaign-detail funnel. Setup collapses into first-run empty state on `/crawler`.
- **`/logs` page** — Move to Settings, low priority.

Net: **35 pages → 11**, **141 routes → ~40**, **38 tables → 12**, **[main.py](Schildinc/app/main.py) 4708 lines → ~1200**.

---

# 2. New information architecture

## Top-level nav (5 items, plus avatar menu)

```
┌──────────────────────────────────────────────────────────────────┐
│  SCHILD     🔍 Search   📡 Crawler   👥 Customers   📥 Leads   ✉ Campaigns          ⌘K   🔔    @Kevin  │
└──────────────────────────────────────────────────────────────────┘
```

- **🔍 Search** — universal KVK lookup, one input, `⌘K` from anywhere
- **📡 Crawler** — live queue dashboard + review pile
- **👥 Customers** — exclusion reference, read-mostly
- **📥 Leads** — inbound triage with unread badge
- **✉ Campaigns** — draft/scheduled/sending list

Settings, Suppression, Audit log, Gmail connection, and Sender profile live behind the avatar dropdown. They exist; they don't compete for attention.

## URL structure

```
/                        → redirects to /crawler (or first-run wizard)
/search                  KVK type-ahead lookup
/search?q=…              Deep-linkable results
/crawler                 Daemon dashboard + directory table
/crawler/review          Ambiguous records needing eyes
/crawler/{kvk_id}        Single KVK detail
/customers               Filterable list + analytics
/customers/{id}          Detail + order history
/leads                   Inbound triage
/leads/{id}              Detail
/campaigns               List
/campaigns/new           4-step wizard (audience → message → personalize → review)
/campaigns/{id}          Detail: live progress or post-send analytics
/campaigns/templates     Template library
/settings/*              Suppression, Gmail, sender profile, audit log
/e/o/{token}.gif  /e/c/{token}  /e/u/{token}     Tracking + unsubscribe (public)
```

## Three primary flows

1. **Lookup → Add to draft.** Type in top-bar (or `⌘K`), pick a business, see contact + client-status badge, hit `A` or click "Add to campaign" → floating drawer confirms which draft.
2. **Cold batch.** `/campaigns/new` → pick source (KVK / Leads / CSV) → filter → live count with dedup breakdown → template → merge-tag health → dry-run three real recipients → schedule.
3. **Watch the pipeline.** `/crawler` shows throughput sparkline, hit rate, queue depth, needs-review count. Drill only when needed. Header pulse dot (green/amber/red) is visible from every page.

---

# 3. Page-by-page design (5 core pages)

## 3.1 `/search` — KVK lookup

**Purpose.** Answer "does this business exist, what's its email, and is it already a customer?" in under two seconds.

```
┌──────────────────────────────────────────────────────────────────┐
│  🔍  bakkerij de vries                                     [Esc] │
├──────────────────────────────────────────────────────────────────┤
│  Recent searches                                                 │
│    Molen & Meel BV                            KVK 87654321       │
│    Lumen Agency                               KVK 12345678       │
│                                                                  │
│  Results (12)                                                    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Bakkerij De Vries       Amsterdam · Bakery              │    │
│  │ ✉ info@devries.nl  ✓ verified   ● Not a customer        │    │
│  │                                       [A] Add  [E] Copy │    │
│  ├──────────────────────────────────────────────────────────┤    │
│  │ Bakkerij De Vries Zuid  Utrecht · Bakery                │    │
│  │ ✉ (guessed)            ● Customer since 2023            │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

**Primary CTA.** `Add to campaign` (per row). **Secondary.** Copy email, Open detail, Flag bad data, Re-run enrichment.
**Empty state.** "Type a business name or KVK number." Recent searches below (last 10, persisted in cookie).
**Key components.** Universal top-bar search input, result card, verified/guessed badge, customer-status pill (three states: `Not a customer`, `Customer`, `Suppressed`).
**Shortcuts.** `/` focus, `j/k` navigate results, `A` add to campaign, `E` copy email, `Enter` open detail, `Esc` close.

## 3.2 `/crawler` — live queue

**Purpose.** Watch throughput; intervene only on anomalies.

```
┌──────────────────────────────────────────────────────────────────┐
│  Crawler       ● Running · 3 workers · started 14:22             │
│                                          [Pause]  [Workers ▾]    │
├──────────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │  142     │ │   68%    │ │   1,204  │ │    27    │             │
│  │  today   │ │ hit rate │ │ in queue │ │ ⚠ review │             │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘             │
│                                                                  │
│  Throughput (14d)  ▁▂▃▂▄▅▄▆▅▇▆█▇█                                │
│                                                                  │
│  Now processing                                                  │
│   ⟳ Molen & Meel BV — resolving website (searxng)                │
│   ⟳ De Wit Bakkerij — guessing email (mx check)                  │
│                                                                  │
│  Recent (filter: sector ▾ · country ▾ · status ▾)                │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ ☐  Bakker Jan       Amsterdam  ✉ jan@…  ✓ Enriched  14:31│    │
│  │ ☐  Molen & Meel     Utrecht    ✉ info@… ⚠ Review    14:31│    │
│  │ ☐  De Wit           Rotterdam  —        ○ No contact 14:30│   │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

**Primary CTA.** `Pause` / `Resume` (single click, no confirm).
**Secondary.** Adjust workers, Prioritize sector, Open review queue (badge count in nav).
**Empty state.** "Crawler isn't running. Start it to begin auto-enriching the KVK pool." Single `Start crawler` button.
**Components.** Status pulse, four metric tiles, sparkline, live "now processing" list (SSE or 3s poll), directory table with checkbox column.
**Shortcuts.** `Space` pause/resume, `g r` go to review, `j/k` rows, `x` select, `⇧X` range select, `e` export selected, `a` add to campaign.

## 3.3 `/customers` — exclusion reference

**Purpose.** Answer "is X a customer?" in one keystroke, and confirm the exclusion set is healthy.

```
┌──────────────────────────────────────────────────────────────────┐
│  Customers · 3,256                                               │
│  ⓘ These businesses are automatically excluded from campaigns.   │
├──────────────────────────────────────────────────────────────────┤
│  [ sector ▾ ] [ country ▾ ] [ segment ▾ ] [ first order ▾ ]      │
│  Chips:  Bakery ✕   Netherlands ✕                    Clear all   │
├──────────────────────────────────────────────────────────────────┤
│  ☐  Name              Domain          Sector    Last order       │
│  ☐  Bakkerij De Wit   dewit.nl        Bakery    2 wks ago        │
│  ☐  Molen & Meel      molenmeel.nl    Bakery    3 mo ago         │
│  ☐  ...                                                          │
├──────────────────────────────────────────────────────────────────┤
│  (bulk bar appears when >0 selected)                             │
│  3 selected → [Export CSV] [Suppress] [Merge duplicate]          │
└──────────────────────────────────────────────────────────────────┘
```

**Primary CTA.** `Export CSV`.
**Secondary.** Suppress selection, Merge duplicates, Open detail.
**Empty state.** "No customers imported. Upload CSV or connect Stripe."
**Components.** Info banner (always visible), chip filters, table, sticky bulk bar, side rail with saved views.
**Shortcuts.** `/` filter search, `j/k`, `x`, `e` export, `Enter` open.

## 3.4 `/leads` — inbound triage

**Purpose.** Turn inbound noise into a call list.

```
┌──────────────────────────────────────────────────────────────────┐
│  Leads · 8,504  (● 24 new since Monday)                          │
├──────────────────────────────────────────────────────────────────┤
│  [ Unread ▣ ] [ sector ▾ ] [ source ▾ ] [ since ▾: last 7d ]     │
├──────────────────────────────────────────────────────────────────┤
│  ● Molenaar B.V.       Bakery   FB Lead Ads    12m ago  ▸        │
│  ● Café Zoet           F&B      Webform        1h ago   ▸        │
│  ○ Bloem & Meel        Bakery   FB Lead Ads    yesterday▸        │
├──────────────────────────────────────────────────────────────────┤
│  ┌ Detail drawer (opens right, no navigation) ─────────────┐     │
│  │ Molenaar B.V.                                           │     │
│  │ ✉ jan@molenaar.nl · +31 6 …                             │     │
│  │ Sector: Bakery (auto, 0.82 conf.)                       │     │
│  │ Source: FB Lead Ads → "bakery-flour-nl" · 12m ago       │     │
│  │ Matched KVK: 87654321                                   │     │
│  │ Timeline:                                               │     │
│  │   12m — Received via FB                                 │     │
│  │   11m — Classified as Bakery                            │     │
│  │ [Add to campaign] [Promote to customer] [Suppress]      │     │
│  └─────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────┘
```

**Primary CTA.** `Add to campaign`.
**Secondary.** Promote to customer, Reclassify sector, Suppress, Merge.
**Empty state.** "No inbound leads yet. Wire up the webform snippet or connect the FB sheet." Two buttons.
**Components.** Unread dot + since-last-visit badge, chip filters (`Unread` toggle prominent), row list with right-drawer detail.
**Shortcuts.** `j/k`, `Enter` open drawer, `Esc` close, `u` mark read/unread, `a` add to campaign, `p` promote.

## 3.5 `/campaigns/new` — the flagship (details in §5)

**Purpose.** Get a safe, personal-feeling cold batch out in under 10 minutes.

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Campaigns  ·  Untitled campaign [rename]   Draft · saved 2s   │
│  ●───── Audience ───── Message ───── Personalize ───── Review    │
├─────────────────────────────────┬────────────────────────────────┤
│  Step body                      │  Live preview                  │
│                                 │  Sample: Anna de Vries [▾]     │
│                                 │  From: Kevin <kevin@…>         │
│                                 │  Subject: Quick question…      │
│                                 │  Hi Anna, …                    │
├─────────────────────────────────┴────────────────────────────────┤
│  [Back]                        [Save & exit]      [Continue →]   │
└──────────────────────────────────────────────────────────────────┘
```

Primary CTA per step: `Continue →`. Terminal CTA on step 4: `Send campaign →`. See §5 for full walkthrough.

---

# 4. Visual design system

Cool blue-biased ink, cobalt accent, single accent everywhere. Copy the `:root` block into `app/static/css/app.css` and delete every hardcoded color in the templates.

```css
:root {
  /* Ink scale — cool, blue-biased neutrals */
  --ink-0:  #ffffff;
  --ink-50: #f7f9fc;
  --ink-100:#eef2f7;
  --ink-200:#dde3ec;
  --ink-300:#c1cad6;
  --ink-400:#98a3b3;
  --ink-500:#6b7787;
  --ink-600:#4b5566;
  --ink-700:#333b49;
  --ink-800:#1f2530;
  --ink-900:#101520;

  /* Accent — cobalt */
  --accent-50:  #eaf1ff;
  --accent-100: #cddfff;
  --accent-300: #7aa8ff;
  --accent-500: #2f6bf5;   /* primary */
  --accent-600: #1f56d4;
  --accent-700: #1a46ad;

  /* Semantic */
  --success: #1a8f5a;
  --warn:    #b6790b;
  --danger:  #c8321e;
  --info:    #2f6bf5;

  /* Surfaces */
  --bg:        var(--ink-50);
  --surface:   var(--ink-0);
  --surface-2: var(--ink-100);
  --border:    var(--ink-200);
  --text:      var(--ink-800);
  --text-mute: var(--ink-500);

  /* Type */
  --font-sans: ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, sans-serif;
  --font-mono: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
  --fs-xs: 0.75rem;   --lh-xs: 1rem;
  --fs-sm: 0.875rem;  --lh-sm: 1.25rem;
  --fs-md: 1rem;      --lh-md: 1.5rem;
  --fs-lg: 1.125rem;  --lh-lg: 1.625rem;
  --fs-xl: 1.375rem;  --lh-xl: 1.875rem;
  --fs-2xl:1.75rem;   --lh-2xl:2.125rem;

  /* Space */
  --sp-1: 0.25rem; --sp-2: 0.5rem; --sp-3: 0.75rem;
  --sp-4: 1rem;    --sp-6: 1.5rem; --sp-8: 2rem;
  --sp-12:3rem;    --sp-16:4rem;

  /* Radii + elevation */
  --r-sm: 4px; --r-md: 8px; --r-lg: 12px; --r-pill: 999px;
  --sh-1: 0 1px 2px rgba(16,21,32,.06);
  --sh-2: 0 4px 12px rgba(16,21,32,.08);
  --sh-3: 0 12px 32px rgba(16,21,32,.14);

  /* Motion */
  --dur-fast: 120ms; --dur-med: 200ms; --dur-slow: 320ms;
  --ease: cubic-bezier(.2,.7,.2,1);
}

:root[data-theme="dark"] {
  --bg:        #0b0f18;
  --surface:   #131826;
  --surface-2: #1a2030;
  --border:    #262d3d;
  --text:      #e6ebf5;
  --text-mute: #8f9ab0;
  --sh-1: 0 1px 2px rgba(0,0,0,.4);
  --sh-2: 0 4px 12px rgba(0,0,0,.5);
  --sh-3: 0 12px 32px rgba(0,0,0,.6);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { color-scheme: dark; }
}
```

**Component checklist (every one already wired in the design system artifact).** Button (primary / secondary / ghost / danger), input, select, chip filter, table row, checkbox, radio, toggle, tab, dropdown, popover, dialog (used sparingly — only destructive confirmations), toast, banner (info / warn / danger), status pill (`Enriched · Pending · No contact · Needs review · Client · Suppressed` — one palette, app-wide), metric tile, sparkline, skeleton loader, empty state block, kbd shortcut key, avatar menu.

**Hero: the redesigned `/crawler` dashboard.** A single visible pulse dot in the top bar tells you at a glance the daemon is healthy. Four metric tiles above the fold (today's count, hit rate, queue depth, review count) give the state of the world in under a second. A fourteen-day throughput sparkline underneath answers "are we speeding up or slowing down." Below that, a live "now processing" ticker shows two or three records with a spinner — proof of life. The directory table sits at the bottom, filtered by default to the last 24 hours, chip filters above, sticky bulk bar below when rows are selected. No modals. No settings buried three clicks deep. If the crawler stops, a red banner announces it with a `Restart` button. That's the whole page.

---

# 5. The flagship: cold campaign UX

Four-step wizard. Persistent right-rail preview. Auto-save every 800ms. Progress bar with `todo / active / done / warn` states. Global chrome shown in §3.5.

## Screen 1 — Audience

**Layout.** Left: source picker → filter builder → dedup breakdown. Right: preview initializes as soon as sample recipient exists.

```
Who are you emailing?
  ( ) KVK crawler queue      1,204 fresh · updated 3h ago
  ( ) Leads                    482 in pipeline
  ( ) Customers                318 · use with care
  ( ) Upload CSV               max 5,000 rows

Filters
  Country      [Netherlands ▾]        ✕
  Industry     [any of ▾] [Bakery, Agency ▾]
  Has website  [yes ▾]
  Last emailed [never ▾]      ← safety default
  [+ Add filter]                       [Save as segment]

Matching now: 312 recipients
  → 18 removed by suppression list
  →  4 removed as duplicates of another active campaign
  →  3 unsubscribed previously (excluded, cannot include)
  → 287 will be contacted
```

Buttons: `+ Add filter`, `Save as segment`, `Review the 47 →`, `Include them anyway` (soft-confirm inline), `Continue →`.

Customer source shows amber banner: `⚠ You're about to email existing customers. Consider a different channel.` — non-blocking.

Continue disabled until `will be contacted ≥ 1`. Hover tooltip: `"Pick at least 1 recipient to continue"`.

## Screen 2 — Message

Two-column: editor left, live preview right. The right rail becomes primary interaction here.

```
Start from a template?
  [First-touch cold intro]  [Follow-up after silence]  [Value-drop]  [Blank]
                                                       [✕ Don't show]

Subject line
[Quick question about {{company_name}}                    ]  52/78
Spam hint: ✓ Looks natural

[Insert merge tag ▾]  [Plain text | HTML]  [Preview: Anna ▾]

Hi {{contact_name|there}},

I was looking at {{website}} and noticed …

Would 15 minutes next week make sense?

{{sender_name}}

Footer (required, cannot remove)
Sent by Kevin at Schild Inc. Not interested? [Unsubscribe]
```

Buttons: `Insert merge tag`, `Plain text / HTML`, `Set fallbacks…`, `Customize sender info`, `Continue →`.

Plain text is default; switching to HTML shows amber `Plain text usually lands better for cold outreach.` — non-blocking.

Continue rule: subject + body both non-empty. Local spam heuristic (ALL CAPS, `!!`, `FREE`, `$$$`, "act now", excessive emoji) — flags in three tiers, never blocks.

## Screen 3 — Personalize

Explicit health check + per-row fix table + rule-based openers. **No LLM.**

```
Personalization health
  {{company_name}}   ✓ 287/287 will merge
  {{contact_name}}   ⚠ 275/287 · 12 will use "there"
  {{city}}           ✓ 287/287
  {{website}}        ⚠ 262/287 · 25 will use "your site"
  [Fix missing data →]

Recipients (287)    [Search…]  [Filter: has issues ▾]
┌────┬──────────────┬─────────┬───────┬──────────┬──────┐
│  ⚠ │ Lumen Agency │ (miss.) │ A'dam │ lumen.nl │ [👁] │
│  ✓ │ Bloom Studio │ Sanne   │ Utr.  │ bloom.co │ [👁] │
│  ⚠ │ Waaghals BV  │ Tom     │ (miss)│ (miss.)  │ [👁] │
└────┴──────────────┴─────────┴───────┴──────────┴──────┘

Custom opener (optional)                    [+ Add opener rule]
  Rule 1: if city = Amsterdam → "Saw you're based in Amsterdam…"  47 recip.
  Rule 2: if industry = Agency → "Working with a lot of agencies…" 112 recip.
  Default: "Hope your week's going well."                          128 recip.
Insert into body with {{opener}}.
```

Click cell to edit inline (Enter commits, Esc cancels). Bulk edit fills blanks only, never overwrites. Buttons: `Fix missing data`, `+ Add opener rule`, `Continue →`.

Nudge if body has no merge tags: `"Cold emails without personalization typically get 3–5× fewer replies. [Add {{company_name}} to subject] [Skip anyway]"` — non-blocking.

## Screen 4 — Review & Send

```
Ready to send

Audience         287 recipients · from KVK crawler queue · 3 filters
Message          "Quick question about {{company_name}}" · plain · 142 words
Personalization  4 merge tags · 1 opener rule · 37 fallbacks in use
From             Kevin <kevin@schildinc.com>

  [Edit audience]  [Edit message]  [Edit personalize]

When should we send?
  ( ) Send now
  (•) Schedule
      Date [Wed 10 Jul]  Time [09:00] Amsterdam
      ✓ Weekday morning — good default for B2B

Throttle
  80/day · 8s spacing → finishes Fri 12 Jul ~11:24
  [Adjust throttle]

Preview 3 real emails                             [Refresh 🔄]
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ Anna de Vries │ │ Tom Bakker    │ │ Sanne Klein   │
│ Lumen Agency  │ │ Waaghals BV   │ │ Bloom Studio  │
│ Subject: …    │ │ Subject: …    │ │ Subject: …    │
│ Hi Anna, …    │ │ Hi Tom, …     │ │ Hi Sanne, …   │
│ [Open full]   │ │ [Open full]   │ │ [Open full]   │
└───────────────┘ └───────────────┘ └───────────────┘
[Send test to me → kevin@schildinc.com]

Deliverability
  Sender reputation  ● Good
  Daily quota        24/80 used today · 56 left
                     Will send 56 today, 80 tomorrow, remainder Fri
  Domain auth        ✓ SPF  ✓ DKIM  ⚠ DMARC (p=none) [Show DNS]
  Warmup             ○ Not running [Start warmup]

[Back]                                  [Save & exit]  [Send campaign →]
```

Terminal button label switches to `Schedule campaign` when scheduled. Clicking it produces an inline soft-confirm (not modal):

`You're about to email 287 recipients starting Wed 10 Jul, 09:00. Cannot be paused before the first send.  [Cancel]  [Confirm send]`

**Blockers** (button disabled with tooltip): DNS auth failing, sender email unverified, provider token expired. **Nags** (allowed): warmup off + reputation `New`, red spam-score subject.

## Screen 5 — Sending

Progress bar with per-day breakdown, live activity feed, `Pause / Resume` (soft-confirm), `Cancel` (type-name confirm — the only modal in the app).

```
Q3 Dutch SaaS outreach              ● Sending · started 09:00

████████████░░░░░░░░░░░░░░░  47/287 sent
56 today · 80 tomorrow · 80 Fri · 71 Sat (finish ~Sat 12:04)

[Pause]  [Cancel campaign]                  [Send test to me]

Recent activity                             [Only failures ▾]
09:24 ✓ Sent  anna@lumen-agency.nl
09:24 ⚠ Skipped hans@somesite.com — suppression re-check
09:24 ✗ Bounced maria@old-domain.nl — hard bounce
```

## Screen 6 — Post-send analytics

Metric tiles compared to the **sender's own** historical average (not industry benchmarks — reps trust their own numbers). Timeline chart (opens/clicks/replies, 24h buckets). Per-recipient drawer with timeline. Flagship "Follow up" block:

```
Follow up
[Target non-openers (168)]  [Target openers who didn't click (86)]
[Target clickers who didn't reply (12)]
```

Clicking creates a new draft campaign pre-loaded with the segment and drops you on Step 2. Toast: `"Started a follow-up to 168 non-openers. Edit the message and continue."`

This is the multi-touch sequences replacement — 80% of the value at 20% of the complexity.

---

# 6. Backend simplification

## Modules to delete outright

- [app/audit.py](Schildinc/app/audit.py)
- [app/contacts.py](Schildinc/app/contacts.py)
- [app/inbox.py](Schildinc/app/inbox.py)
- [app/gmail_inbound.py](Schildinc/app/gmail_inbound.py)
- [app/whatsapp.py](Schildinc/app/whatsapp.py)
- [app/instagram.py](Schildinc/app/instagram.py)
- [app/reporting.py](Schildinc/app/reporting.py)
- [app/sequences.py](Schildinc/app/sequences.py)
- [app/sequence_library.py](Schildinc/app/sequence_library.py)
- [app/personalization.py](Schildinc/app/personalization.py)
- [app/lead_scoring.py](Schildinc/app/lead_scoring.py)
- [app/enrichment_facts.py](Schildinc/app/enrichment_facts.py)
- [app/fact_extract.py](Schildinc/app/fact_extract.py)
- [app/emailing.py](Schildinc/app/emailing.py) (old queue)
- [app/jobs.py](Schildinc/app/jobs.py) (old queue jobs)
- [app/klaviyo.py](Schildinc/app/klaviyo.py), [app/klaviyo_sync.py](Schildinc/app/klaviyo_sync.py)
- [app/outreach_templates.py](Schildinc/app/outreach_templates.py)
- [app/brave_search.py](Schildinc/app/brave_search.py), [app/bing_search.py](Schildinc/app/bing_search.py) (dead fallbacks)

Also delete templates: `audit.html`, `contacts.html`, `contact_detail.html`, `inbox.html`, `inbox_settings.html`, `login.html`, `logs.html`, `prospects.html`, `prospect_detail.html`, `queue.html`, `queue_preview.html`, `reports.html`, `review_*.html` (5 files), `sequences.html`, `sequence_enrollment.html`, `setup.html`.

## Modules to keep, refactored

Consolidate discovery: fold [app/discovery_open.py](Schildinc/app/discovery_open.py), [app/enrichment_open.py](Schildinc/app/enrichment_open.py), [app/web_extract.py](Schildinc/app/web_extract.py) into a single `app/services/discovery.py`. Merge send: [app/email_engine.py](Schildinc/app/email_engine.py), [app/email_library.py](Schildinc/app/email_library.py), [app/email_providers.py](Schildinc/app/email_providers.py), [app/gmail_sender.py](Schildinc/app/gmail_sender.py) into `app/services/sender.py`. Keep [app/kvk_enrichment.py](Schildinc/app/kvk_enrichment.py) as the daemon core.

## Tables to drop

`prospects`, `outreach_queue_items`, `prospect_activity_logs`, `email_logs`, `webhook_logs`, `contacts`, `contact_channels`, `activities`, `agents`, `conversations`, `messages`, `message_attachments`, `notifications`, `canned_replies`, `whatsapp_templates`, `audit_logs`, `enrichment_facts`, `lead_scores`, `personalizations`, `email_sequences`, `sequence_steps`, `sequence_enrollments`, `sequence_emails`.

**Keeper 12:** `customers`, `invoices`, `kvk_companies`, `kvk_establishments`, `kvk_import_logs`, `facebook_leads`, `suppression_entries`, `email_templates`, `email_campaigns`, `email_campaign_recipients`, `email_events`, `gmail_accounts`.

Drop columns: `Customer.match_key_domain`, `.website_domain_candidate`, `.customer_name_variants`, `.customer_email_variants`; `KvkCompany.headquarters_required`, `.franchise_or_buying_group`, `.recommended_sales_angle`, `.recommended_contact_type`. Keep `KvkCompany.approved_for_outreach` (still gates campaign audience).

## Dead-weight migrations

Alembic is linear — don't rewrite history. Write **one squash rev** `0026_reduce_to_five_features.py` that drops FKs then tables in the right order. Revs 0002, 0003, 0004, 0006, 0014, 0015, 0016, 0017 (partial), 0019, 0020, 0022 (partial), 0023, 0024, 0025 become semantic no-ops after 0026 lands.

## New folder tree

```
app/
├── main.py                    # ~1200 lines: lifespan, auth, dashboard redirect
├── config.py                  # trimmed
├── db.py
├── models.py                  # 12 tables
├── auth.py                    # HTTP Basic only
├── routers/
│   ├── search.py              # /search + typeahead API
│   ├── crawler.py             # /crawler + /crawler/review + progress API + agent poll
│   ├── customers.py           # /customers + analytics + Stripe webhook + imports
│   ├── leads.py               # /leads + webform + FB import
│   ├── campaigns.py           # /campaigns + wizard + Gmail OAuth
│   ├── tracking.py            # /e/o /e/c /e/u (public)
│   └── settings.py            # /settings/suppression, /settings/audit
└── services/
    ├── discovery.py           # discovery + enrichment + web extract
    ├── matching.py            # STRICT rules
    ├── sender.py              # engine + templates + provider + gmail
    ├── crawler_daemon.py      # renamed from kvk_enrichment.py
    ├── facebook_leads.py
    └── lead_classifier.py
```

## Env vars to retire

`WHATSAPP_*`, `INSTAGRAM_*`, `GMAIL_INBOUND_*`, `SEQUENCE_*`, `PERSONALIZATION_*`, `ANTHROPIC_API_KEY`, `DISCOVERY_FACTS_*`, `FACT_*`, `LEAD_SCORING_*`, `DISCOVERY_RECALL_*`, `DISCOVERY_MAX_QUERY_VARIANTS`, `KLAVIYO_*`, `SESSION_SECRET`, `SESSION_TTL_HOURS`, `AUTO_CONTACT_DISCOVERY_*`, `AUTO_CONTACT_REFRESH_*`, `DAILY_SEND_LIMIT` (old queue), `DEFAULT_QUEUE_SIZE`, `SEND_WINDOW_START/END`, `OUTREACH_COOLDOWN_DAYS`, `PREVIEW_CONTACT_COUNT`, `CAMPAIGN_ACTIVE`, `OFFICIAL_INSTAGRAM_HANDLE`, `OFFICIAL_LINKEDIN_URL`, `BRAVE_API_KEY`, `BRAVE_DAILY_LIMIT`.

Keep the rest as listed in the Code Surgery stream.

---

# 7. Migration plan (three weeks, non-breaking cutover)

## Week 1 — Ship the new shell without deleting anything

- Day 1–2: Drop the new [app/static/css/app.css](Schildinc/app/static/css/app.css) token block; rebuild [base.html](Schildinc/app/templates/base.html) with the new nav (five items) but keep every existing route mounted. Old templates render inside the new shell — ugly but functional.
- Day 3: Extract routers under `app/routers/` for the five keepers; leave the deleted-page routes in [main.py](Schildinc/app/main.py) temporarily. `git grep` for broken imports.
- Day 4–5: Redesign `/crawler` dashboard (metric tiles + sparkline + directory table). This is the most-visited page — get it right first.
- Weekend: `railway up`, smoke-test in prod, confirm daemons still tick.

## Week 2 — Redesign the surfaces, keep old routes 302'ing

- Day 6–7: New `/search`, new `/customers`, new `/leads`.
- Day 8–10: Rewrite `/campaigns/new` as the 4-step wizard. Preserve the existing send loop and tracking endpoints unchanged. Add the `{{opener}}` rule engine (small pure function, no LLM). Add local spam heuristic. Add dry-run panel with three random real recipients.
- Day 11: Add `Follow up` block to campaign detail post-send. Wire the three preset segments to a new draft.
- Day 12: 302-redirect old paths (`/prospects`, `/queue`, `/inbox`, `/contacts`, `/sequences`, `/reports`, `/audit`, `/setup`, `/review/*`) to the closest survivor.

## Week 3 — Delete, drop, reduce

- Day 13: Delete module files listed in §6. Delete templates. Rip WhatsApp/Instagram/inbound-Gmail webhook routes.
- Day 14: Write [alembic/versions/0026_reduce_to_five_features.py](Schildinc/alembic/versions/0026_reduce_to_five_features.py) — drops FKs to `prospects`, then drops the 22 tables. `alembic upgrade head` locally against a prod dump first.
- Day 15: Retire env vars in Railway. Re-run smoke test. Confirm `railway logs` clean.
- Day 16: Run in prod. If crawler throughput and campaign send rate match pre-cutover 24h numbers → cutover confirmed.
- Day 17: Delete the redirect stubs; final [main.py](Schildinc/app/main.py) should be ~1200 lines.

Rollback: revert to the pre-week-3 tag. All week 1 + 2 changes are additive; only week 3 is destructive.

---

# 8. What we did NOT recommend and why

1. **Not a React rewrite.** Explicit user constraint. Jinja2 + a small vanilla-JS layer (a `hx-`-style attribute or 200 lines of `fetch` helpers) delivers the wizard, live preview, and typeahead without a bundler on Railway. The design system CSS token block does 80% of the "feels modern" work.
2. **Not multi-step drip sequences.** The `Follow up non-openers` button on the campaign detail page covers the actual use case (one bump after silence) without a new object, a new scheduler, or a new state machine on top of every wizard step.
3. **Not LLM personalization.** You gated it off, and rule-based `{{opener}}` snippets with segmented conditions produce better cold-email copy than the median LLM opener anyway. When you want it back, add a `Rewrite with AI` button on the message editor — one API call, not a whole subsystem.
4. **Not unified "Contacts" hub.** Merging KVK/Customer/Lead into one object was the drift that broke the previous version. The three populations behave differently, need different filters, and need different exclusion semantics. Keeping them apart is the honest model.
5. **Not per-agent login / roles.** You're solo. HTTP Basic + a single admin cred is fine. When a teammate joins, add a `is_admin` boolean — don't rebuild the agents/sessions/audit UI on speculation.
6. **Not command palette as day-one work.** `⌘K` gets a stub that opens `/search` in week 1; upgrade to a real palette (search across KVK + Customers + Leads with `type:` prefix) in a later cycle.
7. **Not A/B subject-line testing.** Would double the wizard's state machine. Ship the single-variant flow; when send volume justifies it (>50 campaigns), add variants as a second subject field, not a new object.
8. **Not real-time SSE dashboards.** 3-second polling on `/crawler` metrics is indistinguishable from live at Railway's latency and doesn't need a new dependency. Same for campaign send progress.
9. **Not "unified inbox" replacement.** Trengo owns replies. Don't rebuild it. The reply notification on the campaign live feed is enough — click through to Trengo.
10. **Not Klaviyo push.** Second source of truth for sends creates suppression and analytics drift. If you need Klaviyo, export a CSV once from `/customers`.

The whole shape: **eleven pages, twelve tables, ~40 routes, one wizard, one daemon.** Each of the five features gets exactly one place to live and one primary CTA. That's the tool.

Files with load-bearing references:
- [C:/Users/Kevin/AI Workspace/Schildinc/app/main.py](Schildinc/app/main.py)
- [C:/Users/Kevin/AI Workspace/Schildinc/app/models.py](Schildinc/app/models.py)
- [C:/Users/Kevin/AI Workspace/Schildinc/app/config.py](Schildinc/app/config.py)
- [C:/Users/Kevin/AI Workspace/Schildinc/app/kvk_enrichment.py](Schildinc/app/kvk_enrichment.py)
- [C:/Users/Kevin/AI Workspace/Schildinc/app/email_engine.py](Schildinc/app/email_engine.py)
- [C:/Users/Kevin/AI Workspace/Schildinc/app/facebook_leads.py](Schildinc/app/facebook_leads.py)
- [C:/Users/Kevin/AI Workspace/Schildinc/alembic/versions/](Schildinc/alembic/versions/) (write `0026_reduce_to_five_features.py`)
- [C:/Users/Kevin/AI Workspace/Schildinc/app/templates/base.html](Schildinc/app/templates/base.html) (new nav + token block)
- Design system reference: `C:\Users\Kevin\AppData\Local\Temp\claude\C--Users-Kevin-AI-Workspace\6bfd5782-8be9-4920-85de-c3643603908f\scratchpad\design-system.html`