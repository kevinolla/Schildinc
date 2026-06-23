# Deploying SearXNG + Photon (the non-Google discovery infrastructure)

The app's open discovery stack needs two small self-hosted services:

| Service | Purpose in the app | Env var the app reads |
|---|---|---|
| **SearXNG** | business **name → website** meta-search (no Google API/cost) | `SEARXNG_URL` |
| **Photon** | **address → coordinates / region** geocoding (no public Nominatim) | `GEOCODER_URL` (+ `GEOCODER_PROVIDER=photon`) |

Until these are set, the app **degrades gracefully**: name→website search returns
nothing (rows land in the Discovery review queue), and geocoding is skipped.
Nothing breaks; you just don't get automatic website discovery yet.

> Recommended order: deploy SearXNG first (biggest payoff), confirm discovery
> works, then add Photon only if you actually need geocoding/region matching.

---

## Option A — Railway (matches your current hosting)

### A1. SearXNG on Railway
1. Railway → **New → Deploy from Docker Image** → image: `searxng/searxng:latest`.
2. Add a **Volume** mounted at `/etc/searxng` (holds config).
3. Variables:
   - `SEARXNG_BASE_URL = https://<your-searxng>.up.railway.app/`
   - `SEARXNG_SECRET = <run: openssl rand -hex 32>`
4. **Enable the JSON API** (the app needs JSON, SearXNG ships HTML-only by default).
   In `/etc/searxng/settings.yml` add:
   ```yaml
   search:
     formats:
       - html
       - json
   server:
     limiter: false          # off for server-to-server use on a private URL
     secret_key: "<same as SEARXNG_SECRET>"
   ```
   (Edit via the mounted volume, then redeploy.)
5. Deploy. Test the JSON API:
   ```bash
   curl "https://<your-searxng>.up.railway.app/search?q=fietsenwinkel+utrecht&format=json" | head -c 300
   ```
   You should get JSON with a `results` array.
6. In the **main app** service variables set:
   ```
   SEARXNG_URL=https://<your-searxng>.up.railway.app
   SEARXNG_ENGINES=bing,duckduckgo,brave,mojeek
   DISCOVERY_ENGINE=open
   ```
   (Tip: prefer non-Google engines like bing/duckduckgo/brave/mojeek to keep it
   truly Google-independent and avoid Google rate-blocks.)

### A2. Photon on Railway (optional)
Photon needs a prebuilt OpenSearch index (large). Two paths:
- **Country extract (recommended):** use a Photon image with the **Netherlands**
  extract only (a few GB) instead of the full planet (~75GB). Deploy
  `rtuszik/photon-docker` (or build from `komoot/photon`), attach a Volume for
  the index, set the country to `nl` on first run so it downloads only the NL data.
- Variables on the main app:
  ```
  GEOCODER_PROVIDER=photon
  GEOCODER_URL=https://<your-photon>.up.railway.app
  ```
- Test: `curl "https://<your-photon>.up.railway.app/api?q=Amsterdam&limit=1"`.

> Railway disk for a full-planet Photon index is costly. If you only need NL,
> the country extract keeps it cheap. If geocoding isn't essential yet, **skip
> Photon** — the app works without it.

---

## Option B — Any Docker host / VPS (cheapest for Photon)

`docker-compose.yml`:
```yaml
services:
  searxng:
    image: searxng/searxng:latest
    ports: ["8080:8080"]
    volumes: ["./searxng:/etc/searxng"]
    environment:
      - SEARXNG_BASE_URL=http://localhost:8080/
    restart: unless-stopped

  photon:                       # optional; NL extract
    image: rtuszik/photon-docker:latest
    environment:
      - COUNTRY_CODE=nl         # downloads only the Netherlands index on first run
    volumes: ["./photon-data:/photon/photon_data"]
    ports: ["2322:2322"]
    restart: unless-stopped
```
Then on the app: `SEARXNG_URL=http://<host>:8080`, `GEOCODER_URL=http://<host>:2322`.
Remember to enable the JSON format in `./searxng/settings.yml` as in A1.4.

---

## Wiring it into the app (recap)

App service env (Railway → Variables):
```
DISCOVERY_ENGINE=open
SEARXNG_URL=https://<searxng-host>
SEARXNG_ENGINES=bing,duckduckgo,brave,mojeek
SEARXNG_TIMEOUT_S=8
# optional:
GEOCODER_PROVIDER=photon
GEOCODER_URL=https://<photon-host>
DISCOVERY_REVIEW_THRESHOLD=60   # below this confidence -> manual review queue
DISCOVERY_AUTOPICK_SCORE=80     # at/above this -> auto-accept the website
```
No redeploy of the app code is needed beyond setting the vars — the modules read
them at runtime via `getattr`.

## Verify end-to-end
1. App → **Review Workflow → Discovery queue** → **Run discovery on next 25**.
2. Watch the rows flip from `needs_review`/`no_website` to `discovered`/`partial`
   as websites + emails are found. Low-confidence picks stay in the queue with
   their candidate query for a human to confirm.
3. Run **Match review → Scan for possible customers** to populate the suppression
   queue, then confirm/dismiss.
4. **Tier review** → Auto/assign tiers. **Outreach-ready** → Approve →
   **Campaigns** → send.

## Operating notes / gotchas
- **Enable JSON in SearXNG** or the app gets HTML it can't parse (most common mistake).
- **Avoid the `google` engine inside SearXNG** for bulk — it gets rate-limited/blocked; bing/duckduckgo/brave/mojeek are more reliable server-side.
- **Rate-limit politely:** discovery crawls live sites; keep batch sizes modest (the "next 25" button) and let it run in the background.
- **Photon disk:** full planet ≈ 75 GB; the **NL country extract** is the cheap, correct choice for a Dutch list.
- **Cost:** SearXNG is tiny (a small container). Photon's cost is disk for the index. Both are one-time setup, no per-call fees — that's the whole point vs. Google APIs.
- **Fallback:** set `DISCOVERY_ENGINE=google` to revert to the legacy Google enrichment path at any time (rollback-safe).
