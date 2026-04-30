"use strict";

// ── Built-in categories ─────────────────────────────────────────────────────
const BUILTIN = [
  { name: "Comida",           icon: "🍔", bg: "#FEE2E2", text: "#DC2626" },
  { name: "Mercado",          icon: "🛒", bg: "#D1FAE5", text: "#059669" },
  { name: "Salidas",          icon: "🎬", bg: "#DBEAFE", text: "#2563EB" },
  { name: "Fitness",          icon: "💪", bg: "#FEF3C7", text: "#D97706" },
  { name: "Compras",          icon: "🛍️", bg: "#EDE9FE", text: "#7C3AED" },
  { name: "Servicios",        icon: "📱", bg: "#FEF9C3", text: "#A16207" },
  { name: "Gasolina",         icon: "⛽", bg: "#E0E7FF", text: "#4338CA" },
  { name: "Digital",          icon: "💻", bg: "#DCFCE7", text: "#15803D" },
  { name: "Pagos",            icon: "💳", bg: "#F1F5F9", text: "#475569" },
  { name: "Centro Comercial", icon: "🏬", bg: "#FFE4E6", text: "#E11D48" },
  { name: "Ingresos",         icon: "💰", bg: "#D1FAE5", text: "#059669" },
  { name: "Efectivo",         icon: "🏧", bg: "#FEF3C7", text: "#D97706" },
  { name: "Ahorro",           icon: "🏦", bg: "#DBEAFE", text: "#2563EB" },
  { name: "Transferencias",   icon: "↔️",  bg: "#EDE9FE", text: "#7C3AED" },
  { name: "Otros",            icon: "📦", bg: "#F3F4F6", text: "#374151" },
];

const COLOR_PALETTE = [
  { bg: "#FEE2E2", text: "#DC2626" },
  { bg: "#FEF3C7", text: "#D97706" },
  { bg: "#D1FAE5", text: "#059669" },
  { bg: "#DBEAFE", text: "#2563EB" },
  { bg: "#EDE9FE", text: "#7C3AED" },
  { bg: "#FFE4E6", text: "#E11D48" },
  { bg: "#F1F5F9", text: "#475569" },
  { bg: "#FEF9C3", text: "#A16207" },
];

let selectedColorIdx = 0;
let selectedEmoji    = "🏷️";

const EMOJI_OPTIONS = [
  "🏷️","🍔","🍕","🍜","🍣","🥗","☕","🍺","🥤","🍦",
  "🛒","🧺","🏠","🚗","⛽","✈️","🏨","🎬","🎮","🎵",
  "💪","🏋️","💊","🐕","💼","📱","💻","🎁","🛍️","👟",
  "💍","🔧","⚡","🌐","📚","🎓","💰","💳","🎯","🚀",
  "🌮","🧴","🧹","🌿","🏥","🎲","🏊","🎰","👗","🛠️",
];

// ── App-level caches (loaded from API on init) ───────────────────────────────
let currentUser      = null;
let customCats       = [];   // [{name,icon,bg,text,custom:true}]
let learnedRulesCache = {};  // {DESCRIPTION_KEY: [cats]}
let historyCache     = [];   // [{id,filename,fecha_corte,uploadedAt,total,summary,transactions}]

// ── Categories ────────────────────────────────────────────────────────────────
function loadCategories() {
  return [...BUILTIN, ...customCats];
}

function saveCustomCategory(name, icon) {
  if (customCats.find(c => c.name === name) || BUILTIN.find(c => c.name === name)) return;
  const color = COLOR_PALETTE[selectedColorIdx];
  const cat   = { name, icon, bg: color.bg, text: color.text, custom: true };
  customCats.push(cat);
  apiFetch("/api/categories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, icon, bg: color.bg, text: color.text }),
  });
}

function deleteCustomCategory(name) {
  customCats = customCats.filter(c => c.name !== name);
  apiFetch(`/api/categories/${encodeURIComponent(name)}`, { method: "DELETE" });
  allTxns.forEach(t => {
    t.categories = t.categories.filter(c => c !== name);
    if (t.categories.length === 0) t.categories = ["Otros"];
  });
}

function getCat(name) {
  return loadCategories().find(c => c.name === name)
    || { name, icon: "📦", bg: "#F3F4F6", text: "#374151" };
}

// ── History (backed by API) ───────────────────────────────────────────────────
function loadHistory() {
  return historyCache;
}

async function saveToHistory(filename, data) {
  const type  = data.statement_type || "credito";
  const total = type === "debito"
    ? data.transactions.filter(t => t.amount < 0).reduce((s, t) => s + Math.abs(t.amount), 0)
    : data.transactions.filter(t => t.amount > 0).reduce((s, t) => s + ((t.cargos_mes != null) ? t.cargos_mes : t.amount), 0);

  const payload = {
    filename,
    fecha_corte:  data.summary?.fecha_corte || data.summary?.periodo || "",
    total,
    transactions: data.transactions.map(t => ({ ...t, categories: [t.category] })),
    summary:      data.summary || {},
    file_hash:    data.file_hash || null,
  };

  const res = await apiFetch("/api/history", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(payload),
  });
  if (!res || !res.ok) return null;

  const result = await res.json();
  const id     = result.id;

  // If server returned an existing entry (re-upload), reload transactions from server
  // so local cache reflects the merged categories
  const existingIdx = historyCache.findIndex(h => h.id === id);
  if (existingIdx !== -1) {
    const existing = historyCache[existingIdx];
    existing.filename    = filename;
    existing.fecha_corte = payload.fecha_corte;
    existing.total       = total;
    existing.summary     = payload.summary;
    existing.file_hash   = payload.file_hash;
    // Merge categories from server: re-fetch this entry's transactions
    const refreshed = await fetch(`/api/history`).then(r => r.json()).catch(() => null);
    if (refreshed) {
      const srv = refreshed.find(h => h.id === id);
      if (srv) existing.transactions = srv.transactions;
    }
    // Bubble to top
    historyCache.splice(existingIdx, 1);
    historyCache.unshift(existing);
  } else {
    const entry = {
      id,
      filename,
      uploadedAt:  new Date().toLocaleDateString("es-CO"),
      fecha_corte: payload.fecha_corte,
      total,
      transactions: payload.transactions,
      summary:      payload.summary,
      file_hash:    payload.file_hash,
    };
    historyCache.unshift(entry);
    if (historyCache.length > 20) historyCache.length = 20;
  }
  return id;
}

function persistCurrentState() {
  if (!currentHistoryId) return;
  const entry = historyCache.find(h => h.id === currentHistoryId);
  if (!entry) return;
  entry.transactions = allTxns;
  entry.total = statementType === "debito"
    ? allTxns.filter(t => t.amount < 0).reduce((s, t) => s + Math.abs(t.amount), 0)
    : allTxns.filter(t => t.amount > 0).reduce((s, t) => s + chargedAmount(t), 0);

  apiFetch(`/api/history/${currentHistoryId}`, {
    method:  "PUT",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ transactions: entry.transactions, total: entry.total }),
  });
}

function deleteFromHistory(id) {
  historyCache = historyCache.filter(h => h.id !== id);
  apiFetch(`/api/history/${id}`, { method: "DELETE" });
  renderDocuments();
}

function loadFromHistory(id) {
  const entry = historyCache.find(h => h.id === id);
  if (!entry) return;
  const seen = new Set();
  allTxns = entry.transactions.filter(t => {
    const key = `${t.date}|${t.description}|${t.amount}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
  summary       = entry.summary;
  statementType = entry.summary?.statement_type || "credito";
  currentHistoryId = id;
  activeFilter = "Todos"; activeTab = "gastos"; cuotasOpen = false;
  renderResults();
}

function renderDocuments() {
  const section = document.getElementById("documents-section");
  const history = loadHistory();

  if (history.length === 0) {
    section.innerHTML = `
      <div class="docs-page">
        <div class="docs-empty">
          <div class="docs-empty-icon">📄</div>
          <h3 class="docs-empty-title">Sin extractos aún</h3>
          <p class="docs-empty-sub">Sube tu primer extracto bancario para comenzar a ver tus gastos</p>
          <button class="btn-primary" onclick="openUploadModal()">Subir primer extracto</button>
        </div>
      </div>`;
    return;
  }

  section.innerHTML = `
    <div class="docs-page">
      <div class="docs-page-header">
        <div>
          <h2 class="docs-title">Mis extractos</h2>
          <p class="docs-sub">${history.length} guardado${history.length !== 1 ? "s" : ""}</p>
        </div>
        <button class="btn-primary docs-upload-btn" onclick="openUploadModal()">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 10V3M4 6l3-3 3 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M2 12h10" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
          </svg>
          Subir extracto
        </button>
      </div>
      <div class="history-grid">
        ${history.map(h => {
          const hType = h.summary?.statement_type || "credito";
          const hMeta = ACCOUNT_TYPE_META[hType] || ACCOUNT_TYPE_META.credito;
          return `
          <div class="history-card" onclick="loadFromHistory('${escapeAttr(h.id)}')">
            <div class="history-icon">${hMeta.icon}</div>
            <div class="history-info">
              <div class="history-filename">${escapeHTML(h.filename)}</div>
              <div class="history-meta">${h.fecha_corte ? `${escapeHTML(h.fecha_corte)} · ` : ""}${hMeta.label} · ${h.uploadedAt}</div>
            </div>
            <div class="history-amount">${formatCOP(h.total)}</div>
            <button class="history-del" onclick="event.stopPropagation(); deleteFromHistory('${escapeAttr(h.id)}')" title="Eliminar">✕</button>
          </div>`;
        }).join("")}
      </div>
    </div>`;
}

// ── State ────────────────────────────────────────────────────────────────────
let allTxns          = [];
let summary          = {};
let statementType    = "credito";
let activeFilter     = "Todos";
let editingIdx       = null;
let pendingCats      = [];
let currentHistoryId = null;
let selectedTxns     = new Set();
let isBulkMode       = false;

// ── Format ───────────────────────────────────────────────────────────────────
function formatCOP(n) {
  return new Intl.NumberFormat("es-CO", {
    style: "currency", currency: "COP",
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(Math.abs(n));
}

// ── Merchant key normalization ────────────────────────────────────────────────
const _MK_NOISE = new Set([
  "CO","COL","BOGOTA","BOGOTÁ","MEDELLIN","MEDELLÍN",
  "CALI","BARRANQUILLA","CARTAGENA","SAS","LTDA","SA",
  "DE","EL","LA","LOS","LAS",
]);

function merchantKey(desc) {
  return desc.toUpperCase().trim()
    .replace(/\.(COM|NET|ORG|CO|IO|APP)\b/g, "")
    .replace(/[*#@.]/g, " ")
    .replace(/\b\d{3,}\b/g, "")
    .split(/\s+/)
    .filter(t => t.length > 1 && !_MK_NOISE.has(t))
    .join(" ");
}

// ── Seguridad: escape HTML y helpers ──────────────────────────────────────────
function escapeHTML(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function escapeAttr(s) { return escapeHTML(s); }
function safeColor(s) { return /^#[0-9a-fA-F]{6}$/.test(s) ? s : "#6B7280"; }

// ── Auth helpers ──────────────────────────────────────────────────────────────
async function apiFetch(url, options = {}) {
  options.headers = { "X-Drafiti-CSRF": "1", ...(options.headers || {}) };
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    return null;
  }
  return res;
}

async function logout() {
  await apiFetch("/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

// ── Upload ───────────────────────────────────────────────────────────────────
const dropZone  = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");

dropZone.addEventListener("dragover",  e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => {
  e.preventDefault(); dropZone.classList.remove("drag-over");
  if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
});
document.getElementById("select-file-btn").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", e => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

let pendingFile = null;

async function uploadFile(file, password = null) {
  closeUploadModal();
  document.getElementById("upload-error").classList.add("hidden");
  show("loading");
  const form = new FormData();
  form.append("file", file);
  if (password) form.append("password", password);
  try {
    const res = await apiFetch("/api/extract", { method: "POST", body: form });
    if (!res) return;
    if (!res.ok) {
      const err = await res.json();
      if (err.detail === "password_required") {
        pendingFile = file;
        show(uploadOrigin);
        if (password) {
          openPwdModal("Contraseña incorrecta, intenta de nuevo");
        } else {
          openPwdModal();
        }
        return;
      }
      const msg = typeof err.detail === "string"
        ? err.detail
        : "No se pudo procesar el PDF. Verifica que el archivo sea válido.";
      throw new Error(msg);
    }
    const data = await res.json();
    summary       = data.summary || {};
    statementType = data.statement_type || "credito";

    const seen = new Set();
    allTxns = data.transactions
      .map(t => ({ ...t, categories: [t.category] }))
      .filter(t => {
        const key = `${t.date}|${t.description}|${t.amount}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });

    allTxns = applyLearnedRules(allTxns);
    currentHistoryId = await saveToHistory(file.name, data);
    activeFilter = "Todos";
    renderResults();
  } catch (e) {
    show(uploadOrigin);
    openUploadModal();
    const errEl = document.getElementById("upload-error");
    errEl.textContent = e.message || "No se pudo procesar el PDF. Intenta de nuevo.";
    errEl.classList.remove("hidden");
    setTimeout(() => errEl.classList.add("hidden"), 6000);
  }
}

// ── Visibility ───────────────────────────────────────────────────────────────
function show(id) {
  ["documents-section", "loading", "results"].forEach(s =>
    document.getElementById(s).classList.toggle("hidden", s !== id));
  const isResults = id === "results";
  document.getElementById("header-info").classList.toggle("hidden", !isResults);
  document.getElementById("main-nav").classList.toggle("hidden", !isResults);
  document.getElementById("fab-nuevo-pdf").classList.toggle("hidden", !isResults);
  if (id === "documents-section") renderDocuments();
}

// ── Upload modal ──────────────────────────────────────────────────────────────
let uploadOrigin = "documents-section";

function openUploadModal() {
  uploadOrigin = document.getElementById("results").classList.contains("hidden")
    ? "documents-section" : "results";
  document.getElementById("upload-error").classList.add("hidden");
  document.getElementById("file-input").value = "";
  document.getElementById("upload-modal").classList.remove("hidden");
}

function closeUploadModal() {
  document.getElementById("upload-modal").classList.add("hidden");
}

function handleUploadOverlayClick(e) {
  if (e.target.id === "upload-modal") closeUploadModal();
}

// ── Tab navigation ───────────────────────────────────────────────────────────
let activeTab = "gastos";

function setTab(tab) {
  activeTab = tab;
  document.querySelectorAll(".nav-tab").forEach(el =>
    el.classList.toggle("active", el.dataset.tab === tab));
  document.getElementById("tab-gastos").classList.toggle("hidden", tab !== "gastos");
  document.getElementById("tab-deuda").classList.toggle("hidden",  tab !== "deuda");
}

// ── Render all ───────────────────────────────────────────────────────────────
function renderResults() {
  show("results");
  // Update "Deuda" tab label depending on statement type
  const deudaTab = document.querySelector(".nav-tab[data-tab='deuda']");
  if (deudaTab) deudaTab.childNodes[deudaTab.childNodes.length - 1].textContent =
    statementType === "debito" ? " Cuenta" : " Deuda";
  setTab(activeTab);
  renderHero();
  renderDebt();
  renderCuotas();
  renderCatGrid();
  renderFilters();
  renderTable();
}

function chargedAmount(t) {
  return (t.cargos_mes != null) ? t.cargos_mes : t.amount;
}

const ACCOUNT_TYPE_META = {
  credito: { label: "Tarjeta de Crédito", icon: "💳", color: "#5C6BC0" },
  debito:  { label: "Cuenta de Ahorros / Débito", icon: "🏦", color: "#059669" },
};

// ── Hero (gasto) ──────────────────────────────────────────────────────────────
function renderHero() {
  document.getElementById("txn-count").textContent = `${allTxns.length} movimientos`;

  const meta = ACCOUNT_TYPE_META[statementType] || ACCOUNT_TYPE_META.credito;
  const badge = document.getElementById("account-type-badge");
  if (badge) {
    badge.textContent = `${meta.icon} ${meta.label}`;
    badge.style.background = "rgba(255,255,255,0.2)";
    badge.style.color       = "#fff";
  }

  if (statementType === "debito") {
    const expenses = allTxns.filter(t => t.amount < 0);
    const income   = allTxns.filter(t => t.amount > 0);
    const total    = expenses.reduce((s, t) => s + Math.abs(t.amount), 0);
    document.getElementById("hero-amount").textContent = formatCOP(total);
    document.getElementById("hero-label").textContent  = "Gastos este período";
    document.getElementById("hero-sub").textContent    =
      `${expenses.length} gastos · ${income.length} ingresos`;
  } else {
    const pos   = allTxns.filter(t => t.amount > 0);
    const total = pos.reduce((s, t) => s + chargedAmount(t), 0);
    document.getElementById("hero-amount").textContent = formatCOP(total);
    document.getElementById("hero-label").textContent  = "Cobrado este período";
    document.getElementById("hero-sub").textContent    =
      `${pos.length} transacciones · ${allTxns.filter(t => t.amount < 0).length} créditos`;
  }
}

// ── Debt card / Account card ──────────────────────────────────────────────────
function renderDebt() {
  const isDebito = statementType === "debito";
  document.getElementById("deuda-credito").classList.toggle("hidden",  isDebito);
  document.getElementById("deuda-debito").classList.toggle("hidden",  !isDebito);
  if (isDebito) { renderDebitAccount(); return; }
  // ── credit card logic below ────────────────────────────────────────────────
  const pos         = allTxns.filter(t => t.amount > 0);
  const cargosTotal = pos.reduce((s, t) => s + (t.cargos_mes || 0), 0);
  const difTotal    = pos.reduce((s, t) => s + (t.saldo_dif  || 0), 0);
  const saldoTotal  = summary.saldo_total || (cargosTotal + difTotal);
  const minimo      = summary.pago_minimo  || 0;
  const pagado      = summary.pagos_abonos || 0;
  const fechaLimite = summary.fecha_limite  || "";

  const activeInstallments = allTxns.filter(
    t => t.cuota_tot && t.cuota_tot > 1 && t.amount > 0 && (t.cuota_act || 0) > 0
  );
  const difActivo = activeInstallments.reduce((s, t) => s + (t.saldo_dif || 0), 0);

  document.getElementById("ds-saldo").textContent  = formatCOP(saldoTotal);
  document.getElementById("ds-vence").textContent  = fechaLimite ? `Vence ${fechaLimite}` : "";
  document.getElementById("ds-minimo").textContent = formatCOP(minimo);
  document.getElementById("ds-pagado").textContent = pagado > 0 ? formatCOP(pagado) : "—";
  document.getElementById("ds-cuotas").textContent = formatCOP(difActivo);
  document.getElementById("ds-cuotas-sub").textContent = activeInstallments.length > 0
    ? `${activeInstallments.length} plan${activeInstallments.length !== 1 ? "es" : ""} en curso`
    : "Sin cuotas activas";

  const owedTxns = allTxns
    .filter(t => t.amount > 0 && chargedAmount(t) > 0)
    .sort((a, b) => chargedAmount(b) - chargedAmount(a));
  document.getElementById("ds-detail-owed").innerHTML = owedTxns.length
    ? owedTxns.map(t => {
        const cuotaTag = (t.cuota_tot > 1)
          ? `<span class="ds-cuota-tag">${t.cuota_act ?? 0}/${t.cuota_tot}</span>`
          : "";
        return `<div class="ds-detail-row">
          <div class="ds-detail-left">
            <div class="ds-detail-desc">${escapeHTML(t.description)} ${cuotaTag}</div>
            <div class="ds-detail-date">${t.date}</div>
          </div>
          <div class="ds-detail-val">${formatCOP(chargedAmount(t))}</div>
        </div>`;
      }).join("")
    : `<div class="ds-empty">Sin cargos</div>`;

  const paidTxns = allTxns
    .filter(t => t.amount < 0)
    .sort((a, b) => a.amount - b.amount);
  document.getElementById("ds-detail-paid").innerHTML = paidTxns.length
    ? paidTxns.map(t => `<div class="ds-detail-row">
        <div class="ds-detail-left">
          <div class="ds-detail-desc">${escapeHTML(t.description)}</div>
          <div class="ds-detail-date">${t.date}</div>
        </div>
        <div class="ds-detail-val green">+${formatCOP(Math.abs(t.amount))}</div>
      </div>`).join("")
    : `<div class="ds-empty">Sin pagos registrados</div>`;

  document.getElementById("ds-detail-installments").innerHTML = activeInstallments.length
    ? activeInstallments.map(t => {
        const act = t.cuota_act || 0;
        const tot = t.cuota_tot;
        const pct = Math.round(act / tot * 100);
        return `<div class="ds-detail-row ds-inst-row">
          <div class="ds-detail-left">
            <div class="ds-detail-desc">${escapeHTML(t.description)}</div>
            <div class="ds-inst-progress">
              <div class="ds-inst-track">
                <div class="ds-inst-fill" style="width:${pct}%"></div>
              </div>
              <span>${act} de ${tot} cuotas</span>
            </div>
          </div>
          <div class="ds-detail-right">
            <div class="ds-detail-val">${formatCOP(t.cargos_mes || 0)}<span class="ds-per-mes">/mes</span></div>
            ${(t.saldo_dif || 0) > 0 ? `<div class="ds-dif-val">${formatCOP(t.saldo_dif)} pendiente</div>` : ""}
          </div>
        </div>`;
      }).join("")
    : `<div class="ds-empty">Sin cuotas activas</div>`;

  document.getElementById("debt-cargos").textContent   = formatCOP(cargosTotal);
  document.getElementById("debt-diferido").textContent = formatCOP(difTotal);
  const total = cargosTotal + difTotal || 1;
  document.getElementById("debt-bar-cargos").style.width = `${cargosTotal / total * 100}%`;
  document.getElementById("debt-bar-dif").style.width    = `${difTotal    / total * 100}%`;

  document.getElementById("sim-input").value = "";
  document.getElementById("sim-result").classList.add("hidden");
}

function renderDebitAccount() {
  const entradas   = summary.total_entradas || 0;
  const salidas    = summary.total_salidas  || 0;
  const rendimien  = summary.rendimientos   || 0;
  const saldoFinal = summary.saldo_final    || 0;
  const periodo    = summary.periodo        || "";

  document.getElementById("da-saldo-final").textContent = formatCOP(saldoFinal);
  document.getElementById("da-periodo").textContent     = periodo;
  document.getElementById("da-entradas").textContent    = formatCOP(entradas);
  document.getElementById("da-salidas").textContent     = formatCOP(salidas);

  const incomeTxns  = allTxns.filter(t => t.amount > 0).sort((a, b) => b.amount - a.amount);
  const expenseTxns = allTxns.filter(t => t.amount < 0).sort((a, b) => a.amount - b.amount);

  document.getElementById("da-detail-income").innerHTML = incomeTxns.length
    ? incomeTxns.map(t => `<div class="ds-detail-row">
        <div class="ds-detail-left">
          <div class="ds-detail-desc">${escapeHTML(t.description)}</div>
          <div class="ds-detail-date">${t.date}</div>
        </div>
        <div class="ds-detail-val green">+${formatCOP(t.amount)}</div>
      </div>`).join("")
    : `<div class="ds-empty">Sin ingresos</div>`;

  document.getElementById("da-detail-expense").innerHTML = expenseTxns.length
    ? expenseTxns.map(t => `<div class="ds-detail-row">
        <div class="ds-detail-left">
          <div class="ds-detail-desc">${escapeHTML(t.description)}</div>
          <div class="ds-detail-date">${t.date}</div>
        </div>
        <div class="ds-detail-val">${formatCOP(Math.abs(t.amount))}</div>
      </div>`).join("")
    : `<div class="ds-empty">Sin gastos</div>`;

  // Bar proportions
  const total = (entradas + salidas) || 1;
  document.getElementById("da-bar-entradas").style.width    = `${entradas / total * 100}%`;
  document.getElementById("da-bar-salidas").style.width     = `${salidas  / total * 100}%`;
  document.getElementById("da-bar-entradas-val").textContent = formatCOP(entradas);
  document.getElementById("da-bar-salidas-val").textContent  = formatCOP(salidas);

  const rendRow = document.getElementById("da-rendimientos-row");
  if (rendimien > 0) {
    rendRow.style.display = "flex";
    document.getElementById("da-rendimientos-val").textContent = `+${formatCOP(rendimien)}`;
  } else {
    rendRow.style.display = "none";
  }
}

function renderSimulator() {
  const raw   = document.getElementById("sim-input").value.replace(/[^\d]/g, "");
  const pago  = parseFloat(raw) || 0;
  const saldo = summary.saldo_total || 0;
  const result = document.getElementById("sim-result");
  if (pago <= 0 || saldo <= 0) { result.classList.add("hidden"); return; }
  const restante = Math.max(0, saldo - pago);
  result.textContent = restante === 0
    ? "🎉 ¡Deuda saldada!"
    : `Saldo restante: ${formatCOP(restante)}`;
  result.classList.remove("hidden");
}

// ── Installment accordion ────────────────────────────────────────────────────
let cuotasOpen = false;

function renderCuotas() {
  if (statementType === "debito") return;
  const installments = allTxns.filter(t => t.cuota_tot && t.cuota_tot > 1 && t.amount > 0);
  const section = document.getElementById("cuotas-section");
  if (installments.length === 0) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");

  const totalDif = installments.reduce((s, t) => s + (t.saldo_dif || 0), 0);
  document.getElementById("cuotas-toggle-label").textContent =
    `${installments.length} planes activos · ${formatCOP(totalDif)} pendiente`;

  document.getElementById("cuotas-list").innerHTML = installments.map(t => {
    const act       = t.cuota_act ?? 0;
    const tot       = t.cuota_tot;
    const isPending = act === 0;
    const pct       = isPending ? 0 : Math.round(act / tot * 100);
    const cargos    = t.cargos_mes || 0;
    const dif       = t.saldo_dif  || 0;
    const tag = isPending
      ? `<span class="cuota-tag-pending">Inicia próximo mes</span>`
      : `<span style="font-size:11px;color:var(--muted)">${act} de ${tot} pagadas</span>`;

    return `<div class="cuota-row">
      <div>
        <div class="cuota-desc">${escapeHTML(t.description)}</div>
        <div class="cuota-meta">${tag} · ${t.date}</div>
        <div class="cuota-track"><div class="cuota-fill" style="width:${pct}%"></div></div>
      </div>
      <div class="cuota-right">
        <div class="cuota-mes">${isPending ? "cuota estimada" : "cuota este mes"}</div>
        <div class="cuota-val">${formatCOP(cargos)}</div>
        ${dif > 0 ? `<div class="cuota-dif">+${formatCOP(dif)} pendiente</div>` : ""}
      </div>
    </div>`;
  }).join("");

  document.getElementById("cuotas-list").classList.toggle("cuotas-open", cuotasOpen);
  document.getElementById("cuotas-chevron").style.transform = cuotasOpen ? "rotate(180deg)" : "";
}

function toggleCuotas() {
  cuotasOpen = !cuotasOpen;
  document.getElementById("cuotas-list").classList.toggle("cuotas-open", cuotasOpen);
  document.getElementById("cuotas-chevron").style.transform = cuotasOpen ? "rotate(180deg)" : "";
}

// ── Category grid ─────────────────────────────────────────────────────────────
function renderCatGrid() {
  const cats = loadCategories();

  // For debit: expenses are negative amounts; for credit: charges are positive
  const relevant = statementType === "debito"
    ? allTxns.filter(t => t.amount < 0)
    : allTxns.filter(t => t.amount > 0);
  const amountOf = t => statementType === "debito" ? Math.abs(t.amount) : chargedAmount(t);
  const total    = relevant.reduce((s, t) => s + amountOf(t), 0);

  const bycat = {};
  relevant.forEach(t => t.categories.forEach(cat => {
    bycat[cat] = bycat[cat] || { sum: 0, count: 0 };
    bycat[cat].sum   += amountOf(t);
    bycat[cat].count += 1;
  }));

  const used = Object.entries(bycat).sort((a, b) => b[1].sum - a[1].sum);
  const usedNames = new Set(used.map(([n]) => n));
  cats.filter(c => c.custom && !usedNames.has(c.name))
      .forEach(c => used.push([c.name, { sum: 0, count: 0 }]));

  const visible = used.filter(([, d]) => d.count > 0 && d.sum >= 1);

  const grid = document.getElementById("cat-grid");
  grid.innerHTML = visible.map(([name, d]) => {
    const cat     = getCat(name);
    const pct     = total > 0 ? (d.sum / total * 100).toFixed(1) : 0;
    const isActive = activeFilter === name;
    const deleteBtn = cat.custom
      ? `<button class="cat-delete" onclick="removeCat(event,'${escapeAttr(name)}')" title="Eliminar">✕</button>`
      : "";
    return `
      <div class="cat-card ${isActive ? "active" : ""}"
        style="--cat-accent:${safeColor(cat.text)}"
        onclick="setFilter('${escapeAttr(name)}')">
        ${deleteBtn}
        <div class="cat-icon">${cat.icon}</div>
        <div class="cat-name">${escapeHTML(cat.name)}</div>
        <div class="cat-amount" style="color:${safeColor(cat.text)}">${formatCOP(d.sum)}</div>
        <div class="cat-count">${d.count} mov.</div>
        <div class="cat-bar">
          <div class="cat-bar-fill" style="width:${pct}%;background:${safeColor(cat.text)}"></div>
        </div>
        <div class="cat-pct">${pct}% del total</div>
      </div>`;
  }).join("") + `
    <div class="cat-card add-card" onclick="openNewCatForm()">
      <span style="font-size:22px">＋</span>
      <span>Nueva categoría</span>
    </div>`;
}

function removeCat(e, name) {
  e.stopPropagation();
  showConfirm(
    `¿Eliminar la categoría "${name}"? Las transacciones quedarán en "Otros".`,
    () => {
      deleteCustomCategory(name);
      if (activeFilter === name) activeFilter = "Todos";
      renderResults();
      persistCurrentState();
    }
  );
}

function showConfirm(msg, onOk) {
  document.getElementById("confirm-msg").textContent = msg;
  document.getElementById("confirm-ok").onclick = () => { hideConfirm(); onOk(); };
  document.getElementById("confirm-modal").classList.remove("hidden");
}

function hideConfirm() {
  document.getElementById("confirm-modal").classList.add("hidden");
}

function cancelConfirm(e) {
  if (!e || e.target.id === "confirm-modal") hideConfirm();
}

// ── Filters ──────────────────────────────────────────────────────────────────
function renderFilters() {
  const used  = new Set(allTxns.flatMap(t => t.categories));
  const chips = ["Todos", ...used];
  document.getElementById("filters").innerHTML = chips.map(name => {
    const active = name === activeFilter;
    if (name === "Todos") {
      return `<button class="chip ${active ? "active" : ""}"
        style="background:${active ? "#111827" : "var(--surface)"};color:${active ? "#fff" : "var(--muted)"}"
        onclick="setFilter('Todos')">Todos</button>`;
    }
    const cat = getCat(name);
    return `<button class="chip ${active ? "active" : ""}"
      style="background:${active ? safeColor(cat.bg) : "var(--surface)"};color:${active ? safeColor(cat.text) : "var(--muted)"}"
      onclick="setFilter('${escapeAttr(name)}')">
      <span class="chip-icon">${cat.icon}</span>${escapeHTML(cat.name)}
    </button>`;
  }).join("");
}

function setFilter(name) {
  activeFilter = name;
  renderCatGrid();
  renderFilters();
  renderTable();
}

// ── Bulk selection ────────────────────────────────────────────────────────────
function toggleSelect(idx) {
  if (selectedTxns.has(idx)) selectedTxns.delete(idx);
  else selectedTxns.add(idx);
  updateBulkBar();
}

function toggleSelectAll(checked) {
  const query = (document.getElementById("search-input").value || "").toLowerCase().trim();
  allTxns.forEach((t, idx) => {
    const matchCat = activeFilter === "Todos" || t.categories.includes(activeFilter);
    const matchQ   = !query || t.description.toLowerCase().includes(query);
    if (matchCat && matchQ) {
      if (checked) selectedTxns.add(idx);
      else selectedTxns.delete(idx);
    }
  });
  updateBulkBar();
  renderTable();
}

function clearSelection() {
  selectedTxns.clear();
  isBulkMode = false;
  updateBulkBar();
  renderTable();
}

function updateBulkBar() {
  const bar = document.getElementById("bulk-bar");
  const n   = selectedTxns.size;
  if (n === 0) {
    bar.classList.add("hidden");
  } else {
    bar.classList.remove("hidden");
    document.getElementById("bulk-count").textContent =
      `${n} transacción${n !== 1 ? "es" : ""} seleccionada${n !== 1 ? "s" : ""}`;
  }
  const allBox = document.getElementById("check-all");
  if (allBox) allBox.checked = n > 0 && n >= allTxns.length;
}

function openBulkModal() {
  isBulkMode = true;
  editingIdx  = null;
  pendingCats = [];
  document.getElementById("modal-desc").textContent =
    `Agregar categoría a ${selectedTxns.size} transacciones (se suma a las existentes)`;
  renderModalCats();
  document.getElementById("modal").classList.remove("hidden");
}

// ── Table ────────────────────────────────────────────────────────────────────
function renderTable() {
  const query = (document.getElementById("search-input").value || "").toLowerCase().trim();
  const filtered = allTxns.filter(t => {
    const matchCat = activeFilter === "Todos" || t.categories.includes(activeFilter);
    const matchQ   = !query || t.description.toLowerCase().includes(query);
    return matchCat && matchQ;
  });

  const empty = document.getElementById("empty-state");
  const tbl   = document.getElementById("txn-table");

  if (filtered.length === 0) {
    empty.classList.remove("hidden"); tbl.classList.add("hidden"); return;
  }
  empty.classList.add("hidden"); tbl.classList.remove("hidden");

  document.getElementById("table-body").innerHTML = filtered.map(t => {
    const idx      = allTxns.indexOf(t);
    const firstCat = getCat(t.categories[0]);
    const initial  = t.description[0].toUpperCase();
    const checked  = selectedTxns.has(idx);

    // Credit: negative = payment (green). Debit: positive = income (green).
    const isGreen = statementType === "debito" ? t.amount > 0 : t.amount < 0;
    const displayAmt = statementType === "debito"
      ? (t.amount > 0 ? `+${formatCOP(t.amount)}` : formatCOP(Math.abs(t.amount)))
      : (t.amount < 0 ? `+${formatCOP(Math.abs(t.amount))}` : formatCOP(chargedAmount(t)));

    const badges = t.categories.map(name => {
      const c = getCat(name);
      return `<span class="badge" style="background:${safeColor(c.bg)};color:${safeColor(c.text)}"
        onclick="openModal(${idx})" title="Editar categorías">
        <span class="badge-icon">${c.icon}</span>${escapeHTML(c.name)}
      </span>`;
    }).join("") +
    `<span class="badge-add" onclick="openModal(${idx})" title="Añadir categoría">+</span>`;

    return `<tr class="${checked ? "row-selected" : ""}">
      <td onclick="toggleSelect(${idx})" style="cursor:pointer">
        <input type="checkbox" class="txn-checkbox" ${checked ? "checked" : ""}
          onchange="toggleSelect(${idx})" onclick="event.stopPropagation()" />
      </td>
      <td class="avatar-cell">
        <div class="avatar" style="background:${safeColor(firstCat.bg)};color:${safeColor(firstCat.text)}">${escapeHTML(initial)}</div>
      </td>
      <td>
        <div class="desc-name">${escapeHTML(t.description)}</div>
        <div class="desc-date">${t.date}</div>
      </td>
      <td><div class="badges">${badges}</div></td>
      <td class="amount-cell right ${isGreen ? "credit" : ""}">${displayAmt}</td>
    </tr>`;
  }).join("");
}

// ── Category modal (multi-select) ─────────────────────────────────────────────
function openModal(idx) {
  editingIdx  = idx;
  pendingCats = [...allTxns[idx].categories];
  document.getElementById("modal-desc").textContent = allTxns[idx].description;
  renderModalCats();
  document.getElementById("modal").classList.remove("hidden");
}

function renderModalCats() {
  const cats = loadCategories();
  document.getElementById("modal-cats").innerHTML =
    cats.map(cat => {
      const sel = pendingCats.includes(cat.name);
      return `<button class="modal-cat-btn ${sel ? "selected" : ""}"
        style="background:${safeColor(cat.bg)};color:${safeColor(cat.text)}"
        onclick="togglePendingCat('${escapeAttr(cat.name)}')">
        <span style="font-size:18px">${cat.icon}</span>
        <span>${escapeHTML(cat.name)}</span>
        <span class="modal-cat-check">${sel ? "✓" : ""}</span>
      </button>`;
    }).join("") +
    `<div class="modal-actions" style="grid-column:1/-1">
      <button class="btn-cancel" onclick="closeModal()">Cancelar</button>
      <button class="btn-confirm" onclick="saveModal()">Guardar</button>
    </div>`;
}

function togglePendingCat(name) {
  if (pendingCats.includes(name)) {
    if (pendingCats.length === 1) return;
    pendingCats = pendingCats.filter(c => c !== name);
  } else {
    pendingCats.push(name);
  }
  renderModalCats();
}

function saveModal() {
  if (pendingCats.length === 0) { closeModal(); return; }

  if (isBulkMode && selectedTxns.size > 0) {
    selectedTxns.forEach(idx => {
      const merged = [...new Set([...allTxns[idx].categories, ...pendingCats])];
      allTxns[idx].categories = merged;
      learnRule(allTxns[idx].description, merged);
    });
    clearSelection();
  } else if (editingIdx !== null) {
    allTxns[editingIdx].categories = [...pendingCats];
    learnRule(allTxns[editingIdx].description, pendingCats);
  }

  isBulkMode = false;
  closeModal();
  renderResults();
  persistCurrentState();
}

function pickEmoji(e) {
  selectedEmoji = e;
  document.querySelectorAll(".emoji-btn").forEach(el =>
    el.classList.toggle("selected", el.textContent === e));
}

function closeModal(e) {
  if (!e || e.target.id === "modal") {
    document.getElementById("modal").classList.add("hidden");
    editingIdx = null; pendingCats = [];
  }
}

// ── New category from grid ────────────────────────────────────────────────────
function openNewCatForm() {
  editingIdx  = null;
  pendingCats = [];
  document.getElementById("modal-desc").textContent = "Crear nueva categoría";
  document.getElementById("modal-cats").innerHTML =
    `<div class="new-cat-block" style="grid-column:1/-1">
      <div class="new-cat-sub">Icono</div>
      <div class="emoji-picker">
        ${EMOJI_OPTIONS.map(e => `<button class="emoji-btn ${e === selectedEmoji ? "selected" : ""}" onclick="pickEmoji('${e}')">${e}</button>`).join("")}
      </div>
      <div class="new-cat-sub" style="margin-top:10px">Color</div>
      <div class="color-dots" id="color-dots">
        ${COLOR_PALETTE.map((c, i) => `
          <div class="color-dot ${i === selectedColorIdx ? "selected" : ""}"
            style="background:${c.text}" onclick="pickColorGrid(${i})"></div>`).join("")}
      </div>
      <div class="new-cat-form" style="margin-top:10px">
        <input class="new-cat-name" id="new-name" type="text" placeholder="Nombre de categoría" />
        <button class="btn-add-cat" onclick="addCatFromGrid()">Crear</button>
      </div>
    </div>
    <div class="modal-actions" style="grid-column:1/-1">
      <button class="btn-cancel" onclick="closeModal()">Cancelar</button>
    </div>`;
  document.getElementById("modal").classList.remove("hidden");
}

function addCatFromGrid() {
  const name = document.getElementById("new-name").value.trim();
  if (!name) return;
  saveCustomCategory(name, selectedEmoji);
  selectedEmoji = "🏷️";
  closeModal();
  renderResults();
}

function pickColorGrid(i) {
  selectedColorIdx = i;
  document.querySelectorAll(".color-dot").forEach((d, j) =>
    d.classList.toggle("selected", j === i));
}

// ── Export CSV ───────────────────────────────────────────────────────────────
function exportCSV() {
  const BOM    = "﻿";
  const header = "Fecha,Descripcion,Categorias,Valor COP\n";
  const rows   = allTxns.map(t =>
    `${t.date},"${t.description.replace(/"/g, '""')}","${t.categories.join(" / ")}",${t.amount.toFixed(0)}`
  ).join("\n");
  const blob = new Blob([BOM + header + rows], { type: "text/csv;charset=utf-8;" });
  Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob), download: "gastos_drafiti.csv",
  }).click();
}

// ── Category learning (backed by API) ─────────────────────────────────────────
function learnRule(description, categories) {
  const key = merchantKey(description);
  if (!key) return;
  learnedRulesCache[key] = categories;
  apiFetch("/api/rules", {
    method:  "PUT",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ key, categories }),
  });
}

function applyLearnedRules(txns) {
  const entries = Object.entries(learnedRulesCache);
  if (entries.length === 0) return txns;

  // Pre-compute normalized rules; keep raw key for legacy fallback
  const rules = entries.map(([k, cats]) => ({
    raw: k,
    norm: merchantKey(k),
    tokens: merchantKey(k).split(" ").filter(Boolean),
    cats,
  })).filter(r => r.tokens.length > 0);

  return txns.map(t => {
    const txnNorm = merchantKey(t.description);

    // 1. Exact normalized match
    const exact = rules.find(r => r.norm === txnNorm);
    if (exact) return { ...t, categories: exact.cats };

    // 2. Token subset: all rule tokens present in txn key (most specific wins)
    const candidates = rules
      .filter(r => r.tokens.every(tok => txnNorm.includes(tok)))
      .sort((a, b) => b.tokens.length - a.tokens.length);
    if (candidates.length > 0) return { ...t, categories: candidates[0].cats };

    // 3. Legacy fallback for old-format keys (raw description strings)
    const rawDesc = t.description.toUpperCase().trim();
    const legacy = entries.find(([k]) => rawDesc === k || rawDesc.includes(k) || k.includes(rawDesc));
    if (legacy) return { ...t, categories: legacy[1] };

    return t;
  });
}

// ── Password modal ────────────────────────────────────────────────────────────
function openPwdModal(errorMsg = "") {
  document.getElementById("password-modal").classList.remove("hidden");
  const input = document.getElementById("pdf-password");
  const errEl = document.getElementById("pwd-error");
  input.value = "";
  if (errEl) {
    errEl.textContent = errorMsg;
    errEl.classList.toggle("hidden", !errorMsg);
    if (errorMsg) input.classList.add("pwd-input-error");
    else input.classList.remove("pwd-input-error");
  }
  setTimeout(() => input.focus(), 80);
}

function closePwdModal() {
  document.getElementById("password-modal").classList.add("hidden");
  pendingFile = null;
}

async function submitWithPassword() {
  const pwd = document.getElementById("pdf-password").value.trim();
  if (!pwd || !pendingFile) { closePwdModal(); return; }
  const file = pendingFile;  // save before closePwdModal clears it
  closePwdModal();
  await uploadFile(file, pwd);
}

// ── Reset ─────────────────────────────────────────────────────────────────────
function resetApp() {
  allTxns = []; summary = {}; statementType = "credito";
  activeFilter = "Todos"; activeTab = "gastos";
  cuotasOpen = false; currentHistoryId = null;
  selectedTxns.clear(); isBulkMode = false;
  document.getElementById("file-input").value   = "";
  document.getElementById("search-input").value = "";
  show("documents-section");
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  const meRes = await fetch("/api/me");
  if (!meRes.ok) {
    window.location.href = "/login";
    return;
  }
  currentUser = await meRes.json();
  document.getElementById("user-name").textContent = currentUser.username;
  if (currentUser.is_admin) {
    document.getElementById("admin-btn").classList.remove("hidden");
  }

  const [catsData, rulesData, historyData] = await Promise.all([
    fetch("/api/categories").then(r => r.json()),
    fetch("/api/rules").then(r => r.json()),
    fetch("/api/history").then(r => r.json()),
  ]);

  customCats        = catsData;
  learnedRulesCache = rulesData;
  historyCache      = historyData;

  show("documents-section");
}

init();
