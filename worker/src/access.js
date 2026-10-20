// Cloudflare Access check, done again inside the Worker (defence in depth).
//
// Access puts a signed token (JWT) on every request it lets through to the site; the
// Pages Function passes the request on unchanged, so it arrives here too. We verify the
// signature with the team's public keys, the audience (the site's Access application),
// the issuer and the expiry, and, when ALLOWED_EMAILS is set, the email.
//
// Settings (wrangler secrets): ACCESS_TEAM_DOMAIN (e.g. myteam.cloudflareaccess.com),
// ACCESS_AUD (the application's Audience tag), optional ALLOWED_EMAILS (comma separated;
// an entry like "@company.com" allows the whole domain).
// REQUIRE_ACCESS = "false" turns the check off, for `wrangler dev` on your machine only.

let certs = { at: 0, keys: [] };

const b64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")),
  (c) => c.charCodeAt(0));
const decode = (s) => JSON.parse(new TextDecoder().decode(b64url(s)));
const deny = (why) => new Response(JSON.stringify({ error: { message: `access denied: ${why}` } }),
  { status: 403, headers: { "Content-Type": "application/json" } });

async function keysFor(team, fetchFn) {
  if (Date.now() - certs.at < 10 * 60 * 1000 && certs.keys.length) return certs.keys;
  const r = await fetchFn(`https://${team}/cdn-cgi/access/certs`);
  if (!r.ok) throw new Error(`certs ${r.status}`);
  certs = { at: Date.now(), keys: (await r.json()).keys || [] };
  return certs.keys;
}

export function resetCertCache() { certs = { at: 0, keys: [] }; }

export async function verifyAccess(request, env, fetchFn = fetch) {
  if (env.REQUIRE_ACCESS === "false") return null;
  const team = env.ACCESS_TEAM_DOMAIN;
  if (!team || !env.ACCESS_AUD) return deny("ACCESS_TEAM_DOMAIN / ACCESS_AUD not configured");
  const token = request.headers.get("cf-access-jwt-assertion");
  if (!token) return deny("no Access token");
  const parts = token.split(".");
  if (parts.length !== 3) return deny("malformed token");
  let header, payload;
  try { header = decode(parts[0]); payload = decode(parts[1]); } catch { return deny("malformed token"); }
  if (header.alg !== "RS256") return deny("unexpected algorithm");
  let jwk;
  try { jwk = (await keysFor(team, fetchFn)).find((k) => k.kid === header.kid); } catch (e) {
    return deny(`cannot read Access keys (${e.message})`);
  }
  if (!jwk) return deny("unknown signing key");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64url(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`));
  if (!ok) return deny("bad signature");
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.includes(env.ACCESS_AUD)) return deny("wrong audience");
  if (payload.iss !== `https://${team}`) return deny("wrong issuer");
  if (!payload.exp || payload.exp * 1000 < Date.now()) return deny("token expired");
  if (env.ALLOWED_EMAILS) {
    const allowed = env.ALLOWED_EMAILS.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    const email = String(payload.email || "").toLowerCase();
    const ok = allowed.some((a) => (a.startsWith("@") ? email.endsWith(a) : email === a));
    if (!ok) return deny(`email not allowed (${email || "no email"}): add it to ALLOWED_EMAILS`);
  }
  return null;
}
