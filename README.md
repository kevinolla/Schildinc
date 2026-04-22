# Schild Prospect Engine

A local web application for discovering companies from Google Maps-style search, researching their websites, deciding whether they fit Schild Inc, drafting personalized outreach, and saving the review queue.

## What it does

- Searches Google Places API for company name, website, phone number, business type, industry, address, and Maps link.
- Opens each company website and extracts readable business text from metadata, headings, and paragraphs.
- Scores fit against editable Schild Inc criteria.
- Writes a personalized first-touch outreach draft.
- Saves everything in `data/leads.json`.
- Exports review data to CSV for Google Sheets.
- Optionally posts leads to a Google Sheets webhook.

This implementation uses Google Places API rather than scraping Google Maps pages. It is more stable, easier to operate, and aligned with Google's API model. Google's current Places Text Search requires a `textQuery` and response field mask, and Place Details/Text Search data fields are controlled through field masks.

Sources:
- [Google Places Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Google Places Data Fields](https://developers.google.com/maps/documentation/places/web-service/data-fields)
- [Google Place Details](https://developers.google.com/maps/documentation/places/web-service/place-details)

## Run locally

```bash
npm start
```

Open:

```text
http://localhost:3000
```

Without environment variables, the app runs with demo leads so the workflow can be reviewed immediately.

## Deploy on Railway

1. Create a Railway account and connect the GitHub repo that contains this project.
2. Create a new Railway service from the repo.
3. Add a Railway volume and mount it to:

```text
/app/data
```

4. Add these Railway variables:

```text
DATA_DIR=/app/data
GOOGLE_PLACES_API_KEY=your_google_places_api_key
APP_USERNAME=schild
APP_PASSWORD=a-long-shared-team-password
```

5. Deploy. Railway will use `railway.json` and run `npm start`.

The app exposes `/health` for Railway health checks. The review queue is stored at `DATA_DIR/leads.json`, so the volume keeps leads across deploys and restarts.

## Live Google Maps data

Set a Places API key before starting the server:

```bash
export GOOGLE_PLACES_API_KEY="your_google_places_api_key"
npm start
```

The server calls:

```text
https://places.googleapis.com/v1/places:searchText
```

Requested fields:

```text
places.id, places.displayName, places.formattedAddress, places.websiteUri,
places.nationalPhoneNumber, places.internationalPhoneNumber, places.types,
places.primaryTypeDisplayName, places.businessStatus, places.googleMapsUri
```

## Google Sheets sync

The simplest production path is a Google Apps Script web app that accepts JSON and appends rows to a Sheet. Deploy the script, then set:

```bash
export GOOGLE_SHEETS_WEBHOOK_URL="https://script.google.com/macros/s/..."
npm start
```

Expected webhook payload:

```json
{
  "leads": [
    {
      "name": "Company",
      "website": "https://example.com",
      "phone": "555-0100",
      "companyType": "Industrial automation",
      "industry": "Manufacturing",
      "fitScore": 82,
      "fitLabel": "Strong fit",
      "outreachDraft": "Subject: Quick idea..."
    }
  ]
}
```

## Notes

- The local database file is created automatically at `data/leads.json`.
- CSV export is available at `/api/export.csv`.
- Website research observes normal fetch behavior and will fail gracefully when a site blocks bots, returns non-HTML content, or times out.
- Fit scoring is transparent and rule based, so the criteria can be tuned directly in the app before each run.
