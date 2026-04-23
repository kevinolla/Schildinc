# Google Apps Script Email Webhook

This script receives the app's email-send payload and sends mail through your Google account with `MailApp.sendEmail()`.

## Script properties to set

In Apps Script:

1. Open **Project Settings**
2. Under **Script Properties**, add:

```text
WEBHOOK_SECRET=choose-a-long-random-secret
SENDER_NAME=Schild Inc
REPLY_TO=your-team@yourdomain.com
```

`REPLY_TO` is optional.

## Deploy

1. Open [script.new](https://script.new)
2. Replace the default code with `Code.gs`
3. Create `appsscript.json` and paste the manifest
4. Save
5. Click **Deploy** > **New deployment**
6. Deployment type: **Web app**
7. Execute as: **Me**
8. Who has access: **Anyone**
9. Deploy and authorize
10. Copy the web app URL

## Connect to Railway

Set these Railway variables:

```text
EMAIL_SEND_WEBHOOK_URL=your_web_app_url
EMAIL_SEND_WEBHOOK_SECRET=the_same_secret_as_WEBHOOK_SECRET
```

Then redeploy Railway.

## Notes

- This endpoint must be public so Railway can call it, which is why the shared secret matters.
- `MailApp` is used instead of `GmailApp` because it is simpler and more stable for pure sending.
- Google enforces daily send quotas for Apps Script / MailApp.
