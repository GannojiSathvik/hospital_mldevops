/*
 * Readmission Risk Console - front-end logic (plain JavaScript, no libraries).
 *
 * How it works, top to bottom:
 *   1. api()            - one wrapper around fetch(): adds the X-API-Key header,
 *                         records status + X-Request-ID in the on-page request log.
 *   2. Sign-in          - calls GET /whoami. 401 = bad key. On success we learn the
 *                         role and its permissions, and mark actions the role lacks
 *                         as "403 expected" (they stay clickable for the demo).
 *   3. Patient form     - built from GET /schema/features, so the form's ranges and
 *                         dropdown options are the same ones the server validates.
 *   4. Actions          - /predict, /predict/explain, /predict/batch, /monitor/drift,
 *                         /model-info. Results are drawn with DOM methods and
 *                         textContent (never innerHTML with server data), so text
 *                         coming back from the API can't inject HTML/JS (XSS).
 *
 * Security notes:
 *   - The API key lives only in a JS variable (memory). Not in localStorage,
 *     not in a cookie: a page refresh signs you out, and other scripts/tabs
 *     can't read it from storage.
 *   - The UI hides nothing for security. Authorisation is enforced by the
 *     server on every request; the lock badges are only a hint.
 */
"use strict";

// --------------------------------------------------------------------- state
const state = {
  apiKey: null,        // kept in memory only
  principal: null,     // { name, role, description, permissions }
  schema: null,        // { numeric: {col: [min,max]}, binary: [...], categorical: {col: [...]}, feature_order }
  threshold: null,     // decision threshold, from /model-info (if the role may read it)
};

// Same values as data/sample_input.json (the spec's example patient).
const SAMPLE_PATIENT = {
  age: 67, gender: "Male", bmi: 31.5, systolic_bp: 145, diastolic_bp: 92, heart_rate: 88,
  blood_glucose: 178, hba1c: 8.2, cholesterol: 235, number_of_medications: 7,
  previous_admissions: 3, length_of_stay: 8, emergency_visits_last_year: 2,
  chronic_disease_count: 4, diabetes: 1, hypertension: 1, heart_disease: 1, kidney_disease: 0,
  smoking_status: "Former", alcohol_consumption: "Low", physical_activity_level: "Low",
  discharge_destination: "Home", follow_up_scheduled: 0, insurance_type: "Private",
  admission_type: "Emergency",
};

// Integer-valued numeric fields (everything else numeric accepts decimals).
const INTEGER_FIELDS = new Set([
  "age", "systolic_bp", "diastolic_bp", "heart_rate", "number_of_medications",
  "previous_admissions", "length_of_stay", "emergency_visits_last_year", "chronic_disease_count",
]);

// Form sections: purely visual grouping of the 25 features.
const SECTIONS = [
  ["Demographics", ["age", "gender", "bmi", "insurance_type"]],
  ["Vitals & labs", ["systolic_bp", "diastolic_bp", "heart_rate", "blood_glucose", "hba1c", "cholesterol"]],
  ["Conditions", ["diabetes", "hypertension", "heart_disease", "kidney_disease", "chronic_disease_count"]],
  ["Hospital history", ["admission_type", "length_of_stay", "previous_admissions",
    "emergency_visits_last_year", "number_of_medications"]],
  ["Lifestyle", ["smoking_status", "alcohol_consumption", "physical_activity_level"]],
  ["Discharge", ["discharge_destination", "follow_up_scheduled"]],
];

// Documentation mirror of configs/access_control.yaml for the "Access matrix"
// tab. The server's copy is the one that is enforced.
const ROLES = ["viewer", "clinician", "analyst", "ml_engineer", "admin"];
const ENDPOINTS = [
  ["GET /health, /ready, /metrics", null],
  ["GET /model-info", "model:read"],
  ["POST /predict", "predict:single"],
  ["POST /predict/explain", "predict:explain"],
  ["POST /predict/batch", "predict:batch"],
  ["POST /monitor/drift", "monitor:drift"],
];
const ROLE_PERMISSIONS = {
  viewer: ["model:read"],
  clinician: ["model:read", "predict:single", "predict:explain"],
  analyst: ["model:read", "predict:single", "predict:batch", "monitor:drift"],
  ml_engineer: ["model:read", "predict:single", "predict:batch", "monitor:drift",
    "pipeline:train", "pipeline:validate", "pipeline:retrain", "model:register"],
  admin: ["*"],
};

// --------------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Create an element safely: text goes through textContent, never innerHTML. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const pretty = (name) => name.replace(/_/g, " ");
const pct = (p) => `${(p * 100).toFixed(1)}%`;

function can(permission) {
  if (!permission) return true;
  const perms = state.principal ? state.principal.permissions : [];
  return perms.includes("*") || perms.includes(permission);
}

let toastTimer = null;
function toast(message) {
  const t = $("#toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
}

// --------------------------------------------------------------------- API wrapper
/**
 * Call the API and log the outcome.
 * Returns { status, ok, data, text, blob, requestId }.
 */
async function api(method, path, { json, form, expect = "json", auth = true } = {}) {
  const headers = {};
  if (auth && state.apiKey) headers["X-API-Key"] = state.apiKey;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form; // browser sets the multipart boundary itself
  }

  let res;
  try {
    res = await fetch(path, { method, headers, body, cache: "no-store" });
  } catch (err) {
    logRequest(method, path, 0, null, "Network error - is the API running?");
    return { status: 0, ok: false, data: null };
  }

  const requestId = res.headers.get("X-Request-ID");
  const out = { status: res.status, ok: res.ok, requestId, data: null, text: null, blob: null };
  const type = res.headers.get("Content-Type") || "";
  if (res.ok && expect === "blob") {
    out.blob = await res.blob();
    out.text = await out.blob.text();
  } else if (type.includes("application/json")) {
    out.data = await res.json().catch(() => null);
  } else {
    out.text = await res.text();
  }

  logRequest(method, path, res.status, requestId, res.ok ? "" : errorDetail(out));
  return out;
}

/** Turn an error response into one readable line (422 has a list of field errors). */
function errorDetail(out) {
  const d = out.data && out.data.detail;
  if (Array.isArray(d)) {
    return d.map((e) => `${(e.loc || []).slice(1).join(".") || "body"}: ${e.msg}`).join("; ");
  }
  if (typeof d === "string") return d;
  return out.text ? out.text.slice(0, 200) : `HTTP ${out.status}`;
}

const STATUS_TEXT = { 0: "ERR", 200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
  413: "Too Large", 422: "Invalid", 429: "Rate Limited", 500: "Server Error", 503: "Unavailable" };

function logRequest(method, path, status, requestId, detail) {
  $("#log-card").hidden = false;
  const cls = status >= 500 || status === 0 ? "c5" : status >= 400 ? "c4" : "c2";
  const item = el("li", {},
    el("span", { class: `code ${cls}`, text: `${status || "---"} ${STATUS_TEXT[status] || ""}`.trim() }),
    el("span", { text: method }),
    el("span", { text: path }),
    el("span", { class: "rid", title: "X-Request-ID (search logs/audit.log for it)", text: requestId || "" }),
    detail ? el("span", { class: "detail", text: detail }) : null,
  );
  $("#req-log").prepend(item);
}

/** Show the outcome of a refused request in a friendly way. */
function explainFailure(out, permission) {
  if (out.status === 401) toast("401 Unauthorized - the API doesn't recognise this key.");
  else if (out.status === 403) toast(`403 Forbidden - role '${state.principal.role}' lacks '${permission}'. The server refused it.`);
  else if (out.status === 422) toast(`422 - invalid input: ${errorDetail(out)}`);
  else if (out.status === 429) toast("429 - rate limit reached, wait a minute.");
  else if (out.status) toast(`${out.status} - ${errorDetail(out)}`);
  else toast("Could not reach the API.");
}

async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } finally { button.disabled = false; }
}

// --------------------------------------------------------------------- health
async function checkReady() {
  const box = $("#api-status");
  let res;
  try { res = await fetch("/ready", { cache: "no-store" }); } catch { res = null; }
  const data = res ? await res.json().catch(() => ({})) : {};
  box.classList.toggle("ok", !!(res && res.ok));
  box.classList.toggle("bad", !(res && res.ok));
  $(".status-text", box).textContent = res && res.ok
    ? `API ready · model v${data.model_version}`
    : res ? "API up · model not loaded" : "API unreachable";
}

// --------------------------------------------------------------------- sign in / out
async function signIn(event) {
  event.preventDefault();
  const key = $("#api-key").value.trim();
  const err = $("#signin-error");
  err.hidden = true;
  if (!key) return;

  state.apiKey = key;
  const out = await api("GET", "/whoami");
  if (!out.ok) {
    state.apiKey = null;
    err.textContent = out.status === 401
      ? "401 Unauthorized: that key is not valid."
      : `Sign-in failed (${out.status || "network error"}).`;
    err.hidden = false;
    return;
  }
  $("#api-key").value = "";
  state.principal = out.data;
  renderIdentity();
  applyPermissions();
  renderMatrix();
  $("#signin-card").hidden = true;
  $("#identity-card").hidden = false;
  $("#workspace").hidden = false;
  if (can("model:read")) loadModelInfo();
}

function signOut() {
  state.apiKey = null;
  state.principal = null;
  state.threshold = null;
  $("#identity-card").hidden = true;
  $("#workspace").hidden = true;
  $("#signin-card").hidden = false;
  $("#result-body").hidden = true;
  $("#result-empty").hidden = false;
  $("#explain-card").hidden = true;
  $("#api-key").focus();
}

function renderIdentity() {
  const p = state.principal;
  $("#who-name").textContent = p.name;
  $("#who-role").textContent = p.role;
  $("#who-desc").textContent = p.description;
  const list = $("#perm-list");
  list.replaceChildren(...p.permissions.map((perm) =>
    el("span", { class: "perm", text: perm === "*" ? "* (all permissions)" : perm })));
}

/** Mark buttons/tabs whose permission the role lacks. They stay clickable. */
function applyPermissions() {
  for (const node of $$("[data-perm]")) {
    const perm = node.dataset.perm;
    const locked = perm && !can(perm);
    node.classList.toggle("locked", !!locked);
    if (locked) node.title = `Your role lacks '${perm}' - the server will answer 403`;
    else node.removeAttribute("title");
  }
}

// --------------------------------------------------------------------- tabs
function showTab(name) {
  for (const t of $$(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const p of $$(".panel")) p.hidden = p.dataset.panel !== name;
  if (name === "model") loadModelInfo();
}

// --------------------------------------------------------------------- patient form
function buildForm() {
  const s = state.schema;
  const container = $("#form-fields");
  container.replaceChildren();

  for (const [title, cols] of SECTIONS) {
    container.append(el("div", { class: "fieldset-title", text: title }));
    for (const col of cols) {
      const id = `f-${col}`;
      let input;
      let hint = "";
      if (s.numeric[col]) {
        const [min, max] = s.numeric[col];
        input = el("input", {
          id, name: col, type: "number", required: true, min, max,
          step: INTEGER_FIELDS.has(col) ? "1" : "0.1", inputmode: "decimal",
        });
        hint = `${min}–${max}`;
      } else if (s.categorical[col]) {
        input = el("select", { id, name: col, required: true },
          s.categorical[col].map((v) => el("option", { value: v, text: v })));
      } else if (s.binary.includes(col)) {
        input = el("select", { id, name: col, required: true },
          el("option", { value: "0", text: "No" }), el("option", { value: "1", text: "Yes" }));
      } else {
        continue;
      }
      container.append(el("div", { class: "field" },
        el("label", { for: id, text: pretty(col) }), input,
        hint ? el("div", { class: "hint", text: hint }) : null));
    }
  }
  fillSample();
}

function fillSample() {
  for (const [col, value] of Object.entries(SAMPLE_PATIENT)) {
    const input = document.getElementById(`f-${col}`);
    if (input) input.value = String(value);
  }
}

/** Read the form into the JSON shape the API expects (numbers as numbers). */
function readForm() {
  const s = state.schema;
  const patient = {};
  for (const col of s.feature_order) {
    const input = document.getElementById(`f-${col}`);
    if (!input) continue;
    if (s.numeric[col]) patient[col] = input.value === "" ? null : Number(input.value);
    else if (s.binary.includes(col)) patient[col] = Number(input.value);
    else patient[col] = input.value;
  }
  return patient;
}

// --------------------------------------------------------------------- predict / explain
async function predict(withExplain) {
  const form = $("#patient-form");
  // Browser-side check is a convenience only; the server re-validates (422).
  if (!form.checkValidity()) {
    form.reportValidity();
    return;
  }
  const permission = withExplain ? "predict:explain" : "predict:single";
  const path = withExplain ? "/predict/explain" : "/predict";
  const out = await api("POST", path, { json: readForm() });
  if (!out.ok) {
    explainFailure(out, permission);
    return;
  }
  renderResult(out.data);
  if (withExplain) renderExplanation(out.data.top_features || []);
  else $("#explain-card").hidden = true;
}

function renderResult(r) {
  $("#result-empty").hidden = true;
  $("#result-body").hidden = false;

  const pill = $("#risk-pill");
  pill.className = `risk-pill risk-${r.risk_level}`;
  pill.textContent = `${r.risk_level} risk`;
  $("#prob-value").textContent = pct(r.readmission_probability);

  const fill = $("#meter-fill");
  fill.className = `meter-fill risk-${r.risk_level}`;
  fill.style.width = pct(r.readmission_probability);

  const mark = $("#meter-mark");
  const legend = $("#meter-legend");
  if (state.threshold !== null) {
    mark.style.display = "block";
    mark.style.left = `calc(${pct(state.threshold)} - 1px)`;
    legend.textContent = `Black line = decision threshold (${pct(state.threshold)}). ` +
      "At or above it the patient is flagged for follow-up.";
  } else {
    mark.style.display = "none";
    legend.textContent = "";
  }

  $("#pred-label").textContent = `${r.prediction_label} (prediction = ${r.prediction})`;
  $("#pred-meta").textContent = `Model v${r.model_version} · ${new Date(r.timestamp).toLocaleString()}`;
}

function renderExplanation(features) {
  const card = $("#explain-card");
  const bars = $("#explain-bars");
  card.hidden = false;
  if (!features.length) {
    bars.replaceChildren(el("p", { class: "muted", text: "No explanation returned." }));
    return;
  }
  const maxAbs = Math.max(...features.map((f) => Math.abs(f.contribution))) || 1;
  bars.replaceChildren(...features.map((f) => {
    const width = (Math.abs(f.contribution) / maxAbs) * 50; // half the track per side
    const bar = el("div", { class: `bar ${f.contribution >= 0 ? "up" : "down"}` });
    bar.style.width = `${width}%`;
    // "num__age" -> "age": strip the ColumnTransformer prefix for readability.
    const name = f.feature.replace(/^[a-z]+__/, "");
    const sign = f.contribution >= 0 ? "+" : "";
    return el("div", { class: "bar-row" },
      el("span", { class: "bar-name", title: f.feature, text: name }),
      el("div", { class: "bar-track" }, bar),
      el("span", { class: "bar-val", text: `${sign}${f.contribution.toFixed(3)}` }));
  }));
}

// --------------------------------------------------------------------- batch
/** Minimal CSV parser (handles quoted fields) for the API's batch output. */
function parseCsv(text) {
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.length > 1 || r[0]);
}

let downloadUrl = null;
async function runBatch() {
  const file = $("#batch-file").files[0];
  // If the role lacks predict:batch we still call the API (even without a file):
  // the server checks the key before it looks at the upload, so it answers 403.
  if (!file && can("predict:batch")) { toast("Choose a CSV file first."); return; }
  const form = new FormData();
  if (file) form.append("file", file);
  const out = await api("POST", "/predict/batch", { form, expect: "blob" });
  if (!out.ok) { explainFailure(out, "predict:batch"); return; }

  const rows = parseCsv(out.text);
  const [header, ...data] = rows;
  const idx = (name) => header.indexOf(name);
  const riskCounts = { Low: 0, Medium: 0, High: 0 };
  let flagged = 0;
  for (const r of data) {
    riskCounts[r[idx("risk_level")]] = (riskCounts[r[idx("risk_level")]] || 0) + 1;
    if (r[idx("prediction")] === "1") flagged++;
  }

  const summary = $("#batch-summary");
  summary.hidden = false;
  summary.replaceChildren(
    stat("Patients scored", data.length),
    stat("Flagged for follow-up", `${flagged} (${pct(flagged / (data.length || 1))})`),
    stat("High risk", riskCounts.High),
    stat("Medium risk", riskCounts.Medium),
    stat("Low risk", riskCounts.Low),
  );

  const shown = ["patient_id", "prediction", "readmission_probability", "risk_level", "prediction_label"];
  const table = $("#batch-table");
  table.replaceChildren(
    el("thead", {}, el("tr", {}, shown.map((h) => el("th", { text: pretty(h) })))),
    el("tbody", {}, data.map((r) => el("tr", {}, shown.map((h) => {
      const v = r[idx(h)];
      if (h === "risk_level") return el("td", {}, el("span", { class: `tag risk-${v}`, text: v }));
      if (h === "readmission_probability") return el("td", { class: "num", text: pct(Number(v)) });
      return el("td", { text: v });
    })))),
  );
  $("#batch-table-wrap").hidden = false;

  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = URL.createObjectURL(out.blob);
  const link = $("#batch-download");
  link.href = downloadUrl;
  link.hidden = false;
}

function stat(label, value) {
  return el("div", { class: "stat" }, el("div", { class: "k", text: label }), el("div", { class: "v", text: value }));
}

// --------------------------------------------------------------------- drift
async function runDrift() {
  const out = await api("POST", "/monitor/drift");
  const box = $("#drift-result");
  if (!out.ok) {
    explainFailure(out, "monitor:drift");
    if (out.status === 400) {
      box.hidden = false;
      box.replaceChildren(el("p", { class: "muted", text: errorDetail(out) +
        " - run `python -m scripts.seed_monitoring_data --mode normal` or make some predictions first." }));
    }
    return;
  }
  const d = out.data;
  box.hidden = false;
  box.replaceChildren(
    el("div", { class: "summary-row" },
      stat("Drifted features", `${d.n_drifted} / ${d.n_columns}`),
      stat("Drift share", pct(d.dataset_drift_share)),
      stat("Reference rows", d.n_reference_rows ?? "–"),
      stat("Recent rows", d.n_current_rows ?? "–")),
    d.drifted_columns.length
      ? el("div", {}, el("div", { class: "small muted", text: "Drifted features:" }),
        el("div", { class: "chips" }, d.drifted_columns.map((c) => el("span", { class: "chip", text: c }))))
      : el("p", { class: "muted", text: "No individual feature drifted." }),
    el("div", {
      class: `verdict ${d.drift_detected ? "bad" : "ok"}`,
      text: d.drift_detected
        ? "Drift detected - the retraining policy would trigger (share above 30%)."
        : "No significant drift - the model is still seeing data like its training data.",
    }),
    el("p", { class: "small muted", text: `Full HTML report saved on the server: ${d.report_path}` }),
  );
}

// --------------------------------------------------------------------- model info
async function loadModelInfo() {
  const box = $("#model-info");
  const out = await api("GET", "/model-info");
  if (!out.ok) {
    box.replaceChildren(el("p", { class: "muted",
      text: out.status === 403 ? "403 - your role cannot read model metadata." : errorDetail(out) }));
    return;
  }
  const m = out.data;
  state.threshold = typeof m.threshold === "number" ? m.threshold : null;

  const metrics = (m.metrics && (m.metrics.best_model_test_metrics || m.metrics)) || {};
  const metricKeys = ["roc_auc", "pr_auc", "recall", "precision", "f1", "accuracy"]
    .filter((k) => typeof metrics[k] === "number");

  box.replaceChildren(
    el("dl", { class: "kv" },
      el("dt", { text: "Model" }), el("dd", { text: m.model_name ?? "–" }),
      el("dt", { text: "Version" }), el("dd", { text: m.model_version ?? "–" }),
      el("dt", { text: "Trained at" }), el("dd", { text: m.trained_at ?? "–" }),
      el("dt", { text: "Decision threshold" }),
      el("dd", { text: m.threshold != null ? `${m.threshold.toFixed(4)} (${m.threshold_strategy ?? ""})` : "–" }),
      el("dt", { text: "Training data SHA-256" }), el("dd", { text: m.training_data_hash ?? "–" }),
      el("dt", { text: "Input features" }), el("dd", { text: String((m.feature_columns || []).length) })),
    metricKeys.length
      ? el("div", { class: "summary-row" }, metricKeys.map((k) => stat(k.replace("_", "-").toUpperCase(), metrics[k].toFixed(3))))
      : null,
    el("p", { class: "small muted",
      text: "Recall is prioritised: missing a patient who will be readmitted costs more than an extra follow-up call." }),
  );
}

// --------------------------------------------------------------------- access matrix
function renderMatrix() {
  const table = $("#matrix");
  const me = state.principal ? state.principal.role : null;
  table.replaceChildren(
    el("thead", {}, el("tr", {}, el("th", { text: "Endpoint" }), el("th", { text: "Permission" }),
      ROLES.map((r) => el("th", { text: r === me ? `${r} (you)` : r })))),
    el("tbody", {}, ENDPOINTS.map(([endpoint, perm]) =>
      el("tr", {},
        el("td", { text: endpoint }),
        el("td", {}, el("code", { text: perm || "public" })),
        ROLES.map((role) => {
          if (!perm) return el("td", { class: "pub", text: "open" });
          const p = ROLE_PERMISSIONS[role];
          const ok = p.includes("*") || p.includes(perm);
          return el("td", { class: ok ? "yes" : "no", text: ok ? "200" : "403" });
        })))),
  );
}

// --------------------------------------------------------------------- boot
async function boot() {
  $("#signin-form").addEventListener("submit", signIn);
  $("#signout-btn").addEventListener("click", signOut);
  for (const t of $$(".tab")) t.addEventListener("click", () => showTab(t.dataset.tab));
  $("#patient-form").addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(e.submitter || $("[data-action=predict]"), () => predict(false));
  });
  $("#explain-btn").addEventListener("click", (e) => withBusy(e.currentTarget, () => predict(true)));
  $("#reset-sample").addEventListener("click", fillSample);
  $("#batch-btn").addEventListener("click", (e) => withBusy(e.currentTarget, runBatch));
  $("#drift-btn").addEventListener("click", (e) => withBusy(e.currentTarget, runDrift));
  $("#model-btn").addEventListener("click", loadModelInfo);

  checkReady();
  setInterval(checkReady, 15000);

  const out = await api("GET", "/schema/features", { auth: false });
  if (out.ok) {
    state.schema = out.data;
    buildForm();
  } else {
    toast("Could not load the feature schema from the API.");
  }
  $("#api-key").focus();
}

document.addEventListener("DOMContentLoaded", boot);
