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
const APP_USERNAME = process.env.APP_USERNAME || "schild";
const APP_PASSWORD = process.env.APP_PASSWORD || "";

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
  "Schild Inc helps B2B companies improve revenue operations, lead generation, data enrichment, automation, and outbound sales workflows.",
  "Best fit customers are B2B service providers, manufacturers, logistics companies, SaaS companies, agencies, and industrial firms that sell to other businesses.",
  "Signals of fit include a public website, clear commercial offering, B2B language, growth hiring, multiple locations, complex operations, and visible sales or partnership motions.",
  "Poor fits include consumer-only retail, restaurants, personal services, hobby businesses, and organizations with no visible commercial website."
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
    id: "demo-riverbend",
    displayName: { text: "Riverbend Family Dental" },
    formattedAddress: "Austin, TX",
    websiteUri: "https://example.com/riverbend-dental",
    nationalPhoneNumber: "(512) 555-0121",
    types: ["dentist", "health", "local_service"],
    primaryTypeDisplayName: { text: "Dentist" },
    googleMapsUri: "https://maps.google.com/?q=Riverbend+Family+Dental"
  }
];

const DEMO_SITE_TEXT = {
  "https://example.com/apex-industrial": "Apex Industrial Automation designs robotic cells, PLC controls, and preventive maintenance programs for manufacturers. Our engineering team helps plants reduce downtime and scale production across multiple facilities.",
  "https://example.com/northstar-freight": "Northstar Freight Systems provides B2B freight brokerage, warehousing, and managed transportation for regional distributors. We support shippers with quoting, carrier management, shipment tracking, and custom logistics reporting.",
  "https://example.com/riverbend-dental": "Riverbend Family Dental is a local dental clinic offering cleanings, cosmetic dentistry, and emergency appointments for families."
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
    const websiteResearch = await researchWebsite(company.website);
    const fit = scoreFit({ company, websiteResearch, schildProfile });
    const outreach = draftOutreach({ company, websiteResearch, fit, schildProfile });
    leads.push({
      id: stableId(company.name, company.website, company.phone),
      createdAt: new Date().toISOString(),
      source: GOOGLE_PLACES_API_KEY ? "Google Places API" : "Demo data",
      searchQuery: query,
      location,
      ...company,
      websiteSummary: websiteResearch.summary,
      websiteSignals: websiteResearch.signals,
      fitScore: fit.score,
      fitLabel: fit.label,
      fitReasons: fit.reasons,
      outreachDraft: outreach
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

async function researchWebsite(website) {
  if (!website) {
    return {
      summary: "No public website was found in the Maps data.",
      signals: ["No website available"],
      text: ""
    };
  }

  if (DEMO_SITE_TEXT[website]) {
    return summarizeWebsite(DEMO_SITE_TEXT[website]);
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 9000);
    const response = await fetch(website, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        "User-Agent": "SchildProspectResearchBot/1.0 (+https://schild.example)"
      }
    });
    clearTimeout(timeout);

    if (!response.ok) {
      throw new Error(`Website returned ${response.status}`);
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("text/html")) {
      return {
        summary: `Website is reachable but returned ${contentType || "non-HTML content"}.`,
        signals: ["Website reachable", "Non-HTML response"],
        text: ""
      };
    }

    const html = await response.text();
    return summarizeWebsite(extractReadableText(html));
  } catch (error) {
    return {
      summary: `Website could not be researched automatically: ${error.message}.`,
      signals: ["Website research failed"],
      text: ""
    };
  }
}

function summarizeWebsite(text) {
  const cleaned = sanitizeText(text).slice(0, 7000);
  const sentences = cleaned.match(/[^.!?]+[.!?]+/g) || [cleaned];
  const useful = sentences
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence.length > 40)
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
  const titles = [...html.matchAll(/<(title|h1|h2|h3)[^>]*>([\s\S]*?)<\/\1>/gi)]
    .map((match) => match[2]);
  const paragraphs = [...withoutScripts.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/gi)]
    .map((match) => match[1]);

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
    "B2B language": /\b(b2b|businesses|enterprise|commercial|industrial|distributors|manufacturers|clients)\b/i,
    "Operations complexity": /\b(operations|logistics|supply chain|workflow|process|automation|facility|fleet|warehouse)\b/i,
    "Sales motion": /\b(sales|quote|demo|consultation|partners|request a proposal|contact our team)\b/i,
    "Growth language": /\b(growth|scale|expansion|multi-location|hiring|new markets)\b/i,
    "Technical offering": /\b(software|platform|engineering|integration|analytics|automation|systems)\b/i,
    "Local consumer service": /\b(family|appointment|walk-ins|restaurant|dental|salon|spa|cafe|retail)\b/i
  };

  const signals = Object.entries(signalMap)
    .filter(([, pattern]) => pattern.test(text))
    .map(([label]) => label);

  return signals.length ? signals : ["Limited public signals"];
}

function scoreFit({ company, websiteResearch, schildProfile }) {
  const text = [
    company.name,
    company.companyType,
    company.industry,
    company.placeTypes.join(" "),
    websiteResearch.summary,
    websiteResearch.signals.join(" "),
    schildProfile
  ].join(" ").toLowerCase();

  const positiveSignals = [
    "b2b",
    "industrial",
    "manufacturer",
    "manufacturing",
    "logistics",
    "freight",
    "warehouse",
    "software",
    "saas",
    "agency",
    "consulting",
    "automation",
    "enterprise",
    "commercial",
    "distributor",
    "supplier",
    "engineering",
    "operations",
    "sales",
    "lead generation"
  ];
  const negativeSignals = [
    "restaurant",
    "cafe",
    "salon",
    "spa",
    "dentist",
    "dental",
    "family",
    "retail",
    "consumer-only",
    "appointment"
  ];

  let score = company.website ? 42 : 25;
  const reasons = [];

  for (const signal of positiveSignals) {
    if (text.includes(signal)) {
      score += 5;
      if (reasons.length < 4) reasons.push(`Shows ${signal.replace("-", " ")} relevance`);
    }
  }

  for (const signal of negativeSignals) {
    if (text.includes(signal)) {
      score -= 8;
      if (reasons.length < 5) reasons.push(`Possible consumer/local-service signal: ${signal}`);
    }
  }

  if (websiteResearch.signals.includes("Website research failed")) {
    score -= 12;
    reasons.push("Website could not be researched automatically");
  }

  score = clamp(score, 0, 100);
  const label = score >= 72 ? "Strong fit" : score >= 52 ? "Review fit" : "Low fit";

  if (!reasons.length) {
    reasons.push("Limited evidence, needs manual review");
  }

  return { score, label, reasons: [...new Set(reasons)].slice(0, 5) };
}

function draftOutreach({ company, websiteResearch, fit }) {
  const firstSignal = fit.reasons[0] || "your company profile";
  const websiteLine = websiteResearch.summary
    ? `I noticed ${company.name} appears to focus on ${lowerFirst(websiteResearch.summary.slice(0, 180))}`
    : `I came across ${company.name} while researching companies in ${company.industry}.`;

  return [
    `Subject: Quick idea for ${company.name}`,
    "",
    `Hi ${company.name} team,`,
    "",
    `${websiteLine}`,
    "",
    `Schild Inc works with companies where better lead data, sales process automation, and targeted outbound can create more predictable pipeline. Based on ${firstSignal.toLowerCase()}, there may be a practical opportunity to identify higher-intent accounts and personalize outreach around the problems your buyers already care about.`,
    "",
    `Would it be worth a short conversation to compare what is working today against a few account segments Schild could help test?`,
    "",
    "Best,",
    "Schild Inc"
  ].join("\n");
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

function inferIndustry(text) {
  const value = text.toLowerCase();
  const rules = [
    ["Manufacturing", /\b(manufactur|industrial|factory|machinery|automation|equipment)\b/],
    ["Logistics", /\b(logistics|freight|shipping|trucking|transport|warehouse|supply_chain)\b/],
    ["Software / SaaS", /\b(software|saas|it_service|technology|platform)\b/],
    ["Professional Services", /\b(consult|agency|accounting|legal|marketing|business_service)\b/],
    ["Healthcare", /\b(health|doctor|dentist|clinic|medical|hospital)\b/],
    ["Retail / Local Service", /\b(store|retail|restaurant|salon|spa|cafe|local_service)\b/]
  ];
  const match = rules.find(([, pattern]) => pattern.test(value));
  return match ? match[0] : "Other";
}

function mergeLeads(existing, incoming) {
  const map = new Map();
  for (const lead of existing) map.set(lead.id, lead);
  for (const lead of incoming) map.set(lead.id, lead);
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
    "phone",
    "companyType",
    "industry",
    "address",
    "fitScore",
    "fitLabel",
    "fitReasons",
    "websiteSummary",
    "outreachDraft",
    "googleMapsUrl",
    "createdAt"
  ];

  const lines = [headers.join(",")];
  for (const lead of leads) {
    lines.push(headers.map((header) => csvCell(Array.isArray(lead[header]) ? lead[header].join("; ") : lead[header] || "")).join(","));
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
