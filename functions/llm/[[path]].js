// /llm/* on the site goes to the LLM Worker through a service binding: same origin as
// the page, so the Access login that guards the site also guards these calls, and the
// Worker itself needs no public URL. The request (with Access's token) passes unchanged.
export async function onRequest({ request, env }) {
  if (!env.LLM) {
    return new Response(JSON.stringify({ error: { message: "LLM Worker not bound (service binding LLM)" } }),
      { status: 503, headers: { "Content-Type": "application/json" } });
  }
  return env.LLM.fetch(request);
}
