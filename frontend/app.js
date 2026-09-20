/* ═══════════════════════════════════════════════════════════
   NukeNER Review — app.js
   Talks to the FastAPI backend for accounts, reviews, locks,
   metrics, and saved versions.
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
  pendingInvite: null, resetToken: null,
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
const escA = s => esc(s).replace(/"/g,"&quot;");
const escJ = s => String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'")
  .replace(/\r/g, "\\r").replace(/\n/g, "\\n")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const pct  = v => (v != null ? (v*100).toFixed(1)+"%" : "—");
const wsUrl = () => API_BASE_URL.replace(/^http/, "ws");
function _qs(id) { return document.getElementById(id); }

function togglePasswordVisibility(inputId, button) {
  const input = _qs(inputId);
  const visible = input.type === "password";
  input.type = visible ? "text" : "password";
  button.setAttribute("aria-label", visible ? "Hide password" : "Show password");
  button.setAttribute("aria-pressed", String(visible));
  input.focus({preventScroll:true});
}

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
  updateThemeButtons();
  checkApiStatus();
  const params = new URLSearchParams(location.search);
  S.pendingInvite = params.get("invite");
  S.resetToken = params.get("reset");
  if (S.resetToken) {
    openAuth('reset');
  } else if (S.pendingInvite) {
    openAuth('signup');
  }
  const savedRefresh = localStorage.getItem("ner_refresh_token");
  if (savedRefresh && !S.resetToken) {
    S.refreshToken = savedRefresh;
    const ok = await tryRefreshToken();
    if (ok) { await afterLogin(); }
    else { localStorage.removeItem("ner_refresh_token"); }
  }

  document.getElementById("uploadFileInput").addEventListener("change", e => {
    if (e.target.files[0]) handleUpload(e.target.files[0]);
  });
  document.addEventListener("click", e => { if (!e.target.closest(".ent-wrap")) closeMenu(); });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      closeMenu();
      if (!_qs("saveVersionOverlay").classList.contains("hidden")) hideSaveVersionModal();
    }
  });
  setInterval(() => {
    if (S.pid && S.currentDoc && S.locks[S.currentDoc]?.locked_by === S.meId) lockCurrentDoc();
  }, 60000);
});

async function checkApiStatus() {
  const el = _qs("apiStatus");
  el.textContent = ""; el.style.color = "";
  try {
    const res = await fetch(`${API_BASE_URL}/health`);
    if (!res.ok) throw new Error("unreachable");
    el.textContent = "";
  } catch (e) {
    el.textContent = "The service is unavailable right now. Please try again shortly.";
    el.style.color = "var(--bad)";
  }
}

/* ══════════════════════════════════════════════════════════
   Home, theme, and account access
   ══════════════════════════════════════════════════════════ */
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("nukener-theme", next);
  updateThemeButtons();
}

function updateThemeButtons() {
  const dark = document.documentElement.dataset.theme === "dark";
  document.querySelectorAll(".theme-toggle").forEach(button => {
    button.textContent = dark ? "☀ Light mode" : "☾ Dark mode";
    button.setAttribute("aria-pressed", String(dark));
  });
}

function openAuth(tab) {
  switchAuthTab(tab);
  _qs("authPanel").scrollIntoView({ behavior: "smooth", block: "center" });
  const firstField = {login:"loginEmail", signup:"signupName", forgot:"forgotEmail", reset:"resetPassword"}[tab];
  if (firstField) _qs(firstField).focus({preventScroll:true});
}

function switchAuthTab(tab) {
  document.querySelectorAll("[data-authtab]").forEach(b => b.classList.toggle("active", b.dataset.authtab === tab));
  _qs("authTabs").style.display = tab === "login" || tab === "signup" ? "flex" : "none";
  const copy = {
    login: ["Welcome back", "Sign in to continue your review."],
    signup: ["Create your account", "Start a workspace or join a project invitation."],
    forgot: ["Reset your password", "We’ll email you a link to choose a new one."],
    reset: ["Choose a new password", "Enter a new password for your account."],
  }[tab];
  _qs("authTitle").textContent = copy[0];
  _qs("authDescription").textContent = copy[1];
  _qs("authFormLogin").style.display  = tab === "login"  ? "" : "none";
  _qs("authFormSignup").style.display = tab === "signup" ? "" : "none";
  _qs("authFormForgot").style.display = tab === "forgot" ? "" : "none";
  _qs("authFormReset").style.display = tab === "reset" ? "" : "none";
}

async function requestPasswordReset() {
  const el = _qs("forgotStatus");
  const button = _qs("forgotSubmit");
  const email = _qs("forgotEmail");
  button.disabled = true;
  email.disabled = true;
  button.textContent = "Sending...";
  el.textContent = "";
  try {
    const result = await api("/auth/forgot-password", {method:"POST", body:{email:email.value.trim()}, auth:false});
    el.textContent = result.message;
    button.textContent = "Password Reset Link Sent";
  } catch (e) {
    el.textContent = e.message;
    button.textContent = "Send reset link";
    button.disabled = false;
  } finally { email.disabled = false; }
}

function resetPasswordRequestState() {
  const button = _qs("forgotSubmit");
  button.textContent = "Send reset link";
  button.disabled = false;
  _qs("forgotStatus").textContent = "";
}

async function submitPasswordReset() {
  const el = _qs("resetStatus");
  try {
    const result = await api("/auth/reset-password", {method:"POST", body:{token:S.resetToken, password:_qs("resetPassword").value}, auth:false});
    el.textContent = result.message;
    S.resetToken = null;
    history.replaceState({}, "", location.pathname);
    setTimeout(() => switchAuthTab("login"), 1600);
  } catch (e) { el.textContent = e.message; }
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
  _qs("landingPage").classList.add("hidden");
  _qs("userPill").style.display = "flex";
  _qs("userPillName").textContent = S.meName;
  _qs("userAvatar").textContent = (S.meName[0] || "?").toUpperCase();
  showDashboard();
  if (S.pendingInvite) {
    try {
      await api("/projects/invitations/accept", {method:"POST", body:{token:S.pendingInvite}});
      S.pendingInvite = null;
      history.replaceState({}, "", location.pathname);
      await loadProjectList();
      alert("Invitation accepted. The project is now in your dashboard.");
    } catch (e) { alert("Could not accept invitation: " + e.message); }
  }
}

function showDashboard() {
  if (!S.meId) return;
  if (S.currentDoc) unlockDoc(S.currentDoc);
  if (S.ws) { S.ws.close(); S.ws = null; }
  resetUI();
  _qs("appShell").classList.add("hidden");
  _qs("landingPage").classList.add("hidden");
  _qs("dashboardPage").classList.remove("hidden");
  _qs("dashboardGreeting").textContent = `Welcome, ${S.meName.split(" ")[0]}`;
  loadProjectList();
  loadTrash();
}

function logout() {
  if (S.currentDoc) unlockDoc(S.currentDoc);
  if (S.ws) { S.ws.close(); S.ws = null; }
  localStorage.removeItem("ner_refresh_token");
  S = Object.assign(S, {
    accessToken: null, refreshToken: null, meId: null, meName: "", meEmail: "",
    pid: null, pname: "", myRole: null, ownerId: null,
  });
  resetUI();
  _qs("userPill").style.display = "none";
  hideModal();
  _qs("dashboardPage").classList.add("hidden");
  _qs("appShell").classList.add("hidden");
  _qs("landingPage").classList.remove("hidden");
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
    renderDashboardProjects(list);
    if (!list.length) { el.innerHTML = `<div class="empty-state">No projects yet — create one above.</div>`; return; }
    el.innerHTML = list.map(p => {
      const isOwner = p.role === "owner";
      return `
      <div class="project-row" onclick="openProject('${p.id}','${escJ(p.name)}')">
        <div>
          <div class="project-name">${esc(p.name)}</div>
          <div class="project-date">${(p.created_at||"").slice(0,10)}</div>
        </div>
        <div class="project-actions">
          <span class="project-role">${esc(p.role || (isOwner ? "owner" : "member"))}</span>
          ${isOwner ? `<button class="btn btn-danger btn-tiny" onclick="event.stopPropagation(); deleteProject('${p.id}','${escJ(p.name)}')">Delete</button>` : ""}
        </div>
      </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
  }
}

function renderDashboardProjects(projects) {
  const el = _qs("dashboardProjects");
  if (!projects.length) {
    el.innerHTML = `<div class="dashboard-card"><h3>Your first project starts here</h3><p>Create a project, then upload your model predictions.</p><button class="btn btn-primary" style="margin-top:18px" onclick="showModal()">Create project</button></div>`;
    return;
  }
  el.innerHTML = projects.map(p => `<button class="dashboard-card" onclick="openProject('${p.id}')">
    <span class="project-role">${esc(p.role || "member")}</span><h3>${esc(p.name)}</h3>
    <p>Created ${(p.created_at || "").slice(0,10)} · Open project →</p>
  </button>`).join("");
}

async function loadTrash() {
  const el = _qs("dashboardTrash");
  try {
    const items = await api("/projects/deleted/mine");
    el.innerHTML = items.length ? items.map(p => `<div class="trash-row"><span>${esc(p.name)} <small>· recoverable for 30 days</small></span><button class="btn btn-ghost" onclick="restoreProject('${p.id}')">Restore</button></div>`).join("") : `<div class="empty-state">No deleted projects.</div>`;
  } catch (e) { el.textContent = e.message; }
}

async function restoreProject(projectId) {
  try { await api(`/projects/${projectId}/restore`, {method:"POST"}); await loadProjectList(); await loadTrash(); }
  catch (e) { alert("Could not restore project: " + e.message); }
}

async function deleteProject(projectId, name) {
  if (prompt(`Type the project name to move it to Recently deleted for 30 days:\n${name}`) !== name) return;
  try {
    await api(`/projects/${projectId}`, { method: "DELETE" });
    if (S.pid === projectId) { resetUI(); }
    loadProjectList(); loadTrash();
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
  if (S.currentDoc) await unlockDoc(S.currentDoc);
  if (S.ws) { S.ws.close(); S.ws = null; }
  clearTimeout(S.wsRetry);
  S.pid = pid; S.pname = name || S.pname || "Project";
  Object.assign(S, {
    documents: [], currentDoc: null, docDetail: null, sentenceIndex: new Map(),
    entityIndex: new Map(), entityCounts: {}, labels: new Set(), activeLabels: new Set(),
    lStyle: {}, openEntityId: null, locks: {}, versionPreview: null,
  });

  hideModal();
  _qs("dashboardPage").classList.add("hidden");
  _qs("appShell").classList.remove("hidden");
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
    alert("Failed to open project: " + e.message); showDashboard(); return;
  }

  updateStats();
  buildDocList();
  connectLockSocket();
  _qs("topSaveBtn").style.display = canEdit() ? "inline-flex" : "none";

  if (S.documents.length) selectDoc(S.documents[0].doc_id_external);
  else {
    _qs("mainHeader").style.display = "none";
    _qs("sentencePane").innerHTML = `<div class="empty-state">This project has no documents yet — upload a dataset from the ⊕ Projects menu.</div>`;
    _qs("welcomeState").style.display = "none";
  }
}

function resetUI() {
  if (S.ws) { S.ws.close(); S.ws = null; }
  S.pid = null; S.pname = ""; S.currentDoc = null; S.tab = "annotate"; S.ownerId = null; S.myRole = null;
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
function canEditCurrentDoc() { return canEdit() && S.locks[S.currentDoc]?.locked_by === S.meId; }

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
window.addEventListener("pagehide", () => {
  if (S.pid && S.currentDoc && S.locks[S.currentDoc]?.locked_by === S.meId) {
    fetch(`${API_BASE_URL}/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}/unlock`, {
      method:"POST", headers:{Authorization:`Bearer ${S.accessToken}`}, keepalive:true,
    }).catch(() => {});
  }
});

function updateDocHeader() {
  const d = S.docDetail;
  if (!d) return;
  const entCount = d.sentences.reduce((n,s)=>n+s.entities.filter(e=>e.source==="model").length, 0);
  const reviewedCount = d.sentences.reduce((n,s)=>n+s.entities.filter(e=>e.source==="model" && e.current_review).length, 0);
  _qs("mainDocTitle").textContent = d.doc_id_external;
  if (d.source) _qs("mainDocTitle").innerHTML = `${esc(d.doc_id_external)} <span class="source-note">· ${esc(d.source)}</span>`;
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
    for (const ent of sent.entities.filter(e => e.source === "model")) {
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
  ws.onclose = async event => {
    _qs("wsDot").className = "conn-dot off";
    if (S.ws !== ws || !S.pid || event.code === 4403) return;
    if (event.code === 4401 && !(await tryRefreshToken())) return;
    S.wsRetry = setTimeout(connectLockSocket, 3000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (evt) => {
    let msg; try { msg = JSON.parse(evt.data); } catch (_) { return; }
    if (msg.type === "review") {
      if (S.docDetail && msg.document_id === S.docDetail.id) {
        api(`/projects/${S.pid}/documents/${encodeURIComponent(S.currentDoc)}`).then(detail => {
          if (S.currentDoc !== detail.doc_id_external) return;
          S.docDetail = detail; indexDocDetail(); renderSentences(); updateDocHeader();
          if (S.tab === "metrics") loadMetrics();
        }).catch(() => {});
      }
      return;
    }
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
  const ents = sent.entities.filter(e => e.source === "model").sort((a,b)=>a.start_char-b.start_char);
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
    el.textContent = `.entity[data-type="${CSS.escape(label)}"]{ background:${s.bg}; border-bottom-color:${s.bd}; }`;
    document.head.appendChild(el);
  }
  return S.lStyle[label];
}

function buildEntitySpan(ent) {
  const s = getStyle(ent.label);
  const verdict = ent.current_review ? ent.current_review.verdict.toLowerCase() : "none";
  const saving = S.saving.has(ent.id);
  const dis = saving ? "disabled" : "";
  const editable = canEditCurrentDoc();
  const isOpen = S.openEntityId === ent.id;
  const eidJs = escJ(ent.id);

  const reviewer = ent.current_review ? ` · last: ${esc(ent.current_review.reviewer_name)}` : "";
  const bar = `<span class="ent-bar" onclick="event.stopPropagation()">
      <span class="ent-label-tip">${esc(ent.label)}${reviewer}</span>
      <button class="ent-btn ent-btn-tp ${verdict==='tp'?'ent-active-tp':''}" ${dis||!editable?"disabled":""} onclick="setVerdict('${eidJs}','TP',event)">TP</button>
      <button class="ent-btn ent-btn-fp ${verdict==='fp'?'ent-active-fp':''}" ${dis||!editable?"disabled":""} onclick="setVerdict('${eidJs}','FP',event)">FP</button>
    </span>`;

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
      <div class="metric-card"><div class="metric-val" style="color:#0a84ff">${m.percent_reviewed}%</div><div class="metric-lbl">Review coverage</div><div class="metric-sub">${m.reviewed_model_entities} of ${m.total_model_entities} predictions</div></div>
      <div class="metric-card"><div class="metric-val">${m.tp + m.fp}</div><div class="metric-lbl">Reviewed predictions</div><div class="metric-sub">${m.tp} TP · ${m.fp} FP</div></div>
    </div>`;
}
function renderMetrics(project, doc) {
  let html = `<div class="btn-group" style="margin-bottom:16px"><button class="btn btn-ghost" onclick="downloadExport('csv')">Export CSV</button><button class="btn btn-ghost" onclick="downloadExport('json')">Export JSON</button></div>` + metricCards(project, "Project-wide");
  if (doc) html += `<div style="margin-top:20px">${metricCards(doc, `Current document — ${esc(S.currentDoc)}`)}</div>`;
  _qs("metricsPanel").innerHTML = html;
}

async function downloadExport(format) {
  const url = `${API_BASE_URL}/projects/${S.pid}/export?format=${format}`;
  let response = await fetch(url, {headers:{Authorization:`Bearer ${S.accessToken}`}});
  if (response.status === 401 && await tryRefreshToken()) response = await fetch(url, {headers:{Authorization:`Bearer ${S.accessToken}`}});
  if (!response.ok) { alert("Export failed. Please try again."); return; }
  const blobUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = blobUrl; link.download = `ner-reviews-${S.pid}.${format}`; link.click();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}

/* ── Team ── */
async function loadTeam() {
  if (!S.pid) return;
  const panel = _qs("teamPanel");
  panel.innerHTML = `<div class="empty-state">Loading…</div>`;
  try {
    const [members, invitations] = await Promise.all([
      api(`/projects/${S.pid}/members`),
      S.myRole === "owner" ? api(`/projects/${S.pid}/invitations`) : Promise.resolve([]),
    ]);
    renderTeam(members, invitations);
  } catch (e) {
    panel.innerHTML = `<div class="empty-state" style="color:#e5484d">⚠️ ${esc(e.message)}</div>`;
  }
}
function renderTeam(members, invitations = []) {
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
      <p style="font-size:12.5px;color:var(--t2);margin:2px 0 0">They can sign up after receiving the invitation email.</p>
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
  const invites = S.myRole === "owner" ? `<h4>Invitations</h4>${invitations.length ? invitations.map(i => `
    <div class="invitation-row"><div><strong>${esc(i.email)}</strong> · ${esc(i.role)}<br><small>${esc(i.status)} · expires ${new Date(i.expires_at).toLocaleDateString()}</small></div>
    ${i.status === "pending" || i.status === "expired" ? `<div class="btn-group"><button class="btn-tiny" onclick="resendInvitation('${i.id}')">Resend</button><button class="btn-tiny danger" onclick="revokeInvitation('${i.id}')">Revoke</button></div>` : ""}</div>`).join("") : `<div class="empty-state">No invitations yet.</div>`}` : "";
  _qs("teamPanel").innerHTML = `<div class="team-grid">${rows}</div>${inviteForm}${invites}`;
}
async function inviteMember() {
  const email = _qs("inviteEmail").value.trim();
  const role = _qs("inviteRole").value;
  const resEl = _qs("inviteResult");
  if (!email) return;
  try {
    await api(`/projects/${S.pid}/invite`, { method: "POST", body: { email, role } });
    resEl.innerHTML = `<div class="invite-box invite-ok">Invitation email sent to ${esc(email)}.</div>`;
    loadTeam();
  } catch (e) {
    resEl.innerHTML = `<div class="invite-box invite-warn">⚠️ ${esc(e.message)}</div>`;
  }
}

async function resendInvitation(id) {
  try { await api(`/projects/${S.pid}/invitations/${id}/resend`, {method:"POST"}); loadTeam(); }
  catch (e) { alert("Could not resend invitation: " + e.message); }
}
async function revokeInvitation(id) {
  if (!confirm("Revoke this invitation?")) return;
  try { await api(`/projects/${S.pid}/invitations/${id}`, {method:"DELETE"}); loadTeam(); }
  catch (e) { alert("Could not revoke invitation: " + e.message); }
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
  const toolbar = canEdit() ? `<div class="version-toolbar">
    <input class="field" id="versionLabel" placeholder="Version label (optional)" style="flex:1">
    <button class="btn btn-primary" onclick="saveVersion()">💾 Save version</button>
  </div>` : "";
  const choices = `<label class="field-label">Compare against</label><select class="field" id="compareAgainst"><option value="current">Current project</option>${versions.map(v => `<option value="${v.id}">${esc(v.label)}</option>`).join("")}</select>`;
  const rows = versions.length ? versions.map(v => `
    <div class="version-row">
      <div class="version-dot"></div>
      <div>
        <div class="version-label">${esc(v.label)}</div>
        <div class="version-meta">by ${esc(v.created_by_name)} · ${new Date(v.created_at).toLocaleString()}</div>
      </div>
      <div class="version-actions"><button class="btn btn-ghost" onclick="inspectVersion('${v.id}')">Inspect snapshot</button><button class="btn btn-ghost" onclick="compareVersion('${v.id}','${escJ(v.label)}')">Compare</button></div>
    </div>`).join("") : `<div class="empty-state">No saved versions yet.</div>`;
  _qs("versionsPanel").innerHTML = `${toolbar}${choices}<div class="version-list">${rows}</div><div id="versionInspection"></div><div id="versionPreview"></div>`;
}

async function inspectVersion(versionId) {
  const panel = _qs("versionInspection");
  panel.innerHTML = `<div class="empty-state">Loading saved snapshot…</div>`;
  try {
    const data = await api(`/projects/${S.pid}/versions/${versionId}`);
    S.inspectedSnapshot = data.snapshot;
    const docs = data.snapshot.documents || [];
    const sentenceCount = docs.reduce((total, doc) => total + doc.sentences.length, 0);
    const entityCount = docs.reduce((total, doc) => total + doc.sentences.reduce((n, sentence) => n + sentence.entities.length, 0), 0);
    panel.innerHTML = `<div class="version-preview"><h4>${esc(data.version.label)}</h4>
      <p>Saved by ${esc(data.version.created_by_name)} on ${new Date(data.version.created_at).toLocaleString()} · ${docs.length} documents · ${sentenceCount} sentences · ${entityCount} entities</p>
      ${docs.length ? `<label class="field-label" for="snapshotDocument">Document</label><select class="field" id="snapshotDocument" onchange="showVersionDocument(Number(this.value))">${docs.map((doc, index) => `<option value="${index}">${esc(doc.doc_id_external)}</option>`).join("")}</select><div id="snapshotDocumentDetail"></div>` : `<p>This snapshot has no documents.</p>`}
    </div>`;
    if (docs.length) showVersionDocument(0);
  } catch (e) { panel.textContent = "Could not inspect version: " + e.message; }
}

function showVersionDocument(index) {
  const doc = S.inspectedSnapshot?.documents?.[index];
  const panel = _qs("snapshotDocumentDetail");
  if (!doc || !panel) return;
  panel.innerHTML = `${doc.source ? `<p class="source-note">Source: ${esc(doc.source)}</p>` : ""}` +
    doc.sentences.map(sentence => `<div class="sent-card"><div class="sent-meta">${esc(sentence.sentence_id_external)}</div><div class="sent-text">${esc(sentence.text)}</div>
      <div class="snapshot-entities">${sentence.entities.map(entity => {
        const reviews = entity.reviews || [];
        const latest = reviews.length ? [...reviews].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).at(-1) : null;
        return `<span class="snapshot-entity">${esc(entity.text)} · ${esc(entity.label)} · ${esc(latest?.verdict || "unreviewed")}</span>`;
      }).join("") || `<span class="metric-sub">No predictions</span>`}</div></div>`).join("");
}

async function compareVersion(versionId, label) {
  const against = _qs("compareAgainst").value;
  const panel = _qs("versionPreview");
  panel.innerHTML = `<div class="empty-state">Comparing versions…</div>`;
  try {
    const diff = await api(`/projects/${S.pid}/versions/${versionId}/compare?against=${encodeURIComponent(against)}`);
    S.versionPreview = {versionId, label, hash:diff.current_hash, against};
    const details = diff.changes.slice(0, 150).map(c => `<div class="version-change"><span>${esc(c.document_id)} · ${esc(c.text)} <small>(${esc(c.label)})</small></span><strong>${esc(c.before || "unreviewed")} → ${esc(c.after || "unreviewed")}</strong></div>`).join("");
    const revert = S.myRole === "owner" && against === "current" ? `<button class="btn btn-danger" onclick="revertVersion('${versionId}','${escJ(label)}')">Revert to this version</button>` : "";
    panel.innerHTML = `<div class="version-preview"><h4>${esc(label)} compared with ${against === "current" ? "current project" : "another version"}</h4><p>${diff.changed} changed · ${diff.added} added · ${diff.removed} removed predictions</p>${details || "No entity changes."}${diff.changes.length > 150 ? `<p>Showing first 150 changes.</p>` : ""}<div style="margin-top:16px">${revert}</div></div>`;
  } catch (e) { panel.textContent = "Could not compare versions: " + e.message; }
}
async function saveVersion() {
  const label = _qs("versionLabel").value.trim() || null;
  try {
    await api(`/projects/${S.pid}/versions`, { method: "POST", body: { label } });
    loadVersions();
  } catch (e) { alert("Could not save version: " + e.message); }
}
async function revertVersion(versionId, label) {
  if (!S.versionPreview || S.versionPreview.versionId !== versionId || S.versionPreview.against !== "current") return;
  if (!confirm(`Revert the project to "${label}"? This replaces all current documents, entities, and review verdicts.`)) return;
  try {
    const result = await api(`/projects/${S.pid}/versions/${versionId}/revert`, { method: "POST", body:{expected_current_hash:S.versionPreview.hash} });
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
