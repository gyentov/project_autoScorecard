// Page boot for the browser build: install the service worker, start Python in a Web
// Worker, pass /api requests between them, then load the office (js/main.js) unchanged.

const overlay = document.createElement("div");
overlay.id = "boot";
overlay.innerHTML = `<div class="boot-card"><h1>AutoScorecard</h1>
  <p id="boot-text">Menyiapkan kantor...</p>
  <p class="boot-note">Pertama kali butuh 30–60 detik (mengunduh Python ke browser); berikutnya dari cache.
  Data proyek tersimpan di browser ini, tidak dikirim ke server.</p></div>`;
const style = document.createElement("style");
style.textContent = `#boot{position:fixed;inset:0;z-index:9999;display:grid;place-items:center;
  background:#1d1f24;color:#f3efe6;font:16px/1.5 ui-sans-serif,system-ui,sans-serif;padding:16px}
  #boot .boot-card{max-width:440px;text-align:center} #boot h1{font-size:26px;margin:0 0 8px}
  #boot .boot-note{color:#b9b3a6;font-size:13px} #boot.failed #boot-text{color:#ffb4a2}`;
document.head.appendChild(style);
document.body.appendChild(overlay);
const say = (t) => { document.getElementById("boot-text").textContent = t; };

async function controlled() {
  if (!("serviceWorker" in navigator)) throw new Error("browser ini tidak mendukung service worker");
  await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  if (navigator.serviceWorker.controller) return;
  await new Promise((resolve) => navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }));
}

async function main() {
  say("Memasang service worker...");
  await controlled();
  // projects live in IndexedDB; without this the browser may evict them when space runs low
  try { await navigator.storage?.persist?.(); } catch { /* best effort */ }
  const python = new Worker("/web/py-worker.js", { type: "module" });
  const waiting = new Map();
  let next = 0;
  let readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });
  python.onmessage = (m) => {
    const d = m.data;
    if (d.type === "status") say(d.text);
    else if (d.type === "ready") readyResolve(d);
    else if (d.type === "failed") readyReject(new Error(d.text));
    else if (d.type === "response") { waiting.get(d.id)?.(d.res); waiting.delete(d.id); }
  };
  navigator.serviceWorker.addEventListener("message", async (m) => {
    if (m.data?.type !== "api") return;
    await ready;
    const id = next++;
    const { type, ...req } = m.data;
    waiting.set(id, (res) => m.ports[0].postMessage(res, [res.body]));
    python.postMessage({ id, req }, req.body ? [req.body] : []);
  });
  navigator.serviceWorker.startMessages();
  const info = await ready;
  window.AUTOSCORECARD_WEB = info;          // version and LLM status, for debugging
  overlay.remove();
  if (info.llm?.note) {                     // say it on the page: "offline" is not always "quota used up"
    console.warn(info.llm.note);
    const bar = document.createElement("div");
    bar.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:9998;padding:8px 16px;"
      + "background:#5a2d22;color:#ffe9e1;font:13px/1.4 ui-sans-serif,system-ui,sans-serif;cursor:pointer";
    bar.title = "Klik untuk menutup";
    bar.textContent = info.llm.note;
    bar.onclick = () => bar.remove();
    document.body.appendChild(bar);
  }
  await import("/js/main.js");
}

main().catch((e) => {
  console.error(e);
  overlay.classList.add("failed");
  say("Gagal memuat: " + (e.message || e));
});
