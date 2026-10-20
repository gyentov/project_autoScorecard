// Meetings as a visual-novel scene (standing art + projector + choices + free typing),
// and the smaller desk dialog used for the briefing and candid chats.
import { P, S, TEAM, api, escapeHtml, fileUrl, portrait, poseFor, sleep, toast } from "./api.js";

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------- typewriter
let typing = null;
export function typeInto(el, text) {
  return new Promise((done) => {
    if (typing) typing.finish();
    let i = 0;
    el.textContent = "";
    const t = setInterval(() => {
      i += 2;
      el.textContent = text.slice(0, i);
      if (i >= text.length) finish();
    }, 18);
    const finish = () => { clearInterval(t); el.textContent = text; typing = null; done(); };
    typing = { finish };
  });
}

function nametag(el, who) {
  const t = TEAM[who] || { name: who, color: "var(--acc)" };
  el.textContent = t.name;
  el.style.background = t.color;
}

// --------------------------------------------------------------------------- while she works
// The moment the Boss asks, the one who answers says something in character and the
// real steps she takes appear under it ("▸ menghitung bad rate per cust_age"); anything
// she puts on the board shows at once. Never just "…".
const SPIN = ["◐", "◓", "◑", "◒"];

async function startWaiting(text, where, opts) {
  const who = await api(P("/office/route"), { method: "POST", body: { text, ...opts } }).catch(() => null);
  const W = { agent: who?.agent || opts.to || "laras", line: who?.line || "Sebentar, Boss, saya kerjakan.",
    where, steps: [], since: Date.now(), k: 0 };
  S.waiting = W;
  S.wakePoll?.();                                  // poll the event stream fast from now on
  if (where === "vn") {
    showChar(W.agent, "tablet", "thinking");
    nametag($("vn-name"), W.agent);
    $("vn-next").classList.add("hidden");
  } else {
    $("dialog-portrait").src = portrait(W.agent, "tablet", "thinking");
    nametag($("dialog-name"), W.agent);
  }
  const el = where === "vn" ? $("vn-text") : $("dialog-text");
  const render = () => {
    if (S.waiting !== W) return;
    const secs = (Date.now() - W.since) / 1000;
    const steps = W.steps.map((s) => `▸ ${s}`);
    const tail = secs > 8 && !W.steps.length ? "\nMasih saya kerjakan, Boss, sebentar lagi." : "";
    el.textContent = `${W.line}\n${steps.join("\n")}${steps.length ? " " : ""}${SPIN[W.k++ % 4]}${tail}`;
  };
  W.timer = setInterval(render, 250);
  render();
  return W;
}

function stopWaiting(W) {
  clearInterval(W?.timer);
  if (S.waiting === W) S.waiting = null;
}

// called by the event poller for every event while an answer is being worked on
export async function onWork(ev) {
  const W = S.waiting;
  if (!W) return;
  const d = ev.data || {};
  if (ev.type === "agent_status" && ev.actor === W.agent && d.status === "working"
      && d.task && d.task !== "menjawab Boss" && !W.steps.includes(d.task)) {
    W.steps.push(d.task);
  } else if (ev.type === "board") {                // show it NOW, before she finishes talking
    if (W.where === "vn" && VN.open && d.meeting_id === VN.meeting.id && d.board !== null
        && d.board !== undefined) {
      await refresh();
      showBoard(d.board);
    } else if (W.where === "desk" && !d.meeting_id) {
      DLG.onBoard?.([{ file: d.file }]);
    }
  }
}

// --------------------------------------------------------------------------- VN meeting
export const VN = { open: false, meeting: null, queue: [], onClose: null, busy: false };

// Everyone at the meeting stands on the stage the whole time, each on a fixed side:
// the host on the left, the others alternating right, left, right. On each side the
// latest speaker stands in front; the other steps back, smaller and dimmed.
const cast = {};                                  // who -> {img, side}
const front = { left: null, right: null };

function setupCast(m) {
  const box = $("vn-cast");
  box.innerHTML = "";
  for (const k of Object.keys(cast)) delete cast[k];
  const others = m.attendees.filter((a) => a !== m.host && a !== "boss");
  const order = [m.host, ...others].filter((a) => TEAM[a]);
  order.forEach((who, k) => {
    const side = k % 2 === 0 ? "left" : "right";
    const img = document.createElement("img");
    img.className = `vn-char ${side} dim`;
    img.alt = TEAM[who].name;
    img.src = portrait(who, "neutral", "neutral");
    box.appendChild(img);
    cast[who] = { img, side };
  });
  front.left = order[0] || null;
  front.right = order[1] || null;
  layoutCast(null);
}

function layoutCast(speaker) {
  for (const [who, c] of Object.entries(cast)) {
    const isFront = front[c.side] === who;
    c.img.classList.toggle("front", isFront);
    c.img.classList.toggle("back", !isFront);
    c.img.classList.toggle("dim", who !== speaker);
  }
}

function showChar(who, pose, mood) {
  const c = cast[who];
  if (!c) return;
  front[c.side] = who;
  const src = portrait(who, pose, mood);
  if (!c.img.src.endsWith(src)) c.img.src = src;
  c.img.classList.toggle("mirror", c.side === "left" && pose === "presenting");   // point at the screen
  layoutCast(who);
  c.img.classList.remove("talk");
  void c.img.offsetWidth;                          // restart the bob animation
  c.img.classList.add("talk");
}

// The projector has two tabs: the prepared deck, and the board (what the Boss asked to
// see during the meeting). Board items never go into the deck.
function render(item, label, force) {
  const url = fileUrl(item.file) + (force ? `?v=${Date.now()}` : "");  // rebuilt slides reload
  $("vn-slide-title").textContent = label;
  $("vn-slide").innerHTML = item.file.endsWith(".svg")
    ? `<img src="${url}" alt="${escapeHtml(item.title)}">`
    : `<iframe src="${url}" title="${escapeHtml(item.title)}"></iframe>`;
}

function setTab(tab) {
  VN.tab = tab;
  $("vn-tab-deck").classList.toggle("on", tab === "deck");
  $("vn-tab-board").classList.toggle("on", tab === "board");
  if (tab === "board") $("vn-tab-board").classList.remove("fresh");
  const n = (VN.meeting?.board || []).length;
  $("vn-tab-board").textContent = n ? `📋 Papan (${n})` : "📋 Papan";
}

function showSlide(k, force = false) {
  const s = VN.meeting.slides[k];
  if (!s || (VN.tab === "deck" && VN.slide === k && !force)) return;
  VN.slide = k;
  setTab("deck");
  render(s, `${k + 1}/${VN.meeting.slides.length} · ${s.title}`, force);
}

function showBoard(k, force = false) {
  const b = (VN.meeting.board || [])[k];
  if (!b) return;
  VN.boardK = k;
  setTab("board");
  render(b, `Papan ${k + 1}/${VN.meeting.board.length} · ${b.title}`, force);
}

function step(d) {                               // ‹ › within the tab on screen
  if (VN.tab === "board") showBoard(Math.max(0, Math.min((VN.meeting.board || []).length - 1, (VN.boardK ?? 0) + d)));
  else showSlide(Math.max(0, Math.min(VN.meeting.slides.length - 1, (VN.slide ?? 0) + d)));
}

async function playLine(ln) {
  if (ln.board !== null && ln.board !== undefined) showBoard(ln.board);
  else if (ln.slide !== null && ln.slide !== undefined) showSlide(ln.slide);
  nametag($("vn-name"), ln.speaker);
  if (ln.speaker === "boss") {
    layoutCast(null);
  } else {
    showChar(ln.speaker, ln.pose || poseFor(ln.mood, ln.slide), ln.mood || "neutral");
  }
  $("vn-next").classList.remove("hidden");
  await typeInto($("vn-text"), ln.text);
}

// click the box to advance; resolves when the Boss has seen the line
function waitClick() {
  return new Promise((res) => { VN.advance = res; });
}

async function playAll(lines) {
  VN.busy = true;
  $("vn-choices").innerHTML = "";
  $("vn-form").classList.add("hidden");           // the Boss speaks when it is their turn
  for (const ln of lines) {
    await playLine(ln);
    await waitClick();
  }
  VN.busy = false;
  $("vn-next").classList.add("hidden");
}

function renderChoices() {
  const box = $("vn-choices");
  box.innerHTML = "";
  $("vn-form").classList.toggle("hidden", VN.meeting.status === "ended");
  if (VN.meeting.status === "ended") return;
  for (const c of VN.meeting.choices) {
    const b = document.createElement("button");
    b.textContent = "▸ " + c.label;
    b.onclick = (e) => { e.stopPropagation(); choose(c); };
    box.appendChild(b);
  }
}

async function refresh() {
  VN.meeting = await api(P(`/meetings/${VN.meeting.id}`));
}

// after the Boss edited the plan in the editor: reload the slides, play Citra's reply
export async function vnAfterPlanEdit() {
  if (!VN.open) return;
  await refresh();
  const last = VN.meeting.transcript[VN.meeting.transcript.length - 1];
  if (last && last.who !== "boss") {
    await playAll([{ speaker: last.who, mood: last.mood, text: last.text, slide: last.slide,
      board: last.board }]);
  }
  if (last?.board !== null && last?.board !== undefined) showBoard(last.board, true);
  else if (last?.slide !== null && last?.slide !== undefined) showSlide(last.slide, true);
  renderChoices();
}

async function choose(c) {
  if (c.action === "edit_waterfall") { VN.onEditWaterfall?.(); return; }   // opens the editor
  if (c.action === "edit_levels") { VN.onEditLevels?.(VN.meeting.cut); return; }
  $("vn-choices").innerHTML = "";
  await playAll([{ speaker: "boss", text: c.label }]);
  let res;
  try {
    res = await api(P(`/meetings/${VN.meeting.id}/choose/${c.id}`), { method: "POST" });
  } catch (e) {
    await playAll([{ speaker: "laras", mood: "apologetic", text: `Maaf Boss, belum bisa: ${e.message}` }]);
    renderChoices();
    return;
  }
  if (res.note) await playAll([{ speaker: "laras", mood: "worried", text: res.note }]);
  await refresh();
  VN.onChange?.();
  if (VN.meeting.status === "ended") {
    await playAll([{ speaker: "laras", mood: "happy",
      text: "Terima kasih, Boss. MOM-nya saya kirim lewat email, tim lanjut kerja." }]);
    closeVN();
    return;
  }
  renderChoices();
}

async function sendText(text, costOk = false) {
  if (!costOk) await playAll([{ speaker: "boss", text }]);
  const W = await startWaiting(text, "vn", { meeting_id: VN.meeting.id });
  let r;
  try {
    r = await api(P("/office/say"), { method: "POST",
      body: { text, meeting_id: VN.meeting.id, cost_ok: costOk } });
  } catch (e) {
    stopWaiting(W);
    await playAll([{ speaker: "laras", mood: "apologetic", text: `Maaf Boss, error: ${e.message}` }]);
    return;
  }
  stopWaiting(W);
  if (r.board?.length) await refresh();             // the new slides are on the projector
  const line = (x) => ({ speaker: x.agent, mood: x.mood, text: x.text,
    board: x.board?.length ? x.board[x.board.length - 1].board : null });
  await playAll([line(r), ...(r.more || []).map(line)]);   // everyone's turn, in order
  if (r.needs_cost_ok) {                    // Laras asks before paid work above the limit
    const box = $("vn-choices");
    box.innerHTML = "";
    const yes = document.createElement("button");
    yes.textContent = `▸ Ya, lanjut (maks ~$${r.estimate.high_usd.toFixed(4)})`;
    yes.onclick = (e) => { e.stopPropagation(); sendText(text, true); };
    const no = document.createElement("button");
    no.textContent = "▸ Tidak usah";
    no.onclick = (e) => { e.stopPropagation(); renderChoices(); };
    box.append(yes, no);
    return;
  }
  const usd = [r, ...(r.more || [])].reduce((t, x) => t + ((x.actual && x.actual.actual) || 0), 0);
  if (usd > 0) toast(`Laras: biaya jawaban $${usd.toFixed(4)}`);
  await refresh();
  VN.onChange?.();
  renderChoices();
}

export async function openVN(meetingId) {
  if (VN.open) return;
  VN.open = true;
  VN.slide = null;
  VN.boardK = null;
  VN.meeting = await api(P(`/meetings/${meetingId}`));
  setTab("deck");
  const m = VN.meeting;
  $("vn").classList.remove("hidden");
  $("vn-title").textContent = `Rapat: ${m.title}`;
  setupCast(m);
  $("vn-slide").innerHTML = "";
  $("vn-choices").innerHTML = "";
  const history = m.transcript.map((t) => ({ speaker: t.who, mood: t.mood, text: t.text, slide: t.slide,
    board: t.board }));
  await playAll([...m.script, ...history]);
  renderChoices();
}

export function closeVN() {
  VN.open = false;
  if (typing) typing.finish();
  VN.advance?.();
  $("vn").classList.add("hidden");
  VN.onClose?.();
}

export function setupVN() {
  $("vn-tab-deck").onclick = (e) => { e.stopPropagation(); showSlide(VN.slide ?? 0, true); };
  $("vn-tab-board").onclick = (e) => {
    e.stopPropagation();
    const n = (VN.meeting?.board || []).length;
    if (n) showBoard(VN.boardK ?? n - 1, true);
  };
  $("vn-prev").onclick = (e) => { e.stopPropagation(); step(-1); };
  $("vn-next-slide").onclick = (e) => { e.stopPropagation(); step(1); };
  document.querySelector(".vn-box").addEventListener("click", (e) => {
    if (e.target.closest("form") || e.target.closest(".choices")) return;
    if (typing) { typing.finish(); return; }
    const adv = VN.advance;
    VN.advance = null;
    adv?.();
  });
  document.addEventListener("keydown", (e) => {
    if (!VN.open || ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      if (typing) { typing.finish(); return; }
      const adv = VN.advance;
      VN.advance = null;
      adv?.();
    }
  });
  sendOnlyWithText("vn-form", "vn-input");
  $("vn-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("vn-input").value.trim();
    if (!t || VN.busy) return;
    $("vn-input").value = "";
    $("vn-input").dispatchEvent(new Event("input"));
    sendText(t);
  });
  $("vn-close").onclick = closeVN;
}

// --------------------------------------------------------------------------- desk dialog
export const DLG = { who: null, onClose: null, afterReply: null, onBoard: null };   // afterReply: the tutorial

export async function dialogLines(lines) {        // briefing: several lines, click to continue
  $("dialog").classList.remove("hidden");
  $("dialog-form").classList.add("hidden");
  $("dialog-choices").innerHTML = "";
  for (const ln of lines) {
    DLG.who = ln.speaker;
    $("dialog-portrait").src = portrait(ln.speaker, ln.pose || poseFor(ln.mood), ln.mood);
    nametag($("dialog-name"), ln.speaker);
    await typeInto($("dialog-text"), ln.text);
    await new Promise((res) => { DLG.advance = res; });
  }
}

// the send button is usable only when there is something to send
function sendOnlyWithText(formId, inputId) {
  const btn = $(formId).querySelector("button:not([type=button])");
  const sync = () => { btn.disabled = !$(inputId).value.trim(); };
  $(inputId).addEventListener("input", sync);
  sync();
}

export async function openChat(who, greeting) {
  DLG.who = who;
  $("dialog").classList.remove("hidden");
  $("dialog-form").classList.remove("hidden");
  $("dialog-choices").innerHTML = "";
  $("dialog-portrait").src = portrait(who, "neutral", "happy");
  nametag($("dialog-name"), who);
  await typeInto($("dialog-text"), greeting);
  $("dialog-input").focus();
}

export function closeDialog() {
  if (typing) typing.finish();
  DLG.advance?.();
  $("dialog").classList.add("hidden");
  const who = DLG.who;
  DLG.who = null;
  DLG.onClose?.(who);
}

async function chatSend(text, costOk = false) {
  const who = DLG.who;
  $("dialog-choices").innerHTML = "";
  const W = await startWaiting(text, "desk", { to: who, candid: true });
  let r;
  try {
    r = await api(P("/office/say"), { method: "POST", body: { text, to: who, candid: true, cost_ok: costOk } });
  } catch (e) {
    stopWaiting(W);
    $("dialog-text").textContent = `(error: ${e.message})`;
    DLG.afterReply?.();
    return;
  }
  stopWaiting(W);
  if (DLG.who !== who) return;
  $("dialog-portrait").src = portrait(r.agent, r.board?.length ? "presenting" : poseFor(r.mood), r.mood);
  nametag($("dialog-name"), r.agent);
  if (r.board?.length) DLG.onBoard?.(r.board);     // data goes on the board, not in the chat
  await typeInto($("dialog-text"), r.text);
  if (r.needs_cost_ok) {
    const yes = document.createElement("button");
    yes.textContent = `▸ Ya, lanjut (maks ~$${r.estimate.high_usd.toFixed(4)})`;
    yes.onclick = () => chatSend(text, true);
    const no = document.createElement("button");
    no.textContent = "▸ Tidak usah";
    no.onclick = () => { $("dialog-choices").innerHTML = ""; DLG.afterReply?.(); };
    $("dialog-choices").append(yes, no);
    return;
  }
  if (r.actual && r.actual.actual > 0) toast(`Laras: obrolan tadi $${r.actual.actual.toFixed(4)}`);
  DLG.afterReply?.();
}

export function setupDialog() {
  document.querySelector(".dialog-box").addEventListener("click", (e) => {
    if (e.target.closest("form") || e.target.closest(".choices")) return;
    if (typing) { typing.finish(); return; }
    const adv = DLG.advance;
    DLG.advance = null;
    adv?.();
  });
  sendOnlyWithText("dialog-form", "dialog-input");
  $("dialog-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("dialog-input").value.trim();
    if (!t) return;
    $("dialog-input").value = "";
    $("dialog-input").dispatchEvent(new Event("input"));
    chatSend(t);
  });
  $("dialog-close").onclick = closeDialog;
  document.addEventListener("keydown", (e) => {
    if ($("dialog").classList.contains("hidden") || VN.open) return;
    if (e.key === "Escape") closeDialog();
    else if ((e.key === "Enter" || e.key === " ") && document.activeElement?.tagName !== "INPUT") {
      e.preventDefault();
      if (typing) { typing.finish(); return; }
      const adv = DLG.advance;
      DLG.advance = null;
      adv?.();
    }
  });
}

export { sleep };
