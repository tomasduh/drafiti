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
  fetch("/api/categories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, icon, bg: color.bg, text: color.text }),
  });
}

function deleteCustomCategory(name) {
  customCats = customCats.filter(c => c.name !== name);
  fetch(`/api/categories/${encodeURIComponent(name)}`, { method: "DELETE" });
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

function saveToHistory(filename, data) {
  const id    = Date.now().toString();
  const total = data.transactions
    .filter(t => t.amount > 0)
    .reduce((s, t) => s + ((t.cargos_mes != null) ? t.cargos_mes : t.amount), 0);
  const entry = {
    id,
    filename,
    uploadedAt:  new Date().toLocaleDateString("es-CO"),
    fecha_corte: data.summary?.fecha_corte || "",
    total,
    transactions: data.transactions.map(t => ({ ...t, categories: [t.category] })),
    summary:      data.summary || {},
  };
  historyCache.unshift(entry);
  if (historyCache.length > 20) historyCache.length = 20;

  fetch("/api/history", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(entry),
  });
  return id;
}

function persistCurrentState() {
  if (!currentHistoryId) return;
  const entry = historyCache.find(h => h.id === currentHistoryId);
  if (!entry) return;
  entry.transactions = allTxns;
  entry.total = allTxns
    .filter(t => t.amount > 0)
    .reduce((s, t) => s + chargedAmount(t), 0);

  fetch(`/api/history/${currentHistoryId}`, {
    method:  "PUT",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ transactions: entry.transactions, total: entry.total }),
  });
}

function deleteFromHistory(id) {
  historyCache = historyCache.filter(h => h.id !== id);
  fetch(`/api/history/${id}`, { method: "DELETE" });
  renderHistory();
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
  summary = entry.summary;
  currentHistoryId = id;
  activeFilter = "Todos"; activeTab = "gastos"; cuotasOpen = false;
  renderResults();
}

function renderHistory() {
  const section = document.getElementById("history-section");
  const history = loadHistory();
  if (history.length === 0) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");
  section.innerHTML = `
    <div class="history-heading">
      <span>Extractos anteriores</span>
      <span>${history.length} guardado${history.length !== 1 ? "s" : ""}</span>
    </div>
    <div class="history-grid">
      ${history.map(h => `
        <div class="history-card" onclick="loadFromHistory('${h.id}')">
          <div class="history-icon">📄</div>
          <div class="history-info">
            <div class="history-filename">${h.filename}</div>
            <div class="history-meta">${h.fecha_corte ? `Corte ${h.fecha_corte} · ` : ""}Subido ${h.uploadedAt}</div>
          </div>
          <div class="history-amount">${formatCOP(h.total)}</div>
          <button class="history-del" onclick="event.stopPropagation(); deleteFromHistory('${h.id}')" title="Eliminar">✕</button>
        </div>`).join("")}
    </div>`;
}

// ── State ────────────────────────────────────────────────────────────────────
let allTxns          = [];
let summary          = {};
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

// ── Auth helpers ──────────────────────────────────────────────────────────────
async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    return null;
  }
  return res;
}

async function logout() {
  await fetch("/auth/logout", { method: "POST" });
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

async function uploadFile(file) {
  show("loading");
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await apiFetch("/api/extract", { method: "POST", body: form });
    if (!res) return;
    if (!res.ok) throw new Error((await res.json()).detail || "Error desconocido");
    const data = await res.json();
    summary = data.summary || {};

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
    currentHistoryId = saveToHistory(file.name, data);
    activeFilter = "Todos";
    renderResults();
  } catch (e) {
    alert("Error al procesar el PDF:\n" + e.message);
    show("upload-section");
  }
}

// ── Visibility ───────────────────────────────────────────────────────────────
function show(id) {
  ["upload-section", "loading", "results"].forEach(s =>
    document.getElementById(s).classList.toggle("hidden", s !== id));
  const isResults = id === "results";
  document.getElementById("header-info").classList.toggle("hidden", !isResults);
  document.getElementById("main-nav").classList.toggle("hidden", !isResults);
  if (id === "upload-section") renderHistory();
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

// ── Hero (gasto) ──────────────────────────────────────────────────────────────
function renderHero() {
  const pos   = allTxns.filter(t => t.amount > 0);
  const total = pos.reduce((s, t) => s + chargedAmount(t), 0);
  document.getElementById("hero-amount").textContent = formatCOP(total);
  document.getElementById("hero-sub").textContent =
    `${pos.length} transacciones · ${allTxns.filter(t => t.amount < 0).length} créditos`;
  document.getElementById("txn-count").textContent = `${allTxns.length} movimientos`;
}

// ── Debt card ─────────────────────────────────────────────────────────────────
function renderDebt() {
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
            <div class="ds-detail-desc">${t.description} ${cuotaTag}</div>
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
          <div class="ds-detail-desc">${t.description}</div>
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
            <div class="ds-detail-desc">${t.description}</div>
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
        <div class="cuota-desc">${t.description}</div>
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
  const cats  = loadCategories();
  const pos   = allTxns.filter(t => t.amount > 0);
  const total = pos.reduce((s, t) => s + chargedAmount(t), 0);

  const bycat = {};
  pos.forEach(t => t.categories.forEach(cat => {
    bycat[cat] = bycat[cat] || { sum: 0, count: 0 };
    bycat[cat].sum   += chargedAmount(t);
    bycat[cat].count += 1;
  }));

  const used = Object.entries(bycat).sort((a, b) => b[1].sum - a[1].sum);
  const usedNames = new Set(used.map(([n]) => n));
  cats.filter(c => c.custom && !usedNames.has(c.name))
      .forEach(c => used.push([c.name, { sum: 0, count: 0 }]));

  const grid = document.getElementById("cat-grid");
  grid.innerHTML = used.map(([name, d]) => {
    const cat     = getCat(name);
    const pct     = total > 0 ? (d.sum / total * 100).toFixed(1) : 0;
    const isActive = activeFilter === name;
    const deleteBtn = cat.custom
      ? `<button class="cat-delete" onclick="removeCat(event,'${name}')" title="Eliminar">✕</button>`
      : "";
    return `
      <div class="cat-card ${isActive ? "active" : ""}"
        style="--cat-accent:${cat.text}"
        onclick="setFilter('${name}')">
        ${deleteBtn}
        <div class="cat-icon">${cat.icon}</div>
        <div class="cat-name">${cat.name}</div>
        <div class="cat-amount" style="color:${cat.text}">${formatCOP(d.sum)}</div>
        <div class="cat-count">${d.count} mov.</div>
        <div class="cat-bar">
          <div class="cat-bar-fill" style="width:${pct}%;background:${cat.text}"></div>
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
      style="background:${active ? cat.bg : "var(--surface)"};color:${active ? cat.text : "var(--muted)"}"
      onclick="setFilter('${name}')">
      <span class="chip-icon">${cat.icon}</span>${cat.name}
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
    const neg      = t.amount < 0;
    const charged  = chargedAmount(t);
    const firstCat = getCat(t.categories[0]);
    const initial  = t.description[0].toUpperCase();
    const checked  = selectedTxns.has(idx);

    const badges = t.categories.map(name => {
      const c = getCat(name);
      return `<span class="badge" style="background:${c.bg};color:${c.text}"
        onclick="openModal(${idx})" title="Editar categorías">
        <span class="badge-icon">${c.icon}</span>${c.name}
      </span>`;
    }).join("") +
    `<span class="badge-add" onclick="openModal(${idx})" title="Añadir categoría">+</span>`;

    return `<tr class="${checked ? "row-selected" : ""}">
      <td onclick="toggleSelect(${idx})" style="cursor:pointer">
        <input type="checkbox" class="txn-checkbox" ${checked ? "checked" : ""}
          onchange="toggleSelect(${idx})" onclick="event.stopPropagation()" />
      </td>
      <td class="avatar-cell">
        <div class="avatar" style="background:${firstCat.bg};color:${firstCat.text}">${initial}</div>
      </td>
      <td>
        <div class="desc-name">${t.description}</div>
        <div class="desc-date">${t.date}</div>
      </td>
      <td><div class="badges">${badges}</div></td>
      <td class="amount-cell right ${neg ? "credit" : ""}">${neg ? "+" : ""}${formatCOP(charged)}</td>
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
        style="background:${cat.bg};color:${cat.text}"
        onclick="togglePendingCat('${cat.name}')">
        <span style="font-size:18px">${cat.icon}</span>
        <span>${cat.name}</span>
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
  const key = description.toUpperCase().trim();
  learnedRulesCache[key] = categories;
  fetch("/api/rules", {
    method:  "PUT",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ key, categories }),
  });
}

function applyLearnedRules(txns) {
  const entries = Object.entries(learnedRulesCache);
  if (entries.length === 0) return txns;
  return txns.map(t => {
    const desc  = t.description.toUpperCase().trim();
    const match = entries.find(([k]) => desc === k || desc.includes(k) || k.includes(desc));
    return match ? { ...t, categories: match[1] } : t;
  });
}

// ── Admin panel ───────────────────────────────────────────────────────────────
async function openAdminPanel() {
  document.getElementById("admin-modal").classList.remove("hidden");
  await refreshAdminUsers();
}

function closeAdminPanel(e) {
  if (!e || e.target.id === "admin-modal") {
    document.getElementById("admin-modal").classList.add("hidden");
    document.getElementById("admin-error").classList.add("hidden");
  }
}

async function refreshAdminUsers() {
  const res   = await apiFetch("/api/admin/users");
  if (!res) return;
  const users = await res.json();
  const list  = document.getElementById("admin-user-list");
  list.innerHTML = users.map(u => `
    <div class="admin-user-row">
      <div class="admin-user-info">
        <span class="admin-user-name">${u.username}</span>
        <span class="admin-user-email">${u.email}</span>
        ${u.is_admin ? `<span class="admin-badge">admin</span>` : ""}
        ${u.has_google ? `<span class="admin-google">✔ Google</span>` : `<span class="admin-pending">Sin cuenta Google</span>`}
      </div>
      ${u.id !== currentUser.id ? `
        <button class="history-del" onclick="adminDeleteUser(${u.id})" title="Eliminar">✕</button>
      ` : ""}
    </div>`).join("");
}

async function adminCreateUser() {
  const username = document.getElementById("admin-username").value.trim();
  const email    = document.getElementById("admin-email").value.trim();
  const isAdmin  = document.getElementById("admin-is-admin").checked;
  const errEl    = document.getElementById("admin-error");

  if (!username || !email) {
    errEl.textContent = "Username y email son obligatorios.";
    errEl.classList.remove("hidden");
    return;
  }

  const res = await apiFetch("/api/admin/users", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ username, email, is_admin: isAdmin }),
  });
  if (!res) return;

  if (!res.ok) {
    const err = await res.json();
    errEl.textContent = err.detail || "Error al crear usuario.";
    errEl.classList.remove("hidden");
    return;
  }

  errEl.classList.add("hidden");
  document.getElementById("admin-username").value  = "";
  document.getElementById("admin-email").value     = "";
  document.getElementById("admin-is-admin").checked = false;
  await refreshAdminUsers();
}

async function adminDeleteUser(userId) {
  showConfirm("¿Eliminar este usuario y todos sus datos?", async () => {
    const res = await apiFetch(`/api/admin/users/${userId}`, { method: "DELETE" });
    if (res && res.ok) await refreshAdminUsers();
  });
}

// ── Reset ─────────────────────────────────────────────────────────────────────
function resetApp() {
  allTxns = []; summary = {}; activeFilter = "Todos"; activeTab = "gastos";
  cuotasOpen = false; currentHistoryId = null;
  selectedTxns.clear(); isBulkMode = false;
  document.getElementById("file-input").value   = "";
  document.getElementById("search-input").value = "";
  show("upload-section");
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

  renderHistory();
}

init();
