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
  clearLeads: document.querySelector("#clearLeads")
};

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = els.form.querySelector("button[type='submit']");
  button.disabled = true;
  setStatus("Running Maps discovery, website research, fit scoring, and outreach drafting...");

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
      : `Created ${result.count} live lead(s) and saved them locally.`);
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

loadLeads();

async function loadLeads() {
  try {
    const leads = await fetchJson("/api/leads");
    state.leads = leads;
    if (!state.selectedId && leads[0]) state.selectedId = leads[0].id;
    render();
    setStatus(leads.length ? `Loaded ${leads.length} saved lead(s).` : "Ready. Add a Google Places API key for live Maps results.");
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
        <td>${escapeHtml(lead.industry || "Other")}<br><span class="muted">${escapeHtml(lead.companyType || "")}</span></td>
        <td>${escapeHtml(lead.phone || "Not found")}</td>
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
        <p>Research summary, fit reasons, and the outreach draft will appear here.</p>
      </div>
    `;
    return;
  }

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
        <div class="metric"><span>Phone</span><strong>${escapeHtml(lead.phone || "Not found")}</strong></div>
        <div class="metric"><span>Type</span><strong>${escapeHtml(lead.companyType || "Unknown")}</strong></div>
      </div>

      <div class="section-block">
        <h4>Links</h4>
        ${lead.website ? `<a href="${escapeAttribute(lead.website)}" target="_blank" rel="noreferrer">${escapeHtml(lead.website)}</a>` : `<p>No website found.</p>`}
        ${lead.googleMapsUrl ? `<a href="${escapeAttribute(lead.googleMapsUrl)}" target="_blank" rel="noreferrer">Open Google Maps result</a>` : ""}
      </div>

      <div class="section-block">
        <h4>What the company does</h4>
        <p>${escapeHtml(lead.websiteSummary || "No website summary available.")}</p>
      </div>

      <div class="section-block">
        <h4>Fit reasons</h4>
        <ul>${(lead.fitReasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
      </div>

      <div class="section-block">
        <h4>Outreach draft</h4>
        <div class="draft">${escapeHtml(lead.outreachDraft || "")}</div>
      </div>
    </div>
  `;
}

function filteredLeads() {
  return state.leads.filter((lead) => {
    const haystack = [lead.name, lead.industry, lead.companyType, lead.phone, lead.website, lead.websiteSummary]
      .join(" ")
      .toLowerCase();
    return haystack.includes(state.filter) && Number(lead.fitScore || 0) >= state.minFit;
  });
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
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
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
