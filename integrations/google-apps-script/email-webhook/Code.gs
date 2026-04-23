function doPost(e) {
  try {
    var payload = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    var secret = PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET");
    var senderName = PropertiesService.getScriptProperties().getProperty("SENDER_NAME") || "Schild Inc";
    var replyTo = PropertiesService.getScriptProperties().getProperty("REPLY_TO") || "";

    if (secret && payload.secret !== secret) {
      return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
    }

    var to = sanitize(payload.to);
    var subject = sanitize(payload.subject);
    var body = String(payload.body || "").trim();

    if (!to || !subject || !body) {
      return jsonResponse({ ok: false, error: "Missing to, subject, or body" }, 400);
    }

    var options = {
      name: senderName,
      htmlBody: bodyToHtml(body)
    };

    if (replyTo) {
      options.replyTo = replyTo;
    }

    MailApp.sendEmail(to, subject, body, options);

    return jsonResponse({
      ok: true,
      to: to,
      subject: subject,
      sentAt: new Date().toISOString()
    }, 200);
  } catch (error) {
    return jsonResponse({
      ok: false,
      error: error && error.message ? error.message : String(error)
    }, 500);
  }
}

function jsonResponse(payload, status) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function sanitize(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function bodyToHtml(body) {
  return String(body || "")
    .split(/\r?\n\r?\n/)
    .map(function (paragraph) {
      return "<p>" + escapeHtml(paragraph).replace(/\r?\n/g, "<br>") + "</p>";
    })
    .join("");
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
