// The Python side of the browser build: Pyodide runs the AutoScorecard app here, off the
// page's main thread. Projects are kept in the browser (IndexedDB, mounted at /data);
// LLM calls go to the Worker at /llm (it holds the API keys).

// A module worker: Pyodide 314 ships as ES modules only.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";

const PACKAGES = ["numpy", "pandas", "pyarrow", "pydantic", "pyyaml", "fastapi", "micropip"];
let py = null;
let serve = null;
let queue = Promise.resolve();           // one request at a time, in arrival order
let saving = null;

const status = (text) => postMessage({ type: "status", text });

// the proxy transport: synchronous, so the Python code stays synchronous (allowed in workers)
self.syncPost = (url, body) => {
  const x = new XMLHttpRequest();
  try {
    x.open("POST", url, false);
    x.setRequestHeader("Content-Type", "application/json");
    x.send(body);
  } catch (e) {
    return { status: 0, headers: "", text: String(e) };
  }
  return { status: x.status, headers: x.getAllResponseHeaders(), text: x.responseText };
};

function syncfs(populate) {
  return new Promise((resolve, reject) =>
    py.FS.syncfs(populate, (err) => (err ? reject(err) : resolve())));
}

function persistSoon() {                 // write /data back to IndexedDB after changes
  if (saving) return;
  saving = setTimeout(async () => {
    saving = null;
    try { await syncfs(false); } catch (e) { console.error("menyimpan proyek gagal", e); }
  }, 300);
}

async function llmKeys() {
  try {
    const r = await fetch("/llm/status", { credentials: "include" });
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch (e) {
    return { keys: [], note: `LLM Worker tidak terjangkau (${e.message}); tim memakai mode offline ($0).` };
  }
}

async function boot() {
  status("Memuat Python (Pyodide)...");
  py = await loadPyodide({ indexURL: PYODIDE });
  status("Memuat numpy, pandas, pyarrow, fastapi...");
  await py.loadPackage(PACKAGES);
  status("Memasang paket tambahan...");
  const wheels = await (await fetch("/wheels/index.json", { cache: "no-cache" })).json();
  const micropip = py.pyimport("micropip");
  await micropip.install(wheels.map((w) => new URL("/wheels/" + w, location.origin).href), { deps: false });
  status("Menyalin kode AutoScorecard...");
  const bundle = await (await fetch("/py/bundle.json", { cache: "no-cache" })).json();
  for (const [path, text] of Object.entries(bundle.files)) {
    const full = "/app/" + path;
    py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    py.FS.writeFile(full, text);
  }
  status("Membuka penyimpanan proyek di browser...");
  py.FS.mkdirTree("/data");
  py.FS.mount(py.FS.filesystems.IDBFS, {}, "/data");
  await syncfs(true);
  const llm = await llmKeys();
  status("Menyalakan kantor...");
  py.globals.set("PROXY_URL", new URL("/llm", location.origin).href);
  py.globals.set("KEYS", py.toPy(llm.keys || []));
  await py.runPythonAsync(`
import sys
sys.path.insert(0, "/app")
from autoscorecard.web import boot
boot.start(PROXY_URL, KEYS)
`);
  serve = py.pyimport("autoscorecard.web.boot").serve;
  await syncfs(false);
  postMessage({ type: "ready", version: bundle.version, llm });
}

async function answer(req) {
  const r = await serve(req.method, req.url, JSON.stringify(req.headers),
    req.body ? new Uint8Array(req.body) : null);
  const out = r.toJs({ dict_converter: Object.fromEntries });
  r.destroy();
  if (req.method !== "GET" && req.method !== "HEAD") persistSoon();
  const body = out.body.buffer.slice(out.body.byteOffset, out.body.byteOffset + out.body.byteLength);
  return { status: out.status, headers: JSON.parse(out.headers), body };
}

onmessage = (m) => {
  const { id, req } = m.data;
  queue = queue.then(() => answer(req)).then(
    (res) => postMessage({ type: "response", id, res }, [res.body]),
    (err) => {
      console.error(err);
      const body = new TextEncoder().encode(JSON.stringify({ detail: String(err.message || err) })).buffer;
      postMessage({ type: "response", id, res: { status: 500, headers: [["content-type", "application/json"]], body } },
        [body]);
    });
};

boot().catch((e) => { console.error(e); postMessage({ type: "failed", text: String(e.message || e) }); });
