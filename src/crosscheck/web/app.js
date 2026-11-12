"use strict";
// All user-supplied text is rendered with textContent or text nodes, never parsed as markup.

const SEVERITIES = ["discrepancy", "data_quality", "unverified"];
const SEVERITY_TITLE = {
  discrepancy: "Discrepancies",
  data_quality: "Source conflicts",
  unverified: "Unverified",
};
const SEVERITY_HELP = {
  discrepancy: "Evidence in the sources",
  data_quality: "The sources disagree with each other",
  unverified: "Nothing found; absence is not proof",
};
const SEVERITY_BADGE = { discrepancy: "Discrepancy", data_quality: "Source conflict", unverified: "Unverified" };
const MAX_NOTES = 5;
const SIDEBAR_LIMIT = 20;
const AUTO_CHECK_DELAY_MS = 700;

const $ = (id) => document.getElementById(id);
const state = {
  apps: [],
  appId: null,
  form: blankForm(),
  reviews: {},
  result: null,
  resultNotes: [],
  filter: null,
  cache: {},
  graph: null,
  seq: 0,
  timer: null,
};

function blankForm() {
  return { name: "", mrn: "", conditions: [], medications: [], allergies: [], notes: [{ docId: null, text: "" }], pharmacy: [] };
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* keep the status text */ }
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

function toast(message, kind = "info") {
  const node = el("div", { class: `toast ${kind}`, text: message });
  $("toasts").append(node);
  setTimeout(() => node.remove(), 4500);
}

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.className = isError ? "error-text" : "muted";
}

/* theme */
function applyTheme(theme, persist) {
  document.documentElement.setAttribute("data-theme", theme);
  if (persist) {
    try { localStorage.setItem("theme", theme); } catch (_) { /* private mode */ }
  }
  const dark = theme === "dark";
  const button = $("theme-toggle");
  button.setAttribute("aria-pressed", String(dark));
  button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
}

function initTheme() {
  applyTheme(document.documentElement.getAttribute("data-theme") || "light", false);
  $("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next, true);
  });
}

/* tabs */
function showTab(tabsId, name) {
  const tabs = $(tabsId);
  tabs.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  tabs.parentElement.querySelectorAll(":scope > .tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== name; });
}

function initTabs() {
  for (const id of ["editor-tabs", "result-tabs"]) {
    $(id).querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(id, b.dataset.tab)));
  }
}

/* sidebar */
function renderSidebar() {
  const query = $("search").value.trim().toLowerCase();
  const onlyFlawed = $("only-flawed").checked;
  const list = $("app-list");
  list.replaceChildren();
  const shown = state.apps.filter((a) =>
    (!onlyFlawed || a.planted_errors > 0) &&
    (!query || a.name.toLowerCase().includes(query) || a.app_id.toLowerCase().includes(query)));
  if (!shown.length) list.append(el("li", { class: "muted small", text: "No applications match." }));
  const visible = shown.slice(0, SIDEBAR_LIMIT);
  if (shown.length > visible.length) {
    list.append(el("li", { class: "muted small more", text: `Showing the first ${visible.length} of ${shown.length}. Type in the search box to find others.` }));
  }
  for (const app of visible) {
    const cached = state.cache[app.app_id];
    let badge;
    if (cached === undefined) badge = el("span", { class: "count", text: `${app.planted_errors} planted` });
    else if (cached === 0) badge = el("span", { class: "count clean", text: "clean" });
    else badge = el("span", { class: "count hit", text: `${cached} flag${cached === 1 ? "" : "s"}` });
    const button = el(
      "button",
      { type: "button", class: "app-item", "aria-current": String(state.appId === app.app_id), onclick: () => selectApp(app.app_id) },
      el("span", {}, el("span", { class: "name", text: app.name }), el("span", { class: "id", text: app.app_id })),
      badge,
    );
    list.append(el("li", {}, button));
  }
}

/* editors */
function markDirty() {
  if ($("extractor").value === "rules") {
    setStatus("Edited; re-checking...");
    clearTimeout(state.timer);
    state.timer = setTimeout(runCheck, AUTO_CHECK_DELAY_MS);
  } else {
    setStatus("Edited. Press Run check to use the model.");
  }
}

function renderChips(kind) {
  const box = $(`${kind}-chips`);
  box.replaceChildren();
  state.form[kind].forEach((value, index) => {
    const remove = el("button", {
      type: "button",
      "aria-label": `Remove ${value}`,
      text: "×",
      onclick: () => { state.form[kind].splice(index, 1); renderChips(kind); markDirty(); },
    });
    box.append(el("span", { class: "chip" }, document.createTextNode(value), remove));
  });
}

function bindChipInput(kind) {
  const input = $(`${kind}-input`);
  const add = () => {
    const parts = input.value.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return;
    state.form[kind].push(...parts);
    input.value = "";
    renderChips(kind);
    markDirty();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      add();
    } else if (event.key === "Backspace" && !input.value && state.form[kind].length) {
      state.form[kind].pop();
      renderChips(kind);
      markDirty();
    }
  });
  input.addEventListener("blur", add);
}

function textInput(value, label, placeholder, onChange, type = "text") {
  const input = el("input", { type, "aria-label": label, placeholder, autocomplete: "off" });
  if (type === "number") { input.min = "1"; input.max = "5000"; }
  input.value = value ?? "";
  input.addEventListener("input", () => onChange(input.value));
  return input;
}

function removeButton(onClick, label) {
  return el("button", { type: "button", class: "ghost x", "aria-label": label, text: "×", onclick: onClick });
}

function renderMedRows() {
  const box = $("med-rows");
  box.replaceChildren();
  state.form.medications.forEach((med, index) => {
    box.append(el(
      "div",
      { class: "row two" },
      textInput(med.name, "Medication name", "name, e.g. metformin", (v) => { med.name = v; markDirty(); }),
      textInput(med.dose, "Dose in mg", "mg", (v) => { med.dose = v; markDirty(); }, "number"),
      removeButton(() => { state.form.medications.splice(index, 1); renderMedRows(); markDirty(); }, "Remove medication"),
    ));
  });
}

function renderPharmRows() {
  const box = $("pharm-rows");
  box.replaceChildren();
  state.form.pharmacy.forEach((row, index) => {
    box.append(el(
      "div",
      { class: "row" },
      textInput(row.drug, "Drug", "drug", (v) => { row.drug = v; markDirty(); }),
      textInput(row.dose, "Dose in mg", "mg", (v) => { row.dose = v; markDirty(); }, "number"),
      textInput(row.date, "Date dispensed", "date (optional)", (v) => { row.date = v; markDirty(); }),
      removeButton(() => { state.form.pharmacy.splice(index, 1); renderPharmRows(); markDirty(); }, "Remove pharmacy row"),
    ));
  });
}

function renderNotes() {
  const box = $("notes");
  box.replaceChildren();
  state.form.notes.forEach((note, index) => {
    const area = el("textarea", { rows: "9", "aria-label": `Clinical note ${index + 1}`, placeholder: "Paste or write a clinical note..." });
    area.value = note.text;
    area.addEventListener("input", () => { note.text = area.value; markDirty(); });
    box.append(el(
      "div",
      { class: "note-card" },
      el(
        "div",
        { class: "note-head" },
        el("span", { text: `Note ${note.docId || `N${index + 1}`}` }),
        el("button", {
          type: "button",
          class: "ghost",
          text: "Remove",
          onclick: () => { state.form.notes.splice(index, 1); renderNotes(); markDirty(); },
        }),
      ),
      area,
    ));
  });
  $("add-note").disabled = state.form.notes.length >= MAX_NOTES;
}

function renderEditor() {
  $("name").value = state.form.name;
  $("mrn-input").value = state.form.mrn || "";
  renderChips("conditions");
  renderChips("allergies");
  renderMedRows();
  renderPharmRows();
  renderNotes();
}

function setDbInfo(message) {
  $("db-info").textContent = message;
}

async function loadPharmacyFromDb() {
  const mrn = $("mrn-input").value.trim();
  if (!mrn) {
    toast("Enter an applicant ID (MRN) first.", "error");
    return;
  }
  if (!/^[A-Za-z0-9_-]{1,20}$/.test(mrn)) {
    toast("Applicant IDs use letters, digits, - and _ (up to 20 characters).", "error");
    return;
  }
  try {
    const found = await api(`/api/pharmacy/${encodeURIComponent(mrn)}`);
    state.form.mrn = mrn;
    state.form.pharmacy = found.rows.map((r) => ({ drug: r.drug, dose: String(r.dose), date: r.last_dispensed }));
    renderPharmRows();
    setDbInfo(found.rows.length
      ? `${found.rows.length} row(s) read from the pharmacy database for MRN ${mrn}.`
      : `No rows for MRN ${mrn} in the pharmacy database.`);
    markDirty();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* data loading */
async function selectApp(id) {
  clearTimeout(state.timer);
  state.seq += 1;
  if (id === null) {
    state.appId = null;
    state.form = blankForm();
    setDbInfo("");
    $("case-label").textContent = "Blank application";
    $("form-box").hidden = true;
    $("retrieve-results").replaceChildren();
    refreshHistory();
    $("answer-key-box").hidden = true;
    clearResults();
    renderEditor();
    renderSidebar();
    return;
  }
  try {
    const detail = await api(`/api/applications/${encodeURIComponent(id)}`);
    state.appId = id;
    state.form = {
      name: detail.application.name,
      mrn: detail.mrn,
      conditions: [...detail.application.conditions],
      medications: detail.application.medications.map((m) => ({ name: m.name, dose: String(m.dose) })),
      allergies: [...detail.application.allergies],
      notes: detail.notes.map((n) => ({ docId: n.doc_id, text: n.text })),
      pharmacy: detail.pharmacy.map((r) => ({ drug: r.drug, dose: String(r.dose), date: r.last_dispensed })),
    };
    if (!state.form.notes.length) state.form.notes.push({ docId: null, text: "" });
    $("case-label").textContent = `${id} (generated)`;
    $("form-text").textContent = detail.form_text;
    $("form-box").hidden = false;
    const issues = $("form-issues");
    issues.hidden = !detail.unmapped_declared.length;
    issues.textContent = detail.unmapped_declared.length
      ? `The scraper could not map: ${detail.unmapped_declared.join("; ")}. These stay out of the comparison.`
      : "";
    $("retrieve-results").replaceChildren();
    setDbInfo(`${detail.pharmacy.length} row(s) read from the pharmacy database for MRN ${detail.mrn}.`);
    $("answer-key-box").hidden = false;
    $("answer-key").replaceChildren();
    clearResults();
    renderEditor();
    renderSidebar();
    refreshHistory();
    showPendingReviews();
    if ($("extractor").value === "rules") runCheck();
    else setStatus("Loaded. Press Run check to use the model.");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* checking */
function topK() {
  const value = Number($("top-k").value);
  return Number.isInteger(value) && value >= 1 && value <= 10 ? value : 3;
}

function buildPayload() {
  const form = state.form;
  const medications = [];
  for (const med of form.medications) {
    const name = String(med.name).trim();
    const doseText = String(med.dose).trim();
    if (!name && !doseText) continue;
    const dose = Number(doseText);
    if (!name || !Number.isInteger(dose) || dose < 1 || dose > 5000) {
      return { error: `Medication "${name || "?"}" needs a name and a dose from 1 to 5000 mg.` };
    }
    medications.push({ name, dose, unit: "mg" });
  }
  const pharmacy = [];
  for (const row of form.pharmacy) {
    const drug = String(row.drug).trim();
    const doseText = String(row.dose).trim();
    if (!drug && !doseText) continue;
    const dose = Number(doseText);
    if (!drug || !Number.isInteger(dose) || dose < 1 || dose > 5000) {
      return { error: `Pharmacy row "${drug || "?"}" needs a drug and a dose from 1 to 5000 mg.` };
    }
    pharmacy.push({ drug, dose, unit: "mg", last_dispensed: String(row.date).trim() || "unknown" });
  }
  const notes = form.notes
    .map((n) => ({ doc_id: n.docId || null, text: n.text }))
    .filter((n) => n.text.trim());
  return {
    payload: {
      app_id: state.appId || "adhoc",
      application: {
        name: form.name.trim() || "Applicant",
        conditions: form.conditions,
        medications,
        allergies: form.allergies,
      },
      notes,
      pharmacy,
      extractor: $("extractor").value,
      retrieval: $("retrieval").value,
      top_k: topK(),
    },
  };
}

async function runCheck() {
  clearTimeout(state.timer);
  const built = buildPayload();
  if (built.error) {
    setStatus(built.error, true);
    return;
  }
  const token = ++state.seq;
  setStatus("Checking...");
  $("check").disabled = true;
  try {
    const result = await api("/api/check", { method: "POST", body: JSON.stringify(built.payload) });
    if (token !== state.seq) return;
    state.result = result;
    state.resultNotes = built.payload.notes;
    state.filter = null;
    if (state.appId) state.cache[state.appId] = result.flags.length;
    renderResult();
    renderSidebar();
    refreshHistory();
    const when = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const read = result.retrieval ? ` reading ${Math.round((100 * result.retrieval.chars_sent) / Math.max(1, result.retrieval.chars_total))}% of the note text (RAG)` : "";
    setStatus(`Checked at ${when} with the ${result.extractor} extractor${read}.`);
  } catch (error) {
    if (token === state.seq) setStatus(error.message, true);
  } finally {
    if (token === state.seq) $("check").disabled = false;
  }
}

function clearResults() {
  state.result = null;
  state.resultNotes = [];
  state.filter = null;
  for (const id of ["stats", "warnings", "flags", "compare", "facts", "evidence", "pharm-evidence", "retrieval-info", "history", "workflow-view"]) $(id).replaceChildren();
  $("empty").hidden = false;
  $("facts-box").hidden = true;
  $("copy-report").disabled = true;
  $("download-json").disabled = true;
  setStatus("");
}

/* results */
function reviewedCount(flags) {
  return flags.filter((f) => state.reviews[f.id]).length;
}

function renderStats() {
  const box = $("stats");
  box.replaceChildren();
  const flags = state.result.flags;
  for (const severity of SEVERITIES) {
    const count = flags.filter((f) => f.severity === severity).length;
    const card = el(
      "button",
      {
        type: "button",
        class: `stat ${severity}`,
        "aria-pressed": String(state.filter === severity),
        "aria-label": `${SEVERITY_TITLE[severity]}: ${count}. Click to filter.`,
        onclick: () => { state.filter = state.filter === severity ? null : severity; renderStats(); renderFlags(); },
      },
      el("span", { class: "n", text: String(count) }),
      el("span", { class: "t", text: SEVERITY_TITLE[severity] }),
    );
    box.append(card);
  }
  box.append(el(
    "div",
    { class: "stat reviewed" },
    el("span", { class: "n", text: `${reviewedCount(flags)}/${flags.length}` }),
    el("span", { class: "t", text: "Reviewed" }),
  ));
}

function renderWarnings() {
  const box = $("warnings");
  box.replaceChildren();
  const r = state.result;
  const add = (message) => box.append(el("div", { class: "warning", text: message }));
  if (r.unmapped_declared.length) add(`Not recognised, left out of the comparison: ${r.unmapped_declared.join("; ")}`);
  if (r.unmapped_extracted.length) add(`Terms found in the notes with no canonical mapping: ${r.unmapped_extracted.join("; ")}`);
  if (r.dropped_for_missing_evidence) add(`${r.dropped_for_missing_evidence} flag(s) were dropped because their evidence could not be verified.`);
}

function locate(docId, quote) {
  showTab("result-tabs", "evidence");
  const wanted = quote.toLowerCase();
  const marks = [...$("evidence").querySelectorAll("mark")];
  const mark = marks.find((m) => m.dataset.doc === docId && m.dataset.q === wanted);
  if (!mark) return;
  mark.scrollIntoView({ block: "center", behavior: "smooth" });
  mark.classList.add("pulse");
  setTimeout(() => mark.classList.remove("pulse"), 1600);
}

function flagCard(flag) {
  const head = el(
    "div",
    { class: "card-head" },
    el("span", { class: `badge ${flag.severity}`, text: SEVERITY_BADGE[flag.severity] }),
    el("span", { class: "field", text: flag.field }),
    el("span", { class: "type", text: flag.type }),
  );
  const card = el("div", { class: `card ${flag.severity}` }, head, el("div", { text: flag.message }));
  for (const evidence of flag.evidence) {
    const where = evidence.doc_id ? `note ${evidence.doc_id}` : "pharmacy database";
    const line = el(
      "div",
      { class: "evidence-line" },
      el("span", {}, el("span", { class: "src", text: where }), el("span", { class: "q", text: `"${evidence.quote}"` })),
    );
    if (evidence.doc_id) line.append(el("button", { type: "button", class: "link", text: "Locate", onclick: () => locate(evidence.doc_id, evidence.quote) }));
    card.append(line);
  }
  if (flag.severity === "unverified") {
    card.append(el("div", { class: "muted small", text: `Checked notes: ${flag.checked_docs.join(", ") || "none"}` }));
  }
  const decisions = el("div", { class: "decisions" }, el("span", { text: "Reviewer decision:" }));
  const buttons = {};
  const refresh = () => {
    for (const [name, button] of Object.entries(buttons)) {
      button.setAttribute("aria-pressed", String(state.reviews[flag.id] === name));
    }
  };
  for (const name of ["Confirm", "Dismiss"]) {
    buttons[name] = el("button", {
      type: "button",
      class: "choice",
      text: name,
      onclick: async () => {
        const next = state.reviews[flag.id] === name ? "—" : name;
        try {
          state.reviews = await api("/api/reviews", { method: "PUT", body: JSON.stringify({ flag_id: flag.id, decision: next, run_id: state.result ? state.result.run_id : null }) });
          refresh();
          renderStats();
          refreshFeedback();
        } catch (error) {
          toast(error.message, "error");
        }
      },
    });
    decisions.append(buttons[name]);
  }
  refresh();
  card.append(decisions);
  return card;
}

function renderFlags() {
  const box = $("flags");
  box.replaceChildren();
  const flags = state.result.flags;
  $("empty").hidden = true;
  if (!flags.length) {
    box.append(el("div", { class: "card ok" }, el("div", { class: "card-head" }, el("span", { class: "badge ok", text: "All clear" })), el("div", { text: "No flags: the application matches the sources." })));
    return;
  }
  for (const severity of SEVERITIES) {
    if (state.filter && state.filter !== severity) continue;
    const group = flags.filter((f) => f.severity === severity);
    if (!group.length) continue;
    box.append(el("h3", { text: `${SEVERITY_TITLE[severity]} · ${SEVERITY_HELP[severity]}` }));
    for (const flag of group) box.append(flagCard(flag));
  }
}

const union = (...lists) => [...new Set(lists.flat())].sort();

function statusCell(flags) {
  const cell = el("td");
  if (!flags.length) {
    cell.append(el("span", { class: "badge ok", text: "match" }));
    return cell;
  }
  for (const flag of flags) {
    cell.append(el("span", { class: `badge ${flag.severity}`, text: flag.type.replaceAll("_", " "), title: flag.message }), document.createTextNode(" "));
  }
  return cell;
}

function renderCompare() {
  const box = $("compare");
  box.replaceChildren();
  const r = state.result;
  const flagsFor = (field, types) => r.flags.filter((f) => f.field === field && types.includes(f.type));
  const sections = [
    {
      title: "Conditions",
      types: ["undisclosed_condition", "contradicted_declaration", "unverified_declaration"],
      items: union(r.declared.conditions, r.facts.conditions, r.facts.negated_conditions),
      declared: (i) => (r.declared.conditions.includes(i) ? "declared" : "—"),
      notes: (i) => (r.facts.conditions.includes(i) ? "documented" : r.facts.negated_conditions.includes(i) ? "denied" : "not found"),
      pharmacy: () => "n/a",
    },
    {
      title: "Medications",
      types: ["undisclosed_medication", "dose_mismatch", "source_conflict", "unverified_medication"],
      items: union(Object.keys(r.declared.medications), Object.keys(r.facts.medications), Object.keys(r.pharmacy)),
      declared: (i) => (i in r.declared.medications ? `${r.declared.medications[i]} mg` : "—"),
      notes: (i) => (i in r.facts.medications ? (r.facts.medications[i].map((d) => `${d} mg`).join(" / ") || "mentioned") : "—"),
      pharmacy: (i) => (i in r.pharmacy ? `${r.pharmacy[i]} mg` : "—"),
    },
    {
      title: "Allergies",
      types: ["undisclosed_allergy"],
      items: union(r.declared.allergies, r.facts.allergies),
      declared: (i) => (r.declared.allergies.includes(i) ? "declared" : "—"),
      notes: (i) => (r.facts.allergies.includes(i) ? "documented" : "—"),
      pharmacy: () => "n/a",
    },
  ];
  const body = el("tbody");
  for (const section of sections) {
    if (!section.items.length) continue;
    body.append(el("tr", { class: "section" }, el("td", { colspan: "5", text: section.title })));
    for (const item of section.items) {
      const cell = (text, cls = "") => el("td", { class: cls, text });
      const notes = section.notes(item);
      const row = el(
        "tr",
        {},
        cell(item),
        cell(section.declared(item), section.declared(item) === "—" ? "miss" : ""),
        cell(notes, notes === "denied" ? "denied" : notes === "not found" || notes === "—" ? "miss" : ""),
        cell(section.pharmacy(item), section.pharmacy(item) === "n/a" || section.pharmacy(item) === "—" ? "miss" : ""),
        statusCell(flagsFor(item, section.types)),
      );
      body.append(row);
    }
  }
  if (!body.children.length) {
    box.append(el("p", { class: "empty", text: "Nothing to compare yet: add conditions, medications or notes." }));
    return;
  }
  const head = el("thead", {}, el("tr", {}, ...["Item", "Declared", "Notes", "Pharmacy", "Status"].map((t) => el("th", { text: t }))));
  box.append(el("div", { class: "table-wrap" }, el("table", {}, head, body)));
}

function renderFacts() {
  const facts = state.result.facts;
  const box = $("facts");
  box.replaceChildren();
  const row = (label, value) => box.append(el("p", {}, el("strong", { text: `${label}: ` }), document.createTextNode(value || "none")));
  row("Conditions", facts.conditions.join(", "));
  row("Explicitly denied", facts.negated_conditions.join(", "));
  row("Medications", Object.entries(facts.medications).map(([drug, doses]) => `${drug} ${doses.join("/")} mg`).join(", "));
  row("Allergies", facts.allergies.join(", "));
  $("facts-box").hidden = false;
}

function highlightInto(container, text, quotes, docId) {
  const lower = text.toLowerCase();
  const ranges = [];
  for (const quote of quotes) {
    const start = lower.indexOf(quote.toLowerCase());
    if (start >= 0 && !ranges.some(([a, b]) => start < b && start + quote.length > a)) {
      ranges.push([start, start + quote.length, quote.toLowerCase()]);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  let cursor = 0;
  for (const [start, end, q] of ranges) {
    container.append(document.createTextNode(text.slice(cursor, start)));
    const mark = el("mark", { text: text.slice(start, end) });
    mark.dataset.doc = docId;
    mark.dataset.q = q;
    container.append(mark);
    cursor = end;
  }
  container.append(document.createTextNode(text.slice(cursor)));
}

function renderEvidence() {
  const box = $("evidence");
  box.replaceChildren();
  const r = state.result;
  if (!state.resultNotes.length) box.append(el("p", { class: "empty", text: "No clinical notes were provided." }));
  state.resultNotes.forEach((note, index) => {
    const id = note.doc_id || `N${index + 1}`;
    const quotes = r.flags.flatMap((f) => f.evidence.filter((e) => e.doc_id === id).map((e) => e.quote));
    const body = el("div", { class: "evidence-note" }, el("div", { class: "title", text: `Note ${id}` }));
    highlightInto(body, note.text, quotes, id);
    box.append(body);
  });
  const pharm = $("pharm-evidence");
  pharm.replaceChildren();
  const cited = [...new Set(r.flags.flatMap((f) => f.evidence.filter((e) => e.source === "pharmacy").map((e) => e.quote)))];
  if (cited.length) {
    pharm.append(el("h3", { text: "Pharmacy rows cited by flags" }));
    for (const quote of cited) pharm.append(el("div", { class: "evidence-line" }, el("span", { class: "q", text: quote })));
  }
}

function renderResult() {
  renderStats();
  renderWarnings();
  renderFlags();
  renderCompare();
  renderFacts();
  renderEvidence();
  renderRetrievalTab();
  $("copy-report").disabled = false;
  $("download-json").disabled = false;
}

function passageRow(hit) {
  return el(
    "div",
    { class: "passage" },
    el("span", { class: "where", text: `${hit.doc_id} · line ${hit.line + 1}` }),
    el("span", { class: "text", text: hit.text }),
    el("span", { class: "score", text: hit.score.toFixed(2), title: `Matched: ${hit.queries.join(" | ")}` }),
  );
}

function renderRetrievalTab() {
  const box = $("retrieval-info");
  box.replaceChildren();
  const info = state.result.retrieval;
  if (!info) {
    box.append(el("p", { class: "empty", text: "The extractor read all of the note text. Set \"Notes read\" to Retrieved passages (RAG) to see which lines are retrieved." }));
    return;
  }
  const share = Math.round((100 * info.chars_sent) / Math.max(1, info.chars_total));
  box.append(el("p", {}, el("strong", { text: `${share}% of the note text was sent to the extractor` }), document.createTextNode(` (${info.chars_sent} of ${info.chars_total} characters). Top ${info.k} passages for each of ${info.queries.length} queries, ranked by embedding similarity (${info.embedder}).`)));
  const meter = el("div", { class: "meter" }, el("span"));
  meter.firstChild.style.width = `${share}%`;
  box.append(meter);
  const sent = new Set(info.hits.map((h) => `${h.doc_id}|${h.line}`));
  state.resultNotes.forEach((note, index) => {
    const id = note.doc_id || `N${index + 1}`;
    const card = el("div", { class: "evidence-note" }, el("div", { class: "title", text: `Note ${id}: sent lines are highlighted, struck-through lines were not read` }));
    note.text.split("\n").map((line) => line.trim()).filter(Boolean).forEach((line, lineIndex) => {
      card.append(el("span", { class: sent.has(`${id}|${lineIndex}`) ? "sent-line" : "skipped-line", text: line }));
    });
    box.append(card);
  });
  box.append(el("h3", { text: "Passages and their scores" }));
  for (const hit of info.hits) box.append(passageRow(hit));
  if (!info.hits.length) box.append(el("p", { class: "empty", text: "Nothing in the notes matched the queries." }));
}

async function searchRecord() {
  const box = $("retrieve-results");
  if (!state.appId) {
    toast("Choose a stored application first: its notes are indexed in the database.", "error");
    return;
  }
  const query = $("retrieve-input").value.trim();
  try {
    const params = new URLSearchParams({ k: String(topK()) });
    if (query) params.set("q", query);
    const found = await api(`/api/applications/${encodeURIComponent(state.appId)}/retrieve?${params}`);
    box.replaceChildren();
    const share = Math.round((100 * found.chars_sent) / Math.max(1, found.chars_total));
    box.append(el("p", { class: "muted small", text: query
      ? `Top ${found.k} passages for "${query}" (${found.embedder}).`
      : `A check reads these passages: ${share}% of this applicant's note text, from ${found.queries.length} queries (${found.embedder}).` }));
    for (const hit of found.hits) box.append(passageRow(hit));
    if (!found.hits.length) {
      box.append(el("p", { class: "empty", text: "No passage matched. The offline embedder matches words plus known abbreviations and brand names (HTN = hypertension); only a real embedding model also matches paraphrases." }));
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

function pct(part, whole) {
  return whole ? `${Math.round((100 * part) / whole)}%` : "n/a";
}

function runSummary(run) {
  const when = new Date(run.ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const read = run.retrieval === "rag" ? `RAG top-${run.top_k}, ${pct(run.chars_sent, run.chars_total)} of text` : "all text";
  const what = `${run.extractor === "llm" ? run.model || "model" : "rules"} · ${read} · ${run.n_flags} flag${run.n_flags === 1 ? "" : "s"}`;
  const cost = run.cost_usd ? `$${run.cost_usd.toFixed(4)}` : "free";
  return el("summary", {}, el("span", { class: "when", text: when }), el("span", { class: "what", text: what }), el("span", { class: "meta", text: `${cost} · ${run.duration_ms} ms` }));
}

async function openRun(run, body) {
  try {
    const full = await api(`/api/audit/runs/${run.run_id}`);
    body.replaceChildren();
    const facts = [
      ["Run", full.run_id],
      ["Source / status", `${full.source} / ${full.status}`],
      ["Extractor", full.extractor],
      ["Model", full.model || "none"],
      ["Prompt version", full.prompt_version || "none"],
      ["Embedder", full.embedder || "none"],
      ["Tokens in / out", `${full.input_tokens} / ${full.output_tokens}`],
      ["Dropped for missing evidence", String(full.dropped)],
      ["Unmapped note terms", full.unmapped.join("; ") || "none"],
    ];
    const grid = el("div", { class: "kv" });
    for (const [key, value] of facts) grid.append(el("span", { text: key }), el("span", { text: value }));
    body.append(grid);
    if (!full.flags.length) body.append(el("p", { class: "muted", text: "No flags in this run." }));
    for (const flag of full.flags) {
      const card = el("div", { class: `card ${flag.severity}` }, el("div", { class: "card-head" }, el("span", { class: `badge ${flag.severity}`, text: SEVERITY_BADGE[flag.severity] }), el("span", { class: "field", text: flag.field }), el("span", { class: "type", text: flag.type })));
      for (const e of flag.evidence) card.append(el("div", { class: "evidence-line" }, el("span", {}, el("span", { class: "src", text: e.doc_id ? `note ${e.doc_id}` : "pharmacy database" }), el("span", { class: "q", text: `"${e.quote}"` }))));
      body.append(card);
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

async function refreshHistory() {
  const box = $("history");
  box.replaceChildren();
  if (!state.appId) {
    box.append(el("p", { class: "empty", text: "Choose a stored application to see its history." }));
    return;
  }
  try {
    const runs = await api(`/api/audit/runs?app_id=${encodeURIComponent(state.appId)}&limit=20`);
    if (!runs.length) box.append(el("p", { class: "empty", text: "No checks recorded for this application yet." }));
    for (const run of runs) {
      const body = el("div", { class: "run-body" });
      const details = el("details", { class: "run" }, runSummary(run), body);
      details.addEventListener("toggle", () => { if (details.open && !body.children.length) openRun(run, body); });
      box.append(details);
    }
  } catch (error) {
    box.append(el("p", { class: "warning", text: error.message }));
  }
}

async function refreshFeedback() {
  const box = $("review-stats");
  try {
    const stats = (await api("/api/review-stats")).by_type;
    box.replaceChildren();
    const types = Object.keys(stats);
    if (!types.length) {
      box.append(el("p", { class: "muted small", text: "No reviewer decisions yet. Confirm or Dismiss a flag to start." }));
      return;
    }
    const head = el("thead", {}, el("tr", {}, ...["Flag type", "Confirmed", "Dismissed", "Dismissed share"].map((t) => el("th", { text: t }))));
    const body = el("tbody");
    for (const type of types) {
      const row = stats[type];
      body.append(el("tr", {}, el("td", { text: type }), el("td", { text: String(row.Confirm) }), el("td", { text: String(row.Dismiss) }), el("td", { text: `${Math.round(row.dismiss_rate * 100)}%` })));
    }
    box.append(el("div", { class: "table-wrap" }, el("table", {}, head, body)));
    box.append(el("p", { class: "muted small", text: "A high dismissed share means the flag type is noisy: a reviewer signal worth feeding back into the evaluation." }));
  } catch (error) {
    box.replaceChildren(el("p", { class: "warning", text: error.message }));
  }
}

const REASON_TEXT = {
  flags: "There are flags to look at.",
  unmapped_terms: "The notes contain terms the system could not map to a known name.",
  no_facts_read: "Notes exist, but nothing could be read from them, so 'no flags' would mean nothing.",
};

function pathChips(path) {
  const box = el("div", { class: "path" });
  path.forEach((step, index) => {
    if (index) box.append(el("span", { class: "arrow", text: "→" }));
    box.append(el("span", { class: step === "human_review" ? "step pause" : "step", text: step.replaceAll("_", " ") }));
  });
  return box;
}

/* the graph diagram, drawn from LangGraph's own description of itself */
const SVG_NS = "http://www.w3.org/2000/svg";
const NODE_W = 118;
const NODE_H = 30;
const NODE_GAP = 44;

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function layoutGraph(graph) {
  const depth = {};
  graph.nodes.forEach((n) => { depth[n] = 0; });
  let changed = true;
  for (let guard = 0; changed && guard < 50; guard += 1) {
    changed = false;
    for (const edge of graph.edges) {
      if (depth[edge.target] < depth[edge.source] + 1) {
        depth[edge.target] = depth[edge.source] + 1;
        changed = true;
      }
    }
  }
  const ordered = [...graph.nodes].sort((a, b) => depth[a] - depth[b] || a.localeCompare(b));
  const pos = {};
  ordered.forEach((n, i) => { pos[n] = { x: 72, y: 20 + i * NODE_GAP, i }; });
  return { ordered, pos };
}

function drawGraph(graph) {
  const { ordered, pos } = layoutGraph(graph);
  const svg = svgEl("svg", {
    viewBox: `0 0 250 ${20 + ordered.length * NODE_GAP}`,
    class: "wf-svg",
    role: "img",
    "aria-label": "The LangGraph workflow graph",
  });
  const marker = svgEl("marker", { id: "wf-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto" });
  marker.append(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "garrow" }));
  svg.append(svgEl("defs", {}, undefined));
  svg.firstChild.append(marker);
  const arcs = {};
  for (const edge of graph.edges) {
    const from = pos[edge.source];
    const to = pos[edge.target];
    const diff = to.i - from.i;
    const cls = edge.conditional ? "gedge cond" : "gedge";
    let d;
    if (diff === 1) {
      d = `M ${from.x} ${from.y + NODE_H / 2} L ${to.x} ${to.y - NODE_H / 2 - 3}`;
    } else {
      arcs[edge.source] = (arcs[edge.source] || 0) + 1;
      const bulge = 26 + 11 * diff + 8 * arcs[edge.source];
      const x = from.x + NODE_W / 2;
      d = `M ${x} ${from.y} C ${x + bulge} ${from.y}, ${x + bulge} ${to.y}, ${x + 3} ${to.y}`;
    }
    svg.append(svgEl("path", { d, class: cls, "marker-end": "url(#wf-arrow)" }));
  }
  const groups = {};
  const labels = {};
  for (const id of ordered) {
    const p = pos[id];
    const special = id === "__start__" || id === "__end__";
    const g = svgEl("g", { class: "gnode idle", "data-node": id });
    g.append(svgEl("rect", { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2, width: NODE_W, height: NODE_H, rx: special ? 15 : 8 }));
    labels[id] = svgEl("text", { x: p.x, y: p.y + 4, "text-anchor": "middle" }, special ? id.replaceAll("_", "") : id);
    g.append(labels[id]);
    svg.append(g);
    groups[id] = g;
  }
  return {
    svg,
    setState(id, status) {
      if (!groups[id]) return;
      groups[id].setAttribute("class", `gnode ${status}`);
    },
    setCount(id, n) {
      if (labels[id]) labels[id].textContent = n > 1 ? `${id} ×${n}` : id;
    },
    finish(visited, paused) {
      for (const id of Object.keys(groups)) {
        if (visited.has(id)) groups[id].setAttribute("class", `gnode ${id === paused ? "paused" : "done"}`);
        else groups[id].setAttribute("class", "gnode skipped");
      }
    },
  };
}

function workflowFlag(flag, picks, onPick) {
  const card = el("div", { class: `card ${flag.severity}` }, el("div", { class: "card-head" }, el("span", { class: `badge ${flag.severity}`, text: SEVERITY_BADGE[flag.severity] }), el("span", { class: "field", text: flag.field }), el("span", { class: "type", text: flag.type })), el("div", { text: flag.message }));
  for (const e of flag.evidence) card.append(el("div", { class: "evidence-line" }, el("span", {}, el("span", { class: "src", text: e.doc_id ? `note ${e.doc_id}` : "pharmacy database" }), el("span", { class: "q", text: `"${e.quote}"` }))));
  const row = el("div", { class: "choices" }, el("span", { text: "Decision:" }));
  const buttons = {};
  const refresh = () => { for (const [name, b] of Object.entries(buttons)) b.setAttribute("aria-pressed", String(picks[flag.id] === name)); };
  for (const name of ["Confirm", "Dismiss"]) {
    buttons[name] = el("button", { type: "button", class: "choice", text: name, onclick: () => { picks[flag.id] = picks[flag.id] === name ? undefined : name; refresh(); onPick(); } });
    row.append(buttons[name]);
  }
  refresh();
  card.append(row);
  return card;
}

function groupRepeats(names) {
  const counts = new Map();
  for (const name of names) counts.set(name, (counts.get(name) || 0) + 1);
  return [...counts].map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
}

async function renderCheckpoints(box, threadId) {
  try {
    const history = (await api(`/api/workflow/${threadId}/history`)).slice().reverse();
    const head = el("thead", {}, el("tr", {}, ...["Checkpoint", "Completed so far", "Runs next", "Flags"].map((t) => el("th", { text: t }))));
    const body = el("tbody");
    history.forEach((cp, index) => {
      body.append(el("tr", {}, el("td", { text: String(index) }), el("td", { text: cp.path.join(" → ") || "(nothing yet)" }), el("td", { text: groupRepeats(cp.next).join(", ") || "(finished)" }), el("td", { text: String(cp.flags) })));
    });
    box.append(el("details", { class: "checkpoints" }, el("summary", { text: `LangGraph checkpoints (${history.length}), saved in SQLite` }), el("div", { class: "table-wrap" }, el("table", {}, head, body)), el("p", { class: "muted small", text: "Each row is a saved state of this run. A paused review is resumed from the last one, even after a server restart." })));
  } catch (error) {
    box.append(el("p", { class: "warning", text: error.message }));
  }
}

function renderWorkflowDetail(box, result, diagram) {
  box.replaceChildren(pathChips(result.path));
  const retrieval = result.retrieval ? ` RAG read ${pct(result.retrieval.chars_sent, result.retrieval.chars_total)} of the text.` : "";
  box.append(el("p", { class: "muted small", text: `Read ${result.facts_read} fact(s) from ${result.notes_available} note(s).${retrieval}` }));
  if (result.status === "auto_clear") {
    box.append(el("div", { class: "card ok" }, el("div", { class: "card-head" }, el("span", { class: "badge ok", text: "Cleared" })), el("div", { text: "The sources were read and agree with the application. No review needed." })));
    renderCheckpoints(box, result.thread_id);
    return;
  }
  const reasons = el("div", { class: "reasons" });
  for (const reason of result.reasons) reasons.append(el("div", { class: "warning", text: REASON_TEXT[reason] || reason }));
  box.append(reasons);
  if (result.unmapped.length) box.append(el("p", { class: "muted small", text: `Note terms to read yourself: ${result.unmapped.join("; ")}` }));
  const picks = {};
  const submit = el("button", { type: "button", class: "primary", text: "Submit review" });
  const sync = () => { submit.textContent = `Submit review (${Object.values(picks).filter(Boolean).length} of ${result.flags.length} decided)`; };
  for (const flag of result.flags) box.append(workflowFlag(flag, picks, sync));
  sync();
  submit.addEventListener("click", async () => {
    const decisions = Object.fromEntries(Object.entries(picks).filter(([, v]) => v));
    submit.disabled = true;
    try {
      const done = await api(`/api/workflow/${result.thread_id}/resume`, { method: "POST", body: JSON.stringify({ decisions }) });
      state.reviews = await api("/api/reviews");
      if (state.result) {
        renderStats();
        renderFlags();
      }
      refreshFeedback();
      refreshHistory();
      diagram.finish(new Set(done.path.map((s) => s.split(" x")[0]).concat(["__start__", "__end__"])), null);
      box.replaceChildren(pathChips(done.path));
      box.append(el("div", { class: "card ok" }, el("div", { class: "card-head" }, el("span", { class: "badge ok", text: "Reviewed" })), el("div", { text: `${Object.keys(done.decisions).length} decision(s) saved to the audit log. The run resumed from its checkpoint.` })));
      renderCheckpoints(box, result.thread_id);
    } catch (error) {
      submit.disabled = false;
      toast(error.message, "error");
    }
  });
  box.append(submit);
  renderCheckpoints(box, result.thread_id);
}

async function openPendingReview(item) {
  const view = $("workflow-view");
  view.replaceChildren();
  showTab("result-tabs", "workflow");
  try {
    if (!state.graph) state.graph = await api("/api/graph");
    const diagram = drawGraph(state.graph);
    const detail = el("div", { class: "wf-detail" });
    view.append(el("div", { class: "wf-grid" }, el("div", { class: "wf-graph" }, diagram.svg), detail));
    const visited = new Set(item.path.map((s) => s.split(" x")[0]).concat(["__start__"]));
    diagram.finish(visited, "human_review");
    const count = item.path.map((s) => s.match(/^extract_note x(\d+)$/)).find(Boolean);
    if (count) diagram.setCount("extract_note", Number(count[1]));
    renderWorkflowDetail(detail, item, diagram);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function showPendingReviews() {
  if (!state.appId) return;
  const appId = state.appId;
  try {
    const pending = await api(`/api/applications/${encodeURIComponent(appId)}/workflow/pending`);
    const view = $("workflow-view");
    if (!pending.length || state.appId !== appId || view.children.length) return;
    const box = el("div", { class: "card data_quality" }, el("div", { class: "card-head" }, el("span", { class: "badge data_quality", text: "Waiting for review" })), el("div", { text: `${pending.length} paused review(s) for this application are saved in the checkpoint store. They survive a server restart.` }));
    for (const item of pending) {
      const when = new Date(item.started).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      box.append(el("div", { class: "evidence-line" }, el("span", { text: `Started ${when} · ${item.flags.length} flag(s) · ${item.reasons.join(", ")}` }), el("button", { type: "button", class: "link", text: "Open", onclick: () => openPendingReview(item) })));
    }
    view.append(box);
  } catch (_) { /* the list is a convenience; the page works without it */ }
}

async function runWorkflow() {
  if (!state.appId) {
    toast("Choose a stored application first.", "error");
    return;
  }
  const button = $("workflow-btn");
  button.disabled = true;
  const view = $("workflow-view");
  view.replaceChildren();
  showTab("result-tabs", "workflow");
  try {
    if (!state.graph) state.graph = await api("/api/graph");
    const diagram = drawGraph(state.graph);
    const detail = el("div", { class: "wf-detail" }, el("p", { class: "muted small", text: "Running the graph…" }));
    view.append(el("div", { class: "wf-grid" }, el("div", { class: "wf-graph" }, diagram.svg), detail));
    diagram.setState("__start__", "done");
    const rag = $("retrieval").value === "rag";
    diagram.setState(rag ? "retrieve" : "select_notes", "running");
    const response = await fetch(`/api/applications/${encodeURIComponent(state.appId)}/workflow/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ extractor: $("extractor").value, retrieval: $("retrieval").value, top_k: topK() }),
    });
    if (!response.ok) {
      let message = response.statusText;
      try {
        const body = await response.json();
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch (_) { /* keep the status text */ }
      throw new Error(`${response.status}: ${message}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let notes = 0;
    let final = null;
    const handle = (event) => {
      if (event.event === "error") throw new Error(`${event.status}: ${event.detail}`);
      if (event.event === "done") { final = event; return; }
      if (event.event !== "node") return;
      if (event.paused) { diagram.setState("human_review", "paused"); return; }
      if (event.node === "extract_note") {
        notes += 1;
        diagram.setCount("extract_note", notes);
        diagram.setState("extract_note", "running");
        return;
      }
      diagram.setState(event.node, "done");
      if (event.node === "retrieve") diagram.setState("select_notes", "running");
      if (event.node === "select_notes") diagram.setState("extract_note", "running");
      if (event.node === "compare") {
        diagram.setState("extract_note", "done");
        const reasons = (event.detail && event.detail.review_reasons) || [];
        diagram.setState(reasons.length ? "human_review" : "finalize", "running");
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) if (line.trim()) handle(JSON.parse(line));
      if (done) break;
    }
    if (buffer.trim()) handle(JSON.parse(buffer));
    if (!final) throw new Error("The run ended without a result.");
    const visited = new Set(final.path.map((s) => s.split(" x")[0]).concat(["__start__"]));
    if (final.status !== "awaiting_review") visited.add("__end__");
    diagram.finish(visited, final.status === "awaiting_review" ? "human_review" : null);
    renderWorkflowDetail(detail, final, diagram);
    refreshHistory();
  } catch (error) {
    view.replaceChildren(el("p", { class: "warning", text: error.message }));
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* export */
function reportText() {
  const r = state.result;
  const lines = [`# Cross-check report: ${state.form.name || "Applicant"} (${r.app_id})`, "", `Extractor: ${r.extractor}. Flags: ${r.flags.length}.`, ""];
  for (const flag of r.flags) {
    lines.push(`## ${flag.field} - ${flag.type} (${flag.severity})`, flag.message);
    for (const e of flag.evidence) lines.push(`- ${e.doc_id ? `note ${e.doc_id}` : "pharmacy"}: "${e.quote}"`);
    lines.push(`Reviewer decision: ${state.reviews[flag.id] || "none"}`, "");
  }
  return lines.join("\n");
}

async function copyReport() {
  try {
    await navigator.clipboard.writeText(reportText());
    toast("Report copied to the clipboard.");
  } catch (_) {
    toast("Could not copy; your browser blocked clipboard access.", "error");
  }
}

function downloadJson() {
  const blob = new Blob([JSON.stringify({ result: state.result, reviews: state.reviews }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: `crosscheck-${state.result.app_id}.json` });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function revealAnswerKey() {
  if (!state.appId) return;
  try {
    const items = await api(`/api/applications/${encodeURIComponent(state.appId)}/answer-key`);
    const list = $("answer-key");
    list.replaceChildren();
    if (!items.length) list.append(el("li", { text: "This application is clean: no errors were planted." }));
    for (const item of items) list.append(el("li", { text: `${item.type}: ${item.field} (${item.detail})` }));
  } catch (error) {
    toast(error.message, "error");
  }
}

/* start-up */
async function init() {
  initTheme();
  initTabs();
  bindChipInput("conditions");
  bindChipInput("allergies");
  $("name").addEventListener("input", () => { state.form.name = $("name").value; markDirty(); });
  $("add-med").addEventListener("click", () => { state.form.medications.push({ name: "", dose: "" }); renderMedRows(); });
  $("add-pharm").addEventListener("click", () => { state.form.pharmacy.push({ drug: "", dose: "", date: "" }); renderPharmRows(); });
  $("add-note").addEventListener("click", () => { state.form.notes.push({ docId: null, text: "" }); renderNotes(); showTab("editor-tabs", "notes"); });
  $("new-app").addEventListener("click", () => selectApp(null));
  $("mrn-input").addEventListener("input", () => { state.form.mrn = $("mrn-input").value; });
  $("mrn-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); loadPharmacyFromDb(); }
  });
  $("load-pharmacy").addEventListener("click", loadPharmacyFromDb);
  $("search").addEventListener("input", renderSidebar);
  $("only-flawed").addEventListener("change", renderSidebar);
  $("check").addEventListener("click", runCheck);
  $("copy-report").addEventListener("click", copyReport);
  $("download-json").addEventListener("click", downloadJson);
  $("reveal").addEventListener("click", revealAnswerKey);
  $("retrieve-btn").addEventListener("click", searchRecord);
  $("workflow-btn").addEventListener("click", runWorkflow);
  $("retrieve-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); searchRecord(); }
  });
  const onRetrievalChange = () => {
    $("top-k-label").hidden = $("retrieval").value !== "rag";
    markDirty();
  };
  $("retrieval").addEventListener("change", onRetrievalChange);
  $("top-k").addEventListener("change", markDirty);
  $("top-k-label").hidden = true;
  $("extractor").addEventListener("change", () => {
    if ($("extractor").value === "rules") runCheck();
    else setStatus("Model extractor selected. Press Run check (uses API credits).");
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      runCheck();
    }
  });
  try {
    const [health, apps, reviews] = await Promise.all([api("/api/health"), api("/api/applications"), api("/api/reviews")]);
    state.apps = apps;
    state.reviews = reviews;
    if (health.store) {
      const st = health.store;
      const pill = $("store-pill");
      pill.textContent = `Store: ${st.applications} forms · ${st.notes} notes · ${st.chunks} passages`;
      pill.title = `Embedder: ${st.embedder}. Parser issues: ${st.errors} errors, ${st.issues - st.errors} warnings.`;
      pill.classList.toggle("on", st.errors === 0);
    }
    if (health.pharmacy) {
      setDbInfo(`Pharmacy database: ${health.pharmacy.rows} dispensing rows for ${health.pharmacy.patients} applicants.`);
    }
    if (health.llm_enabled) {
      const option = $("llm-option");
      option.disabled = false;
      option.textContent = "LLM (uses the server's API key; costs credits)";
      const pill = $("llm-pill");
      pill.textContent = "LLM on";
      pill.classList.add("on");
    }
    renderSidebar();
    refreshFeedback();
    const first = apps.find((a) => a.planted_errors > 0) || apps[0];
    if (first) await selectApp(first.app_id);
    else await selectApp(null);
  } catch (error) {
    setStatus(`Could not load the server data: ${error.message}`, true);
    renderEditor();
  }
}

init();
