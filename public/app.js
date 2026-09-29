// AI Brief demo — frontend senza build. Tutto il testo che arriva da notizie o LLM passa da esc().
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const TIPO = { regolazione: "Regolazione", caso_uso: "Caso d'uso", investimento: "Investimenti", prodotto: "Prodotto", ricerca: "Ricerca", competenze: "Competenze", policy_pubblica: "Policy pubblica" };
const MAT = { esplorativa: "esplorativa", sperimentale: "sperimentale", in_scala: "in scala", trasformativa: "trasformativa" };

const STEPS = [
  ["pianificazione", "Decide cosa cercare", "llm", ""],
  ["recupero", "Cerca le notizie (ultimi 14 giorni)", "code", ""],
  ["estrazione", "Dà un voto a ogni articolo", "llm", "rilevanza · hype · tema"],
  ["raggruppamento", "Raggruppa e sceglie 5–7 temi", "llm", ""],
  ["redattore", "Il redattore approva", "human", ""],
  ["redazione", "Scrive la newsletter", "llm", ""],
  ["variante", "Adatta il commento a ogni profilo", "llm", ""],
];

let cfg = {}, run = null, mode = "replay", code = "";

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (code) headers["X-Access-Code"] = code;
  const r = await fetch(path, { ...opts, headers });
  if (code && path.startsWith("/api/live")) refreshQuota();
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

// ---------------------------------------------------------------- percorso in 4 passi e modalità
document.querySelectorAll("nav.stepper button").forEach((b) => b.onclick = () => go(+b.dataset.step));
document.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => go(+b.dataset.go));
function go(n) {
  document.querySelectorAll("nav.stepper button").forEach((b) => b.classList.toggle("on", +b.dataset.step === n));
  document.querySelectorAll("main > section").forEach((s) => s.classList.toggle("hidden", s.id !== "s" + n));
  if (n === 2) renderCompare();
  if (n === 3) renderStep3();
  if (n === 4) { loadDeliveries(); loadTrust(); }
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function showTab(t) {  // usato dal flusso live
  if (t === "newsletter") return go(1);
  go(4);
  if (t === "editor") $("#ed-card").open = true;
}
$("#m-replay").onclick = () => setMode("replay");
$("#m-live").onclick = () => setMode("live");
function setMode(m) {
  mode = m;
  $("#m-replay").classList.toggle("on", m === "replay");
  $("#m-live").classList.toggle("on", m === "live");
  $("#live-box").classList.toggle("hidden", m !== "live");
  banner();
  if (m === "replay") loadRun("replay");
}
function banner(extra = "") {
  const b = $("#banner");
  b.classList.remove("warn");
  if (mode === "replay") b.textContent = cfg.replay ? `Uscita generata il ${new Date(run?.creato || Date.now()).toLocaleDateString("it-IT")} su notizie reali. ${extra}` : "Replay non disponibile.";
  else b.textContent = cfg.live_disponibile ? `Genera un'uscita adesso con modelli gratuiti (qualche minuto). ${extra}` : "Modalità live non attiva.";
}

// ---------------------------------------------------------------- rendering del run
function renderSteps(state = {}) {
  const log = run?.log || [];
  $("#steps").innerHTML = STEPS.map(([key, title, kind, desc]) => {
    const entries = log.filter((l) => l.passo.startsWith(key === "redattore" ? "@@" : key));
    let st = state[key] || (entries.length ? "ok" : "");
    if (key === "redattore" && run?.core) st = "ok";
    const detail = entries.map((e) => `${esc(e.passo)} · <b>${esc(e.model)}</b>${e.attempts?.length > 1 ? ` · tentativi: ${esc(e.attempts.join(" → "))}` : ""}`).join("<br>");
    const icon = { ok: "✓", run: "…", err: "!" }[st] || "";
    const pill = { llm: '<span class="pill llm">LLM</span>', code: '<span class="pill code">codice</span>', human: '<span class="pill">umano</span>' }[kind];
    return `<li><span class="dot ${st}">${icon}</span><div><b>${title}</b> ${pill}${desc ? `<div class="muted" style="font-size:13px">${desc}</div>` : ""}${detail ? `<div class="who">${detail}</div>` : ""}</div></li>`;
  }).join("");
  $("#queries").innerHTML = (run?.queries || []).map((q) => `<span class="chip">${esc(q.query)} · ${esc(q.tema)} · ${q.lingua}</span>`).join("") || '<span class="muted">—</span>';
}

function renderEditor(editable = false) {
  if (!run?.themes) { $("#themes").innerHTML = "<tr><td class='muted'>Ancora nessun tema.</td></tr>"; $("#pool").innerHTML = ""; return; }
  const sel = new Set(run.selected), appr = new Set(run.approved);
  const dis = editable ? "" : "disabled";
  $("#editor-note").textContent = run.editor_note || "";
  $("#themes").innerHTML = `<tr><th>In mail</th><th>Tema</th><th>Tipo</th><th>Testate</th><th>Rilevanza</th><th>Hype</th><th>Affidab.</th><th>Punteggio</th></tr>` +
    run.themes.map((t) => `<tr class="${sel.has(t.id) ? "" : "dim"}"><td><input type="checkbox" data-theme="${t.id}" ${sel.has(t.id) ? "checked" : ""} ${dis}></td>
      <td>${esc(t.titolo)}</td><td>${TIPO[t.tipo] || esc(t.tipo)}</td><td>${t.fonti_indipendenti}</td><td>${t.rilevanza}</td><td>${t.hype}</td><td>${t.affidabilita}</td>
      <td><div class="bar" title="${t.score}"><i style="width:${Math.max(4, Math.min(100, t.score * 100))}%"></i></div></td></tr>`).join("");
  const art = Object.fromEntries(run.articles.map((a) => [a.id, a]));
  $("#pool").innerHTML = `<tr><th>Ok</th><th>Notizia</th><th>Fonte</th><th>Tipo</th><th>Ril.</th><th>Hype</th><th>Motivazione</th></tr>` +
    [...run.evals].sort((a, b) => b.rilevanza - a.rilevanza || a.hype - b.hype).map((e) => {
      const a = art[e.id] || {};
      return `<tr class="${appr.has(e.id) ? "" : "dim"}"><td><input type="checkbox" data-art="${e.id}" ${appr.has(e.id) ? "checked" : ""} ${dis}></td>
        <td><a href="${safeUrl(a.url)}" target="_blank" rel="noopener">${esc(a.titolo)}</a><div class="muted" style="font-size:12px">${esc(e.tags.join(" · "))}</div></td>
        <td>${esc(a.testata)}<div class="muted" style="font-size:12px">affidab. ${a.affidabilita}</div></td><td>${TIPO[e.tipo] || esc(e.tipo)}</td><td>${e.rilevanza}</td><td>${e.hype}</td><td class="muted">${esc(e.motivazione)}</td></tr>`;
    }).join("");
}

function fbLink(token, params, label) {
  const q = new URLSearchParams({ run: run.id, ...params }).toString();
  return `<a href="/pannello/${token}?${q}" target="_blank" rel="noopener">${label}</a>`;
}

async function renderNewsletter() {
  if (!run?.core) { $("#mail").innerHTML = "<div class='card muted'>La newsletter comparirà dopo la redazione.</div>"; $("#qa").innerHTML = ""; return; }
  const SHORT = [(d) => d, (d) => d, () => "fonti verificate", () => "temi vari", () => "regolazione inclusa"];
  $("#qa").innerHTML = (run.qa || []).map((c, i) => `<li class="${c.ok ? "ok" : "ko"}" title="${esc(c.check)}">${c.ok ? "✓" : "!"} ${esc((SHORT[i] || ((d) => d))(c.dettaglio))}</li>`).join("");
  const token = $("#who").value;
  if (!token) return renderMail({ notizie: run.core.notizie, ...run.core, personalizzate: [], dall_editore: [] }, null);
  const nl = await api(`/api/run/${run.id}/render/${token}`);
  const d = nl.destinatario;
  renderMail(nl, token);
}

function renderMail(nl, token) {
  const news = nl.notizie.map((n) => `
    <section>
      <div class="label">${TIPO[n.tipo] || ""}</div>
      <h4>${esc(n.titolo)}</h4>
      <div class="label">Fatto</div><p class="fatto">${esc(n.fatto)}</p>
      <div class="label">${n.commento_segmento ? "Perché conta per te" : "Perché conta"}</div><p class="comm">${esc(n.commento_segmento || n.commento)}</p>
      <div class="srcs">✓ Fonti: ${n.fonti.map((f) => `<a href="${safeUrl(f.url)}" target="_blank" rel="noopener">${esc(f.testata)}</a> (${esc(f.data)})`).join(" · ") || "—"}</div>
      ${token ? `<div class="fb">${fbLink(token, { azione: "notizia", valore: "interessa", item: n.tema_id, titolo: n.titolo, tipo_notizia: n.tipo || "" }, "👍 Mi interessa")}
        ${fbLink(token, { azione: "notizia", valore: "non_interessa", item: n.tema_id, titolo: n.titolo, tipo_notizia: n.tipo || "" }, "👎 Non mi interessa")}
        ${n.fonti.slice(0, 1).map((f) => fbLink(token, { azione: "fonte", valore: "affidabile", dominio: f.dominio }, `✔ ${esc(f.dominio)} affidabile`) + fbLink(token, { azione: "fonte", valore: "non_affidabile", dominio: f.dominio }, `✖ non affidabile`)).join("")}</div>` : ""}
    </section>`).join("");
  const pers = nl.personalizzate?.length ? `<section style="background:var(--accent-soft)"><div class="label">Per te</div>` + nl.personalizzate.map((p) => `
      <h4 style="font-size:16px;margin-top:10px">${esc(p.titolo)}</h4><p class="fatto">${esc(p.sintesi)}</p>
      <div class="srcs">${esc(p.perche)} · <a href="${safeUrl(p.fonte.url)}" target="_blank" rel="noopener">${esc(p.fonte.testata)}</a> (${esc(p.fonte.data)})</div>
      <div class="fb">${fbLink(token, { azione: "notizia", valore: "non_interessa", item: p.id, titolo: p.titolo, tipo_notizia: p.tipo }, "👎 Non mi interessa")}
      ${fbLink(token, { azione: "fonte", valore: "non_affidabile", dominio: p.fonte.dominio }, `✖ ${esc(p.fonte.dominio)} non affidabile`)}</div>`).join("") + `</section>` : "";
  const oss = nl.dall_editore?.length ? `<section><div class="label">${esc(cfg.sezione_editore)}</div>${nl.dall_editore.map((o) => `<p style="margin:4px 0">${esc(o.titolo)} <span class="muted" style="font-size:12px">— ${esc(o.nota)}</span></p>`).join("")}</section>` : "";
  $("#mail").innerHTML = `<article class="mail">
    <div class="mhead"><div class="kicker">${esc(cfg.brand)} · ${esc(cfg.editore)}</div><h2>${esc(nl.oggetto)}</h2></div>
    <section class="ed">${esc(nl.editoriale)}</section>
    ${news}
    <section><div class="label">Da portare in riunione</div><ul>${nl.da_portare_in_riunione.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></section>
    ${pers}${oss}
    <section class="foot">Scritta con l'IA, rivista dalla redazione. ${token ? `<a href="/pannello/${token}" target="_blank" rel="noopener">Le tue preferenze</a>` : ""}</section>
  </article>`;
}

// ---------------------------------------------------------------- passo 2: confronto affiancato
async function renderCompare() {
  if (!run?.core) return;
  const base = run.core.notizie.map((n) => n.tema_id);
  for (const [sel, col] of [["#cmpA", "#colA"], ["#cmpB", "#colB"]]) {
    const nl = await api(`/api/run/${run.id}/render/${$(sel).value}`);
    const d = nl.destinatario;
    $(col).innerHTML = `<div class="who-card"><b>${esc(d.ruolo)}</b> · ${esc(d.organizzazione)}<div class="muted" style="font-size:12.5px">${esc(d.settore)} · maturità ${MAT[d.maturita]} · ${esc(d.dimensione)}</div>
</div>` +
      nl.notizie.map((n, i) => `<div class="cmp-item"><div class="pos">${i + 1}<small>(${base.indexOf(n.tema_id) + 1})</small></div><div>
        <b>${esc(n.titolo)}</b><div class="label">Perché conta per te</div><p style="margin:0;font-size:14px">${esc(n.commento_segmento || n.commento)}</p></div></div>`).join("");
  }
}

// ---------------------------------------------------------------- passo 3: feedback dal vivo
async function renderStep3() {
  if (!run?.core) return;
  const token = $("#who3").value;
  $("#panel-link").href = `/pannello/${token}`;
  const nl = await api(`/api/run/${run.id}/render/${token}`);
  const n0 = nl.notizie.slice(0, 3).map((n) => `<div class="card"><b>${esc(n.titolo)}</b>
      <div class="srcs muted" style="font-size:13px;margin:4px 0">Fonte: ${n.fonti.map((f) => esc(f.testata)).join(" · ")}</div>
      <div class="fb">${fbLink(token, { azione: "notizia", valore: "non_interessa", item: n.tema_id, titolo: n.titolo, tipo_notizia: n.tipo || "" }, "👎 Non mi interessa")}
      ${n.fonti.slice(0, 1).map((f) => fbLink(token, { azione: "fonte", valore: "non_affidabile", dominio: f.dominio }, `✖ ${esc(f.dominio)} non affidabile`)).join("")}</div></div>`).join("");
  const pers = nl.personalizzate.length ? nl.personalizzate.map((p) => `<div class="card" style="background:var(--accent-soft)"><b>${esc(p.titolo)}</b>
      <p style="margin:4px 0">${esc(p.sintesi)}</p><div class="muted" style="font-size:13px">${esc(p.perche)} · ${esc(p.fonte.testata)}</div>
      <div class="fb">${fbLink(token, { azione: "notizia", valore: "non_interessa", item: p.id, titolo: p.titolo, tipo_notizia: p.tipo }, "👎 Non mi interessa")}
      ${fbLink(token, { azione: "fonte", valore: "non_affidabile", dominio: p.fonte.dominio }, `✖ ${esc(p.fonte.dominio)} non affidabile`)}</div></div>`).join("")
    : `<div class="card muted">Nessuna notizia sui suoi interessi. Aggiungine uno dal pannello.</div>`;
  $("#pers3").innerHTML = `<h3 style="margin:0 0 8px">Per te <span class="muted" style="font-weight:400">· interessi: ${esc((await api(`/api/panel/${token}`)).interessi.join(", "))}</span></h3>${pers}
    <h3 style="margin:18px 0 8px">Per tutti</h3>${n0}`;
}
window.addEventListener("focus", () => { if (!$("#s3").classList.contains("hidden")) renderStep3(); if (!$("#s1").classList.contains("hidden")) renderNewsletter(); });

function renderAll(state) { renderSteps(state); renderEditor(mode === "live" && run && !run.core); renderNewsletter(); }

async function loadRun(id) {
  try { run = await api(`/api/run/${id}`); } catch (e) { run = null; }
  banner(); renderAll();
}

// ---------------------------------------------------------------- run rimaste oggi (codice + tetto globale)
async function refreshQuota() {
  const c = $("#code").value.trim();
  if (!c) return ($("#quota").textContent = "");
  try {
    const q = await fetch("/api/check-code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: c }) }).then((r) => r.json());
    $("#quota").textContent = q.ok ? `${q.run_rimaste} run rimaste oggi` : "codice non valido";
    $("#quota").classList.toggle("low", !q.ok || q.run_rimaste === 0);
  } catch { $("#quota").textContent = ""; }
}
$("#code").addEventListener("change", refreshQuota);

// ---------------------------------------------------------------- pipeline live
$("#go").onclick = async () => {
  code = $("#code").value.trim();
  const state = {};
  const set = (k, v) => { state[k] = v; renderSteps(state); };
  $("#go").disabled = true;
  try {
    await api("/api/check-code", { method: "POST", body: JSON.stringify({ code }) });
    showTab("pipeline"); run = { log: [] }; set("pianificazione", "run");
    run = await api("/api/live/start", { method: "POST" }); set("pianificazione", "ok"); set("recupero", "run");
    await api(`/api/live/${run.id}/fetch`, { method: "POST" }); run = await api(`/api/run/${run.id}`); set("recupero", "ok"); set("estrazione", "run");
    for (let b = 0; ; b++) { const r = await api(`/api/live/${run.id}/extract/${b}`, { method: "POST" }); run = await api(`/api/run/${run.id}`); renderSteps(state); if (r.done) break; }
    set("estrazione", "ok"); set("raggruppamento", "run");
    await api(`/api/live/${run.id}/cluster`, { method: "POST" }); run = await api(`/api/run/${run.id}`);
    set("raggruppamento", "ok"); set("redattore", "run");
    renderEditor(true); $("#editor-actions").classList.remove("hidden"); showTab("editor");
    banner("Il run è in pausa: rivedi temi e pool nella scheda Redattore, poi approva.");
    window._state = state;
  } catch (e) {
    Object.keys(state).forEach((k) => state[k] === "run" && (state[k] = "err")); renderSteps(state);
    $("#banner").classList.add("warn"); $("#banner").textContent = "Errore: " + e.message;
  } finally { $("#go").disabled = false; }
};

$("#approve").onclick = async () => {
  const state = window._state || {};
  const set = (k, v) => { state[k] = v; renderSteps(state); };
  const selected = [...document.querySelectorAll("[data-theme]:checked")].map((i) => i.dataset.theme);
  const approved = [...document.querySelectorAll("[data-art]:checked")].map((i) => i.dataset.art);
  $("#approve").disabled = true;
  try {
    await api(`/api/run/${run.id}/editor`, { method: "POST", body: JSON.stringify({ selected, approved }) });
    set("redattore", "ok"); set("redazione", "run"); showTab("pipeline");
    await api(`/api/live/${run.id}/write`, { method: "POST" }); set("redazione", "ok"); set("variante", "run");
    for (let n = 0; ; n++) { const r = await api(`/api/live/${run.id}/personalize/${n}`, { method: "POST" }); if (r.done) break; }
    set("variante", "ok"); run = await api(`/api/run/${run.id}`);
    $("#editor-actions").classList.add("hidden"); renderAll(state); showTab("newsletter"); banner("Run completato.");
  } catch (e) {
    Object.keys(state).forEach((k) => state[k] === "run" && (state[k] = "err")); renderSteps(state);
    $("#banner").classList.add("warn"); $("#banner").textContent = "Errore: " + e.message;
  } finally { $("#approve").disabled = false; }
};

// ---------------------------------------------------------------- registro e fonti
$("#send").onclick = async () => { if (!run) return; await api(`/api/run/${run.id}/send`, { method: "POST" }); loadDeliveries(); };
async function loadDeliveries() {
  const rows = await api("/api/deliveries");
  const who = Object.fromEntries(cfg.destinatari.map((d) => [d.token, d]));
  $("#deliv").innerHTML = `<tr><th>Quando</th><th>Destinatario</th><th>Uscita</th><th>Nucleo comune (ordine del segmento)</th><th>Personalizzate</th></tr>` +
    (rows.map((r) => `<tr><td>${esc(r.ts.slice(0, 16).replace("T", " "))}</td><td>${esc(who[r.token]?.ruolo)}<div class="muted" style="font-size:12px">${esc(who[r.token]?.organizzazione)}</div></td>
      <td>${esc(r.run)}</td><td><ol style="margin:0;padding-left:18px">${r.notizie.map((t) => `<li>${esc(t)}</li>`).join("")}</ol></td><td>${r.personalizzate.map(esc).join("<br>") || "—"}</td></tr>`).join("") || "<tr><td class='muted'>Nessun invio registrato.</td></tr>");
}
async function loadTrust() {
  const t = await api(`/api/trust?run_id=${run?.id || "replay"}`);
  $("#trust").innerHTML = `<tr><th>Dominio</th><th>Prior</th><th>👍</th><th>👎</th><th>Valore usato nel punteggio</th></tr>` +
    Object.entries(t).sort((a, b) => b[1].valore - a[1].valore).map(([d, v]) => `<tr><td>${esc(d)}</td><td>${v.prior}</td><td>${v.affidabile}</td><td>${v.non_affidabile}</td>
      <td><div class="bar"><i style="width:${v.valore * 100}%"></i></div> ${v.valore}${v.valore !== v.prior ? " (corretto dai voti)" : ""}</td></tr>`).join("");
}

// ---------------------------------------------------------------- avvio
(async () => {
  cfg = await api("/api/config");
  $("#brand").textContent = cfg.brand;
  $("#brand-sub").textContent = `Newsletter IA quindicinale · ${cfg.editore} · prototipo`;
  document.title = `${cfg.brand} — demo`;
  $("#who").innerHTML = `<option value="">Versione base (nucleo comune)</option>` + cfg.destinatari.map((d) => `<option value="${d.token}">${esc(d.ruolo)} — ${esc(d.organizzazione)}</option>`).join("");
  $("#who").value = cfg.destinatari[0]?.token || "";
  $("#who").onchange = renderNewsletter;
  for (const [sel, i] of [["#cmpA", 0], ["#cmpB", 1], ["#who3", 1]]) {
    $(sel).innerHTML = cfg.destinatari.map((d) => `<option value="${d.token}">${esc(d.ruolo)} — ${esc(d.organizzazione)}</option>`).join("");
    $(sel).value = cfg.destinatari[i]?.token;
  }
  $("#cmpA").onchange = $("#cmpB").onchange = renderCompare;
  $("#who3").onchange = renderStep3;
  $("#m-live").disabled = !cfg.live_disponibile;
  await loadRun("replay");
})();
