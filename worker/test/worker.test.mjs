// node --test worker/test/   (run via `npm run test:worker`, which generates the config first)
import assert from "node:assert/strict";
import { test } from "node:test";

import CONFIG from "../src/config.generated.js";
import worker, { checkRequest, cost } from "../src/index.js";
import { resetCertCache, verifyAccess } from "../src/access.js";

const PAID = Object.entries(CONFIG.providers).find(([, p]) => p.type === "anthropic" && p.price.input > 0);
const [NAME, CFG] = PAID;

function fakeDB() {
  const rows = [];
  return {
    rows,
    prepare(sql) {
      let args = [];
      return {
        bind(...a) { args = a; return this; },
        async run() { if (sql.startsWith("INSERT")) rows.push({ usd: args[10], provider: args[1], status: args[5] }); },
        async first() { return { s: rows.reduce((t, r) => t + r.usd, 0) }; },
      };
    },
  };
}

const chatReq = (body, provider = NAME) => new Request("https://site/llm/chat", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ provider, agent: "partner", project_id: "p1", body }),
});
const okBody = () => ({ model: CFG.model, max_tokens: 100, system: "s", messages: [] });

test("only configured providers, models, fields and token limits pass", () => {
  assert.equal(checkRequest({ provider: "nope", body: okBody() })[1], "unknown provider");
  assert.match(checkRequest({ provider: NAME, body: { ...okBody(), model: "claude-opus-x" } })[1], /model must be/);
  assert.match(checkRequest({ provider: NAME, body: { ...okBody(), temperature: 2 } })[1], /field not allowed/);
  assert.match(checkRequest({ provider: NAME, body: { ...okBody(), max_tokens: CFG.max_tokens + 1 } })[1], /max_tokens/);
  assert.equal(checkRequest({ provider: NAME, body: okBody() })[1], null);
});

test("cost uses the same formula as autoscorecard/llm/cost.py", () => {
  const price = { input: 1, output: 5, cache_write: 1.25, cache_read: 0.1 };
  const c = cost("anthropic", { input_tokens: 1000, output_tokens: 200, cache_creation_input_tokens: 4000,
    cache_read_input_tokens: 10000 }, price);
  assert.ok(Math.abs(c.usd - (1000 * 1 + 200 * 5 + 4000 * 1.25 + 10000 * 0.1) / 1e6) < 1e-12);
  const o = cost("openai_compat", { prompt_tokens: 1000, completion_tokens: 10,
    prompt_tokens_details: { cached_tokens: 400 } }, { input: 2, output: 8 });
  assert.deepEqual(o.tokens, { input: 600, output: 10, cw: 0, cr: 400 });
});

test("chat adds the key server-side, passes the answer through and records the cost", async () => {
  const env = { REQUIRE_ACCESS: "false", DB: fakeDB(), [CFG.key_env]: "sk-test-secret" };
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url, init });
    return new Response(JSON.stringify({ content: [{ type: "text", text: "halo" }], stop_reason: "end_turn",
      usage: { input_tokens: 1000, output_tokens: 100 } }), { status: 200 });
  };
  const res = await worker.fetch(chatReq(okBody()), env);
  assert.equal(res.status, 200);
  assert.equal((await res.json()).content[0].text, "halo");
  assert.equal(seen[0].url, "https://api.anthropic.com/v1/messages");
  assert.equal(seen[0].init.headers["x-api-key"], "sk-test-secret");
  assert.equal(env.DB.rows.length, 1);
  assert.ok(env.DB.rows[0].usd > 0);
  const st = await (await worker.fetch(new Request("https://site/llm/status"), env)).json();
  assert.deepEqual(st.keys, [CFG.key_env]);
  assert.ok(!JSON.stringify(st).includes("sk-test-secret"));     // names only, never values
});

test("missing key is 401, over budget is 402 and nothing is sent", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response("{}"); };
  const noKey = await worker.fetch(chatReq(okBody()), { REQUIRE_ACCESS: "false", DB: fakeDB() });
  assert.equal(noKey.status, 401);
  const db = fakeDB();
  db.rows.push({ usd: CONFIG.budget.total_usd });
  const over = await worker.fetch(chatReq(okBody()), { REQUIRE_ACCESS: "false", DB: db, [CFG.key_env]: "k" });
  assert.equal(over.status, 402);
  assert.match((await over.json()).error.message, /budget reached/);
  assert.equal(calls, 0);
});

// ---------------------------------------------------------------- Cloudflare Access

const enc = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");

async function signer() {
  const kp = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = { ...(await crypto.subtle.exportKey("jwk", kp.publicKey)), kid: "k1" };
  const sign = async (payload, kid = "k1") => {
    const head = enc({ alg: "RS256", kid, typ: "JWT" });
    const body = enc(payload);
    const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", kp.privateKey, new TextEncoder().encode(`${head}.${body}`));
    return `${head}.${body}.${Buffer.from(sig).toString("base64url")}`;
  };
  return { sign, certs: async () => new Response(JSON.stringify({ keys: [jwk] })) };
}

test("Access token: signature, audience, issuer, expiry and email are all checked", async () => {
  resetCertCache();
  const { sign, certs } = await signer();
  const env = { ACCESS_TEAM_DOMAIN: "team.cloudflareaccess.com", ACCESS_AUD: "aud123",
    ALLOWED_EMAILS: "boss@example.com" };
  const good = { aud: ["aud123"], iss: "https://team.cloudflareaccess.com", exp: Date.now() / 1000 + 60,
    email: "Boss@example.com" };
  const req = (t) => new Request("https://site/llm/status", t ? { headers: { "cf-access-jwt-assertion": t } } : {});
  assert.equal(await verifyAccess(req(await sign(good)), env, certs), null);
  const why = async (t) => (await (await verifyAccess(req(t), env, certs)).json()).error.message;
  assert.match(await why(null), /no Access token/);
  assert.match(await why(await sign({ ...good, aud: ["other"] })), /wrong audience/);
  assert.match(await why(await sign({ ...good, iss: "https://evil.example" })), /wrong issuer/);
  assert.match(await why(await sign({ ...good, exp: 1 })), /expired/);
  assert.match(await why(await sign({ ...good, email: "x@example.com" })), /email not allowed/);
  assert.match(await why(await sign(good, "unknown")), /unknown signing key/);
  const t = await sign(good);
  assert.match(await why(t.slice(0, -4) + "AAAA"), /bad signature/);
  assert.match(await (await verifyAccess(req(t), {}, certs)).text(), /not configured/);   // fails closed
});
