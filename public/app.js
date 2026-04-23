const state = {
  leads: [],
  selectedId: null,
  filter: "",
  minFit: 0
};

const els = {
  form: document.querySelector("#run-form"),
  searchQuery: document.querySelector("#searchQuery"),
  location: document.querySelector("#location"),
  maxResults: document.querySelector("#maxResults"),
  schildProfile: document.querySelector("#schildProfile"),
  fitFilter: document.querySelector("#fitFilter"),
  tableSearch: document.querySelector("#tableSearch"),
  leadRows: document.querySelector("#leadRows"),
  leadCount: document.querySelector("#leadCount"),
  detailPanel: document.querySelector("#detailPanel"),
  status: document.querySelector("#status"),
  refresh: document.querySelector("#refresh"),
  syncSheets: document.querySelector("#syncSheets"),
  clearLeads: document.querySelector("#clearLeads"),
  chips: [...document.querySelectorAll(".chip")]
};

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = els.form.querySelector("button[type='submit']");
  button.disabled = true;
  setStatus("Running Maps discovery, crawling sites for emails, scoring fit, and drafting outreach...");

  try {
    const payload = {
      searchQuery: els.searchQuery.value,
      location: els.location.value,
      maxResults: Number(els.maxResults.value),
      schildProfile: els.schildProfile.value
    };
    const result = await postJson("/api/run", payload);
    state.leads = result.leads || [];
    if (!state.selectedId && state.leads[0]) state.selectedId = state.leads[0].id;
    render();
    setStatus(result.usedDemoData
      ? `Created ${result.count} demo lead(s). Add GOOGLE_PLACES_API_KEY for live Google Maps data.`
      : `Created ${result.count} live lead(s) with contact emails and personalization data.`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    button.disabled = false;
  }
});

els.refresh.addEventListener("click", loadLeads);
els.tableSearch.addEventListener("input", () => {
  state.filter = els.tableSearch.value.toLowerCase();
  render();
});
els.fitFilter.addEventListener("change", () => {
  state.minFit = Number(els.fitFilter.value);
  render();
});
els.syncSheets.addEventListener("click", async () => {
  setStatus("Sending saved leads to Google Sheets...");
  try {
    const result = await postJson("/api/leads/sheets", {});
    setStatus(result.message || "Google Sheets sync complete.");
  } catch (error) {
    setStatus(error.message, true);
  }
});
els.clearLeads.addEventListener("click", async () => {
  if (!confirm("Clear all saved leads from the local database?")) return;
  await postJson("/api/leads/clear", {});
  state.leads = [];
  state.selectedId = null;
  render();
  setStatus("Local review queue cleared.");
});

els.chips.forEach((chip) => {
  chip.addEventListener("click", () => {
    els.searchQuery.value = chip.dataset.query || "";
    els.location.value = chip.dataset.location || "";
    setStatus(`Loaded example query: ${chip.dataset.query}`);
  });
});

els.detailPanel.addEventListener("click", async (event) => {
  const sendButton = event.target.closest("[data-action='send-email']");
  if (!sendButton) return;
  const lead = state.leads.find((item) => item.id === state.selectedId);
  if (!lead) return;
  const draft = currentDraftValues();

  sendButton.disabled = true;
  setStatus(`Creating a Trengo review draft for ${lead.bestEmail || "selected contact"}...`);
  try {
    const result = await postJson("/api/leads/send-email", {
      leadId: lead.id,
      to: lead.bestEmail,
      subject: draft.subject,
      body: draft.body
    });
    replaceLead(result.lead);
    render();
    setStatus(result.message || "Draft created.");
    if (result.provider === "trengo" && result.openUrl) {
      window.open(result.openUrl, "_blank", "noopener,noreferrer");
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    sendButton.disabled = false;
  }
});

els.detailPanel.addEventListener("click", async (event) => {
  const saveButton = event.target.closest("[data-action='save-draft']");
  if (!saveButton) return;
  const lead = state.leads.find((item) => item.id === state.selectedId);
  if (!lead) return;
  const draft = currentDraftValues();

  saveButton.disabled = true;
  try {
    const result = await postJson("/api/leads/save-draft", {
      leadId: lead.id,
      subject: draft.subject,
      body: draft.body
    });
    replaceLead(result.lead);
    render();
    setStatus(result.message || "Draft saved.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    saveButton.disabled = false;
  }
});

els.detailPanel.addEventListener("click", async (event) => {
  const regenButton = event.target.closest("[data-action='regenerate-draft']");
  if (!regenButton) return;
  const lead = state.leads.find((item) => item.id === state.selectedId);
  if (!lead) return;
  const commandEl = document.querySelector("#draftCommand");
  const command = commandEl ? commandEl.value : "";

  regenButton.disabled = true;
  setStatus("Regenerating the draft...");
  try {
    const result = await postJson("/api/leads/regenerate-draft", {
      leadId: lead.id,
      command
    });
    replaceLead(result.lead);
    render();
    setStatus(result.message || "Draft regenerated.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    regenButton.disabled = false;
  }
});

loadLeads();

async function loadLeads() {
  try {
    const leads = await fetchJson("/api/leads");
    state.leads = leads;
    if (!state.selectedId && leads[0]) state.selectedId = leads[0].id;
    render();
    setStatus(leads.length
      ? `Loaded ${leads.length} saved lead(s).`
      : "Ready. The crawler will search websites for emails, contact pages, and personalization hooks.");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function render() {
  const leads = filteredLeads();
  els.leadCount.textContent = `${leads.length} ${leads.length === 1 ? "lead" : "leads"}`;

  if (!leads.length) {
    els.leadRows.innerHTML = `<tr><td colspan="5" class="empty-state">No leads match the current filters.</td></tr>`;
  } else {
    els.leadRows.innerHTML = leads.map((lead) => `
      <tr data-id="${escapeHtml(lead.id)}" class="${lead.id === state.selectedId ? "active" : ""}">
        <td>
          <div class="company-cell">
            <strong>${escapeHtml(lead.name)}</strong>
            ${lead.website ? `<a href="${escapeAttribute(lead.website)}" target="_blank" rel="noreferrer">${escapeHtml(shortUrl(lead.website))}</a>` : `<span class="muted">No website</span>`}
            <span class="muted">${escapeHtml(lead.address || "")}</span>
          </div>
        </td>
        <td>${lead.bestEmail ? `<a href="mailto:${escapeAttribute(lead.bestEmail)}">${escapeHtml(lead.bestEmail)}</a>` : `<span class="muted">Not found</span>`}</td>
        <td>${escapeHtml(lead.industry || "Other")}<br><span class="muted">${escapeHtml(lead.companyType || "")}</span></td>
        <td><span class="pill ${fitClass(lead.fitScore)}">${escapeHtml(lead.fitLabel)} · ${lead.fitScore}</span></td>
        <td><span class="muted">${escapeHtml(lead.source || "Local")}</span></td>
      </tr>
    `).join("");
  }

  els.leadRows.querySelectorAll("tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => {
      state.selectedId = row.dataset.id;
      render();
    });
  });

  const selected = state.leads.find((lead) => lead.id === state.selectedId) || leads[0];
  renderDetail(selected);
}

function renderDetail(lead) {
  if (!lead) {
    els.detailPanel.innerHTML = `
      <div class="empty-detail">
        <p class="eyebrow">Lead detail</p>
        <h3>Select a company</h3>
        <p>Emails, website hooks, fit notes, and the outreach draft will appear here.</p>
      </div>
    `;
    return;
  }

  const emails = (lead.contacts || []).length
    ? (lead.contacts || []).map((contact) => `
      <li>
        <a href="mailto:${escapeAttribute(contact.email)}">${escapeHtml(contact.email)}</a>
        <span class="muted">${escapeHtml(shortUrl(contact.sourcePage || ""))}</span>
      </li>
    `).join("")
    : `<li>No email found from the website crawl.</li>`;

  const hooks = (lead.personalization || []).length
    ? (lead.personalization || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")
    : `<li>No strong personalization hooks were found.</li>`;

  const pages = (lead.pagesScanned || []).length
    ? (lead.pagesScanned || []).map((page) => `<li><a href="${escapeAttribute(page)}" target="_blank" rel="noreferrer">${escapeHtml(shortUrl(page))}</a></li>`).join("")
    : `<li>No pages were scanned.</li>`;

  els.detailPanel.innerHTML = `
    <div class="detail-inner">
      <div class="detail-heading">
        <p class="eyebrow">${escapeHtml(lead.industry || "Lead")}</p>
        <h3>${escapeHtml(lead.name)}</h3>
        <p class="muted">${escapeHtml(lead.address || "")}</p>
      </div>

      <div class="metrics">
        <div class="metric"><span>Fit score</span><strong>${lead.fitScore}/100</strong></div>
        <div class="metric"><span>Status</span><strong>${escapeHtml(lead.fitLabel)}</strong></div>
        <div class="metric"><span>Best email</span><strong>${escapeHtml(lead.bestEmail || "Not found")}</strong></div>
        <div class="metric"><span>Phone</span><strong>${escapeHtml(lead.phone || "Not found")}</strong></div>
      </div>

      <div class="section-block">
        <h4>Easy fit meaning</h4>
        <p>${escapeHtml(lead.fitMeaning || "No fit explanation available.")}</p>
        <ul>${(lead.fitReasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
      </div>

      <div class="section-block">
        <h4>Links</h4>
        ${lead.website ? `<a href="${escapeAttribute(lead.website)}" target="_blank" rel="noreferrer">${escapeHtml(lead.website)}</a>` : `<p>No website found.</p>`}
        ${lead.googleMapsUrl ? `<a href="${escapeAttribute(lead.googleMapsUrl)}" target="_blank" rel="noreferrer">Open Google Maps result</a>` : ""}
      </div>

      <div class="section-block">
        <h4>Emails found on site</h4>
        <ul>${emails}</ul>
      </div>

      <div class="section-block">
        <h4>Personalization hooks</h4>
        <ul>${hooks}</ul>
      </div>

      <div class="section-block">
        <h4>What the company appears to do</h4>
        <p>${escapeHtml(lead.websiteSummary || "No website summary available.")}</p>
      </div>

      <div class="section-block">
        <h4>Pages scanned</h4>
        <ul>${pages}</ul>
      </div>

      <div class="section-block">
        <h4>Email draft</h4>
        <div class="draft-meta">
          <label class="draft-field">
            <span>Subject</span>
            <input id="draftSubject" value="${escapeAttribute(lead.outreachSubject || "")}">
          </label>
          ${lead.lastEmailSentAt ? `<span><strong>Last sent:</strong> ${escapeHtml(formatDate(lead.lastEmailSentAt))}</span>` : ""}
        </div>
        <label class="draft-field">
          <span>Generate again with command</span>
          <input id="draftCommand" placeholder="Example: make it shorter, sound more premium, focus on white-label bike accessories">
        </label>
        <div class="action-row">
          <button class="secondary-action" type="button" data-action="save-draft">Save draft</button>
          <button class="secondary-action" type="button" data-action="regenerate-draft">Generate again</button>
          ${lead.bestEmail ? `<a class="secondary-action" href="${escapeAttribute(buildMailto(lead))}">Open in email app</a>` : `<span class="secondary-action disabled-action">No email found</span>`}
          <button class="primary-action" type="button" data-action="send-email" ${lead.bestEmail ? "" : "disabled"}>Create Trengo review draft</button>
        </div>
        <textarea id="draftBody" class="draft-editor" rows="14">${escapeHtml(lead.outreachBody || "")}</textarea>
      </div>
    </div>
  `;
}

function filteredLeads() {
  return state.leads.filter((lead) => {
    const haystack = [
      lead.name,
      lead.industry,
      lead.companyType,
      lead.phone,
      lead.website,
      lead.websiteSummary,
      lead.bestEmail,
      ...(lead.emails || [])
    ].join(" ").toLowerCase();
    return haystack.includes(state.filter) && Number(lead.fitScore || 0) >= state.minFit;
  });
}

function replaceLead(updatedLead) {
  state.leads = state.leads.map((lead) => lead.id === updatedLead.id ? updatedLead : lead);
}

async function fetchJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.style.borderColor = isError ? "#f3b9b4" : "";
  els.status.style.color = isError ? "#b42318" : "";
}

function fitClass(score) {
  if (score >= 72) return "strong";
  if (score >= 52) return "review";
  return "low";
}

function shortUrl(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.hostname.replace(/^www\./, "")}${parsed.pathname === "/" ? "" : parsed.pathname}`;
  } catch {
    return url;
  }
}

function buildMailto(lead) {
  const draft = currentDraftValues();
  const to = lead.bestEmail || "";
  const subject = encodeURIComponent(draft.subject || "");
  const body = encodeURIComponent(draft.body || "");
  return `mailto:${to}?subject=${subject}&body=${body}`;
}

function currentDraftValues() {
  return {
    subject: (document.querySelector("#draftSubject") || {}).value || "",
    body: (document.querySelector("#draftBody") || {}).value || ""
  };
}

function formatDate(value) {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
