/* Schild visual email template builder.
 *
 * A dependency-free, drag-and-drop block editor. The document model is a flat
 * array of blocks; each block type knows how to (a) render an editable preview
 * in the canvas, (b) expose inspector fields, and (c) compile to email-safe,
 * table-based, inline-CSS HTML + a plain-text twin.
 *
 * Defaults per brief: Montserrat (Arial fallback) / 14px / #0A0A0A.
 */
(function () {
  "use strict";

  var FONT = "'Montserrat','Helvetica Neue',Arial,sans-serif";
  var INK = "#0A0A0A";
  var BASE_SIZE = 14;
  var MUTED = "#8a7f76";
  var ACCENT = "#101010";
  var GOLD = "#C9A84C";

  // Merge tags offered in the inserter (kept in sync with email_engine).
  var MERGE_TAGS = [
    "greeting_name", "opener", "company_name", "contact_name", "city",
    "country", "website", "sender_name", "unsubscribe_url"
  ];

  // ---- Block registry --------------------------------------------------------
  // Each entry: label, icon, factory (default props), fields (inspector schema),
  // html(props) -> email HTML string, text(props) -> plain text.

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // Preserve {{merge}} tags + basic inline emphasis the user typed, escape rest.
  function richText(s) {
    var out = esc(s);
    out = out.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/\n/g, "<br>");
    return out;
  }

  var BLOCKS = {
    heading: {
      label: "Heading", icon: "H",
      make: function () { return { text: "Your headline here", align: "left", size: 24 }; },
      fields: [
        { k: "text", t: "text", label: "Heading text" },
        { k: "size", t: "number", label: "Size (px)" },
        { k: "align", t: "align", label: "Align" }
      ],
      html: function (p) {
        return row('<h1 style="margin:0;font-family:' + FONT + ';font-size:' + (p.size || 24) +
          'px;line-height:1.3;font-weight:700;color:' + INK + ';text-align:' + p.align + ';">' +
          richText(p.text) + "</h1>");
      },
      text: function (p) { return (p.text || "") + "\n\n"; }
    },

    text: {
      label: "Text", icon: "¶",
      make: function () { return { text: "Write your message here. Use **bold** for emphasis, and insert {{company_name}} to personalize.", align: "left", size: BASE_SIZE }; },
      fields: [
        { k: "text", t: "textarea", label: "Body text (**bold**, merge tags)" },
        { k: "size", t: "number", label: "Size (px)" },
        { k: "align", t: "align", label: "Align" }
      ],
      html: function (p) {
        return row('<p style="margin:0;font-family:' + FONT + ';font-size:' + (p.size || BASE_SIZE) +
          'px;line-height:1.7;color:' + INK + ';text-align:' + p.align + ';">' +
          richText(p.text) + "</p>");
      },
      text: function (p) { return (p.text || "").replace(/\*\*/g, "") + "\n\n"; }
    },

    button: {
      label: "Button", icon: "▭",
      make: function () { return { text: "See a free sample", url: "https://schildinc.com", align: "left", bg: ACCENT, color: GOLD }; },
      fields: [
        { k: "text", t: "text", label: "Button label" },
        { k: "url", t: "text", label: "Link URL" },
        { k: "bg", t: "color", label: "Background" },
        { k: "color", t: "color", label: "Text color" },
        { k: "align", t: "align", label: "Align" }
      ],
      html: function (p) {
        var btn = '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:' +
          (p.align === "center" ? "0 auto" : (p.align === "right" ? "0 0 0 auto" : "0")) + ';">' +
          '<tr><td style="border-radius:8px;background:' + p.bg + ';">' +
          '<a href="' + esc(p.url) + '" style="display:inline-block;padding:13px 28px;font-family:' + FONT +
          ';font-size:16px;font-weight:600;color:' + p.color + ';text-decoration:none;border-radius:8px;">' +
          esc(p.text) + "</a></td></tr></table>";
        return row(btn, "8px 0");
      },
      text: function (p) { return (p.text || "") + ": " + (p.url || "") + "\n\n"; }
    },

    image: {
      label: "Image", icon: "🖼",
      make: function () { return { url: "", alt: "Image", width: 560, align: "center", link: "" }; },
      fields: [
        { k: "url", t: "text", label: "Image URL (https://…)" },
        { k: "alt", t: "text", label: "Alt text" },
        { k: "width", t: "number", label: "Width (px)" },
        { k: "link", t: "text", label: "Link URL (optional)" },
        { k: "align", t: "align", label: "Align" }
      ],
      html: function (p) {
        var img = '<img src="' + esc(p.url) + '" alt="' + esc(p.alt) + '" width="' + (p.width || 560) +
          '" style="display:block;max-width:100%;width:' + (p.width || 560) + 'px;height:auto;border:0;' +
          (p.align === "center" ? "margin:0 auto;" : (p.align === "right" ? "margin-left:auto;" : "")) +
          'background:#eef2f7;">';
        if (p.link) img = '<a href="' + esc(p.link) + '">' + img + "</a>";
        return row(img, "6px 0");
      },
      text: function (p) { return (p.alt || "Image") + (p.link ? " (" + p.link + ")" : "") + "\n\n"; }
    },

    divider: {
      label: "Divider", icon: "—",
      make: function () { return { color: "#e7e1d6" }; },
      fields: [{ k: "color", t: "color", label: "Line color" }],
      html: function (p) {
        return row('<div style="border-top:1px solid ' + p.color + ';font-size:0;line-height:0;">&nbsp;</div>', "8px 0");
      },
      text: function () { return "----------\n\n"; }
    },

    spacer: {
      label: "Spacer", icon: "␣",
      make: function () { return { height: 24 }; },
      fields: [{ k: "height", t: "number", label: "Height (px)" }],
      html: function (p) {
        return '<tr><td style="height:' + (p.height || 24) + 'px;font-size:0;line-height:' + (p.height || 24) + 'px;">&nbsp;</td></tr>';
      },
      text: function () { return "\n"; }
    },

    signature: {
      label: "Signature", icon: "✒",
      make: function () {
        return {
          name: "Ruben", role: "Owner",
          photo_url: "", logo_url: "",
          phone_eu: "+31 36 2010101", phone_us: "+1 831 661 8635", phone_uk: "+44 20 8129 6161",
          email: "info@schildinc.com", website: "schildinc.com",
          address: "Noorderduinloo 1, Almere Netherlands"
        };
      },
      fields: [
        { k: "name", t: "text", label: "Name" },
        { k: "role", t: "text", label: "Role" },
        { k: "photo_url", t: "text", label: "Photo URL (square, https://…)" },
        { k: "logo_url", t: "text", label: "Logo URL (https://…)" },
        { k: "phone_eu", t: "text", label: "Phone — Europe" },
        { k: "phone_us", t: "text", label: "Phone — USA" },
        { k: "phone_uk", t: "text", label: "Phone — UK" },
        { k: "email", t: "text", label: "Email" },
        { k: "website", t: "text", label: "Website" },
        { k: "address", t: "text", label: "Address" }
      ],
      html: function (p) { return row(signatureHtml(p), "10px 0 0"); },
      text: function (p) {
        return "\n--\n" + p.name + " · " + p.role + " · Schild Inc\n" +
          "Europe " + p.phone_eu + " | USA " + p.phone_us + " | UK " + p.phone_uk + "\n" +
          p.email + " · " + p.website + "\n" + p.address + "\n";
      }
    }
  };

  // A canvas cell wrapper: one <tr><td> with consistent horizontal padding.
  function row(inner, pad) {
    return '<tr><td style="padding:' + (pad || "6px 0") + ';">' + inner + "</td></tr>";
  }

  // Bulletproof signature (icons = dark rounded <td> cells with unicode glyphs,
  // so they render with images off; photo + logo are swappable <img>).
  function signatureHtml(p) {
    var photo = p.photo_url
      ? '<img src="' + esc(p.photo_url) + '" width="84" height="84" alt="' + esc(p.name) +
        '" style="display:block;width:84px;height:84px;border-radius:50%;border:2px solid ' + GOLD + ';background:' + MUTED + ';">'
      : '<div style="width:84px;height:84px;border-radius:50%;border:2px solid ' + GOLD + ';background:' + MUTED +
        ';color:#fff;font-family:' + FONT + ';font-size:30px;font-weight:700;text-align:center;line-height:84px;">' +
        esc((p.name || "?").charAt(0)) + "</div>";
    var logo = p.logo_url
      ? '<img src="' + esc(p.logo_url) + '" width="88" height="88" alt="Schild Inc" style="display:block;width:88px;height:88px;border:0;">'
      : '<div style="width:88px;height:88px;border-radius:12px;background:' + ACCENT + ';color:' + GOLD +
        ';font-family:' + FONT + ';font-weight:700;font-size:13px;text-align:center;line-height:88px;">SCHILD</div>';
    function icon(glyph) {
      return '<td width="24" height="24" align="center" valign="middle" bgcolor="#2b2119" style="width:24px;height:24px;background:#2b2119;border-radius:12px;color:#fff;font-size:12px;line-height:24px;text-align:center;">' + glyph + "</td>";
    }
    function phone(label, num) {
      return '<tr>' + icon("&#9742;") + '<td width="10">&nbsp;</td><td valign="middle">' +
        '<span style="font-family:' + FONT + ';font-size:10px;color:' + MUTED + ';letter-spacing:1px;text-transform:uppercase;">' + esc(label) + '</span><br>' +
        '<a href="tel:' + esc(num.replace(/\s/g, "")) + '" style="font-family:' + FONT + ';font-size:13px;color:' + INK + ';text-decoration:none;">' + esc(num) + '</a></td></tr>' +
        '<tr><td colspan="3" height="12">&nbsp;</td></tr>';
    }
    function line(glyph, inner) {
      return '<tr>' + icon(glyph) + '<td width="10">&nbsp;</td><td valign="middle">' + inner + '</td></tr>' +
        '<tr><td colspan="3" height="12">&nbsp;</td></tr>';
    }
    return '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;border-collapse:collapse;font-family:' + FONT + ';color:' + INK + ';">' +
      '<tr><td valign="top" width="100" style="width:100px;">' + photo + '</td>' +
      '<td valign="top" style="padding-left:16px;">' +
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>' +
          '<td valign="middle" style="font-family:' + FONT + ';font-size:22px;font-weight:700;color:' + INK + ';padding-right:10px;line-height:1;">' + esc(p.name) + '</td>' +
          '<td valign="middle" bgcolor="' + MUTED + '" style="background:' + MUTED + ';border-radius:11px;padding:3px 11px;font-family:' + FONT + ';font-size:11px;color:#fff;line-height:1;">' + esc(p.role) + '</td>' +
        '</tr></table>' +
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td height="16">&nbsp;</td></tr></table>' +
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>' +
          '<td valign="top" width="180" style="width:180px;padding-right:16px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0">' +
            phone("Europe", p.phone_eu) + phone("USA", p.phone_us) + phone("UK", p.phone_uk) +
          '</table></td>' +
          '<td valign="top"><table role="presentation" cellpadding="0" cellspacing="0" border="0">' +
            line("&#9993;", '<a href="mailto:' + esc(p.email) + '" style="font-family:' + FONT + ';font-size:13px;color:' + INK + ';text-decoration:none;">' + esc(p.email) + '</a>') +
            line("&#127760;", '<a href="https://' + esc(p.website.replace(/^https?:\/\//, "")) + '" style="font-family:' + FONT + ';font-size:13px;color:' + INK + ';text-decoration:underline;">' + esc(p.website) + '</a>') +
            line("&#128205;", '<span style="font-family:' + FONT + ';font-size:13px;color:' + INK + ';">' + esc(p.address) + '</span>') +
          '</table></td>' +
        '</tr></table>' +
      '</td>' +
      '<td valign="top" width="96" align="right" style="width:96px;">' + logo + '</td></tr></table>';
  }

  var ORDER = ["heading", "text", "button", "image", "divider", "spacer", "signature"];

  // ---- Editor state ----------------------------------------------------------
  var model = [];        // [{id, type, props}]
  var selectedId = null;
  var uid = 1;

  function nid() { return "b" + (uid++); }

  // ---- Compile to full email HTML + text ------------------------------------
  function compileHtml() {
    var rows = model.map(function (b) { return BLOCKS[b.type].html(b.props); }).join("\n");
    return '<!doctype html><html><body style="margin:0;padding:0;background:#f5f4ef;">' +
      '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f4ef;padding:24px 0;">' +
      '<tr><td align="center"><table role="presentation" width="600" cellspacing="0" cellpadding="0" ' +
      'style="width:600px;max-width:94%;background:#ffffff;border-radius:14px;border:1px solid #e7e1d6;">' +
      '<tr><td style="padding:28px 32px;font-family:' + FONT + ';font-size:' + BASE_SIZE + 'px;color:' + INK + ';">' +
      '<table role="presentation" width="100%" cellspacing="0" cellpadding="0">' + rows + '</table>' +
      '<div style="border-top:1px solid #e7dfd0;margin-top:20px;padding-top:12px;font-family:' + FONT +
      ';font-size:12px;color:#9aa0a6;line-height:1.6;">{{company_legal_name}} · {{company_address}} · ' +
      '<a href="{{unsubscribe_url}}" style="color:#9aa0a6;">unsubscribe</a></div>' +
      '</td></tr></table></td></tr></table></body></html>';
  }
  function compileText() {
    return model.map(function (b) { return BLOCKS[b.type].text(b.props); }).join("") +
      "\n{{company_legal_name}} · {{company_address}}\nUnsubscribe: {{unsubscribe_url}}";
  }

  // ---- Render canvas ---------------------------------------------------------
  var canvas, inspector;

  function renderCanvas() {
    canvas.innerHTML = "";
    if (!model.length) {
      var empty = document.createElement("div");
      empty.className = "tb-empty";
      empty.textContent = "Drag blocks here from the left to start building.";
      canvas.appendChild(empty);
      return;
    }
    model.forEach(function (b, i) {
      var wrap = document.createElement("div");
      wrap.className = "tb-block" + (b.id === selectedId ? " sel" : "");
      wrap.setAttribute("draggable", "true");
      wrap.dataset.id = b.id;
      wrap.dataset.index = i;

      var body = document.createElement("div");
      body.className = "tb-block-body";
      // Render a lightweight preview by wrapping the compiled row in a table.
      body.innerHTML = '<table style="width:100%;border-collapse:collapse;">' + BLOCKS[b.type].html(b.props) + "</table>";
      wrap.appendChild(body);

      var tools = document.createElement("div");
      tools.className = "tb-block-tools";
      tools.innerHTML =
        '<span class="tb-tag">' + BLOCKS[b.type].label + '</span>' +
        '<button type="button" data-act="up" title="Move up">↑</button>' +
        '<button type="button" data-act="down" title="Move down">↓</button>' +
        '<button type="button" data-act="dup" title="Duplicate">⧉</button>' +
        '<button type="button" data-act="del" title="Delete">✕</button>';
      wrap.appendChild(tools);

      wrap.addEventListener("click", function (e) {
        if (e.target.closest(".tb-block-tools button")) return;
        selectBlock(b.id);
      });
      tools.addEventListener("click", function (e) {
        var act = e.target.getAttribute("data-act");
        if (!act) return;
        e.stopPropagation();
        if (act === "up") moveBlock(i, -1);
        else if (act === "down") moveBlock(i, 1);
        else if (act === "dup") duplicateBlock(i);
        else if (act === "del") deleteBlock(i);
      });

      // Reorder via drag within canvas.
      wrap.addEventListener("dragstart", function (e) {
        e.dataTransfer.setData("text/reorder", String(i));
        wrap.classList.add("dragging");
      });
      wrap.addEventListener("dragend", function () { wrap.classList.remove("dragging"); });
      wrap.addEventListener("dragover", function (e) { e.preventDefault(); wrap.classList.add("over"); });
      wrap.addEventListener("dragleave", function () { wrap.classList.remove("over"); });
      wrap.addEventListener("drop", function (e) {
        e.preventDefault(); e.stopPropagation();
        wrap.classList.remove("over");
        var from = e.dataTransfer.getData("text/reorder");
        var newType = e.dataTransfer.getData("text/newblock");
        if (from !== "") reorder(parseInt(from, 10), i);
        else if (newType) insertBlock(newType, i);
      });

      canvas.appendChild(wrap);
    });
  }

  function selectBlock(id) { selectedId = id; renderCanvas(); renderInspector(); }

  function renderInspector() {
    var b = model.filter(function (x) { return x.id === selectedId; })[0];
    if (!b) { inspector.innerHTML = '<div class="tb-hint">Select a block to edit it, or drag a new one in.</div>'; return; }
    var def = BLOCKS[b.type];
    var h = '<div class="tb-insp-title">' + def.label + " block</div>";
    def.fields.forEach(function (f) {
      var val = b.props[f.k] == null ? "" : b.props[f.k];
      h += '<label class="tb-field"><span>' + f.label + "</span>";
      if (f.t === "textarea") {
        h += '<textarea data-k="' + f.k + '" rows="5">' + esc(val) + "</textarea>";
      } else if (f.t === "align") {
        h += '<select data-k="' + f.k + '">' +
          ["left", "center", "right"].map(function (a) {
            return '<option value="' + a + '"' + (val === a ? " selected" : "") + ">" + a + "</option>";
          }).join("") + "</select>";
      } else if (f.t === "color") {
        h += '<input type="color" data-k="' + f.k + '" value="' + esc(val || "#000000") + '">';
      } else if (f.t === "number") {
        h += '<input type="number" data-k="' + f.k + '" value="' + esc(val) + '">';
      } else {
        h += '<input type="text" data-k="' + f.k + '" value="' + esc(val) + '">';
      }
      h += "</label>";
    });
    // Merge-tag inserter for text-ish blocks.
    if (b.type === "heading" || b.type === "text" || b.type === "button") {
      h += '<div class="tb-merge"><span>Insert personalization:</span><div>' +
        MERGE_TAGS.map(function (t) { return '<button type="button" class="tb-chip" data-tag="' + t + '">{{' + t + "}}</button>"; }).join("") +
        "</div></div>";
    }
    inspector.innerHTML = h;

    inspector.querySelectorAll("[data-k]").forEach(function (el) {
      el.addEventListener("input", function () {
        var k = el.getAttribute("data-k");
        b.props[k] = el.type === "number" ? Number(el.value) : el.value;
        renderCanvas();
      });
    });
    inspector.querySelectorAll(".tb-chip").forEach(function (el) {
      el.addEventListener("click", function () {
        var tag = "{{" + el.getAttribute("data-tag") + "}}";
        var target = inspector.querySelector('[data-k="text"]');
        if (target) {
          var s = target.selectionStart || target.value.length;
          target.value = target.value.slice(0, s) + tag + target.value.slice(s);
          b.props.text = target.value; renderCanvas(); target.focus();
        }
      });
    });
  }

  // ---- Model ops -------------------------------------------------------------
  function makeBlock(type) { return { id: nid(), type: type, props: BLOCKS[type].make() }; }
  function insertBlock(type, atIndex) {
    var b = makeBlock(type);
    if (atIndex == null || atIndex < 0) model.push(b); else model.splice(atIndex, 0, b);
    selectedId = b.id; renderCanvas(); renderInspector();
  }
  function reorder(from, to) {
    if (from === to) return;
    var b = model.splice(from, 1)[0];
    model.splice(to, 0, b); renderCanvas();
  }
  function moveBlock(i, dir) {
    var j = i + dir; if (j < 0 || j >= model.length) return;
    var b = model.splice(i, 1)[0]; model.splice(j, 0, b); renderCanvas();
  }
  function duplicateBlock(i) {
    var copy = { id: nid(), type: model[i].type, props: JSON.parse(JSON.stringify(model[i].props)) };
    model.splice(i + 1, 0, copy); selectedId = copy.id; renderCanvas(); renderInspector();
  }
  function deleteBlock(i) {
    if (model[i].id === selectedId) selectedId = null;
    model.splice(i, 1); renderCanvas(); renderInspector();
  }

  // ---- Boot ------------------------------------------------------------------
  function init(cfg) {
    canvas = document.getElementById("tb-canvas");
    inspector = document.getElementById("tb-inspector");
    var palette = document.getElementById("tb-palette");

    // Build palette.
    ORDER.forEach(function (type) {
      var item = document.createElement("div");
      item.className = "tb-pal-item";
      item.setAttribute("draggable", "true");
      item.innerHTML = '<span class="tb-pal-icon">' + BLOCKS[type].icon + "</span>" + BLOCKS[type].label;
      item.addEventListener("dragstart", function (e) { e.dataTransfer.setData("text/newblock", type); });
      item.addEventListener("click", function () { insertBlock(type, null); });
      palette.appendChild(item);
    });

    // Canvas is also a drop target (append to end).
    canvas.addEventListener("dragover", function (e) { e.preventDefault(); });
    canvas.addEventListener("drop", function (e) {
      if (e.target !== canvas && e.target.className !== "tb-empty") return;
      e.preventDefault();
      var newType = e.dataTransfer.getData("text/newblock");
      if (newType) insertBlock(newType, null);
    });

    // Load existing block model, else a sensible starter.
    if (cfg.builder && cfg.builder.length) {
      model = cfg.builder.map(function (b) {
        var t = BLOCKS[b.type] ? b.type : "text";
        var props = Object.assign(BLOCKS[t].make(), b.props || {});
        return { id: nid(), type: t, props: props };
      });
    } else {
      model = [
        { id: nid(), type: "heading", props: Object.assign(BLOCKS.heading.make(), { text: "A premium finishing touch for {{company_name}}" }) },
        { id: nid(), type: "text", props: BLOCKS.text.make() },
        { id: nid(), type: "button", props: BLOCKS.button.make() },
        { id: nid(), type: "signature", props: BLOCKS.signature.make() }
      ];
    }
    renderCanvas(); renderInspector();

    // Save: compile + write hidden fields + submit.
    var form = document.getElementById("tb-form");
    form.addEventListener("submit", function () {
      document.getElementById("tb-body-html").value = compileHtml();
      document.getElementById("tb-body-text").value = compileText();
      document.getElementById("tb-builder-json").value = JSON.stringify(
        model.map(function (b) { return { type: b.type, props: b.props }; })
      );
    });

    // Live preview modal.
    var previewBtn = document.getElementById("tb-preview-btn");
    if (previewBtn) {
      previewBtn.addEventListener("click", function () {
        var f = document.getElementById("tb-preview-frame");
        var sample = compileHtml()
          .replace(/\{\{company_name\}\}/g, "Van Dijk Staal")
          .replace(/\{\{greeting_name\}\}/g, "Ruben")
          .replace(/\{\{opener\}\}/g, "I came across Van Dijk Staal in Rotterdam and wanted to reach out.")
          .replace(/\{\{[a-z_]+\}\}/g, "");
        f.srcdoc = sample;
        document.getElementById("tb-preview-modal").style.display = "flex";
      });
      document.getElementById("tb-preview-close").addEventListener("click", function () {
        document.getElementById("tb-preview-modal").style.display = "none";
      });
    }
  }

  window.SchildBuilder = { init: init };
})();
