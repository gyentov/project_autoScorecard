// Small helpers shared by the office: API calls, the team, portraits, markdown.

export const TEAM = {
  laras: { name: "Laras", color: "var(--laras)", drink: "teh", snack: "buah" },
  bening: { name: "Bening", color: "var(--bening)", drink: "air putih", snack: "crackers" },
  citra: { name: "Citra", color: "var(--citra)", drink: "kopi", snack: "cokelat" },
  kirana: { name: "Kirana", color: "var(--kirana)", drink: "milk tea", snack: "frozen meal" },
  boss: { name: "Boss", color: "var(--boss)" },
};

// shared session state; `waiting` = an answer is being worked on (the poller speeds up)
export const S = { pid: null, state: null, manifest: null, waiting: null, wakePoll: null };

export async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

export const P = (path) => `/api/projects/${S.pid}${path}`;
export const fileUrl = (path) => `/api/projects/${S.pid}/files/${path}`;

// --- standing art: assets/characters/<id>/<pose>_<mood>.png with the documented fallback
const POSE_BY_MOOD = { proud: "presenting", thinking: "thinking", worried: "thinking",
  apologetic: "bowing", excited: "cheering", serious: "arms_crossed", happy: "explaining" };
export const poseFor = (mood, slide) =>
  POSE_BY_MOOD[mood] || (slide !== null && slide !== undefined ? "presenting" : "neutral");

export async function loadManifest() {
  try { S.manifest = await api("/assets/characters/manifest.json"); }
  catch (e) { S.manifest = { files: {} }; }
}

export function portrait(id, pose = "neutral", mood = "neutral") {
  const have = new Set((S.manifest?.files?.[id]) || []);
  for (const name of [`${pose}_${mood}`, `neutral_${mood}`, `${pose}_neutral`, "neutral_neutral"]) {
    if (have.has(`${id}/${name}.png`)) return `/assets/characters/${id}/${name}.png`;
  }
  return `/assets/characters/${id}/neutral_neutral.png`;
}

// --- tiny markdown (emails, MOM): headings, bold, italics, code, lists, tables, links
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const inline = (s) => esc(s)
  .replace(/`([^`]+)`/g, "<code>$1</code>")
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
  .replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g, "$1<i>$2</i>");

export function markdown(md) {
  const out = [];
  const lines = (md || "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*\|/.test(l)) {                       // table block
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      i--;
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      out.push("<table>" + body.map((r, k) => "<tr>" + cells(r).map((c) =>
        k === 0 ? `<th>${c}</th>` : `<td>${c}</td>`).join("") + "</tr>").join("") + "</table>");
    } else if (/^#{1,3} /.test(l)) {
      const n = l.match(/^#+/)[0].length;
      out.push(`<h${n}>${inline(l.replace(/^#+ /, ""))}</h${n}>`);
    } else if (/^\s*[-*] /.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*] /, ""));
      i--;
      out.push("<ul>" + items.map((t) => `<li>${inline(t)}</li>`).join("") + "</ul>");
    } else if (/^\s*\d+\. /.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\. /.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+\. /, ""));
      i--;
      out.push("<ol>" + items.map((t) => `<li>${inline(t)}</li>`).join("") + "</ol>");
    } else if (l.trim() === "") {
      out.push("");
    } else {
      out.push(`<p>${inline(l)}</p>`);
    }
  }
  return `<div class="md">${out.join("\n")}</div>`;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function toast(html, opts = {}) {
  const el = document.createElement("div");
  el.className = "toast";
  el.style.borderLeftColor = opts.color || "var(--acc)";
  el.innerHTML = html;
  if (opts.action) {
    const b = document.createElement("button");
    b.textContent = opts.action.label;
    b.className = "primary";
    b.onclick = () => { opts.action.run(); el.remove(); };
    el.appendChild(document.createElement("br"));
    el.appendChild(b);
  }
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), opts.ms || 7000);
  return el;
}

export const escapeHtml = esc;
