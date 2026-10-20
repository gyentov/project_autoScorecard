// Service worker: the page's /api/... requests (fetch, XHR uploads, slide iframes, images)
// never go to a server. They are handed to the top-level page, whose Python worker
// (Pyodide) answers them with the same FastAPI app uvicorn serves locally.
// Everything else (the page, /assets, /llm) goes to the network as usual.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

async function bridgeClient(clientId) {
  const all = await self.clients.matchAll({ type: "window" });
  const top = all.filter((c) => c.frameType === "top-level" && !new URL(c.url).pathname.startsWith("/api/"));
  return top.find((c) => c.id === clientId) || top[0];
}

async function forward(event) {
  const req = event.request;
  const client = await bridgeClient(event.clientId);
  if (!client) {
    return new Response(JSON.stringify({ detail: "AutoScorecard belum dimuat; buka halaman utamanya dulu." }),
      { status: 503, headers: { "Content-Type": "application/json" } });
  }
  const body = ["GET", "HEAD"].includes(req.method) ? null : await req.arrayBuffer();
  const channel = new MessageChannel();
  const answer = new Promise((resolve) => { channel.port1.onmessage = (m) => resolve(m.data); });
  client.postMessage({
    type: "api", method: req.method, url: req.url,
    headers: [...req.headers.entries()], body,
  }, [channel.port2, ...(body ? [body] : [])]);
  const r = await answer;
  return new Response(r.status === 204 || r.status === 304 ? null : r.body,
    { status: r.status, headers: r.headers });
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) {
    event.respondWith(forward(event));
  }
});
