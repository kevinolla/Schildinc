const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const PORT = Number(process.env.PORT || 3000);
const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, "public");
const DATA_DIR = process.env.DATA_DIR || path.join(ROOT, "data");
const DB_PATH = path.join(DATA_DIR, "leads.json");

const GOOGLE_PLACES_API_KEY = process.env.GOOGLE_PLACES_API_KEY || "";
const GOOGLE_SHEETS_WEBHOOK_URL = process.env.GOOGLE_SHEETS_WEBHOOK_URL || "";
const EMAIL_SEND_WEBHOOK_URL = process.env.EMAIL_SEND_WEBHOOK_URL || "";
const EMAIL_SEND_WEBHOOK_SECRET = process.env.EMAIL_SEND_WEBHOOK_SECRET || "";
const TRENGO_API_TOKEN = process.env.TRENGO_API_TOKEN || "";
const TRENGO_EMAIL_CHANNEL_ID = process.env.TRENGO_EMAIL_CHANNEL_ID || "";
const TRENGO_APP_URL = process.env.TRENGO_APP_URL || "https://app.trengo.com";
const APP_USERNAME = process.env.APP_USERNAME || "schild";
const APP_PASSWORD = process.env.APP_PASSWORD || "";

const USER_AGENT = "SchildProspectResearchBot/1.1 (+https://schild.example)";
const MAX_INTERNAL_PAGES = 4;
const FETCH_TIMEOUT_MS = 9000;

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".csv": "text/csv; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg"
};

const FALLBACK_PROFILE = [
  "Schild Inc is a good fit for companies that already look like real businesses online, have a clear offer, and could use better lead generation, automation, follow-up, or sales process support.",
  "Strong fit examples: B2B service companies, manufacturers, logistics companies, agencies, software firms, distributors, and multi-location businesses with a real website and visible growth goals.",
  "Review fit examples: specialty retail or bike stores with e-commerce, repairs, service booking, or multiple locations.",
  "Low fit examples: hobby projects, personal-service businesses with no real lead flow, restaurants, salons, clinics, and companies with no useful website."
].join(" ");

const DEMO_PLACES = [
  {
    id: "demo-apex",
    displayName: { text: "Apex Industrial Automation" },
    formattedAddress: "Chicago, IL",
    websiteUri: "https://example.com/apex-industrial",
    nationalPhoneNumber: "(312) 555-0184",
    types: ["industrial", "automation", "manufacturer"],
    primaryTypeDisplayName: { text: "Industrial automation" },
    googleMapsUri: "https://maps.google.com/?q=Apex+Industrial+Automation"
  },
  {
    id: "demo-northstar",
    displayName: { text: "Northstar Freight Systems" },
    formattedAddress: "Dallas, TX",
    websiteUri: "https://example.com/northstar-freight",
    nationalPhoneNumber: "(214) 555-0167",
    types: ["logistics", "freight_forwarding", "transportation"],
    primaryTypeDisplayName: { text: "Logistics service" },
    googleMapsUri: "https://maps.google.com/?q=Northstar+Freight+Systems"
  },
  {
    id: "demo-velocity",
    displayName: { text: "Velocity Bike Works" },
    formattedAddress: "Denver, CO",
    websiteUri: "https://example.com/velocity-bike-works",
    nationalPhoneNumber: "(303) 555-0178",
    types: ["bicycle_store", "store", "repair_service"],
    primaryTypeDisplayName: { text: "Bike store" },
    googleMapsUri: "https://maps.google.com/?q=Velocity+Bike+Works"
  }
];

const DEMO_SITE_RESEARCH = {
  "https://example.com/apex-industrial": {
    summary: "Apex Industrial Automation designs robotic cells, PLC controls, and preventive maintenance programs for manufacturers. The company positions itself as an engineering partner for plants that want less downtime and more production capacity.",
    signals: ["B2B language", "Operations complexity", "Sales motion", "Technical offering"],
    text: "Apex Industrial Automation designs robotic cells, PLC controls, and preventive maintenance programs for manufacturers. Our engineering team helps plants reduce downtime and scale production across multiple facilities.",
    contacts: [
      { email: "sales@apex-industrial.example.com", sourcePage: "https://example.com/apex-industrial/contact" },
      { email: "info@apex-industrial.example.com", sourcePage: "https://example.com/apex-industrial" }
    ],
    personalization: [
      "Homepage highlights robotic cells, PLC controls, and plant uptime improvements.",
      "The site speaks directly to manufacturers with multiple facilities.",
      "Contact language suggests they are open to commercial conversations and proposals."
    ],
    pagesScanned: [
      "https://example.com/apex-industrial",
      "https://example.com/apex-industrial/contact"
    ]
  },
  "https://example.com/northstar-freight": {
    summary: "Northstar Freight Systems provides B2B freight brokerage, warehousing, and managed transportation for regional distributors. The company emphasizes quoting, carrier management, and reporting for shippers.",
    signals: ["B2B language", "Operations complexity", "Sales motion"],
    text: "Northstar Freight Systems provides B2B freight brokerage, warehousing, and managed transportation for regional distributors. We support shippers with quoting, carrier management, shipment tracking, and custom logistics reporting.",
    contacts: [
      { email: "hello@northstarfreight.example.com", sourcePage: "https://example.com/northstar-freight" },
      { email: "quotes@northstarfreight.example.com", sourcePage: "https://example.com/northstar-freight/contact" }
    ],
    personalization: [
      "The website emphasizes freight brokerage, warehousing, and managed transportation.",
      "Quoting and shipment tracking are visible buyer-facing workflows.",
      "The company appears to serve distributors and regional shippers rather than consumers."
    ],
    pagesScanned: [
      "https://example.com/northstar-freight",
      "https://example.com/northstar-freight/contact"
    ]
  },
  "https://example.com/velocity-bike-works": {
    summary: "Velocity Bike Works is a bike retailer with repair service, online shopping, and local pickup. The company looks like a stronger review-fit candidate when the target list includes specialty bike stores with service and repeat customer marketing needs.",
    signals: ["Sales motion", "Growth language"],
    text: "Velocity Bike Works sells road, gravel, and commuter bikes, offers pro fitting and tune-up packages, and lets customers shop online for pickup or shipping. Visit our service department or contact us for fleet and corporate cycling programs.",
    contacts: [
      { email: "shop@velocitybikeworks.example.com", sourcePage: "https://example.com/velocity-bike-works" },
      { email: "service@velocitybikeworks.example.com", sourcePage: "https://example.com/velocity-bike-works/service" }
    ],
    personalization: [
      "The store combines bike sales, service packages, and online shopping.",
      "Service department and tune-up offers create repeat-visit opportunities.",
      "The site mentions fleet and corporate cycling programs, which can make it more commercially interesting."
    ],
    pagesScanned: [
      "https://example.com/velocity-bike-works",
      "https://example.com/velocity-bike-works/service"
    ]
  }
};

ensureDataStore();

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (req.method === "GET" && url.pathname === "/health") {
      return sendJson(res, { ok: true });
    }

    if (!isAuthorized(req)) {
      return requestAuth(res);
    }

    if (req.method === "GET" && url.pathname === "/api/leads") {
      return sendJson(res, readLeads());
    }

    if (req.method === "GET" && url.pathname === "/api/export.csv") {
      const csv = toCsv(readLeads());
      res.writeHead(200, {
        "Content-Type": MIME_TYPES[".csv"],
        "Content-Disposition": "attachment; filename=\"schild-leads.csv\""
      });
      return res.end(csv);
    }

    if (req.method === "POST" && url.pathname === "/api/leads/clear") {
      writeLeads([]);
      return sendJson(res, { ok: true, leads: [] });
    }

    if (req.method === "POST" && url.pathname === "/api/run") {
      const body = await readBody(req);
      const result = await runProspecting(body);
      return sendJson(res, result);
    }

    if (req.method === "POST" && url.pathname === "/api/leads/sheets") {
      const leads = readLeads();
      const result = await syncSheets(leads);
      return sendJson(res, result);
    }

    if (req.method === "POST" && url.pathname === "/api/leads/send-email") {
      const body = await readBody(req);
      const result = await sendLeadEmail(body);
      return sendJson(res, result);
    }

    return serveStatic(url.pathname, res);
  } catch (error) {
    console.error(error);
    return sendJson(res, { error: error.message || "Unexpected server error" }, 500);
  }
});

server.listen(PORT, () => {
  console.log(`Schild prospect engine running at http://localhost:${PORT}`);
  if (!APP_PASSWORD) {
    console.log("APP_PASSWORD is not set. The app is open to anyone who can access the URL.");
  }
});

async function runProspecting(input) {
  const query = sanitizeText(input.searchQuery || "");
  const location = sanitizeText(input.location || "");
  const maxResults = clamp(Number(input.maxResults || 10), 1, 25);
  const schildProfile = sanitizeText(input.schildProfile || FALLBACK_PROFILE);

  if (!query) {
    throw new Error("Search query is required.");
  }

  const places = await findPlaces({ query, location, maxResults });
  const leads = [];

  for (const place of places.slice(0, maxResults)) {
    const company = normalizePlace(place);
    const websiteResearch = await researchWebsite(company, query);
    const fit = scoreFit({ company, websiteResearch, schildProfile, searchQuery: query });
    const outreach = draftOutreach({ company, websiteResearch, fit, schildProfile, searchQuery: query });

    leads.push({
      id: stableId(company.name, company.website, company.phone),
      createdAt: new Date().toISOString(),
      source: GOOGLE_PLACES_API_KEY ? "Google Places API" : "Demo data",
      searchQuery: query,
      location,
      ...company,
      bestEmail: websiteResearch.bestEmail,
      contacts: websiteResearch.contacts,
      emails: websiteResearch.contacts.map((contact) => contact.email),
      emailSourcePages: [...new Set(websiteResearch.contacts.map((contact) => contact.sourcePage).filter(Boolean))],
      pagesScanned: websiteResearch.pagesScanned,
      personalization: websiteResearch.personalization,
      websiteSummary: websiteResearch.summary,
      websiteSignals: websiteResearch.signals,
      fitScore: fit.score,
      fitLabel: fit.label,
      fitMeaning: fit.meaning,
      fitReasons: fit.reasons,
      outreachSubject: outreach.subject,
      outreachBody: outreach.body,
      outreachDraft: `${outreach.subject}\n\n${outreach.body}`,
      lastEmailSentAt: null,
      lastEmailRecipient: null
    });
  }

  const merged = mergeLeads(readLeads(), leads);
  writeLeads(merged);

  if (GOOGLE_SHEETS_WEBHOOK_URL) {
    await syncSheets(leads);
  }

  return {
    ok: true,
    usedDemoData: !GOOGLE_PLACES_API_KEY,
    count: leads.length,
    leads: merged
  };
}

async function findPlaces({ query, location, maxResults }) {
  if (!GOOGLE_PLACES_API_KEY) {
    return DEMO_PLACES.filter((place) => {
      const haystack = `${place.displayName.text} ${place.formattedAddress} ${place.types.join(" ")}`.toLowerCase();
      const tokens = `${query} ${location}`.toLowerCase().split(/\s+/).filter(Boolean);
      return tokens.length === 0 || tokens.some((token) => haystack.includes(token));
    }).slice(0, maxResults);
  }

  const textQuery = location ? `${query} in ${location}` : query;
  const response = await fetch("https://places.googleapis.com/v1/places:searchText", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
      "X-Goog-FieldMask": [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.types",
        "places.primaryTypeDisplayName",
        "places.businessStatus",
        "places.googleMapsUri"
      ].join(",")
    },
    body: JSON.stringify({
      textQuery,
      pageSize: maxResults
    })
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Google Places request failed: ${response.status} ${detail}`);
  }

  const data = await response.json();
  return data.places || [];
}

function normalizePlace(place) {
  const types = Array.isArray(place.types) ? place.types : [];
  const primaryType = place.primaryTypeDisplayName?.text || "";

  return {
    name: place.displayName?.text || place.name || "Unknown company",
    website: place.websiteUri || "",
    phone: place.nationalPhoneNumber || place.internationalPhoneNumber || "",
    companyType: primaryType || humanize(types[0] || "Unknown"),
    industry: inferIndustry(`${primaryType} ${types.join(" ")}`),
    address: place.formattedAddress || "",
    googleMapsUrl: place.googleMapsUri || "",
    placeTypes: types
  };
}

async function researchWebsite(company, searchQuery) {
  if (!company.website) {
    return {
      summary: "No public website was found in the Maps data.",
      signals: ["No website available"],
      text: "",
      contacts: [],
      bestEmail: "",
      personalization: [],
      pagesScanned: []
    };
  }

  if (DEMO_SITE_RESEARCH[company.website]) {
    const demo = DEMO_SITE_RESEARCH[company.website];
    return {
      ...demo,
      contacts: demo.contacts.slice(),
      bestEmail: pickBestContact(demo.contacts, company).email,
      personalization: demo.personalization.slice(),
      pagesScanned: demo.pagesScanned.slice()
    };
  }

  try {
    const homepage = await fetchHtmlPage(company.website);
    const pages = [homepage];
    const queue = prioritizeInternalLinks(homepage.links, company.website);

    for (const target of queue.slice(0, MAX_INTERNAL_PAGES)) {
      try {
        pages.push(await fetchHtmlPage(target.url));
      } catch {
        continue;
      }
    }

    const combinedText = pages
      .map((page) => page.text)
      .join(" ")
      .slice(0, 12000);

    const summary = summarizeWebsite(combinedText);
    const contacts = rankContacts(
      dedupeContacts(pages.flatMap((page) => page.contacts)),
      company
    );

    return {
      summary: summary.summary,
      signals: summary.signals,
      text: summary.text,
      contacts,
      bestEmail: pickBestContact(contacts, company).email,
      personalization: buildPersonalization(pages, company, searchQuery),
      pagesScanned: pages.map((page) => page.url)
    };
  } catch (error) {
    return {
      summary: `Website could not be researched automatically: ${error.message}.`,
      signals: ["Website research failed"],
      text: "",
      contacts: [],
      bestEmail: "",
      personalization: [],
      pagesScanned: []
    };
  }
}

async function fetchHtmlPage(targetUrl) {
  const normalizedUrl = ensureAbsoluteUrl(targetUrl);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

  try {
    const response = await fetch(normalizedUrl, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        "User-Agent": USER_AGENT
      }
    });

    if (!response.ok) {
      throw new Error(`Website returned ${response.status}`);
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("text/html")) {
      throw new Error(`Website returned non-HTML content (${contentType || "unknown"})`);
    }

    const html = await response.text();
    const finalUrl = response.url || normalizedUrl;

    return {
      url: finalUrl,
      title: extractTitle(html),
      description: extractMetaDescription(html),
      headings: extractHeadings(html),
      text: extractReadableText(html),
      contacts: extractContacts(html, finalUrl),
      links: extractInternalLinks(html, finalUrl)
    };
  } finally {
    clearTimeout(timeout);
  }
}

function summarizeWebsite(text) {
  const cleaned = sanitizeText(text).slice(0, 7000);
  const sentences = cleaned.match(/[^.!?]+[.!?]+/g) || [cleaned];
  const useful = sentences
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence.length > 50)
    .slice(0, 3);
  const signals = extractSignals(cleaned);

  return {
    summary: useful.join(" ") || cleaned.slice(0, 280) || "Website text was sparse.",
    signals,
    text: cleaned
  };
}

function extractReadableText(html) {
  const withoutScripts = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ");
  const metaDescriptions = [...html.matchAll(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["'][^>]*>/gi)]
    .map((match) => match[1]);
  const titles = [...html.matchAll(/<(title|h1|h2|h3|h4)[^>]*>([\s\S]*?)<\/\1>/gi)]
    .map((match) => match[2]);
  const paragraphs = [...withoutScripts.matchAll(/<(p|li)[^>]*>([\s\S]*?)<\/\1>/gi)]
    .map((match) => match[2]);

  return [...metaDescriptions, ...titles, ...paragraphs]
    .join(" ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function extractSignals(text) {
  const signalMap = {
    "B2B language": /\b(b2b|businesses|enterprise|commercial|industrial|distributors|manufacturers|clients|fleet|wholesale)\b/i,
    "Operations complexity": /\b(operations|logistics|supply chain|workflow|process|automation|facility|fleet|warehouse)\b/i,
    "Sales motion": /\b(sales|quote|demo|consultation|partners|request a proposal|contact our team|book a service|shop online)\b/i,
    "Growth language": /\b(growth|scale|expansion|multi-location|hiring|new markets|pickup|shipping)\b/i,
    "Technical offering": /\b(software|platform|engineering|integration|analytics|automation|systems)\b/i,
    "Retail / service signals": /\b(store|repair|shop|pickup|service department|tune-up|online store)\b/i,
    "Local consumer service": /\b(family|appointment|walk-ins|restaurant|dental|salon|spa|cafe)\b/i
  };

  const signals = Object.entries(signalMap)
    .filter(([, pattern]) => pattern.test(text))
    .map(([label]) => label);

  return signals.length ? signals : ["Limited public signals"];
}

function scoreFit({ company, websiteResearch, schildProfile, searchQuery }) {
  const text = [
    company.name,
    company.companyType,
    company.industry,
    company.placeTypes.join(" "),
    websiteResearch.summary,
    websiteResearch.signals.join(" "),
    schildProfile,
    searchQuery
  ].join(" ").toLowerCase();

  let score = company.website ? 38 : 18;
  const positives = [];
  const concerns = [];

  const positiveRules = [
    { pattern: /\b(b2b|enterprise|commercial|wholesale|distributor|supplier)\b/, points: 14, reason: "Looks like a business that sells commercially, not just casual walk-in traffic." },
    { pattern: /\b(manufactur|industrial|automation|engineering|logistics|freight|warehouse|operations)\b/, points: 12, reason: "The company works in an industry where process, pipeline, and automation usually matter." },
    { pattern: /\b(quote|demo|consultation|proposal|contact our team|sales)\b/, points: 9, reason: "The website shows a clear buying or sales conversation path." },
    { pattern: /\b(shop online|pickup|shipping|service department|repair|tune-up)\b/, points: 7, reason: "There is a repeatable service or e-commerce workflow that can support better outreach or retention." },
    { pattern: /\b(multi-location|multiple facilities|expansion|scale|hiring|new markets)\b/, points: 8, reason: "The company shows growth signals, which usually makes prospecting support more relevant." }
  ];

  const negativeRules = [
    { pattern: /\b(restaurant|cafe|salon|spa)\b/, points: -18, reason: "This looks more like a local walk-in business than a prospecting-heavy sales model." },
    { pattern: /\b(dentist|dental|clinic|medical|hospital)\b/, points: -16, reason: "This is more healthcare/provider style outreach than Schild's typical use case." },
    { pattern: /\b(hobby|personal service|walk-ins only)\b/, points: -12, reason: "The business looks too small or too local to clearly benefit from this workflow." }
  ];

  for (const rule of positiveRules) {
    if (rule.pattern.test(text)) {
      score += rule.points;
      if (positives.length < 4) positives.push(rule.reason);
    }
  }

  for (const rule of negativeRules) {
    if (rule.pattern.test(text)) {
      score += rule.points;
      if (concerns.length < 3) concerns.push(rule.reason);
    }
  }

  const queryTokens = tokenize(searchQuery).filter((token) => token.length > 3);
  const matchedTokens = queryTokens.filter((token) => text.includes(token));
  if (matchedTokens.length) {
    score += Math.min(12, matchedTokens.length * 3);
    positives.push(`The company matches the search intent closely: ${matchedTokens.slice(0, 4).join(", ")}.`);
  }

  if (websiteResearch.contacts.length) {
    score += 6;
    positives.push("A matching public email was found, so the lead is easier to contact.");
  } else {
    concerns.push("No public email was found on the website crawl, so outreach may need manual contact lookup.");
  }

  if (websiteResearch.signals.includes("Website research failed")) {
    score -= 12;
    concerns.push("The website could not be crawled fully, so this lead needs manual review.");
  }

  if (!company.website) {
    score -= 16;
    concerns.push("There is no useful website to personalize from.");
  }

  score = clamp(score, 0, 100);
  const label = score >= 72 ? "Strong fit" : score >= 52 ? "Review fit" : "Low fit";
  const meaning = label === "Strong fit"
    ? "Easy read: this company looks reachable, credible, and likely to benefit from better lead generation or sales process support."
    : label === "Review fit"
      ? "Easy read: there are some good signals here, but a person should review whether Schild is the right match."
      : "Easy read: this probably is not a strong Schild prospect unless you have a very specific campaign angle.";

  const reasons = [...positives.slice(0, 3), ...concerns.slice(0, 2)];
  if (!reasons.length) reasons.push("There is not enough public information yet, so this lead needs manual review.");

  return { score, label, meaning, reasons };
}

function draftOutreach({ company, websiteResearch, fit, searchQuery }) {
  const hook = websiteResearch.personalization[0]
    || websiteResearch.summary
    || `your work in ${company.industry.toLowerCase()}`;
  const secondHook = websiteResearch.personalization[1] || "";
  const contactReference = websiteResearch.bestEmail
    ? `I also found ${websiteResearch.bestEmail} listed publicly on your site, which made me confident I had the right team.`
    : "";
  const subjectLead = fit.label === "Strong fit"
    ? `Idea for ${company.name}`
    : `Quick question for ${company.name}`;
  const subject = `${subjectLead} re: ${company.companyType || searchQuery}`;

  const body = [
    `Hi ${company.name} team,`,
    "",
    `I was researching ${searchQuery || company.industry.toLowerCase()} businesses and noticed ${lowerFirst(hook)}${secondHook ? ` I also saw ${lowerFirst(secondHook)}` : ""}.`,
    "",
    `Schild Inc helps companies improve lead generation, outreach personalization, and follow-up workflows so more of the right prospects actually turn into conversations.`,
    "",
    fit.label === "Strong fit"
      ? "From the public signals on your site, it looks like there may be room to turn your existing traffic, service demand, or sales process into more consistent pipeline."
      : "You may or may not be the right fit, but there looked to be enough signal that I thought a short note was worth trying.",
    "",
    contactReference,
    contactReference ? "" : null,
    "Would it be useful to compare your current outreach or lead flow against a few practical ideas Schild could test?",
    "",
    "Best,",
    "Schild Inc"
  ].filter(Boolean).join("\n");

  return { subject, body };
}

async function syncSheets(leads) {
  if (!GOOGLE_SHEETS_WEBHOOK_URL) {
    return {
      ok: false,
      message: "GOOGLE_SHEETS_WEBHOOK_URL is not configured. Use CSV export or add a webhook URL."
    };
  }

  const response = await fetch(GOOGLE_SHEETS_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ leads })
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Google Sheets sync failed: ${response.status} ${detail}`);
  }

  return { ok: true, message: `Synced ${leads.length} lead(s) to Google Sheets.` };
}

async function sendLeadEmail(input) {
  const leads = readLeads();
  const lead = leads.find((item) => item.id === input.leadId);

  if (!lead) {
    throw new Error("Lead not found.");
  }

  const to = sanitizeText(input.to || lead.bestEmail || (lead.emails || [])[0] || "");
  const subject = sanitizeText(input.subject || lead.outreachSubject || `Idea for ${lead.name}`);
  const body = String(input.body || lead.outreachBody || "").trim();

  if (!to) {
    throw new Error("No destination email is available for this lead.");
  }

  if (!body) {
    throw new Error("Email body is empty.");
  }

  if (TRENGO_API_TOKEN && TRENGO_EMAIL_CHANNEL_ID) {
    return await sendViaTrengo({ lead, to, subject, body });
  }

  if (!EMAIL_SEND_WEBHOOK_URL) {
    throw new Error("No send integration is configured. Add Trengo credentials or EMAIL_SEND_WEBHOOK_URL.");
  }

  const response = await fetch(EMAIL_SEND_WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      secret: EMAIL_SEND_WEBHOOK_SECRET || undefined,
      to,
      subject,
      body,
      lead
    })
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Email send failed: ${response.status} ${detail}`);
  }

  const updatedLead = {
    ...lead,
    lastEmailSentAt: new Date().toISOString(),
    lastEmailRecipient: to
  };
  writeLeads(leads.map((item) => item.id === lead.id ? updatedLead : item));

  return {
    ok: true,
    message: `Email sent to ${to}.`,
    lead: updatedLead
  };
}

async function sendViaTrengo({ lead, to, subject, body }) {
  const channelId = Number(TRENGO_EMAIL_CHANNEL_ID);
  if (!Number.isFinite(channelId) || channelId <= 0) {
    throw new Error("TRENGO_EMAIL_CHANNEL_ID is invalid.");
  }

  let contact = await findTrengoContactByEmail(to);
  if (!contact) {
    contact = await trengoRequest(`/channels/${channelId}/contacts`, {
      method: "POST",
      body: JSON.stringify({
        identifier: to,
        channel_id: channelId,
        name: lead.name
      })
    });
  }

  const ticket = await trengoRequest("/tickets", {
    method: "POST",
    body: JSON.stringify({
      channel_id: channelId,
      contact_id: String(contact.id),
      subject
    })
  });

  await trengoRequest(`/tickets/${ticket.id}/messages`, {
    method: "POST",
    body: JSON.stringify({
      message: body,
      subject,
      internal_note: false
    })
  });

  const leads = readLeads();
  const updatedLead = {
    ...lead,
    lastEmailSentAt: new Date().toISOString(),
    lastEmailRecipient: to,
    trengoContactId: contact.id,
    trengoTicketId: ticket.id
  };
  writeLeads(leads.map((item) => item.id === lead.id ? updatedLead : item));

  return {
    ok: true,
    message: `Message created in Trengo for ${to}.`,
    lead: updatedLead,
    provider: "trengo",
    openUrl: TRENGO_APP_URL
  };
}

async function findTrengoContactByEmail(email) {
  const response = await trengoRequest(`/contacts?term=${encodeURIComponent(email)}`);
  const list = Array.isArray(response.data) ? response.data : Array.isArray(response) ? response : [];
  return list.find((contact) => {
    return String(contact.email || "").toLowerCase() === String(email).toLowerCase()
      || String(contact.identifier || "").toLowerCase() === String(email).toLowerCase();
  }) || null;
}

async function trengoRequest(pathname, init = {}) {
  const response = await fetch(`https://app.trengo.com/api/v2${pathname}`, {
    method: init.method || "GET",
    headers: {
      "Authorization": `Bearer ${TRENGO_API_TOKEN}`,
      "Content-Type": "application/json",
      "Accept": "application/json",
      ...(init.headers || {})
    },
    body: init.body
  });

  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }

  if (!response.ok) {
    throw new Error(`Trengo API failed: ${response.status} ${text || response.statusText}`);
  }

  return data;
}

function inferIndustry(text) {
  const value = text.toLowerCase();
  const rules = [
    ["Manufacturing", /\b(manufactur|industrial|factory|machinery|automation|equipment)\b/],
    ["Logistics", /\b(logistics|freight|shipping|trucking|transport|warehouse|supply_chain)\b/],
    ["Software / SaaS", /\b(software|saas|it_service|technology|platform)\b/],
    ["Professional Services", /\b(consult|agency|accounting|legal|marketing|business_service)\b/],
    ["Retail / Specialty Store", /\b(store|retail|shop|bicycle_store|repair_service)\b/],
    ["Healthcare", /\b(health|doctor|dentist|clinic|medical|hospital)\b/]
  ];
  const match = rules.find(([, pattern]) => pattern.test(value));
  return match ? match[0] : "Other";
}

function mergeLeads(existing, incoming) {
  const map = new Map();
  for (const lead of existing) map.set(lead.id, lead);
  for (const lead of incoming) {
    const previous = map.get(lead.id) || {};
    map.set(lead.id, {
      ...previous,
      ...lead,
      lastEmailSentAt: previous.lastEmailSentAt || lead.lastEmailSentAt || null,
      lastEmailRecipient: previous.lastEmailRecipient || lead.lastEmailRecipient || null
    });
  }
  return [...map.values()].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

function readLeads() {
  ensureDataStore();
  return JSON.parse(fs.readFileSync(DB_PATH, "utf8"));
}

function writeLeads(leads) {
  ensureDataStore();
  fs.writeFileSync(DB_PATH, JSON.stringify(leads, null, 2));
}

function ensureDataStore() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(DB_PATH)) fs.writeFileSync(DB_PATH, "[]\n");
}

function serveStatic(requestPath, res) {
  const normalizedPath = requestPath === "/" ? "/index.html" : requestPath;
  const safePath = path.normalize(normalizedPath).replace(/^(\.\.[/\\])+/, "");
  const filePath = path.join(PUBLIC_DIR, safePath);

  if (!filePath.startsWith(PUBLIC_DIR)) {
    return sendText(res, "Forbidden", 403);
  }

  fs.readFile(filePath, (error, content) => {
    if (error) return sendText(res, "Not found", 404);
    const ext = path.extname(filePath);
    res.writeHead(200, { "Content-Type": MIME_TYPES[ext] || "application/octet-stream" });
    res.end(content);
  });
}

function isAuthorized(req) {
  if (!APP_PASSWORD) return true;

  const header = req.headers.authorization || "";
  if (!header.startsWith("Basic ")) return false;

  const decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
  const separatorIndex = decoded.indexOf(":");
  if (separatorIndex === -1) return false;

  const username = decoded.slice(0, separatorIndex);
  const password = decoded.slice(separatorIndex + 1);

  return timingSafeEqual(username, APP_USERNAME) && timingSafeEqual(password, APP_PASSWORD);
}

function requestAuth(res) {
  res.writeHead(401, {
    "Content-Type": "text/plain; charset=utf-8",
    "WWW-Authenticate": "Basic realm=\"Schild Prospect Engine\""
  });
  res.end("Authentication required.");
}

function timingSafeEqual(left, right) {
  const leftBuffer = Buffer.from(String(left));
  const rightBuffer = Buffer.from(String(right));
  if (leftBuffer.length !== rightBuffer.length) return false;
  return crypto.timingSafeEqual(leftBuffer, rightBuffer);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 2_000_000) {
        req.destroy();
        reject(new Error("Request body too large."));
      }
    });
    req.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch {
        reject(new Error("Invalid JSON request body."));
      }
    });
  });
}

function sendJson(res, payload, status = 200) {
  res.writeHead(status, { "Content-Type": MIME_TYPES[".json"] });
  res.end(JSON.stringify(payload, null, 2));
}

function sendText(res, text, status = 200) {
  res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  res.end(text);
}

function toCsv(leads) {
  const headers = [
    "name",
    "website",
    "bestEmail",
    "emails",
    "phone",
    "companyType",
    "industry",
    "address",
    "fitScore",
    "fitLabel",
    "fitMeaning",
    "fitReasons",
    "personalization",
    "websiteSummary",
    "outreachSubject",
    "outreachBody",
    "googleMapsUrl",
    "createdAt"
  ];

  const lines = [headers.join(",")];
  for (const lead of leads) {
    lines.push(headers.map((header) => {
      const value = Array.isArray(lead[header]) ? lead[header].join("; ") : lead[header] || "";
      return csvCell(value);
    }).join(","));
  }
  return `${lines.join("\n")}\n`;
}

function csvCell(value) {
  const stringValue = String(value).replace(/\r?\n/g, " ");
  return `"${stringValue.replace(/"/g, "\"\"")}"`;
}

function stableId(...parts) {
  return crypto.createHash("sha1").update(parts.filter(Boolean).join("|").toLowerCase()).digest("hex").slice(0, 16);
}

function sanitizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function humanize(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function lowerFirst(value) {
  return value ? value.charAt(0).toLowerCase() + value.slice(1) : value;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

function ensureAbsoluteUrl(value) {
  if (/^https?:\/\//i.test(value)) return value;
  return `https://${String(value || "").replace(/^\/+/, "")}`;
}

function extractTitle(html) {
  return sanitizeText((html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || "");
}

function extractMetaDescription(html) {
  return sanitizeText((html.match(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["'][^>]*>/i) || [])[1] || "");
}

function extractHeadings(html) {
  return [...html.matchAll(/<(h1|h2|h3)[^>]*>([\s\S]*?)<\/\1>/gi)]
    .map((match) => sanitizeText(match[2].replace(/<[^>]+>/g, " ")))
    .filter(Boolean)
    .slice(0, 8);
}

function extractContacts(html, sourcePage) {
  const set = new Set();
  const contacts = [];
  const text = extractReadableText(html);
  const combined = `${html} ${text}`;
  const emailPattern = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
  const mailtoPattern = /mailto:([^"'?\s>]+)/gi;
  const directEmails = combined.match(emailPattern) || [];
  const mailtoEmails = [...combined.matchAll(mailtoPattern)].map((match) => match[1]);

  const matches = [
    ...directEmails,
    ...mailtoEmails
  ];

  for (const raw of matches) {
    const email = raw
      .replace(/^mailto:/i, "")
      .replace(/[),.;:]+$/, "")
      .trim()
      .toLowerCase();
    if (!isLikelyEmail(email)) continue;
    if (set.has(email)) continue;
    set.add(email);
    contacts.push({ email, sourcePage });
  }

  return contacts;
}

function extractInternalLinks(html, baseUrl) {
  const base = new URL(baseUrl);
  const links = [];
  const seen = new Set();
  const pattern = /<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;

  for (const match of html.matchAll(pattern)) {
    const href = sanitizeText(match[1] || "");
    const anchorText = sanitizeText((match[2] || "").replace(/<[^>]+>/g, " "));
    if (!href || href.startsWith("#") || href.startsWith("javascript:") || href.startsWith("mailto:") || href.startsWith("tel:")) {
      continue;
    }

    try {
      const url = new URL(href, base);
      if (url.hostname !== base.hostname) continue;
      if (/\.(pdf|jpg|jpeg|png|gif|svg|zip)$/i.test(url.pathname)) continue;
      url.hash = "";
      const normalized = url.toString();
      if (seen.has(normalized)) continue;
      seen.add(normalized);
      links.push({ url: normalized, anchorText });
    } catch {
      continue;
    }
  }

  return links;
}

function prioritizeInternalLinks(links, baseUrl) {
  const base = new URL(baseUrl);
  const scored = links.map((link) => {
    const url = link.url.toLowerCase();
    const anchor = (link.anchorText || "").toLowerCase();
    let score = 0;

    const priorities = [
      ["contact", 12],
      ["about", 9],
      ["team", 8],
      ["service", 8],
      ["shop", 7],
      ["repair", 7],
      ["company", 6],
      ["solutions", 6],
      ["faq", 4]
    ];

    for (const [keyword, points] of priorities) {
      if (url.includes(keyword) || anchor.includes(keyword)) score += points;
    }

    if (url === base.toString() || url === `${base.toString()}/`) score -= 20;
    return { ...link, score };
  });

  return scored
    .filter((link) => link.score > 0)
    .sort((left, right) => right.score - left.score || left.url.localeCompare(right.url));
}

function dedupeContacts(contacts) {
  const map = new Map();
  for (const contact of contacts) {
    if (!map.has(contact.email)) map.set(contact.email, contact);
  }
  return [...map.values()];
}

function rankContacts(contacts, company) {
  const websiteHost = getHost(company.website);
  return contacts
    .map((contact) => {
      let score = 0;
      const local = contact.email.split("@")[0];
      const domain = contact.email.split("@")[1] || "";
      if (websiteHost && domain.includes(websiteHost.replace(/^www\./, ""))) score += 20;
      if (/\b(info|hello|contact|sales|team|shop|store|service)\b/i.test(local)) score += 12;
      if (/\b(noreply|no-reply|support-ticket|donotreply)\b/i.test(local)) score -= 15;
      const companyTokens = tokenize(company.name);
      if (companyTokens.some((token) => local.includes(token) || domain.includes(token))) score += 6;
      return { ...contact, score };
    })
    .sort((left, right) => right.score - left.score || left.email.localeCompare(right.email));
}

function pickBestContact(contacts) {
  return contacts[0] || { email: "" };
}

function buildPersonalization(pages, company, searchQuery) {
  const candidates = [];
  const queryTokens = tokenize(searchQuery);

  for (const page of pages) {
    const snippets = [
      page.description,
      ...page.headings,
      ...extractInterestingSentences(page.text)
    ];

    for (const snippet of snippets) {
      const clean = sanitizeText(snippet);
      if (!clean || clean.length < 30 || clean.length > 190) continue;
      let score = 0;
      if (queryTokens.some((token) => clean.toLowerCase().includes(token))) score += 8;
      if (/\b(service|repair|quote|shop|pickup|shipping|fleet|commercial|manufacturer|logistics|automation)\b/i.test(clean)) score += 7;
      if (page.url.toLowerCase().includes("contact") || page.url.toLowerCase().includes("about")) score += 2;
      candidates.push({ text: clean, score });
    }
  }

  const unique = [];
  const seen = new Set();
  for (const item of candidates.sort((left, right) => right.score - left.score)) {
    const key = item.text.toLowerCase();
    if (seen.has(key)) continue;
    if (key.includes(company.name.toLowerCase()) && key.split(" ").length < 4) continue;
    seen.add(key);
    unique.push(item.text);
    if (unique.length === 4) break;
  }

  return unique;
}

function extractInterestingSentences(text) {
  const sentences = sanitizeText(text).match(/[^.!?]+[.!?]+/g) || [];
  return sentences.filter((sentence) => {
    return sentence.length >= 40
      && sentence.length <= 180
      && /\b(service|repair|quote|shop|pickup|shipping|commercial|manufacturer|logistics|automation|consult|fleet|program)\b/i.test(sentence);
  }).slice(0, 8);
}

function tokenize(value) {
  return sanitizeText(value)
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

function getHost(url) {
  try {
    return new URL(ensureAbsoluteUrl(url)).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function isLikelyEmail(value) {
  if (!/^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/i.test(value)) return false;
  if (/\.(png|jpg|jpeg|gif|svg)$/i.test(value)) return false;
  return true;
}
