// Paste this entire file into Code.gs in your Google Apps Script project.
// Keep MAIL_SECRET in Project Settings > Script Properties, not in this file.

function doGet() {
  const configured = Boolean(
    PropertiesService.getScriptProperties().getProperty("MAIL_SECRET")
  );
  return jsonResponse({
    ok: configured,
    message: configured
      ? "NukeNER mail endpoint is running. Email is sent by POST."
      : "MAIL_SECRET is missing from Script Properties."
  });
}

function doPost(e) {
  try {
    const data = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    const secret = PropertiesService.getScriptProperties().getProperty("MAIL_SECRET");

    if (!secret || !data || data.secret !== secret) {
      return jsonResponse({ ok: false, error: "Unauthorized" });
    }

    if (typeof data.to !== "string" || !data.to.trim() ||
        typeof data.subject !== "string" || !data.subject.trim() ||
        typeof data.body !== "string" || !data.body.trim()) {
      return jsonResponse({ ok: false, error: "Missing email fields" });
    }

    MailApp.sendEmail({
      to: data.to.trim(),
      subject: data.subject.trim(),
      body: data.body,
      name: "NukeNER-Viz"
    });

    return jsonResponse({ ok: true });
  } catch (error) {
    console.error("Mail delivery failed", error);
    return jsonResponse({
      ok: false,
      error: "Mail delivery failed. Check Apps Script Executions."
    });
  }
}

// Run this once in the editor and grant the requested mail permission.
function authorizeMail() {
  console.log("Remaining daily recipients: " + MailApp.getRemainingDailyQuota());
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
