// The waterfall editor: the Boss rearranges Citra's feature-selection plan directly.
// Drag a card by ⠿ (or use ↑/↓) to reorder, switch steps on/off, edit parameters, add or
// remove steps. Saving applies at once (the Boss's own decision, no ACC): Citra confirms
// what changed and, in the plan meeting, the projector shows the new plan.
import { P, S, api, escapeHtml, toast } from "./api.js";

const $ = (id) => document.getElementById(id);
const W = { types: null, steps: [], criteria: [], drag: null, onSaved: null };

const CHOICES = { linkage: ["average", "complete", "single"] };

export async function openWaterfall() {
  if (!S.pid) return;
  if (!W.types) {
    W.types = Object.fromEntries((await api("/api/waterfall/step-types")).map((t) => [t.type, t]));
  }
  const plan = await api(P("/waterfall"));
  W.steps = plan.steps.map((s) => ({ ...s, params: { ...s.params } }));
  W.criteria = plan.criteria;
  $("wf-error").textContent = "";
  render(plan.warnings);
  $("wfwin").classList.remove("hidden");
}

function input(step, key, value) {
  const id = `wf-${step.type}-${key}`;
  let el;
  if (typeof value === "boolean") {
    el = `<input id="${id}" type="checkbox" ${value ? "checked" : ""}>`;
  } else if (CHOICES[key]) {
    el = `<select id="${id}">${CHOICES[key].map((c) => `<option ${c === value ? "selected" : ""}>${c}</option>`).join("")}</select>`;
  } else if (typeof value === "number") {
    const step_ = Number.isInteger(value) && value > 1 ? 1 : 0.01;
    el = `<input id="${id}" type="number" step="${step_}" value="${value}">`;
  } else {
    el = `<input id="${id}" value="${escapeHtml(String(value ?? ""))}">`;
  }
  return `<label class="wf-param" for="${id}"><span>${escapeHtml(key)}</span>${el}</label>`;
}

function render(warnings = []) {
  const list = $("wf-list");
  list.innerHTML = "";
  W.steps.forEach((s, i) => {
    const t = W.types[s.type] || { title: s.type, kind: "", help: "" };
    const card = document.createElement("div");
    card.className = "wf-step" + (s.enabled ? "" : " off");
    card.draggable = true;
    card.innerHTML = `<span class="grip" title="Seret untuk memindah">⠿</span>
      <div class="wf-main">
        <div class="wf-head"><b>${i + 1}. ${escapeHtml(t.title)}</b>
          <span class="tag">${t.kind === "model" ? "seleksi model" : "filter"}</span>
          <label class="wf-on"><input type="checkbox" ${s.enabled ? "checked" : ""}> aktif</label></div>
        <div class="wf-params">${Object.entries(s.params).map(([k, v]) => input(s, k, v)).join("")}</div>
        <div class="muted wf-crit" title="${escapeHtml(t.help)}">${escapeHtml(W.criteria[i] || t.help || "")}</div>
      </div>
      <div class="wf-btns"><button class="ghost" data-a="up" title="Naik">↑</button>
        <button class="ghost" data-a="down" title="Turun">↓</button>
        <button class="ghost bad" data-a="del" title="Hapus langkah">✕</button></div>`;
    card.querySelector(".wf-on input").onchange = (e) => { s.enabled = e.target.checked; dirty(); render(); };
    for (const [k, v] of Object.entries(s.params)) {
      const el = card.querySelector(`#wf-${s.type}-${k}`);
      el.onchange = () => {
        s.params[k] = typeof v === "boolean" ? el.checked : typeof v === "number" ? Number(el.value) : el.value;
        dirty();
      };
    }
    card.querySelectorAll(".wf-btns button").forEach((b) => {
      b.onclick = () => {
        const a = b.dataset.a;
        if (a === "del") W.steps.splice(i, 1);
        else {
          const j = a === "up" ? i - 1 : i + 1;
          if (j < 0 || j >= W.steps.length) return;
          [W.steps[i], W.steps[j]] = [W.steps[j], W.steps[i]];
        }
        dirty();
        render();
      };
    });
    card.addEventListener("dragstart", (e) => { W.drag = i; card.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (e) => { e.preventDefault(); card.classList.add("over"); });
    card.addEventListener("dragleave", () => card.classList.remove("over"));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      if (W.drag === null || W.drag === i) return;
      const [moved] = W.steps.splice(W.drag, 1);
      W.steps.splice(i, 0, moved);
      W.drag = null;
      dirty();
      render();
    });
    list.appendChild(card);
  });
  const missing = Object.values(W.types).filter((t) => !W.steps.some((s) => s.type === t.type));
  $("wf-add").innerHTML = `<option value="">+ Tambah langkah…</option>` +
    missing.map((t) => `<option value="${t.type}">${escapeHtml(t.title)}</option>`).join("");
  $("wf-add").disabled = !missing.length;
  $("wf-warn").innerHTML = warnings.length
    ? warnings.map((w) => `<div>⚠ ${escapeHtml(w)}</div>`).join("") : "";
}

function dirty() {
  W.criteria = [];                             // the criteria text refreshes after saving
  $("wf-error").textContent = "Belum disimpan.";
}

async function save() {
  $("wf-save").disabled = true;
  try {
    const r = await api(P("/office/waterfall"), { method: "PUT",
      body: { steps: W.steps.map(({ type, params, enabled }) => ({ type, params, enabled })) } });
    $("wfwin").classList.add("hidden");
    toast(r.changes.length ? `📐 <b>Citra:</b> rencana diperbarui: ${escapeHtml(r.changes.join("; "))}`
      : "Tidak ada perubahan pada rencana.", { color: "var(--citra)", ms: 9000 });
    if (r.changes.length) await W.onSaved?.();
  } catch (e) {
    $("wf-error").textContent = `Belum bisa disimpan: ${e.message}`;
  } finally {
    $("wf-save").disabled = false;
  }
}

export function setupWaterfall(onSaved) {
  W.onSaved = onSaved;
  $("wf-save").onclick = save;
  $("wf-close").onclick = $("wf-cancel").onclick = () => $("wfwin").classList.add("hidden");
  $("wf-add").onchange = (e) => {
    const t = W.types[e.target.value];
    if (!t) return;
    const at = W.steps.findIndex((s) => W.types[s.type]?.must_be_last);   // keep "last" steps last
    const step = { type: t.type, params: { ...t.defaults }, enabled: true };
    if (at >= 0 && !t.must_be_last) W.steps.splice(at, 0, step); else W.steps.push(step);
    dirty();
    render();
  };
}
