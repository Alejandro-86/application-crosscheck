"use strict";
// All user-supplied text is rendered with textContent or text nodes, never parsed as markup.

const SEVERITY_ORDER = ["discrepancy", "data_quality", "unverified"];
const SEVERITY_LABEL = {
  discrepancy: "Discrepancies (evidence in the sources)",
  data_quality: "Source conflicts (the sources disagree with each other)",
  unverified: "Unverified (nothing found; absence is not proof)",
};

const $ = (id) => document.getElementById(id);
const state = { apps: [], currentAppId: null, noteIds: [], reviews: {}, lastResult: null };

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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.className = isError ? "error" : "";
}

const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);

function parseMedications(text) {
  return lines(text).map((line) => {
    const match = line.match(/^(.+?)\s+(\d+)\s*(?:mg)?$/i);
    if (!match) throw new Error(`Medication line not understood: "${line}" (use: name dose)`);
    return { name: match[1], dose: Number(match[2]), unit: "mg" };
  });
}

function parsePharmacy(text) {
  return lines(text).map((line) => {
    const match = line.match(/^(\S+)\s+(\d+)\s*(?:mg)?\s*(\S*)$/i);
    if (!match) throw new Error(`Pharmacy line not understood: "${line}" (use: drug dose [date])`);
    return { drug: match[1], dose: Number(match[2]), unit: "mg", last_dispensed: match[3] || "unknown" };
  });
}

function addNote(docId, text) {
  const index = $("notes").children.length;
  const area = el("textarea", { rows: "8", "aria-label": `Clinical note ${index + 1}` });
  area.value = text || "";
  const label = docId || `new note`;
  const box = el(
    "div",
    { class: "note-box" },
    el("div", { class: "note-head" }, el("span", { text: `Clinical note (${label})` }),
      el("button", { type: "button", text: "Remove", onclick: () => { box.remove(); } })),
    area,
  );
  box.dataset.docId = docId || "";
  $("notes").append(box);
}

function currentNotes() {
  return [...$("notes").children]
    .map((box) => ({ doc_id: box.dataset.docId || null, text: box.querySelector("textarea").value }))
    .filter((note) => note.text.trim());
}

function noteIdFor(note, index) {
  return note.doc_id || `N${index + 1}`;
}

function fillApplication(detail) {
  state.currentAppId = detail.app_id;
  $("name").value = detail.application.name;
  $("conditions").value = detail.application.conditions.join("\n");
  $("medications").value = detail.application.medications.map((m) => `${m.name} ${m.dose}`).join("\n");
  $("allergies").value = detail.application.allergies.join("\n");
  $("pharmacy").value = detail.pharmacy.map((r) => `${r.drug} ${r.dose} ${r.last_dispensed}`).join("\n");
  $("notes").replaceChildren();
  for (const note of detail.notes) addNote(note.doc_id, note.text);
  clearResults();
  $("answer-key-box").hidden = false;
  $("answer-key").replaceChildren();
}

function clearResults() {
  $("empty").hidden = false;
  for (const id of ["warnings", "flags", "facts", "evidence"]) $(id).replaceChildren();
  $("facts-box").hidden = true;
  $("evidence-title").hidden = true;
  state.lastResult = null;
}

function populateSelect() {
  const only = $("only-flawed").checked;
  const select = $("app-select");
  select.replaceChildren(el("option", { value: "", text: "(blank application)" }));
  for (const app of state.apps) {
    if (only && app.planted_errors === 0) continue;
    select.append(el("option", { value: app.app_id, text: `${app.app_id} - ${app.name} (${app.planted_errors} planted)` }));
  }
}

async function loadSelected() {
  const id = $("app-select").value;
  if (!id) {
    state.currentAppId = null;
    for (const field of ["name", "conditions", "medications", "allergies", "pharmacy"]) $(field).value = "";
    $("notes").replaceChildren();
    addNote(null, "");
    clearResults();
    $("answer-key-box").hidden = true;
    return;
  }
  try {
    fillApplication(await api(`/api/applications/${encodeURIComponent(id)}`));
    setStatus("");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function buildPayload() {
  return {
    app_id: state.currentAppId || "adhoc",
    application: {
      name: $("name").value || "Applicant",
      conditions: lines($("conditions").value),
      medications: parseMedications($("medications").value),
      allergies: lines($("allergies").value),
    },
    notes: currentNotes(),
    pharmacy: parsePharmacy($("pharmacy").value),
    extractor: $("extractor").value,
  };
}

async function runCheck() {
  let payload;
  try {
    payload = buildPayload();
  } catch (error) {
    setStatus(error.message, true);
    return;
  }
  setStatus("Checking...");
  $("check").disabled = true;
  try {
    const result = await api("/api/check", { method: "POST", body: JSON.stringify(payload) });
    state.lastResult = { result, notes: payload.notes };
    renderResult(result, payload.notes);
    setStatus(`Done: ${result.flags.length} flag(s).`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("check").disabled = false;
  }
}

function renderWarnings(result) {
  const box = $("warnings");
  box.replaceChildren();
  const add = (message) => box.append(el("div", { class: "warning", text: message }));
  if (result.unmapped_declared.length) add(`Not recognised, left out of the comparison: ${result.unmapped_declared.join("; ")}`);
  if (result.unmapped_extracted.length) add(`Terms found in the notes with no canonical mapping: ${result.unmapped_extracted.join("; ")}`);
  if (result.dropped_for_missing_evidence) add(`${result.dropped_for_missing_evidence} flag(s) were dropped because their evidence could not be verified.`);
}

function renderFlags(result) {
  const container = $("flags");
  container.replaceChildren();
  if (!result.flags.length) {
    container.append(el("div", { class: "card unverified", text: "No flags: the application matches the sources." }));
    return;
  }
  for (const severity of SEVERITY_ORDER) {
    const group = result.flags.filter((f) => f.severity === severity);
    if (!group.length) continue;
    container.append(el("h3", { text: SEVERITY_LABEL[severity] }));
    for (const flag of group) container.append(flagCard(flag));
  }
}

function flagCard(flag) {
  const card = el(
    "div",
    { class: `card ${flag.severity}` },
    el("h4", {}, el("span", { class: `badge ${flag.severity}`, text: flag.severity.replace("_", " ") }), document.createTextNode(`${flag.type} - ${flag.field}`)),
    el("div", { text: flag.message }),
  );
  for (const evidence of flag.evidence) {
    const where = evidence.doc_id || "pharmacy database";
    card.append(el("blockquote", { text: `${evidence.source} / ${where}: "${evidence.quote}"` }));
  }
  if (flag.severity === "unverified") {
    card.append(el("div", { class: "muted", text: `Checked notes: ${flag.checked_docs.join(", ") || "none"}` }));
  }
  const decisions = el("div", { class: "decisions" }, el("span", { text: "Reviewer decision:" }));
  const buttons = {};
  const refresh = () => {
    for (const [name, button] of Object.entries(buttons)) {
      button.setAttribute("aria-pressed", String((state.reviews[flag.id] || "—") === name));
    }
  };
  for (const name of ["Confirm", "Dismiss"]) {
    buttons[name] = el("button", {
      type: "button",
      text: name,
      onclick: async () => {
        const next = (state.reviews[flag.id] || "—") === name ? "—" : name;
        try {
          state.reviews = await api("/api/reviews", { method: "PUT", body: JSON.stringify({ flag_id: flag.id, decision: next }) });
          refresh();
        } catch (error) {
          setStatus(error.message, true);
        }
      },
    });
    decisions.append(buttons[name]);
  }
  refresh();
  card.append(decisions);
  return card;
}

function renderFacts(result) {
  const facts = result.facts;
  const box = $("facts");
  box.replaceChildren();
  const row = (label, value) => box.append(el("p", {}, el("strong", { text: `${label}: ` }), document.createTextNode(value || "none")));
  row("Conditions", facts.conditions.join(", "));
  row("Explicitly denied", facts.negated_conditions.join(", "));
  row("Medications", Object.entries(facts.medications).map(([drug, doses]) => `${drug} ${doses.join("/")} mg`).join(", "));
  row("Allergies", facts.allergies.join(", "));
  $("facts-box").hidden = false;
}

function highlightInto(container, text, quotes) {
  const lower = text.toLowerCase();
  const ranges = [];
  for (const quote of quotes) {
    const start = lower.indexOf(quote.toLowerCase());
    if (start >= 0 && !ranges.some(([a, b]) => start < b && start + quote.length > a)) {
      ranges.push([start, start + quote.length]);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  let cursor = 0;
  for (const [start, end] of ranges) {
    container.append(document.createTextNode(text.slice(cursor, start)));
    container.append(el("mark", { text: text.slice(start, end) }));
    cursor = end;
  }
  container.append(document.createTextNode(text.slice(cursor)));
}

function renderEvidence(result, notes) {
  const container = $("evidence");
  container.replaceChildren();
  notes.forEach((note, index) => {
    const id = noteIdFor(note, index);
    const quotes = result.flags.flatMap((f) => f.evidence.filter((e) => e.doc_id === id).map((e) => e.quote));
    const body = el("div", { class: "evidence-note" }, el("div", { class: "title", text: `Clinical note ${id}` }));
    highlightInto(body, note.text, quotes);
    container.append(body);
  });
  $("evidence-title").hidden = notes.length === 0;
}

function renderResult(result, notes) {
  $("empty").hidden = true;
  renderWarnings(result);
  renderFlags(result);
  renderFacts(result);
  renderEvidence(result, notes);
}

async function revealAnswerKey() {
  if (!state.currentAppId) return;
  try {
    const items = await api(`/api/applications/${encodeURIComponent(state.currentAppId)}/answer-key`);
    const list = $("answer-key");
    list.replaceChildren();
    if (!items.length) list.append(el("li", { text: "This application is clean: no errors were planted." }));
    for (const item of items) list.append(el("li", { text: `${item.type}: ${item.field} (${item.detail})` }));
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function init() {
  $("add-note").addEventListener("click", () => addNote(null, ""));
  $("check").addEventListener("click", runCheck);
  $("app-select").addEventListener("change", loadSelected);
  $("only-flawed").addEventListener("change", populateSelect);
  $("reveal").addEventListener("click", revealAnswerKey);
  try {
    const [health, apps, reviews] = await Promise.all([api("/api/health"), api("/api/applications"), api("/api/reviews")]);
    state.apps = apps;
    state.reviews = reviews;
    if (health.llm_enabled) {
      const option = $("llm-option");
      option.disabled = false;
      option.textContent = "LLM (uses the server's API key; costs credits)";
    }
    populateSelect();
    if ($("app-select").options.length > 1) {
      $("app-select").selectedIndex = 1;
      await loadSelected();
    } else {
      addNote(null, "");
    }
  } catch (error) {
    setStatus(`Could not load the server data: ${error.message}`, true);
  }
}

init();
