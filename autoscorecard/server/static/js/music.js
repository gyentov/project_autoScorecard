// Background music, made live in the browser with WebAudio (no files, no cost):
// a soft pad on a four-chord loop, a walking bass, a filtered arpeggio, a bell melody
// that is composed as a 4-bar phrase and repeated, and light hats. Three moods, plus
// "your own music file" played from the computer. Settings stay in this browser.
const $ = (id) => document.getElementById(id);

// chords as MIDI notes; scale = the notes the melody may use
const TRACKS = {
  pagi: { name: "Pagi di kantor", bpm: 96, arp: "triangle", bell: 0.9, hats: true,
    chords: [[65, 69, 72, 76], [64, 67, 71, 74], [62, 65, 69, 72], [60, 64, 67, 71]],
    scale: [72, 74, 76, 79, 81, 84] },
  fokus: { name: "Fokus kerja", bpm: 74, arp: "sine", bell: 0.55, hats: false,
    chords: [[57, 60, 64, 67], [53, 57, 60, 64], [60, 64, 67, 71], [55, 59, 62, 65]],
    scale: [69, 72, 74, 76, 79] },
  lembur: { name: "Lembur malam (lo-fi)", bpm: 80, arp: "square", bell: 0.7, hats: true, lofi: true,
    chords: [[62, 65, 69, 72, 76], [55, 59, 62, 65, 69], [60, 64, 67, 71, 74], [57, 60, 64, 67, 71]],
    scale: [69, 72, 74, 76, 77, 81] },
};
const OWN = "own";

const M = { on: true, track: "pagi", vol: 0.35, ctx: null, master: null, timer: null,
  step: 0, next: 0, phrase: [], audio: null, ownUrl: null, crackle: null };

const hz = (n) => 440 * Math.pow(2, (n - 69) / 12);

function load() {
  try {
    const s = JSON.parse(localStorage.getItem("as_music") || "{}");
    if (typeof s.on === "boolean") M.on = s.on;
    if (TRACKS[s.track]) M.track = s.track;
    if (typeof s.vol === "number") M.vol = s.vol;
  } catch (e) { /* private mode: defaults */ }
}

function save() {
  try {
    localStorage.setItem("as_music", JSON.stringify({ on: M.on, track: M.track === OWN ? "pagi" : M.track, vol: M.vol }));
  } catch (e) { /* ignore */ }
}

// --------------------------------------------------------------------------- synth voices
function voice(type, freq, t, dur, gain, { attack = 0.01, filter = null, dest = M.master } = {}) {
  const c = M.ctx;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(gain, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  let node = o;
  if (filter) {
    const f = c.createBiquadFilter();
    f.type = "lowpass";
    f.frequency.value = filter;
    o.connect(f);
    node = f;
  }
  node.connect(g).connect(dest);
  o.start(t);
  o.stop(t + dur + 0.05);
}

function hat(t, gain) {
  const c = M.ctx;
  const len = Math.floor(c.sampleRate * 0.04);
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
  const s = c.createBufferSource();
  s.buffer = buf;
  const f = c.createBiquadFilter();
  f.type = "highpass";
  f.frequency.value = 7000;
  const g = c.createGain();
  g.gain.value = gain;
  s.connect(f).connect(g).connect(M.master);
  s.start(t);
}

function startCrackle() {                         // vinyl noise for the lo-fi mood
  const c = M.ctx;
  const len = c.sampleRate * 2;
  const buf = c.createBuffer(1, len, c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() < 0.0009 ? (Math.random() * 2 - 1) * 0.6 : (Math.random() * 2 - 1) * 0.012;
  const s = c.createBufferSource();
  s.buffer = buf;
  s.loop = true;
  const g = c.createGain();
  g.gain.value = 0.5;
  s.connect(g).connect(M.master);
  s.start();
  M.crackle = s;
}

// a 4-bar melody: 32 eighth-note slots, about a third of them sounding
function compose(tr) {
  const p = [];
  let k = Math.floor(Math.random() * tr.scale.length);
  for (let i = 0; i < 32; i++) {
    const strong = i % 4 === 0;
    if (Math.random() < (strong ? 0.55 : 0.22)) {
      k = Math.max(0, Math.min(tr.scale.length - 1, k + [-2, -1, -1, 0, 1, 1, 2][Math.floor(Math.random() * 7)]));
      p.push(tr.scale[k]);
    } else p.push(null);
  }
  return p;
}

function schedule() {
  const tr = TRACKS[M.track];
  const c = M.ctx;
  const eighth = 60 / tr.bpm / 2;
  while (M.next < c.currentTime + 0.25) {
    const t = M.next, s = M.step;
    const bar = Math.floor(s / 8) % 4, pos = s % 8;
    const chord = tr.chords[bar];
    if (s % 128 === 0) M.phrase = compose(tr);             // a new tune every 16 bars
    if (pos === 0) {
      for (const n of chord) voice("sine", hz(n - 12), t, eighth * 8.5, 0.035, { attack: 0.3 });
    }
    if (pos === 0 || pos === 4) voice("triangle", hz(chord[0] - 24), t, eighth * 3.5, 0.12, { attack: 0.02 });
    if (pos === 6) voice("triangle", hz(chord[2] - 24), t, eighth * 1.6, 0.07);
    const arp = chord[[0, 1, 2, 3, 2, 1, 2, 3][pos] % chord.length];
    voice(tr.arp, hz(arp), t, eighth * 0.9, tr.arp === "square" ? 0.018 : 0.03, { filter: 1800 });
    const mel = M.phrase[s % 32];
    if (mel) voice("sine", hz(mel), t, eighth * 3, 0.06 * tr.bell, { attack: 0.005 });
    if (tr.hats && pos % 2 === 1) hat(t, 0.025);
    M.next += eighth * (tr.lofi && pos % 2 === 0 ? 1.08 : tr.lofi ? 0.92 : 1);   // lo-fi swing
    M.step++;
  }
}

// --------------------------------------------------------------------------- play / stop
function ensureCtx() {
  if (M.ctx) return;
  M.ctx = new (window.AudioContext || window.webkitAudioContext)();
  M.master = M.ctx.createGain();
  const warm = M.ctx.createBiquadFilter();               // a gentle overall low-pass
  warm.type = "lowpass";
  warm.frequency.value = 5200;
  M.master.connect(warm).connect(M.ctx.destination);
  M.master.gain.value = M.vol;
}

function stopAll() {
  clearInterval(M.timer);
  M.timer = null;
  if (M.crackle) { M.crackle.stop(); M.crackle = null; }
  if (M.audio) M.audio.pause();
  if (M.master) M.master.gain.setTargetAtTime(0, M.ctx.currentTime, 0.1);
}

function play() {
  stopAll();
  if (M.track === OWN) {
    if (!M.audio) return;
    M.audio.volume = M.vol;
    M.audio.play().catch(() => {});
    return;
  }
  ensureCtx();
  M.ctx.resume();
  M.master.gain.cancelScheduledValues(M.ctx.currentTime);
  M.master.gain.setTargetAtTime(M.vol, M.ctx.currentTime, 0.2);
  M.step = 0;
  M.next = M.ctx.currentTime + 0.1;
  if (TRACKS[M.track].lofi) startCrackle();
  M.timer = setInterval(schedule, 90);
  schedule();
}

function render() {
  const b = $("music-toggle");
  b.classList.toggle("on", M.on);
  b.querySelector("span").textContent = M.on ? "ON" : "OFF";
  $("btn-music").classList.toggle("on", M.on);                 // the ribbon shows it too
  $("music-label").textContent = M.on ? "Musik ON" : "Musik";
  $("music-track").value = M.track;
  $("music-vol").value = Math.round(M.vol * 100);
}

export function setupMusic() {
  load();
  const sel = $("music-track");
  for (const [id, t] of Object.entries(TRACKS)) sel.add(new Option(t.name, id));
  sel.add(new Option("🎧 Musik sendiri…", OWN));
  render();
  $("music-toggle").onclick = () => {
    const playing = M.timer || (M.audio && !M.audio.paused);
    if (M.on && !playing) { play(); return; }         // on, but waiting for a first click
    M.on = !M.on;
    if (M.on) play(); else stopAll();
    save();
    render();
  };
  sel.onchange = () => {
    if (sel.value === OWN) { $("music-file").click(); sel.value = M.track; return; }
    M.track = sel.value;
    save();
    if (M.on) play();
  };
  $("music-file").onchange = (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (M.ownUrl) URL.revokeObjectURL(M.ownUrl);
    M.ownUrl = URL.createObjectURL(f);
    M.audio = new Audio(M.ownUrl);
    M.audio.loop = true;
    const own = sel.querySelector(`option[value="${OWN}"]`);
    own.textContent = "🎧 " + f.name.replace(/\.[^.]+$/, "").slice(0, 28);
    M.track = OWN;
    M.on = true;
    render();
    play();
  };
  $("music-vol").oninput = (e) => {
    M.vol = e.target.value / 100;
    if (M.master && M.track !== OWN) M.master.gain.setTargetAtTime(M.on ? M.vol : 0, M.ctx.currentTime, 0.05);
    if (M.audio) M.audio.volume = M.vol;
    save();
  };
  // browsers only allow sound after a click or key press: start then if it was left on
  const first = (e) => {
    if (e.target.closest?.("#pop-music")) return;      // the controls handle themselves
    window.removeEventListener("pointerdown", first, true);
    window.removeEventListener("keydown", first, true);
    if (M.on && !M.timer && !(M.audio && !M.audio.paused)) play();
  };
  window.addEventListener("pointerdown", first, true);
  window.addEventListener("keydown", first, true);
}
