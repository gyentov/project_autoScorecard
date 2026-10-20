// AutoScorecard LLM Worker: the only place the API keys live.
//
//   POST /llm/chat    {provider, agent, project_id, body}  -> the provider's status + JSON
//   GET  /llm/status  -> which key names are set (never the values), spend vs budget
//
// It has no public URL (workers_dev = false): the site reaches it through the Pages
// Function at /llm/*, behind Cloudflare Access, and it checks the Access token again.
// Every call is checked against config/config.yaml (copied at build time into
// config.generated.js): known provider, its configured model, max_tokens within the
// limit, only the request fields the app sends. Usage and cost go to D1; paid calls stop
// at budget.total_usd.

import CONFIG from "./config.generated.js";
import { verifyAccess } from "./access.js";

// Where each key may be sent. The browser never chooses the URL.
export const UPSTREAM = {
  ANTHROPIC_API_KEY: { type: "anthropic", url: "https://api.anthropic.com/v1/messages" },
  OPENAI_API_KEY: { type: "openai_compat", url: "https://api.openai.com/v1/chat/completions" },
  GEMINI_API_KEY: { type: "openai_compat", url: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions" },
  OPENROUTER_API_KEY: { type: "openai_compat", url: "https://openrouter.ai/api/v1/chat/completions" },
};
const FIELDS = {
  anthropic: new Set(["model", "max_tokens", "system", "messages", "tools", "cache_control"]),
  openai_compat: new Set(["model", "max_tokens", "messages", "tools"]),
};
const PER_MILLION = 1_000_000;

const json = (obj, status = 200, headers = {}) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", ...headers } });
const fail = (status, message) => json({ error: { message } }, status);

export function cost(type, usage, price) {
  const cw = price.cache_write ?? price.input;
  const cr = price.cache_read ?? price.input;
  let t;
  if (type === "anthropic") {
    t = { input: usage.input_tokens || 0, output: usage.output_tokens || 0,
      cw: usage.cache_creation_input_tokens || 0, cr: usage.cache_read_input_tokens || 0 };
  } else {
    const cached = usage.prompt_tokens_details?.cached_tokens || 0;
    t = { input: (usage.prompt_tokens || 0) - cached, output: usage.completion_tokens || 0, cw: 0, cr: cached };
  }
  return { tokens: t,
    usd: (t.input * price.input + t.output * price.output + t.cw * cw + t.cr * cr) / PER_MILLION };
}

const isFree = (p) => !p.input && !p.output;

let tableReady = false;
async function db(env) {
  if (!tableReady) {
    await env.DB.prepare(`CREATE TABLE IF NOT EXISTS usage (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, provider TEXT NOT NULL, model TEXT,
      agent TEXT, project_id TEXT, status INTEGER, input_tokens INTEGER, output_tokens INTEGER,
      cache_write_tokens INTEGER, cache_read_tokens INTEGER, usd REAL NOT NULL DEFAULT 0)`).run();
    tableReady = true;
  }
  return env.DB;
}

async function spent(env) {
  const row = await (await db(env)).prepare("SELECT COALESCE(SUM(usd), 0) AS s FROM usage").first();
  return row?.s || 0;
}

export function checkRequest(req) {
  const cfg = CONFIG.providers[req?.provider];
  if (!cfg) return [null, "unknown provider"];
  const up = UPSTREAM[cfg.key_env];
  if (!up || up.type !== cfg.type) return [null, `provider ${req.provider} has no allowed upstream`];
  const body = req.body;
  if (!body || typeof body !== "object") return [null, "missing body"];
  const extra = Object.keys(body).filter((k) => !FIELDS[cfg.type].has(k));
  if (extra.length) return [null, `field not allowed: ${extra.join(", ")}`];
  if (body.model !== cfg.model) return [null, `model must be ${cfg.model}`];
  if (!Number.isInteger(body.max_tokens) || body.max_tokens < 1 || body.max_tokens > cfg.max_tokens) {
    return [null, `max_tokens must be 1..${cfg.max_tokens}`];
  }
  return [{ cfg, up }, null];
}

async function chat(request, env) {
  let req;
  try { req = await request.json(); } catch { return fail(400, "body must be JSON"); }
  const [ok, problem] = checkRequest(req);
  if (!ok) return fail(400, problem);
  const { cfg, up } = ok;
  const key = env[cfg.key_env];
  if (!key) return fail(401, `${cfg.key_env} is not set on the Worker`);
  if (!isFree(cfg.price)) {
    const s = await spent(env);
    if (s >= CONFIG.budget.total_usd) {
      return fail(402, `budget reached: $${s.toFixed(4)} of $${CONFIG.budget.total_usd} spent`);
    }
  }
  const headers = { "Content-Type": "application/json" };
  if (up.type === "anthropic") Object.assign(headers, { "x-api-key": key, "anthropic-version": "2023-06-01" });
  else headers.Authorization = `Bearer ${key}`;
  let res;
  try {
    res = await fetch(up.url, { method: "POST", headers, body: JSON.stringify(req.body) });
  } catch (e) {
    return fail(502, `upstream unreachable: ${e.message}`);
  }
  const text = await res.text();
  let usage = {};
  if (res.ok) { try { usage = JSON.parse(text).usage || {}; } catch { /* passed through as is */ } }
  const c = cost(cfg.type, usage, cfg.price);
  await (await db(env)).prepare(`INSERT INTO usage (ts, provider, model, agent, project_id, status,
      input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, usd)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(new Date().toISOString(), req.provider, cfg.model, String(req.agent || ""),
      String(req.project_id || ""), res.status, c.tokens.input, c.tokens.output, c.tokens.cw, c.tokens.cr, c.usd)
    .run();
  const out = { "Content-Type": "application/json" };
  const ra = res.headers.get("retry-after");
  if (ra) out["Retry-After"] = ra;
  return new Response(text, { status: res.status, headers: out });
}

async function status(env) {
  const keys = Object.keys(UPSTREAM).filter((k) => env[k]);
  const providers = Object.fromEntries(Object.entries(CONFIG.providers)
    .map(([name, p]) => [name, keys.includes(p.key_env)]));
  return json({ keys, providers, spent_usd: Number((await spent(env)).toFixed(4)),
    limit_usd: CONFIG.budget.total_usd });
}

export default {
  async fetch(request, env) {
    const denied = await verifyAccess(request, env);
    if (denied) return denied;
    const { pathname } = new URL(request.url);
    if (pathname === "/llm/chat" && request.method === "POST") return chat(request, env);
    if (pathname === "/llm/status" && request.method === "GET") return status(env);
    return fail(404, "not found");
  },
};
