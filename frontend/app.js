/* ═══════════════════════════════════════════════════════════
   NER Review Platform — app.js
   Talks to the FastAPI backend (JWT auth, WebSocket locks,
   live metrics, versioning) instead of Supabase directly.
   ═══════════════════════════════════════════════════════════ */

let S = {
  accessToken: null, refreshToken: null,
  meId: null, meName: "", meEmail: "",
  pid: null, pname: "", myRole: null, ownerId: null,
  projects: [],
  documents: [],            // [{id, doc_id_external, sentence_count}]
  currentDoc: null,         // doc_id_external
  docDetail: null,          // {id, doc_id_external, sentences:[...]}
  sentenceIndex: new Map(), // sentence_id_external -> sentence obj (with entities)
  entityIndex: new Map(),   // entity id -> {entity, sentenceIdExternal}
  entityCounts: {}, labels: new Set(), activeLabels: new Set(), lStyle: {},
  openEntityId: null, saving: new Set(),
  locks: {},                // doc_id_external -> {locked_by, locked_by_name}
  ws: null, wsRetry: null,
  tab: "annotate",
  fnSelection: null,
};

const ONTOLOGY = {
  "Private Fusion Company":                            {bg:"rgba(20,184,166,.16)",  bd:"rgba(20,184,166,.75)",  dot:"#0d9488", cls:"org"},
  "National Laboratory / Research Lab":               {bg:"rgba(6,182,212,.14)",   bd:"rgba(6,182,212,.72)",   dot:"#0891b2", cls:"org"},
  "Academic Institution":                             {bg:"rgba(8,145,178,.14)",   bd:"rgba(8,145,178,.72)",   dot:"#0369a1", cls:"org"},
  "Investor / Venture Capital":                       {bg:"rgba(14,165,233,.14)",  bd:"rgba(14,165,233,.72)",  dot:"#0284c7", cls:"org"},
  "Big Tech / Industry Partner":                      {bg:"rgba(34,211,238,.14)",  bd:"rgba(34,211,238,.70)",  dot:"#06b6d4", cls:"org"},
  "Government / Policy / Funding / Regulatory Agency":{bg:"rgba(103,232,249,.18)",bd:"rgba(22,189,220,.72)",  dot:"#0e7490", cls:"org"},
  "International Organization / Consortium":          {bg:"rgba(56,189,248,.14)",  bd:"rgba(56,189,248,.70)",  dot:"#0284c7", cls:"org"},
  "Other / Unspecified Organization":                 {bg:"rgba(186,230,253,.24)", bd:"rgba(125,211,252,.65)", dot:"#7dd3fc", cls:"org"},
  "Fusion Device":                                    {bg:"rgba(251,146,60,.15)",  bd:"rgba(251,146,60,.72)",  dot:"#f97316", cls:"device"},
  "Fusion Technique":                                 {bg:"rgba(167,139,250,.16)", bd:"rgba(167,139,250,.72)", dot:"#7c3aed", cls:"technique"},
  "Fusion Metric":                                    {bg:"rgba(52,211,153,.15)",  bd:"rgba(52,211,153,.72)",  dot:"#059669", cls:"metric"},
  "Fusion Materials, Fuels, and Isotopes":            {bg:"rgba(251,113,133,.15)", bd:"rgba(251,113,133,.70)", dot:"#e11d48", cls:"material"},
  "Investment and Funding":                           {bg:"rgba(99,102,241,.15)",  bd:"rgba(99,102,241,.70)",  dot:"#4338ca", cls:"funding"},
};
const FALLBACK = [
  {bg:"rgba(245,158,11,.13)", bd:"rgba(245,158,11,.68)", dot:"#f59e0b"},
  {bg:"rgba(236,72,153,.12)", bd:"rgba(236,72,153,.62)", dot:"#ec4899"},
  {bg:"rgba(132,204,22,.12)", bd:"rgba(132,204,22,.62)", dot:"#84cc16"},
  {bg:"rgba(217,70,239,.11)", bd:"rgba(217,70,239,.58)", dot:"#d946ef"},
];
let _fi = 0;

const esc  = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const escA = s => String(s).replace(/"/g,"&quot;");
const escJ = s => String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
const pct  = v => (v != null ? (v*100).toFixed(1)+"%" : "—");
const wsUrl = () => API_BASE_URL.replace(/^http/, "ws");
function _qs(id) { return document.getElementById(id); }

/* ══════════════════════════════════════════════════════════
   API layer — fetch wrapper with JWT + one-shot refresh retry
   ══════════════════════════════════════════════════════════ */
async function api(path, { method = "GET", body, isForm = false, auth = true } = {}) {
  const headers = {};
  if (!isForm) headers["Content-Type"] = "application/json";
  if (auth && S.accessToken) headers["Authorization"] = `Bearer ${S.accessToken}`;

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: isForm ? body : (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (res.status === 401 && auth && S.refreshToken) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      headers["Authorization"] = `Bearer ${S.accessToken}`;
      const retry = await fetch(`${API_BASE_URL}${path}`, {
        method, headers,
        body: isForm ? body : (body !== undefined ? JSON.stringify(body) : undefined),
      });
      return finishResponse(retry);
    }
    logout();
    throw new Error("Session expired — please log in again");
  }
  return finishResponse(res);
}

async function finishResponse(res) {
  if (res.status === 204) return null;
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch (_) { data = text; } }
  if (!res.ok) {
    const detail = (data && (data.detail?.errors?.join("; ") || data.detail)) || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function tryRefreshToken() {
  try {
    const res = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: S.refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    S.accessToken = data.access_token;
    S.refreshToken = data.refresh_token;
    localStorage.setItem("ner_refresh_token", S.refreshToken);
    return true;
  } catch (_) { return false; }
}

/* ══════════════════════════════════════════════════════════
   Boot
   ══════════════════════════════════════════════════════════ */
window.addEventListener("DOMContentLoaded", async () => {
  checkApiStatus();
  const savedRefresh = localStorage.getItem("ner_refresh_token");
  if (savedRefresh) {
    S.refreshToken = savedRefresh;
    const ok = await tryRefreshToken();
    if (ok) { await afterLogin(); }
    else { localStorage.removeItem("ner_refresh_token"); showAuth(); }
  } else {
    showAuth();
  }

  document.getElementById("uploadFileInput").addEventListener("change", e => {
    if (e.target.files[0]) handleUpload(e.target.files[0]);
  });
  document.addEventListener("click", e => { if (!e.target.closest(".ent-wrap")) closeMenu(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      closeMenu(); cancelFn();
      if (!_qs("saveVersionOverlay").classList.contains("hidden")) hideSaveVersionModal();
    }
  });
  document.addEventListener("mouseup", onSelectionMouseUp);
});

async function checkApiStatus() {
  const el = _qs("apiStatus");
  el.textContent = "⏳ Connecting to API…"; el.style.color = "";
  try {
    const res = await fetch(`${API_BASE_URL}/health`);
    if (!res.ok) throw new Error("unreachable");
    el.textContent = "✅ API connected"; el.style.color = "#12a454";
  } catch (e) {
    el.innerHTML = `❌ <strong>API unreachable</strong> at ${esc(API_BASE_URL)} — is the backend running?`;
    el.style.color = "#e5484d";
  }
}

/* ══════════════════════════════════════════════════════════
   Auth
   ══════════════════════════════════════════════════════════ */
function showAuth() { _qs("authOverlay").classList.remove("hidden"); }
function hideAuth() { _qs("authOverlay").classList.add("hidden"); }

function switchAuthTab(tab) {
  document.querySelectorAll("[data-authtab]").forEach(b => b.classList.toggle("active", b.dataset.authtab === tab));
  _qs("authFormLogin").style.display  = tab === "login"  ? "" : "none";
  _qs("authFormSignup").style.display = tab === "signup" ? "" : "none";
}

async function doLogin() {
  const email = _qs("loginEmail").value.trim();
  const password = _qs("loginPassword").value;
  const errEl = _qs("loginError"); errEl.textContent = "";
  if (!email || !password) { errEl.textContent = "Enter your email and password."; return; }
  try {
    const data = await api("/auth/login", { method: "POST", body: { email, password }, auth: false });
    S.accessToken = data.access_token; S.refreshToken = data.refresh_token;
    localStorage.setItem("ner_refresh_token", S.refreshToken);
    await afterLogin();
  } catch (e) { errEl.textContent = e.message; }
}

async function doSignup() {
  const name = _qs("signupName").value.trim();
  const email = _qs("signupEmail").value.trim();
  const password = _qs("signupPassword").value;
  const errEl = _qs("signupError"); errEl.textContent = "";
  if (!name || !email || !password) { errEl.textContent = "Fill in all fields."; return; }
  try {
    await api("/auth/signup", { method: "POST", body: { name, email, password }, auth: false });
    const data = await api("/auth/login", { method: "POST", body: { email, password }, auth: false });
    S.accessToken = data.access_token; S.refreshToken = data.refresh_token;
    localStorage.setItem("ner_refresh_token", S.refreshToken);
    await afterLogin();
  } catch (e) { errEl.textContent = e.message; }
}

async function afterLogin() {
  const me = await api("/auth/me");
  S.meId = me.id; S.meName = me.name; S.meEmail = me.email;
  hideAuth();
  _qs("userPill").style.display = "flex";
  _qs("userPillName").textContent = S.meName;
  _qs("userAvatar").textContent = (S.meName[0] || "?").toUpperCase();
  showModal(); loadProjectList();
}

function logout() {
  if (S.ws) { S.ws.close(); S.ws = null; }
  localStorage.removeItem("ner_refresh_token");
  S = Object.assign(S, {
    accessToken: null, refreshToken: null, meId: null, meName: "", meEmail: "",
    pid: null, pname: "", myRole: null, ownerId: null,
  });
  resetUI();
  _qs("userPill").style.display = "none";
  hideModal();
  showAuth();
}

/* ══════════════════════════════════════════════════════════
   Project picker
   ══════════════════════════════════════════════════════════ */
function showModal() { _qs("overlay").classList.remove("hidden"); }
function hideModal() { _qs("overlay").classList.add("hidden"); _qs("uploadSection").style.display = "none"; }

async function loadProjectList() {
  const el = _qs("projectList");
  el.innerHTML = `<div class="empty-state">Loading…</div>`;
  try {
    const list = await api("/projects");
    S.projects = list;
    if (!list.length) { el.innerHTML = `<div class="empty-state">No projects yet — create one above.</div>`; return; }
    el.innerHTML = list.map(p => {
      const isOwner = p.owner_id === S.meId;
      return `
      <div class="project-row" onclick="openProject('${p.id}','${escJ(p.name)}')">
        <div>
          <div class="project-name">${esc(p.name)}</div>
          <div class="project-date">${(p.created_at||"").slice(0,10)}</div>
        </div>
        <div class="project-actions">
          <span class="project-role">${isOwner ? "Owner" : "Member"}</span>
          ${isOwner ? `<button class="btn btn-danger btn-tiny" onclick="event.stopPropagation(); deleteProject('${p.id}','${escJ(p.name)}')">Delete</button>` : ""}
        </div>
      </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
  }
}

async function deleteProject(projectId, name) {
  if (!confirm(`Permanently delete "${name}"? This removes every document, entity, review, and saved version — this cannot be undone.`)) return;
  try {
    await api(`/projects/${projectId}`, { method: "DELETE" });
    if (S.pid === projectId) { resetUI(); }
    loadProjectList();
  } catch (e) {
    alert("Could not delete project: " + e.message);
  }
}

async function createProject() {
  const name = _qs("newProjectName").value.trim();
  if (!name) { _qs("newProjectName").focus(); return; }
  try {
    const proj = await api("/projects", { method: "POST", body: { name } });
    S.pid = proj.id; S.pname = proj.name;
    _qs("uploadSection").style.display = "block";
    _qs("uploadStatus").textContent = `Project "${proj.name}" created — now upload its dataset.`;
    _qs("newProjectName").value = "";
    loadProjectList();
  } catch (e) { alert("Could not create project: " + e.message); }
}

async function handleUpload(file) {
  if (!S.pid) { setUploadStatus("❌ Create a project first.", true); return; }
  setUploadStatus(`⏳ Uploading ${file.name}…`);
  const form = new FormData();
  form.append("file", file);
  try {
    const summary = await api(`/projects/${S.pid}/upload`, { method: "POST", body: form, isForm: true });
    setUploadStatus(`✅ ${summary.documents_created} documents · ${summary.sentences_created} sentences · ${summary.entities_created} entities loaded`);
    openProject(S.pid, S.pname);
  } catch (e) { setUploadStatus(`❌ ${e.message}`, true); }
}
function setUploadStatus(msg, err=false) {
  const el = _qs("uploadStatus"); el.textContent = msg; el.style.color = err ? "#e5484d" : "";
}

/* Drag & drop onto the upload zone */
document.addEventListener("dragover", e => {
  if (!_qs("uploadSection") || _qs("uploadSection").style.display === "none") return;
  e.preventDefault(); document.body.classList.add("drag-active");
});
document.addEventListener("dragleave", () => document.body.classList.remove("drag-active"));
document.addEventListener("drop", e => {
  if (!_qs("uploadSection") || _qs("uploadSection").style.display === "none") return;
  e.preventDefault(); document.body.classList.remove("drag-active");
  if (e.dataTransfer.files[0]) handleUpload(e.dataTransfer.files[0]);
});

/* ══════════════════════════════════════════════════════════
   Open project
   ══════════════════════════════════════════════════════════ */
async function openProject(pid, name = "") {
  S.pid = pid; S.pname = name || S.pname || "Project";
  Object.assign(S, {
    documents: [], currentDoc: null, docDetail: null, sentenceIndex: new Map(),
    entityIndex: new Map(), entityCounts: {}, labels: new Set(), activeLabels: new Set(),
    lStyle: {}, openEntityId: null, locks: {},
  });

  hideModal();
  _qs("projectSubtitle").textContent = S.pname;
  _qs("statRow").style.display = "flex";
  _qs("welcomeState").style.display = "none";
  _qs("docList").innerHTML = `<div class="empty-state">Loading…</div>`;

  try {
    const proj = await api(`/projects/${pid}`);
    S.ownerId = proj.owner_id;
    S.pname = proj.name || S.pname;
    _qs("projectSubtitle").textContent = S.pname;

    const members = await api(`/projects/${pid}/members`);
    const mine = members.find(m => m.user_id === S.meId);
    S.myRole = mine ? mine.role : "viewer";

    S.documents = await api(`/projects/${pid}/documents`);
    const lockList = await api(`/projects/${pid}/locks`);
    S.locks = {};
    for (const l of lockList) S.locks[l.doc_id_external] = { locked_by: l.locked_by, locked_by_name: l.locked_by_name };
  } catch (e) {
    alert("Failed to open project: " + e.message); showModal(); return;
  }

  updateStats();
  buildDocList();
  connectLockSocket();
  _qs("topSaveBtn").style.display = "inline-flex";

  if (S.documents.length) selectDoc(S.documents[0].doc_id_external);
  else {
    _qs("mainHeader").style.display = "none";
    _qs("sentencePane").innerHTML = `<div class="empty-state">This project has no documents yet — upload a dataset from the ⊕ Projects menu.</div>`;
    _qs("welcomeState").style.display = "none";
  }
}

function resetUI() {
  if (S.ws) { S.ws.close(); S.ws = null; }
  S.pid = null; S.pname = ""; S.currentDoc = null; S.tab = "annotate"; S.ownerId = null;
  _qs("projectSubtitle").textContent = "No project open";
  _qs("topSaveBtn").style.display = "none";
  _qs("statRow").style.display = "none";
  _qs("docCountBadge").textContent = "0";
  _qs("docList").innerHTML = "";
  _qs("legendItems").innerHTML = "";
  _qs("legendCard").style.display = "none";
  _qs("mainHeader").style.display = "none";
  _qs("sentencePane").innerHTML = "";
  _qs("metricsPanel").innerHTML = "";
  _qs("teamPanel").innerHTML = "";
  _qs("versionsPanel").innerHTML = "";
  _qs("welcomeState").style.display = "flex";
}

function updateStats() {
  const totalSent = S.documents.reduce((n, d) => n + d.sentence_count, 0);
  _qs("sDoc").textContent = `${S.documents.length} docs`;
  _qs("sSent").textContent = `${totalSent} sentences`;
  const totalEnt = Object.values(S.entityCounts).reduce((a,b)=>a+b,0);
  _qs("sEnt").textContent = `${totalEnt} entities`;
  _qs("sType").textContent = `${S.labels.size} types`;
}

function buildDocList() {
  const entries = [...S.documents].sort((a,b) => a.doc_id_external.localeCompare(b.doc_id_external, undefined, {numeric:true}));
  _qs("docCountBadge").textContent = entries.length;
  _qs("docList").innerHTML = entries.map(d => {
    const lock = S.locks[d.doc_id_external];
    const lockedByOther = lock && lock.locked_by !== S.meId;
    const glyph = lock ? (lockedByOther ? `<span class="lock-glyph" title="Locked by ${escA(lock.locked_by_name)}">🔒</span>` : `<span class="lock-glyph" title="Locked by you">🔓</span>`) : "";
    return `<div class="doc-item ${lockedByOther?'locked-other':''}" data-doc="${escA(d.doc_id_external)}">
      <div class="doc-pip"></div>
      <div class="doc-name" title="${escA(d.doc_id_external)}">${esc(d.doc_id_external)}</div>
      ${glyph}
      <div class="doc-count">${d.sentence_count}</div>
    </div>`;
  }).join("");
  _qs("docList").onclick = e => {
    const item = e.target.closest(".doc-item");
    if (item) selectDoc(item.dataset.doc);
  };
}

/* ══════════════════════════════════════════════════════════
   Document selection + locking
   ══════════════════════════════════════════════════════════ */
async function selectDoc(docExternal) {
  if (docExternal === S.currentDoc) return;

  // release our own lock on whatever we were editing before switching
  if (S.currentDoc) await unlockDoc(S.currentDoc);

  S.currentDoc = docExternal; S.openEntityId = null;
  document.querySelectorAll(".doc-item").forEach(el => el.classList.toggle("active", el.dataset.doc === docExternal));

  _qs("mainHeader").style.display = "flex";
  _qs("welcomeState").style.display = "none";
  _qs("sentencePane").innerHTML = `<div class="empty-state">⏳ Loading document…</div>`;
  showTab("annotate");

  try {
    S.docDetail = await api(`/projects/${S.pid}/documents/${encodeURIComponent(docExternal)}`);
  } catch (e) {
    _qs("sentencePane").innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
    return;
  }

  indexDocDetail();
  updateDocHeader();
  renderSentences();
  buildLegend();
  updateStats();

  if (canEdit()) await lockCurrentDoc();
}

function canEdit() {
  return S.myRole === "owner" || S.myRole === "reviewer";
}
function docLockedByOther() {
  const lock = S.locks[S.currentDoc];
  return !!(lock && lock.locked_by !== S.meId);
}
function canEditCurrentDoc() { return canEdit() && !docLockedByOther(); }

async function lockCurrentDoc() {
  try {
    const lock = await api(`/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}/lock`, { method: "POST" });
    S.locks[S.currentDoc] = { locked_by: lock.locked_by, locked_by_name: lock.locked_by_name };
  } catch (e) {
    // Someone else already holds it — fine, just stay read-only for this doc
  }
  buildDocList(); updateDocHeader(); renderSentences();
}

async function unlockDoc(docExternal) {
  const lock = S.locks[docExternal];
  if (!lock || lock.locked_by !== S.meId) return;
  try { await api(`/projects/${S.pid}/documents/${encodeURIComponent(docExternal)}/unlock`, { method: "POST" }); }
  catch (_) { /* best effort */ }
  delete S.locks[docExternal];
}

/* Manual unlock button in the doc header — covers both "unlock your own
   lock" and "Owner force-unlocks someone else's lock". The backend's
   /unlock endpoint already allows either case server-side; this is just
   the UI entry point for it (unlike unlockDoc() above, which silently
   no-ops for anyone but the lock holder, since that one runs
   automatically on every doc switch / tab close). */
async function manualUnlockClick() {
  const lock = S.locks[S.currentDoc];
  if (!lock || !S.currentDoc) return;
  if (lock.locked_by !== S.meId) {
    if (!confirm(`Force unlock this document? ${lock.locked_by_name} is currently reviewing it and will lose their lock.`)) return;
  }
  try {
    await api(`/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}/unlock`, { method: "POST" });
    delete S.locks[S.currentDoc];
    buildDocList(); updateDocHeader(); renderSentences();
  } catch (e) {
    alert("Could not unlock: " + e.message);
  }
}
window.addEventListener("beforeunload", () => {
  if (S.pid && S.currentDoc && S.locks[S.currentDoc]?.locked_by === S.meId) {
    navigator.sendBeacon(`${API_BASE_URL}/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}/unlock`);
  }
});

function updateDocHeader() {
  const d = S.docDetail;
  if (!d) return;
  const entCount = d.sentences.reduce((n,s)=>n+s.entities.length, 0);
  const reviewedCount = d.sentences.reduce((n,s)=>n+s.entities.filter(e=>e.current_review || e.source==="human").length, 0);
  _qs("mainDocTitle").textContent = d.doc_id_external;
  _qs("mainDocBadge").textContent = `${d.sentences.length} sentences · ${entCount} entities`;
  _qs("progressChip").textContent = `${reviewedCount}/${entCount} reviewed`;

  const lock = S.locks[S.currentDoc];
  const chip = _qs("lockChip");
  const unlockBtn = _qs("unlockBtn");
  if (lock) {
    chip.style.display = "inline-block";
    if (lock.locked_by === S.meId) {
      chip.textContent = "🔓 Locked by you"; chip.className = "lock-chip mine";
      unlockBtn.style.display = "inline-flex"; unlockBtn.className = "btn-tiny"; unlockBtn.textContent = "Unlock";
    } else if (S.myRole === "owner") {
      chip.textContent = `🔒 Locked by ${lock.locked_by_name}`; chip.className = "lock-chip other";
      unlockBtn.style.display = "inline-flex"; unlockBtn.className = "btn-tiny danger"; unlockBtn.textContent = "Force unlock";
    } else {
      chip.textContent = `🔒 Locked by ${lock.locked_by_name}`; chip.className = "lock-chip other";
      unlockBtn.style.display = "none";
    }
  } else {
    chip.style.display = "none";
    unlockBtn.style.display = "none";
  }
}

function indexDocDetail() {
  S.sentenceIndex = new Map();
  S.entityIndex = new Map();
  S.entityCounts = {}; S.labels = new Set();
  for (const sent of S.docDetail.sentences) {
    S.sentenceIndex.set(sent.sentence_id_external, sent);
    for (const ent of sent.entities) {
      S.entityIndex.set(ent.id, { entity: ent, sentenceIdExternal: sent.sentence_id_external });
      S.entityCounts[ent.label] = (S.entityCounts[ent.label]||0) + 1;
      S.labels.add(ent.label);
      if (!S.activeLabels.has(ent.label)) S.activeLabels.add(ent.label);
    }
  }
}

/* ══════════════════════════════════════════════════════════
   WebSocket — lock/unlock broadcasts
   ══════════════════════════════════════════════════════════ */
function connectLockSocket() {
  if (S.ws) { S.ws.close(); S.ws = null; }
  clearTimeout(S.wsRetry);
  const url = `${wsUrl()}/projects/${S.pid}/ws?token=${encodeURIComponent(S.accessToken)}`;
  const ws = new WebSocket(url);
  S.ws = ws;
  ws.onopen  = () => { _qs("wsDot").className = "conn-dot on"; };
  ws.onclose = () => {
    _qs("wsDot").className = "conn-dot off";
    if (S.pid) S.wsRetry = setTimeout(connectLockSocket, 3000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (evt) => {
    let msg; try { msg = JSON.parse(evt.data); } catch (_) { return; }
    if (msg.type === "lock") {
      S.locks[msg.doc_id_external] = { locked_by: msg.locked_by, locked_by_name: msg.locked_by_name };
    } else if (msg.type === "unlock") {
      delete S.locks[msg.doc_id_external];
    } else return;
    buildDocList();
    if (msg.doc_id_external === S.currentDoc) { updateDocHeader(); renderSentences(); }
  };
}

/* ══════════════════════════════════════════════════════════
   Sentence / entity rendering
   ══════════════════════════════════════════════════════════ */
function renderSentences() {
  const editable = canEditCurrentDoc();
  _qs("sentencePane").innerHTML = S.docDetail.sentences.map(sent => `
    <div class="sent-card">
      <div class="sent-meta">${esc(S.docDetail.doc_id_external)} · ${esc(sent.sentence_id_external)}</div>
      <div class="sent-text" data-sent="${escA(sent.sentence_id_external)}" data-editable="${editable}">${buildSentHTML(sent)}</div>
    </div>`).join("");
  _qs("mainBody").scrollTop = 0;
  applyVisibility();
}

function buildSentHTML(sent) {
  const text = sent.text || "";
  const ents = [...sent.entities].sort((a,b)=>a.start_char-b.start_char);
  if (!ents.length) return esc(text);
  let html = "", cur = 0;
  for (const ent of ents) {
    if (ent.start_char < cur || ent.start_char >= text.length) continue;
    if (ent.start_char > cur) html += esc(text.slice(cur, ent.start_char));
    html += buildEntitySpan(ent);
    cur = ent.end_char;
  }
  if (cur < text.length) html += esc(text.slice(cur));
  return html;
}

function getStyle(label) {
  if (!S.lStyle[label]) {
    const lower = (label||"").toLowerCase();
    let matched = null;
    for (const k of Object.keys(ONTOLOGY)) if (k.toLowerCase() === lower) { matched = k; break; }
    if (!matched) {
      const norm = lower.replace(/[^a-z0-9 ]/g," ").trim();
      for (const k of Object.keys(ONTOLOGY)) {
        const kn = k.toLowerCase().replace(/[^a-z0-9 ]/g," ").trim();
        if (norm.includes(kn) || kn.includes(norm)) { matched = k; break; }
      }
    }
    const s = matched ? ONTOLOGY[matched] : FALLBACK[_fi++ % FALLBACK.length];
    S.lStyle[label] = s;
    const el = document.createElement("style");
    el.textContent = `.entity[data-type="${label.replace(/"/g,'\\"')}"]{ background:${s.bg}; border-bottom-color:${s.bd}; }`;
    document.head.appendChild(el);
  }
  return S.lStyle[label];
}

function buildEntitySpan(ent) {
  const s = getStyle(ent.label);
  const isHuman = ent.source === "human";
  const verdict = isHuman ? "fn" : (ent.current_review ? ent.current_review.verdict.toLowerCase() : "none");
  const saving = S.saving.has(ent.id);
  const dis = saving ? "disabled" : "";
  const editable = canEditCurrentDoc();
  const isOpen = S.openEntityId === ent.id;
  const eidJs = escJ(ent.id);

  let bar;
  if (isHuman) {
    const who = ent.current_review ? ent.current_review.reviewer_name : "someone";
    bar = `<span class="ent-bar" onclick="event.stopPropagation()"><span class="ent-label-tip">${esc(ent.label)} · FN reported by ${esc(who)}</span></span>`;
  } else {
    const reviewer = ent.current_review ? ` · last: ${esc(ent.current_review.reviewer_name)}` : "";
    bar = `<span class="ent-bar" onclick="event.stopPropagation()">
      <span class="ent-label-tip">${esc(ent.label)}${reviewer}</span>
      <button class="ent-btn ent-btn-tp ${verdict==='tp'?'ent-active-tp':''}" ${dis||!editable?"disabled":""} onclick="setVerdict('${eidJs}','TP',event)">TP</button>
      <button class="ent-btn ent-btn-fp ${verdict==='fp'?'ent-active-fp':''}" ${dis||!editable?"disabled":""} onclick="setVerdict('${eidJs}','FP',event)">FP</button>
    </span>`;
  }

  return `<span class="ent-wrap ${isOpen?"open":""}"><span class="entity verdict-${verdict}" data-type="${escA(ent.label)}" data-eid="${escA(ent.id)}" onclick="toggleEntityBar('${eidJs}',event)">${esc(ent.text)}</span>${bar}</span>`;
}

function toggleEntityBar(id, ev) {
  if (ev) ev.stopPropagation();
  const prev = S.openEntityId;
  S.openEntityId = prev === id ? null : id;
  if (prev && prev !== S.openEntityId) rerenderEntity(prev);
  rerenderEntity(id);
}
function closeMenu() {
  if (!S.openEntityId) return;
  const prev = S.openEntityId; S.openEntityId = null; rerenderEntity(prev);
}

function rerenderEntity(id) {
  const el = [...document.querySelectorAll(".entity")].find(n => n.dataset.eid === String(id));
  const ref = S.entityIndex.get(id);
  if (!el || !ref) return;
  const tmp = document.createElement("span");
  tmp.innerHTML = buildEntitySpan(ref.entity);
  el.closest(".ent-wrap").replaceWith(tmp.firstChild);
}

async function setVerdict(entityId, verdict, ev) {
  ev.stopPropagation();
  if (!canEditCurrentDoc() || S.saving.has(entityId)) return;
  const ref = S.entityIndex.get(entityId);
  if (!ref) return;
  const prevReview = ref.entity.current_review;
  const already = prevReview && prevReview.verdict === verdict;

  S.saving.add(entityId); rerenderEntity(entityId);
  try {
    if (already) {
      // No "clear" endpoint server-side (reviews are append-only) —
      // resubmitting the same verdict is a no-op visually.
    } else {
      const review = await api(`/projects/${S.pid}/entities/${entityId}/review`, { method: "POST", body: { verdict } });
      ref.entity.current_review = review;
    }
  } catch (e) {
    alert("Could not save review: " + e.message);
  } finally {
    S.saving.delete(entityId); S.openEntityId = null;
    rerenderEntity(entityId); updateDocHeader();
  }
}

/* ══════════════════════════════════════════════════════════
   Missed-entity (FN) capture via click-and-drag text selection
   ══════════════════════════════════════════════════════════ */
function onSelectionMouseUp(e) {
  const container = e.target.closest(".sent-text");
  cancelFn();
  if (!container || container.dataset.editable !== "true") return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
  if (!container.contains(sel.anchorNode) || !container.contains(sel.focusNode)) return;

  const range = sel.getRangeAt(0);
  const selectedText = range.toString();
  if (!selectedText.trim()) return;

  const start = offsetWithin(container, range.startContainer, range.startOffset);
  const end = offsetWithin(container, range.endContainer, range.endOffset);
  if (start == null || end == null || end <= start) return;

  const sentExternal = container.dataset.sent;
  const rect = range.getBoundingClientRect();
  showFnToolbar(rect, sentExternal, selectedText, Math.min(start,end), Math.max(start,end));
}

function offsetWithin(container, node, nodeOffset) {
  // Walks all text nodes inside `container` (which may contain entity
  // <span>s) and sums lengths up to `node`/`nodeOffset` to get a plain
  // character offset into the sentence's raw text.
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let total = 0, found = false, result = 0;
  let n;
  while ((n = walker.nextNode())) {
    if (n === node) { result = total + nodeOffset; found = true; break; }
    total += n.textContent.length;
  }
  return found ? result : null;
}

function showFnToolbar(rect, sentExternal, text, start, end) {
  S.fnSelection = { sentExternal, text, start, end };
  let bar = _qs("fnToolbar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "fnToolbar";
    bar.className = "fn-toolbar";
    document.body.appendChild(bar);
  }
  bar.innerHTML = `
    <input id="fnLabelInput" placeholder="Entity label…" autofocus>
    <button onclick="submitFn()">Mark as FN</button>
    <button class="fn-cancel" onclick="cancelFn()">Cancel</button>`;
  bar.style.left = `${Math.max(8, rect.left)}px`;
  bar.style.top = `${Math.max(8, rect.top - 46 + window.scrollY)}px`;
  bar.style.display = "flex";
  setTimeout(() => _qs("fnLabelInput")?.focus(), 0);
}
function cancelFn() {
  const bar = _qs("fnToolbar");
  if (bar) bar.style.display = "none";
  S.fnSelection = null;
}

async function submitFn() {
  const sel = S.fnSelection;
  if (!sel) return;
  const label = (_qs("fnLabelInput")?.value || "").trim();
  if (!label) { _qs("fnLabelInput").focus(); return; }
  try {
    const result = await api(
      `/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}/sentences/${encodeURIComponent(sel.sentExternal)}/missed-entity`,
      { method: "POST", body: { text: sel.text, label, start_char: sel.start, end_char: sel.end } }
    );
    const sentence = S.sentenceIndex.get(sel.sentExternal);
    sentence.entities.push({
      id: result.id, text: result.text, label: result.label,
      start_char: result.start_char, end_char: result.end_char,
      source: result.source, current_review: result.review,
    });
    indexDocDetail();
    renderSentences(); buildLegend(); updateDocHeader(); updateStats();
  } catch (e) {
    alert("Could not report missed entity: " + e.message);
  } finally {
    cancelFn();
    window.getSelection()?.removeAllRanges();
  }
}

/* ══════════════════════════════════════════════════════════
   Legend / label filtering
   ══════════════════════════════════════════════════════════ */
function buildLegend() {
  _qs("legendCard").style.display = "block";
  const sorted = [...S.labels].sort((a,b)=>(S.entityCounts[b]||0)-(S.entityCounts[a]||0));
  _qs("legendItems").innerHTML = sorted.map(lbl => {
    const s = getStyle(lbl);
    return `<div class="legend-tag" data-type="${escA(lbl)}" style="background:${s.bg};border-color:${s.bd}" onclick="toggleLabel('${escJ(lbl)}')">
      <div class="legend-dot" style="background:${s.dot}"></div>
      <span class="legend-label">${esc(lbl)}</span>
      <span class="legend-count">${S.entityCounts[lbl]||0}</span>
    </div>`;
  }).join("");
  applyVisibility();
}
function toggleLabel(t) { S.activeLabels.has(t) ? S.activeLabels.delete(t) : S.activeLabels.add(t); applyVisibility(); }
function selectAllLabels() { S.activeLabels = new Set(S.labels); applyVisibility(); }
function deselectAllLabels() { S.activeLabels.clear(); applyVisibility(); }
function applyVisibility() {
  document.querySelectorAll(".entity").forEach(el => { el.style.display = S.activeLabels.has(el.dataset.type) ? "" : "none"; });
  document.querySelectorAll(".legend-tag").forEach(el => { el.classList.toggle("inactive", !S.activeLabels.has(el.dataset.type)); });
}

/* ══════════════════════════════════════════════════════════
   Tabs
   ══════════════════════════════════════════════════════════ */
function showTab(tab) {
  S.tab = tab;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  _qs("sentencePane").style.display  = tab==="annotate" ? "" : "none";
  _qs("metricsPanel").style.display  = tab==="metrics"  ? "" : "none";
  _qs("teamPanel").style.display     = tab==="team"     ? "" : "none";
  _qs("versionsPanel").style.display = tab==="versions" ? "" : "none";
  if (tab==="metrics")  loadMetrics();
  if (tab==="team")     loadTeam();
  if (tab==="versions") loadVersions();
}

/* ── Metrics ── */
async function loadMetrics() {
  if (!S.pid) return;
  const panel = _qs("metricsPanel");
  panel.innerHTML = `<div class="empty-state">Loading metrics…</div>`;
  try {
    const [project, doc] = await Promise.all([
      api(`/projects/${S.pid}/metrics`),
      S.currentDoc ? api(`/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}/metrics`) : Promise.resolve(null),
    ]);
    renderMetrics(project, doc);
  } catch (e) {
    panel.innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
  }
}
function metricCards(m, title) {
  return `<h4 style="margin:0 0 10px;font-size:13.5px">${esc(title)}</h4>
    <div class="metric-cards">
      <div class="metric-card"><div class="metric-val" style="color:#12a454">${pct(m.precision)}</div><div class="metric-lbl">Precision</div><div class="metric-sub">TP=${m.tp} / FP=${m.fp}</div></div>
      <div class="metric-card"><div class="metric-val" style="color:#0a84ff">${pct(m.recall)}</div><div class="metric-lbl">Recall</div><div class="metric-sub">TP=${m.tp} / FN=${m.fn}</div></div>
      <div class="metric-card"><div class="metric-val" style="color:#5e5ce6">${pct(m.f1)}</div><div class="metric-lbl">F1</div><div class="metric-sub">${m.percent_reviewed}% reviewed</div></div>
      <div class="metric-card"><div class="metric-val">${m.tp+m.fp+m.fn}</div><div class="metric-lbl">Total verdicts</div><div class="metric-sub">${m.tp} TP · ${m.fp} FP · ${m.fn} FN</div></div>
    </div>`;
}
function renderMetrics(project, doc) {
  let html = metricCards(project, "Project-wide");
  if (doc) html += `<div style="margin-top:20px">${metricCards(doc, `Current document — ${esc(S.currentDoc)}`)}</div>`;
  _qs("metricsPanel").innerHTML = html;
}

/* ── Team ── */
async function loadTeam() {
  if (!S.pid) return;
  const panel = _qs("teamPanel");
  panel.innerHTML = `<div class="empty-state">Loading…</div>`;
  try {
    const members = await api(`/projects/${S.pid}/members`);
    renderTeam(members);
  } catch (e) {
    panel.innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
  }
}
function renderTeam(members) {
  const rows = members.map(m => {
    const isOwnerRow = m.user_id === S.ownerId;
    const canRemove = S.myRole === "owner" && !isOwnerRow;
    return `
    <div class="team-member">
      <div class="team-avatar">${esc((m.user_name||"?")[0].toUpperCase())}</div>
      <div><div class="team-name">${esc(m.user_name||m.user_id)}</div><div class="team-role">${esc(m.role)}${isOwnerRow ? " · project owner" : ""}</div></div>
      ${canRemove ? `<button class="btn btn-danger btn-tiny" style="margin-left:auto" onclick="removeMember('${escJ(m.id)}','${escJ(m.user_name||m.user_email)}')">Remove</button>` : ""}
    </div>`;
  }).join("");
  let inviteForm = "";
  if (S.myRole === "owner") {
    inviteForm = `<div class="invite-form">
      <h4>Invite a collaborator</h4>
      <p style="font-size:12.5px;color:var(--t2);margin:2px 0 0">They need an existing account on this platform.</p>
      <div class="invite-row">
        <input class="field" id="inviteEmail" type="email" placeholder="colleague@example.com" style="flex:2">
        <select class="field" id="inviteRole" style="flex:1">
          <option value="reviewer">Reviewer</option>
          <option value="viewer">Viewer</option>
          <option value="owner">Owner</option>
        </select>
        <button class="btn btn-primary" onclick="inviteMember()">Invite</button>
      </div>
      <div class="invite-result" id="inviteResult"></div>
    </div>`;
  }
  _qs("teamPanel").innerHTML = `<div class="team-grid">${rows}</div>${inviteForm}`;
}
async function inviteMember() {
  const email = _qs("inviteEmail").value.trim();
  const role = _qs("inviteRole").value;
  const resEl = _qs("inviteResult");
  if (!email) return;
  try {
    await api(`/projects/${S.pid}/invite`, { method: "POST", body: { email, role } });
    resEl.innerHTML = `<div class="invite-box invite-ok">✅ Added ${esc(email)} as ${esc(role)}.</div>`;
    loadTeam();
  } catch (e) {
    resEl.innerHTML = `<div class="invite-box invite-warn">⚠️ ${esc(e.message)}</div>`;
  }
}

async function removeMember(memberId, displayName) {
  if (!confirm(`Remove ${displayName} from this project? They will immediately lose all access.`)) return;
  try {
    await api(`/projects/${S.pid}/members/${memberId}`, { method: "DELETE" });
    loadTeam();
  } catch (e) {
    alert("Could not remove member: " + e.message);
  }
}

/* ── Versions ── */
async function loadVersions() {
  if (!S.pid) return;
  const panel = _qs("versionsPanel");
  panel.innerHTML = `<div class="empty-state">Loading…</div>`;
  try {
    const versions = await api(`/projects/${S.pid}/versions`);
    renderVersions(versions);
  } catch (e) {
    panel.innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
  }
}
function renderVersions(versions) {
  const toolbar = `<div class="version-toolbar">
    <input class="field" id="versionLabel" placeholder="Version label (optional)" style="flex:1">
    <button class="btn btn-primary" onclick="saveVersion()">💾 Save version</button>
  </div>`;
  const rows = versions.length ? versions.map(v => `
    <div class="version-row">
      <div class="version-dot"></div>
      <div>
        <div class="version-label">${esc(v.label)}</div>
        <div class="version-meta">by ${esc(v.created_by_name)} · ${new Date(v.created_at).toLocaleString()}</div>
      </div>
      ${S.myRole === "owner" ? `<div class="version-actions"><button class="btn btn-danger" onclick="revertVersion('${escJ(v.id)}','${escJ(v.label)}')">Revert to this</button></div>` : ""}
    </div>`).join("") : `<div class="empty-state">No saved versions yet.</div>`;
  _qs("versionsPanel").innerHTML = `${toolbar}<div class="version-list">${rows}</div>`;
}
async function saveVersion() {
  const label = _qs("versionLabel").value.trim() || null;
  try {
    await api(`/projects/${S.pid}/versions`, { method: "POST", body: { label } });
    loadVersions();
  } catch (e) { alert("Could not save version: " + e.message); }
}
async function revertVersion(versionId, label) {
  if (!confirm(`Revert the project to "${label}"? This replaces all current documents, entities, and review verdicts.`)) return;
  try {
    const result = await api(`/projects/${S.pid}/versions/${versionId}/revert`, { method: "POST" });
    alert(`Reverted: ${result.documents_restored} documents, ${result.sentences_restored} sentences, ${result.entities_restored} entities, ${result.reviews_restored} reviews restored.`);
    openProject(S.pid, S.pname);
  } catch (e) { alert("Could not revert: " + e.message); }
}

/* ── Quick save-version shortcut (top bar, any tab) ──
   Same API call as the Versions tab's own form — this is just a second,
   always-visible entry point to it, not a separate feature. */
function showSaveVersionModal() {
  if (!S.pid) return;
  _qs("quickVersionLabel").value = "";
  _qs("quickSaveStatus").textContent = ""; _qs("quickSaveStatus").style.color = "";
  _qs("saveVersionOverlay").classList.remove("hidden");
  setTimeout(() => _qs("quickVersionLabel")?.focus(), 0);
}
function hideSaveVersionModal() { _qs("saveVersionOverlay").classList.add("hidden"); }

async function submitQuickSaveVersion() {
  const label = _qs("quickVersionLabel").value.trim() || null;
  const statusEl = _qs("quickSaveStatus");
  statusEl.textContent = "⏳ Saving…"; statusEl.style.color = "";
  try {
    await api(`/projects/${S.pid}/versions`, { method: "POST", body: { label } });
    statusEl.textContent = "✅ Version saved."; statusEl.style.color = "#12a454";
    if (S.tab === "versions") loadVersions();
    setTimeout(hideSaveVersionModal, 700);
  } catch (e) {
    statusEl.textContent = "❌ " + e.message; statusEl.style.color = "#e5484d";
  }
}
