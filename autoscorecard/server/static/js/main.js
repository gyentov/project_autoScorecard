// Boot: pick a project, start the office, poll the real event stream, wire the HUD and panels.
import { P, S, TEAM, api, escapeHtml, fileUrl, loadManifest, markdown, sleep, toast } from "./api.js";
import { O, arrival, bossApproach, bossWalkToMeeting, larasWalksToBoss, onEvent, setMeeting, setTalking,
  setTimeline, startOffice } from "./office.js";
import { setupMusic } from "./music.js";
import { runTutorial, stopTutorial, tutorialActive, tutorialDone } from "./tutorial.js";
import { DLG, VN, closeDialog, dialogLines, onWork, openChat, openVN, setupDialog, setupVN,
  vnAfterPlanEdit } from "./vn.js";
import { openWaterfall, setupWaterfall } from "./waterfall.js";

const $ = (id) => document.getElementById(id);
let lastSeq = 0;
let game = null;
let knownMeeting = null;
let arrived = false;            // the arrival at the office front plays once per visit

// --------------------------------------------------------------------------- projects
async function showPicker() {
  const list = await api("/api/projects");
  const box = $("projects");
  box.classList.remove("hidden");
  box.innerHTML = `<div class="picker"><h2>Kantor AutoScorecard</h2>
    <p class="muted">Pilih proyek, atau buat proyek demo dengan data kredit sintetis.</p>
    <div id="plist">${list.length ? "" : '<p class="muted">Belum ada proyek.</p>'}</div>
    <p class="row-new"><input id="new-name" placeholder="Nama proyek baru (data sendiri)">
      <button id="new-own">+ Buat</button></p>
    <p><button id="new-demo" class="primary">+ Proyek demo baru</button></p></div>`;
  const pl = $("plist");
  for (const p of list.slice().reverse()) {
    const row = document.createElement("div");
    row.className = "proj";
    row.innerHTML = `<div><b>${escapeHtml(p.name)}</b><div class="muted">${p.id}</div></div>`;
    const acts = document.createElement("div");
    acts.className = "acts";
    const b = document.createElement("button");
    b.textContent = "Masuk";
    b.onclick = () => openProject(p.id);
    const del = document.createElement("button");
    del.textContent = "🗑";
    del.title = "Hapus proyek (dipindah ke projects/.trash, masih bisa dipulihkan)";
    del.className = "bad";
    del.onclick = async () => {
      if (!confirm(`Hapus proyek "${p.name}"?\n\nFolder dipindah ke projects/.trash/${p.id} ` +
                   "(masih bisa dipulihkan dengan memindahkannya kembali).")) return;
      await api(`/api/projects/${p.id}`, { method: "DELETE" });
      try { if (localStorage.getItem("as_project") === p.id) localStorage.removeItem("as_project"); } catch (e) { /* ignore */ }
      showPicker();
    };
    acts.append(b, del);
    row.appendChild(acts);
    pl.appendChild(row);
  }
  $("new-own").onclick = async () => {
    const name = $("new-name").value.trim();
    if (!name) { $("new-name").focus(); return; }
    $("new-own").disabled = true;
    const p = await api("/api/projects", { method: "POST", body: { name } });
    openProject(p.id);
  };
  $("new-demo").onclick = async () => {
    $("new-demo").disabled = true;
    const p = await api("/api/demo", { method: "POST" });
    openProject(p.id);
  };
}

async function openProject(pid) {
  S.pid = pid;
  try { localStorage.setItem("as_project", pid); } catch (e) { /* private mode */ }
  $("projects").classList.add("hidden");
  const meta = (await api("/api/projects")).find((p) => p.id === pid);
  $("hud-project").textContent = meta ? meta.name : pid;
  const evs = await api(P("/office/events?since=0&limit=2000"));
  lastSeq = evs.length ? evs[evs.length - 1].seq : 0;
  const status = {};
  for (const ev of evs) if (ev.type === "agent_status") status[ev.actor] = ev.data.status;
  if (!game) {
    const layout = await api("/assets/office/layout.json");
    await new Promise((ready) => { game = startOffice(layout, ready); });
  }
  for (const [id, st] of Object.entries(status)) if (O.actors[id]) O.actors[id].status = st;
  await refreshState();
  briefing();
  poll();
}

// --------------------------------------------------------------------------- state + HUD
async function refreshState() {
  const st = await api(P("/office"));
  S.state = st;
  const label = { new: "baru", kickoff: "kickoff", data: "data", target: "target", plan: "rencana",
    cut: "modelling", final: "final" }[st.stage] || st.stage;
  $("hud-stage").textContent = `tahap: ${label}`;
  const c = st.cost || {};
  $("hud-budget").textContent = c.spent_project_usd !== undefined
    ? `💰 $${(+c.spent_project_usd).toFixed(4)} / $${(+c.budget_project_usd).toFixed(2)}` : "";
  const setBadge = (id, n) => { $(id).textContent = n; $(id).classList.toggle("hidden", !n); };
  setBadge("unread", st.unread);
  setBadge("pending", st.pending_proposals.length);
  api(P("/orders")).then((os) => setBadge("open-orders",
    os.filter((o) => o.status !== "selesai").length)).catch(() => {});
  $("btn-start").classList.toggle("hidden", st.stage !== "new");
  setTimeline(st.timeline);
  refreshConvo().catch(console.error);
  if (st.open_meeting) {
    const m = await api(P(`/meetings/${st.open_meeting}`));
    const open = m.status !== "ended";
    setMeeting(open ? { id: m.id, host: m.host, attendees: m.attendees, open } : null);
    $("btn-meeting").classList.toggle("hidden", !open);
    if (open && knownMeeting !== m.id) {
      knownMeeting = m.id;
      toast(`<b>Laras:</b> rapat <b>${escapeHtml(m.title)}</b> siap. Tim menunggu di ruang rapat.`,
        { color: "var(--laras)", ms: 12000, action: { label: "Masuk rapat", run: goToMeeting } });
    }
  } else {
    setMeeting(null);
    $("btn-meeting").classList.add("hidden");
  }
}

let refreshTimer = null;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => refreshState().catch(console.error), 300);
}

async function poll() {
  const pid = S.pid;
  while (S.pid === pid) {
    try {
      const evs = await api(P(`/office/events?since=${lastSeq}`));
      for (const ev of evs) {
        lastSeq = ev.seq;
        onEvent(ev);
        await onWork(ev);                           // live progress while an answer is coming
        if (ev.type === "email_sent") {
          toast(`✉ <b>${TEAM[ev.actor]?.name || ev.actor}:</b> ${escapeHtml(ev.data.subject)}`,
            { color: TEAM[ev.actor]?.color });
        }
        if (ev.type === "proposal") {
          toast(`📝 Usulan menunggu ACC: ${escapeHtml(ev.data.summary)}`, { color: "var(--warn)",
            action: { label: "Lihat usulan", run: showProposals } });
        }
        if (["email_sent", "proposal", "approval", "meeting_scheduled", "meeting_started",
          "meeting_ended", "milestone", "job_finished", "cost_actual", "dialogue",
          "data_received"].includes(ev.type)) refreshSoon();
      }
    } catch (e) { console.warn("poll", e); }
    // fast while someone is working on the Boss's question; wakeable when that starts
    await Promise.race([sleep(S.waiting ? 300 : 1500), new Promise((r) => { S.wakePoll = r; })]);
  }
}

// --------------------------------------------------------------------------- briefing + chat
async function briefing() {
  const lines = await api(P("/office/briefing"));
  if (!arrived) {
    arrived = true;
    await arrival();             // Laras meets the Boss at the door and walks them to their desk
    if (!tutorialDone()) await runTutorial();     // first visit: Laras shows the office
  } else {
    await sleep(600);
    await larasWalksToBoss();
  }
  await dialogLines(lines);
  closeDialog();
  setTalking("laras", false);
  const d = await api(P("/data")).catch(() => null);
  if (d && S.state.stage === "new" && !d.confirmed) {   // Laras confirms the data before kickoff
    openData(d.files.length ? "Sebelum kickoff, tolong konfirmasi dulu file mana yang dipakai, Boss."
      : "Datanya belum ada, Boss. Upload dulu ya, baru kita mulai kickoff.");
  }
}

const GREET = {
  laras: "Ya, Boss? Mau bahas apa? Status, biaya, atau ngobrol santai juga boleh.",
  bening: "Eh, Boss! Ada yang mau ditanyakan soal data?",
  citra: "Boss. Ada yang mau dihitung, atau sekadar mampir?",
  kirana: "Boss! Sini sini, mau ngobrol apa?",
};

async function talkTo(id) {
  if (VN.open || DLG.who) return;
  await bossApproach(id);
  await openChat(id, GREET[id]);
  if (id === "citra") {                       // the modeller's plan, editable by the Boss
    const b = document.createElement("button");
    b.textContent = "▸ 📐 Edit rencana waterfall";
    b.onclick = () => { closeDialog(); openWaterfall(); };
    $("dialog-choices").appendChild(b);
  }
  if (id === "laras" && ["new", "kickoff"].includes(S.state?.stage)) {
    const b = document.createElement("button");
    b.textContent = "▸ 📁 Serahkan data";
    b.onclick = () => { closeDialog(); openData(); };
    $("dialog-choices").appendChild(b);
  }
}

// --------------------------------------------------------------------------- data for Laras
const DATA = { files: [], locked: false };
const kb = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

let kickoffAfterData = false;          // the Boss pressed Mulai kickoff before confirming data

async function openData(lead = "") {
  if (!S.pid) return;
  if (DLG.who && !tutorialActive()) closeDialog();       // the tour stays open behind it
  $("data-laras").innerHTML = lead ? `<b>Laras:</b> ${escapeHtml(lead)}` : "";
  $("data-laras").classList.toggle("hidden", !lead);
  $("datawin").classList.remove("hidden");
  try { await loadData(); } catch (e) {
    $("data-note").textContent = `Daftar data gagal dibaca: ${e.message}. Kalau server dijalankan ` +
      "sebelum pembaruan, hentikan uvicorn (Ctrl+C) lalu jalankan lagi.";
  }
}

async function loadData(justAdded = []) {
  const d = await api(P("/data"));
  const before = Object.fromEntries(DATA.files.map((f) => [f.name, f.checked]));
  DATA.locked = d.locked;
  DATA.files = d.files.map((f) => ({ ...f,
    checked: justAdded.includes(f.name) ? true : (f.name in before ? before[f.name] : f.selected) && !f.error }));
  const box = $("data-list");
  box.innerHTML = DATA.files.length ? "" : '<p class="muted">Belum ada file. Tambahkan di bawah.</p>';
  const defaults = DATA.files.filter((f) => f.default);
  if (defaults.length && !DATA.locked) {              // say plainly what the default data is
    const info = document.createElement("div");
    info.className = "default-info";
    info.innerHTML = `<div><b>Data default</b> = data contoh <b>sintetis</b> dari proyek demo
      (${defaults.length} file), bukan data Boss. Hapus kalau mau memakai data sendiri saja.</div>`;
    const all = document.createElement("button");
    all.textContent = "🗑 Hapus semua data default";
    all.className = "bad";
    all.onclick = () => removeData(defaults.map((f) => f.name));
    info.appendChild(all);
    box.appendChild(info);
  }
  for (const f of DATA.files) {
    const row = document.createElement("label");
    row.className = "dfile" + (f.error ? " bad" : "");
    const shape = f.error ? `tidak terbaca: ${escapeHtml(f.error)}`
      : `${(f.rows ?? 0).toLocaleString("id-ID")} baris × ${f.cols} kolom · ${kb(f.bytes)}`;
    row.innerHTML = `<input type="checkbox" ${f.checked ? "checked" : ""} ${DATA.locked || f.error ? "disabled" : ""}>
      <div><div class="name">${escapeHtml(f.name)}</div><div class="muted">${shape}</div></div>
      <span class="tags">${f.default ? '<span class="tag default" title="Data contoh sintetis dari proyek demo">data default (demo)</span>' : ""}
        <span class="tag ${f.kind}">${f.kind === "kamus" ? "kamus data" : "tabel"}</span>
        ${DATA.locked ? "" : '<button class="ghost del" title="Hapus file ini dari proyek">🗑</button>'}</span>`;
    row.querySelector("input").onchange = (e) => { f.checked = e.target.checked; noteData(); };
    row.querySelector(".del")?.addEventListener("click", (e) => { e.preventDefault(); removeData([f.name]); });
    box.appendChild(row);
  }
  $("data-drop").classList.toggle("hidden", DATA.locked);
  $("data-give").disabled = DATA.locked;
  noteData();
}

async function removeData(files) {
  const what = files.length === 1 ? `"${files[0]}"` : `${files.length} file`;
  if (!confirm(`Hapus ${what} dari proyek ini?\n\nFile dipindah ke folder work/removed proyek ` +
               "(masih bisa dikembalikan), dan tidak dipakai lagi.")) return;
  try {
    await api(P("/data/remove"), { method: "POST", body: { files } });
    DATA.files = DATA.files.filter((f) => !files.includes(f.name));
    await loadData();
  } catch (e) {
    $("data-note").textContent = `Gagal menghapus: ${e.message}`;
  }
}

function noteData() {
  const n = DATA.files.filter((f) => f.checked);
  $("data-note").textContent = DATA.locked
    ? "Data sudah dipakai sejak tahap data, jadi tidak bisa diganti di proyek ini. Untuk data lain, buat proyek baru."
    : `${n.filter((f) => f.kind === "tabel").length} tabel dan ${n.filter((f) => f.kind === "kamus").length} kamus data dipilih.`;
  $("data-give").disabled = DATA.locked || !n.length;
}

function uploadData(fileList) {
  if (!fileList.length || DATA.locked) return;
  const ok = /\.(csv|tsv|txt|xlsx|xls|parquet)$/i;
  const bad = fileList.filter((f) => !ok.test(f.name));
  if (bad.length) {
    $("data-note").textContent = `Jenis file belum didukung: ${bad.map((f) => f.name).join(", ")}. ` +
      "Pakai CSV, TSV, Excel (.xlsx/.xls) atau Parquet; file .zip/.gz diekstrak dulu.";
    return;
  }
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  const total = fileList.reduce((a, f) => a + f.size, 0);
  const xhr = new XMLHttpRequest();                 // XHR, not fetch: it reports progress
  xhr.open("POST", P("/upload"));
  xhr.upload.onprogress = (e) => {
    const pct = e.lengthComputable ? Math.round((e.loaded / e.total) * 100) : 0;
    $("data-note").textContent = `Mengunggah ${kb(total)}… ${pct}%`;
  };
  xhr.onerror = () => { $("data-note").textContent = "Gagal mengunggah: server tidak menjawab. Apakah uvicorn masih jalan?"; };
  xhr.onload = async () => {
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
    if (xhr.status !== 200) {
      $("data-note").textContent = `Gagal mengunggah (${xhr.status}): ${body.detail || xhr.statusText}`;
      return;
    }
    $("data-note").textContent = "Terunggah. Membaca ukuran file…";
    try { await loadData(body.saved || []); } catch (e) {
      $("data-note").textContent = `File sudah tersimpan, tapi daftar data gagal dibaca: ${e.message}. ` +
        "Kalau server dijalankan sebelum pembaruan, hentikan uvicorn (Ctrl+C) lalu jalankan lagi.";
    }
  };
  $("data-note").textContent = `Mengunggah ${kb(total)}… 0%`;
  xhr.send(fd);
}

async function giveData() {
  const files = DATA.files.filter((f) => f.checked).map((f) => f.name);
  $("data-give").disabled = true;
  try {
    await api(P("/data/handover"), { method: "POST", body: { files } });
    $("datawin").classList.add("hidden");
    if (kickoffAfterData && S.state?.stage === "new") {      // what the Boss asked for
      kickoffAfterData = false;
      await api(P("/office/start"), { method: "POST" });
    }
  } catch (e) {
    $("data-note").textContent = `Gagal: ${e.message}`;
    $("data-give").disabled = false;
  }
  refreshSoon();
}

// --------------------------------------------------------------------------- conversation log
// The session started when this tab first opened the office; the corner shows what was
// said and paid since then, the drawer shows everything for the project.
const SESSION = (() => {
  let t = null;
  try { t = sessionStorage.getItem("as_session"); } catch (e) { /* ignore */ }
  if (!t) {
    t = new Date().toISOString().replace("Z", "000+00:00");    // same format as the log
    try { sessionStorage.setItem("as_session", t); } catch (e) { /* ignore */ }
  }
  return t;
})();
const usd = (v) => `$${(+v || 0).toFixed(4)}`;
const NAME = (id) => (id === "boss" ? "Boss" : TEAM[id]?.name || id);

async function refreshConvo() {
  if (!S.pid) return;
  const c = await api(P(`/conversations?since=${encodeURIComponent(SESSION)}`));
  $("convo-total").textContent = usd(c.total_usd);
  const last = c.items.slice(-2);
  $("convo-last").innerHTML = last.length ? last.map((i) =>
    `<div>🧑‍💼 ${escapeHtml(i.boss)}</div><div><b>${NAME(i.agent)}:</b> ${escapeHtml(i.text)}
      <span class="cost">${i.usd ? usd(i.usd) : "$0"}</span></div>`).join("")
    : "Belum ada percakapan di sesi ini.";
  if (!$("drawer").classList.contains("hidden")) renderDrawer();
}

const KIND = { llm: "", small_talk: "sapaan ($0)", command: "perintah ($0)", offline: "offline",
  ask: "tanya biaya dulu", held: "ditahan: anggaran", handover: "serah data" };

async function renderDrawer() {
  const c = await api(P("/conversations"));
  const ses = c.items.filter((i) => i.ts >= SESSION);
  $("drawer-sum").innerHTML = `Sesi ini: <b>${usd(ses.reduce((a, i) => a + (i.usd || 0), 0))}</b>
    (${ses.length} percakapan) · Proyek ini: <b>${usd(c.project_total_usd)}</b> (${c.items.length})`;
  const body = $("drawer-body");
  body.innerHTML = c.items.length ? "" : '<p class="muted">Belum ada percakapan.</p>';
  for (const i of c.items.slice().reverse()) {
    const el = document.createElement("div");
    el.className = "log";
    const where = i.where === "desk" ? "di meja" : "rapat";
    el.innerHTML = `<div class="meta"><span>${i.ts.slice(0, 16).replace("T", " ")} UTC · ${where}
      ${KIND[i.kind] ? " · " + KIND[i.kind] : ""}${i.provider ? " · " + escapeHtml(i.provider) : ""}</span>
      <span class="usd">${i.usd ? usd(i.usd) : "$0"}</span></div>
      <div class="boss"><span class="who">Boss${i.to ? " → " + NAME(i.to) : ""}:</span> ${escapeHtml(i.boss)}</div>
      <div><span class="who" style="color:${TEAM[i.agent]?.color || "inherit"}">${NAME(i.agent)}:</span>
        ${escapeHtml(i.text)}</div>`;
    if (i.has_thought) {                            // audit only, closed by default
      const det = document.createElement("details");
      det.innerHTML = "<summary>🔒 Isi hati (audit, tidak ditampilkan ke Boss di percakapan)</summary><pre>…</pre>";
      det.addEventListener("toggle", async () => {
        if (!det.open || det.dataset.loaded) return;
        const a = await api(P("/conversations?audit=true"));
        det.querySelector("pre").textContent = a.items.find((x) => x.seq === i.seq)?.thought || "";
        det.dataset.loaded = "1";
      });
      el.appendChild(det);
    }
    body.appendChild(el);
  }
}

// --------------------------------------------------------------------------- the Boss's orders
const ST_LABEL = { baru: "baru", dikerjakan: "dikerjakan", selesai: "selesai", terhambat: "terhambat" };

async function showOrders() {
  const os = (await api(P("/orders"))).slice().reverse();
  panel("Perintah Boss", os.length ? "<p class='muted'>Semua yang Boss minta, siapa yang mengerjakan, " +
    "dan statusnya. Yang terhambat dicoba lagi otomatis.</p>" :
    "<p class='muted'>Belum ada perintah. Perintah Boss (mis. “tolong dokumentasikan arti variabel”) " +
    "otomatis tercatat di sini.</p>");
  for (const o of os) {
    const el = document.createElement("div");
    el.className = "order";
    el.innerHTML = `<b>#${o.id}</b> · ${TEAM[o.to]?.name || o.to}${o.via !== o.to ? ` (lewat ${TEAM[o.via]?.name || o.via})` : ""}
      <span class="st ${o.status}">${ST_LABEL[o.status] || o.status}</span>
      <div>${escapeHtml(o.summary)}</div>
      ${o.chain ? `<ol class="relay">${o.chain.map((c, k) => `<li class="${k < (o.step || 0) || o.status === "selesai" ? "done" : k === (o.step || 0) ? "now" : ""}">
        ${TEAM[c.to]?.name || c.to}: ${escapeHtml(c.task)}</li>`).join("")}</ol>` : ""}
      ${o.parent ? `<div class="muted">langkah dari alur #${o.parent}</div>` : ""}
      ${o.notes.length ? `<div class="notes muted">${o.notes.map((n) => escapeHtml(n.note)).join(" · ")}</div>` : ""}`;
    if (o.status !== "selesai") {
      const done = document.createElement("button");
      done.textContent = "Tandai selesai";
      done.className = "ghost";
      done.onclick = async () => { await api(P(`/orders/${o.id}`), { method: "POST", body: { status: "selesai", note: "ditutup Boss" } }); showOrders(); refreshSoon(); };
      el.appendChild(done);
    }
    $("panel-body").appendChild(el);
  }
}

// --------------------------------------------------------------------------- risk levels (VH-VL)
const LV = { cut: null, bands: [], labels: [], proposed: [] };
const LEVELS = ["VH", "H", "M", "L", "VL"];

async function openLevels(cut) {
  const r = await api(P(`/office/risk-levels?cut=${encodeURIComponent(cut)}`));
  LV.cut = cut;
  LV.bands = r.bands;
  LV.labels = r.levels.labels.slice();
  LV.proposed = LV.proposed.length && LV.cut === cut ? LV.proposed : r.levels.labels.slice();
  $("lv-error").textContent = "";
  renderLevels();
  $("lvwin").classList.remove("hidden");
}

function renderLevels() {
  const list = $("lv-list");
  list.innerHTML = `<div class="lv-row head"><span>kelompok</span><span>skor</span><span>% akun</span>
    <span>bad rate</span><span>kategori</span></div>`;
  LV.bands.forEach((b, i) => {
    const row = document.createElement("div");
    row.className = `lv-row lv-${LV.labels[i]}`;
    row.innerHTML = `<span>${b.band}</span><span>${Math.round(b.score_min)}–${Math.round(b.score_max)}</span>
      <span>${(b.pct * 100).toFixed(1)}%</span><span>${(b.bad_rate_train * 100).toFixed(1)}%</span>
      <select>${LEVELS.map((l) => `<option ${l === LV.labels[i] ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    row.querySelector("select").onchange = (e) => { LV.labels[i] = e.target.value; renderLevels(); };
    list.appendChild(row);
  });
  const sum = LEVELS.filter((l) => LV.labels.includes(l)).map((l) => {
    const g = LV.bands.filter((_, i) => LV.labels[i] === l);
    const n = g.reduce((a, b) => a + b.n, 0), bads = g.reduce((a, b) => a + b.bads, 0);
    return `<b>${l}</b> ${(g.reduce((a, b) => a + b.pct, 0) * 100).toFixed(0)}% akun, bad rate ${(bads / n * 100).toFixed(1)}%`;
  });
  $("lv-sum").innerHTML = sum.join(" · ");
}

async function saveLevels() {
  try {
    await api(P("/office/risk-levels"), { method: "PUT", body: { cut: LV.cut, labels: LV.labels } });
    $("lvwin").classList.add("hidden");
    toast("🎚 <b>Citra:</b> kategori risiko disimpan.", { color: "var(--citra)" });
    await vnAfterPlanEdit();
    refreshSoon();
  } catch (e) {
    $("lv-error").textContent = `Belum bisa disimpan: ${e.message}`;
  }
}

// --------------------------------------------------------------------------- the board
const BOARD = { items: [], k: 0 };

async function openBoard(focusFile = null) {
  if (!S.pid) return;
  BOARD.items = await api(P("/board"));
  if (!BOARD.items.length) { toast("Papan masih kosong. Minta tim menunjukkan data, misalnya “sampel 10” atau “bad rate per kolom”."); return; }
  const i = focusFile ? BOARD.items.findIndex((b) => b.file === focusFile) : -1;
  BOARD.k = i >= 0 ? i : BOARD.items.length - 1;
  renderBoard();
  $("boardwin").classList.remove("hidden");
}

function renderBoard() {
  const b = BOARD.items[BOARD.k];
  $("board-title").textContent = b.title;
  $("board-meta").textContent = `· ${TEAM[b.by]?.name || b.by} · ${BOARD.k + 1}/${BOARD.items.length}`;
  $("board-view").src = fileUrl(b.file);
  $("board-prev").disabled = BOARD.k === 0;
  $("board-next").disabled = BOARD.k === BOARD.items.length - 1;
}

function openDrawer() {
  if (!S.pid) return;
  $("drawer").classList.remove("hidden");
  renderDrawer().catch(console.error);
}

// --------------------------------------------------------------------------- meetings
async function goToMeeting() {
  if (!S.state?.open_meeting) return;
  if (DLG.who) closeDialog();
  await bossWalkToMeeting();
}

async function enterMeeting() {
  if (VN.open || !S.state?.open_meeting) return;
  await openVN(S.state.open_meeting);
}

VN.onChange = () => refreshSoon();
VN.onClose = () => {
  refreshSoon();
  const d = O.layout.meeting.door;
  O.boss.walkTo(d.x + 1, d.y, "right");            // step out so the room doesn't reopen
};
DLG.onClose = (who) => { stopTutorial(); if (who) setTalking(who, false); };

// --------------------------------------------------------------------------- panels
function panel(title, html) {
  $("panel-title").textContent = title;
  $("panel-body").innerHTML = html;
  $("panel").classList.remove("hidden");
}

async function showInbox() {
  const mails = (await api(P("/inbox"))).sort((a, b) => (a.sent < b.sent ? 1 : -1));   // newest first
  const icon = { mom: "🗒", memo: "📄", invite: "📅", results: "📊", timeline: "📈", info: "✉" };
  panel("Inbox", mails.length ? "" : '<p class="muted">Belum ada email.</p>');
  for (const m of mails) {
    const row = document.createElement("div");
    row.className = "mail" + (m.read ? "" : " unread");
    row.innerHTML = `<div>${icon[m.kind] || "✉"}</div><div><div class="subj">${escapeHtml(m.subject)}</div>
      <div class="who muted">${TEAM[m.sender]?.name || m.sender} · ${m.sent.replace("T", " ").slice(0, 16)}</div></div>`;
    row.onclick = () => readMail(m.id);
    $("panel-body").appendChild(row);
  }
}

async function readMail(id) {
  const m = await api(P(`/inbox/${id}`));
  const att = m.attachments.length
    ? `<div class="attach"><b>Lampiran:</b><br>${m.attachments.map((a) =>
      `<a href="${fileUrl(a)}" target="_blank" rel="noopener">📎 ${escapeHtml(a.split("/").pop())}</a>`).join("")}</div>` : "";
  panel(m.subject, `<p><button id="mail-back" class="ghost">← Inbox</button>
    <span class="muted">dari ${TEAM[m.sender]?.name || m.sender}</span></p>
    ${m.meeting_id && m.meeting_id === S.state?.open_meeting ? '<p><button id="mail-join" class="primary">Masuk rapat</button></p>' : ""}
    ${markdown(m.body_md)}${att}`);
  $("mail-back").onclick = showInbox;
  if ($("mail-join")) $("mail-join").onclick = () => { $("panel").classList.add("hidden"); goToMeeting(); };
  refreshSoon();
}

async function showProposals() {
  const st = await api(P("/office"));
  const items = st.pending_proposals;
  panel("Usulan menunggu ACC", items.length ? "" :
    '<p class="muted">Tidak ada usulan yang menunggu. Usulan muncul kalau Boss meminta perubahan model.</p>');
  for (const p of items) {
    const el = document.createElement("div");
    el.className = "prop";
    el.innerHTML = `<b>${escapeHtml(p.summary)}</b><div class="muted">oleh ${TEAM[p.by]?.name || p.by}
      ${p.reason ? " · " + escapeHtml(p.reason) : ""}</div><div class="row"></div>`;
    const acc = document.createElement("button");
    acc.textContent = "ACC";
    acc.className = "ok";
    const rej = document.createElement("button");
    rej.textContent = "Tolak";
    rej.className = "bad";
    const decide = async (accept) => {
      await api(P(`/proposals/${p.id}`), { method: "POST", body: { accept } });
      showProposals();
      refreshSoon();
    };
    acc.onclick = () => decide(true);
    rej.onclick = () => decide(false);
    el.querySelector(".row").append(acc, rej);
    $("panel-body").appendChild(el);
  }
}

function showTimeline() {
  panel("Timeline proyek", `<p class="muted">Urutan pekerjaan (tanpa tanggal). Oranye = menunggu Boss.</p>
    <img src="${P("/office/timeline.svg")}?t=${Date.now()}" style="width:100%" alt="timeline">`);
}

function stamp(ok) {
  const el = $("stamp");
  el.querySelector("span").textContent = ok ? "ACC" : "DITOLAK";
  el.classList.toggle("rej", !ok);
  el.classList.remove("hidden");
  el.firstElementChild.style.animation = "none";
  void el.offsetWidth;
  el.firstElementChild.style.animation = "";
  setTimeout(() => el.classList.add("hidden"), 1300);
}

// --------------------------------------------------------------------------- wiring
O.handlers = {
  agentClick: talkTo,
  mailbox: showInbox,
  whiteboard: showTimeline,
  enterMeeting,
  stamp,
  isChattingWith: (id) => DLG.who === id,
  drawer: openDrawer,
};

$("btn-inbox").onclick = showInbox;
$("btn-data").onclick = () => openData();
$("data-close").onclick = $("data-cancel").onclick = () => {
  kickoffAfterData = false;
  $("datawin").classList.add("hidden");
};
$("data-give").onclick = giveData;
$("data-upload").onchange = (e) => { uploadData([...e.target.files]); e.target.value = ""; };
const drop = $("data-drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadData([...e.dataTransfer.files]); });
$("convo").onclick = openDrawer;
$("btn-log").onclick = openDrawer;
$("btn-board").onclick = () => openBoard();
$("btn-waterfall").onclick = () => openWaterfall();
$("btn-orders").onclick = () => showOrders();
VN.onEditLevels = (cut) => openLevels(cut);
$("lv-save").onclick = saveLevels;
$("lv-close").onclick = $("lv-cancel").onclick = () => $("lvwin").classList.add("hidden");
$("lv-reset").onclick = () => { LV.labels = LV.proposed.slice(); renderLevels(); };
VN.onEditWaterfall = () => openWaterfall();
setupWaterfall(async () => { await vnAfterPlanEdit(); refreshSoon(); });
$("board-close").onclick = () => $("boardwin").classList.add("hidden");
$("board-prev").onclick = () => { BOARD.k = Math.max(0, BOARD.k - 1); renderBoard(); };
$("board-next").onclick = () => { BOARD.k = Math.min(BOARD.items.length - 1, BOARD.k + 1); renderBoard(); };
DLG.onBoard = (items) => openBoard(items[items.length - 1].file);
// ribbon popovers (music, help): one open at a time, under their button
function togglePop(btn, pop) {
  const open = $(pop).classList.contains("hidden");
  for (const [b, p] of [["btn-music", "pop-music"], ["btn-help", "pop-help"]]) {
    $(p).classList.add("hidden");
    $(b).classList.remove("open");
  }
  if (!open) return;
  const r = $(btn).getBoundingClientRect();
  $(pop).classList.remove("hidden");
  $(pop).style.left = `${Math.max(8, Math.min(innerWidth - $(pop).offsetWidth - 8, r.right - $(pop).offsetWidth))}px`;
  $(btn).classList.add("open");
}
$("btn-music").onclick = (e) => { e.stopPropagation(); togglePop("btn-music", "pop-music"); };
$("btn-help").onclick = (e) => { e.stopPropagation(); togglePop("btn-help", "pop-help"); };
document.addEventListener("click", (e) => {
  if (!e.target.closest(".pop")) for (const p of ["pop-music", "pop-help"]) $(p).classList.add("hidden");
  if (!e.target.closest(".pop")) for (const b of ["btn-music", "btn-help"]) $(b).classList.remove("open");
});
$("help-tutorial").onclick = async () => {
  $("pop-help").classList.add("hidden");
  if (VN.open || !S.pid) return;
  if (DLG.who) closeDialog();
  await runTutorial();
  closeDialog();
};
$("drawer-close").onclick = () => $("drawer").classList.add("hidden");
$("btn-proposals").onclick = showProposals;
$("btn-timeline").onclick = showTimeline;
$("btn-projects").onclick = () => { S.pid = null; showPicker(); };
$("btn-meeting").onclick = goToMeeting;
$("panel-close").onclick = () => $("panel").classList.add("hidden");
$("btn-start").onclick = async () => {
  $("btn-start").disabled = true;
  let r = {};
  try { r = await api(P("/office/start"), { method: "POST" }); } finally { $("btn-start").disabled = false; }
  if (r.needs_data) {                      // no kickoff before Laras has the confirmed data
    kickoffAfterData = true;
    openData(r.note);
  }
  refreshSoon();
};

window.AS = { O, S };                              // for debugging in the browser console
setupVN();
setupDialog();
setupMusic();
// the page is read fresh from disk but Python only reloads when uvicorn restarts:
// say so plainly instead of failing in odd places
const NEEDS = ["data_handover", "conversations", "adjourn", "trash"];
const health = await api("/api/health").catch(() => ({}));
const missing = NEEDS.filter((f) => !(health.features || []).includes(f));
if (missing.length) {
  toast("⚠ <b>Server masih versi lama.</b> Hentikan uvicorn (Ctrl+C) lalu jalankan lagi, " +
        "supaya fitur baru (data, log, rapat) jalan.", { color: "var(--bad)", ms: 60000 });
}
await loadManifest();
let saved = null;
try { saved = localStorage.getItem("as_project"); } catch (e) { /* private mode */ }
const projects = await api("/api/projects");
if (saved && projects.some((p) => p.id === saved)) openProject(saved);
else showPicker();
