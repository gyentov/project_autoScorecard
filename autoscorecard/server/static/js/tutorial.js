// Laras's first-day tour of the office, right after she seats the Boss. It is a fixed
// script ($0, no LLM), with an arrow pointing at each thing she explains. The text box
// stays open: the Boss can type a question at any step (a normal desk chat with Laras,
// with her usual cost check), then carry on with the tour.
import { S, portrait } from "./api.js";
import { O, screenOf } from "./office.js";
import { DLG, typeInto } from "./vn.js";

const $ = (id) => document.getElementById(id);
const T = 32;

// target: a DOM selector, or a function giving a point on the map (map pixels)
const STEPS = [
  { mood: "happy", map: () => deskPoint(),
    text: "Ini meja Boss, persis di sebelah meja saya. Klik mejanya untuk duduk; lacinya berisi log semua percakapan beserta biayanya." },
  { mood: "neutral", dom: "#btn-data",
    text: "Data diserahkan lewat 📁 Data di ribbon ini. Centang file yang mau dipakai, lalu serahkan ke saya; saya teruskan ke Bening." },
  { mood: "neutral", map: () => { const [x, y] = O.layout.mailbox.tile; return [x * T + 16, y * T - 8]; },
    text: "Email dari tim masuk ke kotak surat merah ini: undangan rapat, notulen (MOM), memo, dan hasil model." },
  { mood: "thinking", map: () => { const [x, , w] = O.layout.whiteboard.tile; return [(x + w / 2) * T, 40]; },
    text: "Whiteboard ini timeline proyek. Titik hijau selesai, biru dikerjakan, oranye menunggu keputusan Boss." },
  { mood: "neutral", map: () => { const d = O.layout.meeting.door; return [d.x * T + 16, d.y * T + 4]; },
    text: "Kalau rapat siap, pintu ruang rapat menyala. Masuk ke ruangannya, atau tekan Masuk rapat di atas." },
  { mood: "serious", dom: "#btn-proposals",
    text: "Perubahan model selalu jadi usulan dulu. ACC atau tolak di sini; tanpa ACC Boss, model tidak berubah." },
  { mood: "happy", map: () => { const a = O.actors.bening; return [a.pos.x, a.pos.y - 60]; },
    text: "Klik siapa saja untuk ngobrol. Sapaan gratis; pertanyaan dijawab AI, dan kalau mahal saya tanya Boss dulu." },
  { mood: "neutral", dom: "#convo",
    text: "Di pojok kiri atas ada biaya sesi ini dan dua percakapan terakhir. Lengkapnya di 🗄 Log." },
  { mood: "happy", dom: "#btn-help",
    text: "Musik ada di 🎵, dan rambu ⚠ Petunjuk berisi semua kontrol, termasuk tombol untuk mengulang tur ini." },
  { mood: "excited", dom: "#btn-start",
    text: "Kalau sudah siap, tekan Mulai kickoff. Ada yang mau ditanyakan? Ketik saja di bawah, Boss." },
];

function deskPoint() {
  const [x, y, w] = O.layout.drawer.tile;
  return [(x + w / 2) * T + 20, y * T + 20];
}

// ---------------------------------------------------------------------- the arrow
let follow = null;
function pointAt(step) {
  cancelAnimationFrame(follow);
  const arrow = $("arrow");
  document.querySelectorAll(".hl").forEach((e) => e.classList.remove("hl"));
  const el = step.dom ? document.querySelector(step.dom) : null;
  if (step.dom && (!el || el.offsetParent === null)) { arrow.classList.add("hidden"); return; }
  if (el) el.classList.add("hl");
  const tick = () => {                                   // follow moving targets and resizes
    let x, y, up = false;
    if (el) {
      const r = el.getBoundingClientRect();
      x = r.left + r.width / 2;
      up = r.top < innerHeight / 3;                      // HUD items: arrow below, pointing up
      y = up ? r.bottom + 6 : r.top - 6;
    } else {
      try {
        const [mx, my] = step.map();
        ({ x, y } = screenOf(mx, my));
      } catch (e) { arrow.classList.add("hidden"); return; }
    }
    arrow.classList.toggle("up", up);
    // keep what she points at visible: low targets move the dialog to the top
    $("dialog").classList.toggle("top", y > innerHeight * 0.55);
    arrow.style.left = `${x}px`;
    arrow.style.top = `${y}px`;
    follow = requestAnimationFrame(tick);
  };
  arrow.classList.remove("hidden");
  tick();
}

function clearPointer() {
  cancelAnimationFrame(follow);
  $("dialog").classList.remove("top");
  $("arrow").classList.add("hidden");
  document.querySelectorAll(".hl").forEach((e) => e.classList.remove("hl"));
}

// ---------------------------------------------------------------------- the tour
// the tour plays the first time each project is opened in this browser
const KEY = () => `as_tour_${S.pid}`;
export function tutorialDone() {
  try { return localStorage.getItem(KEY()) === "1"; } catch (e) { return false; }
}
export const tutorialActive = () => active !== null;

let active = null;                          // finish() of the running tour

export function stopTutorial() { active?.(); }   // the Boss closed the dialog mid-tour

export function runTutorial() {
  return new Promise((done) => {
    let k = 0;
    const finish = () => {
      if (active !== finish) return;
      active = null;
      DLG.advance = null;
      $("dialog-choices").innerHTML = "";        // no stale tour buttons in the next dialog
      clearPointer();
      DLG.afterReply = null;
      try { localStorage.setItem(KEY(), "1"); } catch (e) { /* ignore */ }
      done();
    };
    const goNext = () => {
      if (active !== finish) return;            // a leftover click after the tour ended
      k += 1;
      k < STEPS.length ? show() : finish();
    };
    const buttons = () => {
      if (active !== finish) return;
      const box = $("dialog-choices");
      box.innerHTML = "";
      DLG.advance = goNext;                  // clicking the box, Enter or Space also continue
      const next = document.createElement("button");
      next.textContent = k < STEPS.length - 1 ? `▸ Lanjut (${k + 1}/${STEPS.length})` : "▸ Selesai";
      next.onclick = goNext;
      box.appendChild(next);
      if (k < STEPS.length - 1) {
        const skip = document.createElement("button");
        skip.textContent = "▸ Lewati tur";
        skip.onclick = finish;
        box.appendChild(skip);
      }
    };
    const show = async () => {
      if (active !== finish) return;
      const st = STEPS[k];
      if (st.dom === "#btn-start" && S.state?.stage !== "new") {   // already started: skip it
        k += 1;
        if (k >= STEPS.length) { finish(); return; }
        return show();
      }
      DLG.who = "laras";
      $("dialog").classList.remove("hidden");
      $("dialog-form").classList.remove("hidden");
      $("dialog-portrait").src = portrait("laras", st.mood === "happy" ? "explaining" : "neutral", st.mood);
      $("dialog-name").textContent = "Laras";
      $("dialog-name").style.background = "var(--laras)";
      try { pointAt(st); } catch (e) { clearPointer(); }    // never let the arrow block the tour
      $("dialog-choices").innerHTML = "";
      DLG.advance = null;
      await typeInto($("dialog-text"), st.text);
      buttons();
    };
    active = finish;
    DLG.afterReply = buttons;              // after a typed question, offer to carry on
    show();
  });
}
