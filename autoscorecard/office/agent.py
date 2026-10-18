"""Answering what the Boss types.

1. Route: "@citra ..." goes to Citra; otherwise keywords pick the agent ($0).
2. Commands: short instructions ("paksa province", "buang noise_a",
   "bins cust_age 25 35 45") become proposals directly, no LLM ($0).
3. Everything else: Laras estimates the cost; above budget.ask_above_usd the Boss
   must say yes first. Then the agent answers with tools (max 3 LLM calls), and
   the actual cost is compared with the estimate.
Offline (no LLM available): an honest templated reply; choices still work.

Only the spoken words reach the Boss. The model writes them inside <ucap>...</ucap>;
anything else it writes (its <batin> thinking, planning, notes to itself) is kept as
`thought`, which goes to the conversation log for audit and never to the screen.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field

from ..llm import LLMRequest, Message, ToolResult
from ..llm.router import AllProvidersFailed
from . import board as B
from . import costs
from .characters import MOODS, TEAM, WORKING, line, small_talk, system_prompt
from .tools import ToolBox

AGENT_KEY = {"laras": "partner", "bening": "prepper", "citra": "modeller",
             "kirana": "deck_creator"}
MOOD_TAG = re.compile(r"<mood:(\w+)>\s*")
THINK = re.compile(r"<(batin|think|thinking|reasoning)>(.*?)(?:</\1>|(?=<ucap>)|$)", re.S | re.I)
SPOKEN = re.compile(r"<ucap>(.*?)(?:</ucap>|$)", re.S | re.I)


_GUE = re.compile(r"\b(gue|gua|gw)\b", re.I)
_LU = re.compile(r"\b(lu|lo|elu|elo)\b", re.I)


def polite(text: str) -> str:
    """No 'lu/gua' from the team, to the Boss or to each other (the Boss may use it)."""
    return _LU.sub("Boss", _GUE.sub(lambda m: "Saya" if m.group(0)[0].isupper() else "saya", text))


def split_reply(raw: str) -> tuple[str, str, str]:
    """(mood, spoken, thought) from a raw model reply. Without <ucap> the reply minus
    its thinking blocks is the spoken part (older or disobedient models)."""
    thoughts = [m.group(2).strip() for m in THINK.finditer(raw)]
    rest = THINK.sub("", raw)
    mood_m = MOOD_TAG.search(raw)
    mood = mood_m.group(1) if mood_m and mood_m.group(1) in MOODS else "neutral"
    sp = SPOKEN.search(rest)
    if sp:
        spoken = sp.group(1)
        outside = MOOD_TAG.sub("", SPOKEN.sub("", rest)).strip()
        if outside:
            thoughts.append(outside)
    else:
        spoken = rest
    spoken = re.sub(r"</?(ucap|batin)>", "", MOOD_TAG.sub("", spoken)).strip()
    return mood, spoken, "\n\n".join(t for t in thoughts if t)


# what the office shows while an agent uses a tool ("▸ menghitung ...")
def tool_label(name: str, args: dict) -> str:
    if name == "show_aggregate":
        mets = ", ".join((m.get("agg", "") + (f" {m['column']}" if m.get("column") else ""))
                         for m in args.get("metrics", []))
        return f"menghitung {mets or 'agregasi'} per {', '.join(args.get('group_by', [])) or '-'}"
    labels = {"show_sample": "mengambil contoh baris untuk papan",
              "show_vintage": "menghitung vintage", "show_columns": "membaca daftar kolom",
              "show_waterfall": "menampilkan rencana waterfall",
              "tables": "membaca profil tabel", "target_info": "membaca definisi target",
              "iv_table": "membaca IV semua fitur", "bin_table": "membuka tabel bin",
              "feature_status": "menelusuri waterfall", "performance": "membaca Gini/KS",
              "what_if_bins": "mencoba bin manual", "cost_status": "cek biaya",
              "project_status": "cek status proyek", "join_plan": "membaca rencana join",
              "dictionary_gaps": "cek kamus data", "assign_task": "membagi tugas ke tim",
              "update_order": "melaporkan status perintah", "document_variables": "mendokumentasikan arti variabel",
              "make_documentation": "menyusun dokumentasi model", "make_deck": "menyusun draft deck"}
    if name.startswith("propose_"):
        return "menyiapkan usulan untuk ACC"
    return labels.get(name, name.replace("_", " "))


def route(text: str, default: str = "laras") -> str:
    m = re.match(r"\s*@(\w+)", text)
    if m and m.group(1).lower() in TEAM:
        return m.group(1).lower()
    words = re.findall(r"[a-z0-9_]+", text.lower())
    hits = {cid: sum(1 for w in words if any(w.startswith(k) for k in c.keywords))
            for cid, c in TEAM.items() if cid != "kirana"}
    best = max(hits, key=hits.get)
    return best if hits[best] else default


# "show me" shortcuts ($0, no LLM): sample rows and simple aggregates, on the board
_SAMPLE = re.compile(r"^(?:tampilkan|tunjukkan|lihat|show)?\s*(?:sampel|sample|contoh data|contoh)"
                     r"(?:\s+(\d+))?(?:\s+baris)?(?:\s+(?:dari|tabel)\s+(\S+))?\s*$", re.I)
_AGG = re.compile(r"^(?:tampilkan|tunjukkan|lihat|show)?\s*(bad rate|jumlah|count|rata-rata|mean|"
                  r"median|missing(?: rate)?)\s+(?:(\S+)\s+)?per\s+(\S+)"
                  r"(?:\s+(?:dari|tabel)\s+(\S+))?\s*$", re.I)
# "tolong sampaikan ke Citra: ...", "titip pesan buat Bening ...", "kasih tahu Kirana ..."
_RELAY = re.compile(r"(?:titip(?:\s+pesan)?|sampaikan|bilang(?:in)?|kasih\s*tau(?:h)?|kabari|"
                    r"pesan(?:kan)?|teruskan)\s+(?:ke|kepada|buat|untuk|sama|ama)?\s*"
                    r"(laras|bening|citra|kirana)\b[\s,:;-]*(?:bahwa\s+|kalau\s+|agar\s+|supaya\s+)?(.+)",
                    re.I | re.S)
_VIN = re.compile(r"^(?:tampilkan|tunjukkan|lihat|show)?\s*(?:analisis\s+)?vintage"
                  r"(?:\s+(30|60|90)\+?)?\s*$", re.I)
_AGG_NAME = {"bad rate": "bad_rate", "jumlah": "count", "count": "count", "rata-rata": "mean",
             "mean": "mean", "median": "median", "missing": "missing_rate",
             "missing rate": "missing_rate"}

# "variabel baru dti = loan_amount / cust_income", "turunkan log_inc = log(cust_income)"
_DERIVE = re.compile(r"^(?:bening[,:]?\s+)?(?:variabel\s+baru|buat\s+variabel|tambah\w*\s+variabel|"
                     r"turunkan(?:\s+variabel)?|derive)\s*:?\s*(\w+\s*=.+)$", re.I)
# "arti el = exposure limit", "arti tabel bureau: riwayat SLIK"
_MEANING = re.compile(r"^(?:bening[,:]?\s+)?arti\s+(?:variabel\s+|tabel\s+)?([\w.]+)\s*(?:=|:|adalah)\s*(.{3,})$",
                      re.I)

_CMDS = [
    (re.compile(r"^(?:force|paksa)\s+(\S+)\s*$", re.I), "force_feature"),
    (re.compile(r"^(?:unforce|batal\s*paksa)\s+(\S+)\s*$", re.I), "unforce_feature"),
    (re.compile(r"^(?:exclude|buang)\s+(\S+)\s*$", re.I), "exclude_feature"),
    (re.compile(r"^(?:include|kembalikan)\s+(\S+)\s*$", re.I), "include_feature"),
    (re.compile(r"^(?:bins?|potong)\s+(\S+)\s+(?:di\s+)?([-\d.,;\s]+)$", re.I), "bin_edit"),
]


@dataclass
class AgentReply:
    agent: str
    text: str
    mood: str = "neutral"
    proposals: list[str] = field(default_factory=list)
    estimate: dict | None = None
    actual: dict | None = None
    offline: bool = False
    needs_cost_ok: bool = False      # the Boss must agree to the estimate first
    thought: str = ""                # what the model wrote besides its answer: audit only
    provider: str = ""
    kind: str = "llm"                # llm, small_talk, command, offline, ask, held
    board: list[dict] = field(default_factory=list)    # what was put on the board

    def to_dict(self) -> dict:
        return {"agent": self.agent, "name": TEAM[self.agent].name, "text": self.text,
                "mood": self.mood, "proposals": self.proposals, "estimate": self.estimate,
                "actual": self.actual, "offline": self.offline,
                "needs_cost_ok": self.needs_cost_ok,
                "board": [{"title": b["title"], "file": b["file"], "board": b["board"]}
                          for b in self.board]}


class AgentRuntime:
    def __init__(self, office):
        self.office = office

    # ------------------------------------------------------------- commands ($0)
    def show_command(self, body: str, agent: str, meeting_id: str | None) -> AgentReply | None:
        """'sampel 10', 'bad rate per cust_province', 'rata-rata income per cust_age'."""
        from .tools import ToolBox
        box = ToolBox(self.office, agent, meeting_id)
        try:
            if m := _VIN.match(body):
                r = box.show_vintage(int(m.group(1)) if m.group(1) else None)
                rc = r["recommendation"]
                say = (f"Vintage sudah di papan, Boss. Rekomendasi saya: pernah {rc['threshold']}+ "
                       f"DPD dalam {rc['window']} bulan, {rc['bads']:,} bad "
                       f"({rc['bad_rate']:.1%}) dari {rc['n_mature']:,} akun matang.")
            elif m := _SAMPLE.match(body):
                r = box.show_sample(int(m.group(1) or 10), table=m.group(2))
                say = f"Ini {r['rows_shown']} contoh baris, Boss. Sudah di papan."
            elif m := _AGG.match(body):
                agg = _AGG_NAME[m.group(1).lower()]
                col, by = m.group(2), m.group(3)
                if agg == "count" and col:                 # "jumlah X per Y" = count per Y
                    col = None
                metric = {"agg": agg, **({"column": col} if col else {})}
                r = box.show_aggregate([by], [metric], table=m.group(4))
                say = f"Sudah saya tampilkan di papan, Boss: {r['shown_on_board']}."
            else:
                return None
        except (KeyError, ValueError, FileNotFoundError) as e:
            msg = str(e).strip("'\"")
            return AgentReply(agent, f"Belum bisa saya tampilkan, Boss: {msg}.", "apologetic",
                              kind="command")
        return AgentReply(agent, say, "happy", kind="command", board=box.shown)

    def command(self, text: str, agent: str = "bening",
                meeting_id: str | None = None) -> AgentReply | None:
        body = re.sub(r"^\s*@\w+\s*", "", text).strip()
        if m := _RELAY.search(body):                   # a message to pass on ($0)
            to, note = m.group(1).lower(), m.group(2).strip()
            self.office.relay_message(agent, to, note)
            return AgentReply(agent, "Siap, Boss, saya catat." if to == agent else
                              f"Siap, Boss. Pesannya saya sampaikan ke {TEAM[to].name}.",
                              "happy", kind="command")
        shown = self.show_command(body, agent, meeting_id)
        if shown is not None:
            return shown
        if m := _DERIVE.match(body):                   # "variabel baru dti = a / b" ($0)
            return self._derive_command(m.group(1))
        if m := _MEANING.match(body):                  # "arti el = exposure limit" ($0)
            return self._meaning_command(m.group(1), m.group(2))
        for rx, kind in _CMDS:
            m = rx.match(body)
            if not m:
                continue
            feature = m.group(1)
            cols = self.office.columns()
            if cols and feature not in cols:
                close = [c for c in cols if feature.lower() in c.lower()][:5]
                return AgentReply("citra", f"Saya tidak menemukan kolom '{feature}', Boss."
                                  + (f" Maksudnya salah satu ini? {', '.join(close)}"
                                     if close else ""), "apologetic", kind="command")
            payload = {"feature": feature}
            if kind == "bin_edit":
                payload["edges"] = [float(v) for v in re.split(r"[\s,;]+", m.group(2).strip())
                                    if v]
            p = self.office.proposals.add(kind, payload, "citra", "instruksi Boss")
            return AgentReply("citra", f"Siap, Boss. Saya catat sebagai usulan: {p.summary}. "
                              "Berlaku di cut berikutnya setelah Boss ACC.", "serious", [p.id],
                              kind="command")
        return None

    def _derive_command(self, expr: str) -> AgentReply:
        from . import derived as DV
        spec = DV.parse(expr)
        if spec is None:
            return AgentReply("bening", "Rumusnya belum saya pahami, Boss. Contoh: \"variabel baru "
                              "dti = loan_amount / cust_income\", \"log_inc = log(cust_income)\", "
                              "\"tua = cust_age >= 50\".", "apologetic", kind="command")
        try:
            r = self.office.propose_derived(spec, "bening", "instruksi Boss")
        except (ValueError, KeyError, FileNotFoundError) as e:
            return AgentReply("bening", f"Belum bisa, Boss: {e}.", "apologetic", kind="command")
        pv = r["preview"]
        return AgentReply("bening", f"Siap, Boss. Usulan: {r['summary']}. Pratinjau di data train: "
                          + (f"IV {pv['iv']:.3f} ({pv['strength']}), " if "iv" in pv else "")
                          + f"missing {pv['missing_rate']:.0%}. Setelah Boss ACC, variabelnya masuk "
                          "dataset dan kamus (direview Laras), dan jadi kandidat di cut berikutnya.",
                          "happy", [r["proposal_id"]], kind="command")

    def _meaning_command(self, name: str, arti: str) -> AgentReply:
        try:
            r = self.office.update_meaning(name.strip(), arti.strip(), reason="arahan Boss")
        except (ValueError, KeyError) as e:
            return AgentReply("bening", f"Belum bisa, Boss: {e}.", "apologetic", kind="command")
        return AgentReply("bening", f"Siap, Boss. Arti {r['updated']} {name} sekarang: "
                          f"\"{arti.strip()}\". Laras sudah mereview: "
                          + ("lolos, kamus terbaru dipakai di dokumentasi dan bisa dibaca semua tim."
                             if r["review"] == "approved" else "belum lolos; saya perbaiki dulu."),
                          "happy" if r["review"] == "approved" else "worried", kind="command")

    # ------------------------------------------------------------- after a meeting
    MOM_PROMPT = (
        "Kamu Laras, partner dan project lead. Rapat baru selesai. Tugasmu: JALANKAN arahan "
        "Boss dari MOM, jangan cuma mencatat. Ubah setiap arahan menjadi aksi nyata memakai "
        "tool: perubahan model -> propose_* (menunggu ACC Boss); pekerjaan untuk rekan -> "
        "assign_task (Bening: data/target/kamus, Citra: model/waterfall/binning, Kirana: "
        "presentasi), atau plan_relay kalau beberapa rekan bergantian; perubahan urutan "
        "kerja atau jadwal -> update_milestone / add_milestone / remove_milestone; angka yang "
        "perlu dilihat Boss -> show_*. Jangan mengarang angka; pakai "
        "tool untuk fakta. Setelah selesai, tulis ringkasan aksi singkat di dalam <ucap>.")

    def carry_out(self, mom_md: str, directions: list[str]) -> dict:
        """Laras turns the Boss's directions in a MOM into actions with tools (a stronger
        model when configured: agent 'partner_mom'). Without an LLM she forwards each
        direction to the teammate it concerns, and says so."""
        from .tools import ToolBox
        office = self.office
        box = ToolBox(office, "laras", tasks=True)
        msgs = [Message.user(f"MOM:\n{mom_md[:6000]}\n\nArahan Boss:\n"
                             + "\n".join(f"- {d}" for d in directions))]
        system = system_prompt(TEAM["laras"], self.MOM_PROMPT)
        total, text, provider = 0.0, "", "offline"
        try:
            for k in range(4):
                resp = office.router.complete(LLMRequest(
                    system, msgs, [] if k == 3 else box.specs(), max_tokens=700,
                    agent="partner_mom", project_id=office.project.id))
                total += resp.cost_usd
                provider = resp.provider
                if provider == "offline":
                    break
                if not resp.tool_calls:
                    text = split_reply(resp.text)[1]
                    break
                msgs.append(resp.as_message())
                results = []
                for c in resp.tool_calls:
                    office.events.emit("agent_status", "laras", status="working",
                                       task=tool_label(c.name, c.args))
                    results.append(ToolResult(c.id, *box.call(c.name, c.args)))
                msgs.append(Message("user", results))
        except AllProvidersFailed:
            provider = "offline"
        finally:
            office.events.emit("agent_status", "laras", status="idle", task="")
        if provider == "offline":                   # no LLM: forward, and be honest about it
            for d in directions:
                box.assign_task(route(d, "laras"), d)
            text = ("Arahan Boss sudah saya teruskan ke yang bersangkutan. Tanpa LLM saya belum "
                    "bisa mengubahnya jadi usulan otomatis; untuk perubahan model Boss bisa pakai "
                    "perintah (paksa/buang fitur, bins), editor waterfall, atau ketik ke Citra.")
        return {"text": polite(text), "proposals": box.created, "tasks": box.tasks,
                "board": box.shown, "usd": total, "provider": provider}

    WORK_PROMPT = (
        "Kamu sedang di mejamu mengerjakan tugas yang sudah tercatat. KERJAKAN dengan tool "
        "(lihat data, buat usulan, tampilkan di papan, buat file yang ada tool-nya), lalu tutup "
        "dengan update_order: 'selesai' dengan catatan hasil yang konkret, atau 'terhambat' "
        "dengan alasan jujur. Kalau tugasmu butuh hasil rekan, cek dulu dengan "
        "check_teammate_work. Jangan mengaku melakukan sesuatu tanpa tool. Di <ucap> tulis "
        "laporan singkat untuk Boss.")

    def work_on(self, order: dict) -> dict:
        """The owner of an order works on it at her desk with her tools (her own model) and
        reports with update_order. Offline she leaves it open: it is in her context the
        next time the Boss talks to her."""
        from .tools import ToolBox
        office = self.office
        agent = order["to"]
        box = ToolBox(office, agent, tasks=False, consult=True)
        prev = "; ".join(n["note"] for n in order.get("notes", [])[-3:])
        msgs = [Message.user(f"Tugas #{order['id']}: {order['text']}"
                             + (f"\nCatatan sejauh ini: {prev}" if prev else "")
                             + (f"\nKonteks alur: {order['context']}" if order.get("context") else ""))]
        system = system_prompt(TEAM[agent], self.WORK_PROMPT)
        total, text, provider = 0.0, "", "offline"
        office.events.emit("agent_status", agent, status="working", task=order["summary"][:60])
        try:
            for k in range(5):
                resp = office.router.complete(LLMRequest(
                    system, msgs, [] if k == 4 else box.specs(), max_tokens=600,
                    agent=AGENT_KEY[agent], project_id=office.project.id))
                total += resp.cost_usd
                provider = resp.provider
                if provider == "offline":
                    break
                if not resp.tool_calls:
                    text = split_reply(resp.text)[1]
                    break
                msgs.append(resp.as_message())
                results = []
                for c in resp.tool_calls:
                    office.events.emit("agent_status", agent, status="working",
                                       task=tool_label(c.name, c.args))
                    results.append(ToolResult(c.id, *box.call(c.name, c.args)))
                msgs.append(Message("user", results))
        except AllProvidersFailed:
            provider = "offline"
        finally:
            office.events.emit("agent_status", agent, status="idle", task="")
        if text:                                    # her report, where the Boss can see it
            office.events.emit("dialogue", agent, mood="neutral", text=polite(text))
        now = office.orders.get(order["id"])
        if provider != "offline" and now["status"] in ("baru", "dikerjakan"):
            # she answered but never closed it: report what she said, still open
            office.orders.update(order["id"], "dikerjakan", polite(text)[:300] or "sedang dikerjakan")
        return {"text": polite(text), "usd": total, "provider": provider, "board": box.shown,
                "proposals": box.created}

    # ------------------------------------------------------------- LLM answer
    @staticmethod
    def is_shortcut(text: str) -> bool:
        """A $0 command that runs at once (show data, change features, pass a message)."""
        body = re.sub(r"^\s*@\w+\s*", "", text).strip()
        return bool(_VIN.match(body) or _SAMPLE.match(body) or _AGG.match(body) or _RELAY.search(body)
                    or any(rx.match(body) for rx, _ in _CMDS))

    def who(self, text: str, to: str | None = None, default: str = "laras",
            present: list[str] | None = None) -> str:
        """Who answers: the one asked, else keywords; 'show me data' shortcuts with no
        keyword go to Bening. In a meeting (`present` = the attendees) only someone who
        is there answers; otherwise `default` (the host) takes it."""
        if to:
            agent = to
        else:
            body = re.sub(r"^\s*@\w+\s*", "", text).strip()
            if not route(text, "") and (_VIN.match(body) or _SAMPLE.match(body) or _AGG.match(body)):
                agent = "bening"
            else:
                agent = route(text, default)
        if present and agent not in present:
            return default if default in present else present[0]
        return agent

    @staticmethod
    def working_line(agent: str) -> str:
        return random.choice(WORKING[agent])

    # "langsung saya perbaiki", "saya update sekarang": a promise to act in this turn
    PROMISE = re.compile(
        r"\b(?:langsung|segera|sekarang)\s+(?:saya\s+)?(?:perbaiki|update|ubah|ganti|tandai|"
        r"kerjakan|buat|kirim|tutup|tambah|hapus|jalankan|proses)|"
        r"\bsaya\s+(?:akan\s+|segera\s+|langsung\s+)?(?:perbaiki|update|ubah|ganti|tandai|"
        r"tutup|tambah|hapus|jalankan)\b|\bakan\s+saya\s+(?:perbaiki|update|ubah|ganti|tandai|"
        r"tutup|tambah|hapus|kerjakan|buat|kirim)", re.I)
    FOLLOW_THROUGH = ("(Catatan sistem: kamu bilang akan melakukannya, tapi belum memanggil tool "
                      "apa pun, jadi belum ada yang berubah. Panggil tool-nya SEKARANG. Kalau "
                      "tidak ada tool untuk itu atau tool menolak, katakan jujur ke Boss.)")

    def promises(self, text: str) -> bool:
        return bool(self.PROMISE.search(split_reply(text)[1]))

    def respond(self, text: str, to: str | None = None, context: str = "",
                history: list[dict] | None = None, cost_ok: bool = False,
                default: str = "laras", meeting_id: str | None = None,
                present: list[str] | None = None, consult: bool = True,
                ask_above: float | None = None) -> AgentReply:
        agent = self.who(text, to, default, present)
        cmd = self.command(text, agent, meeting_id)
        if cmd is not None:
            return cmd
        chat = small_talk(re.sub(r"^\s*@\w+\s*", "", text), agent)
        if chat is not None:                       # "halo", "makasih": no LLM, $0
            return AgentReply(agent, chat[1], chat[0], kind="small_talk")
        office = self.office
        box = ToolBox(office, agent, meeting_id, tasks=consult, consult=consult)
        notes = office.messages_for(agent)
        if notes:                                  # the Boss's orders she must not forget
            context += ("\nPerintah/pesan Boss untukmu yang BELUM selesai (jangan diabaikan: "
                        "kerjakan dengan tool, atau laporkan statusnya dengan update_order; kalau kamu "
                        "membuat alur untuknya, isi for_order di plan_relay):\n"
                        + "\n".join(f"- #{n['id']} [{n['status']}] {n['text']}" for n in notes))
        system = system_prompt(TEAM[agent], context)
        msgs = [Message.user(h["text"]) if h["role"] == "boss" else Message.assistant(h["text"])
                for h in (history or [])[-6:]]
        msgs.append(Message.user(text))
        chars = len(system) + sum(len(h.get("text", "")) for h in (history or [])[-6:]) \
            + len(text) + len(json.dumps([s.parameters for s in box.specs()]))
        est = costs.estimate(office.router, AGENT_KEY[agent], chars, office.project,
                             max_out=500, max_calls=3)
        if est.provider == "offline":
            return AgentReply(agent, line("offline"), "apologetic", offline=True,
                              estimate=est.to_dict(), kind="offline")
        if not est.affordable:
            return AgentReply("laras", est.say() + " Saya tahan dulu pertanyaan ini, Boss.",
                              "worried", estimate=est.to_dict(), kind="held")
        limit = max(office.settings.budget.ask_above_usd, ask_above or 0.0)
        if est.high_usd > limit and not cost_ok:
            office.events.emit("cost_estimate", "laras", **est.to_dict(), question=text[:200])
            return AgentReply("laras", est.say() + " Lanjut, Boss?", "serious",
                              estimate=est.to_dict(), needs_cost_ok=True, kind="ask")

        office.events.emit("agent_status", agent, status="working", task="menjawab Boss")
        total, reply_text, provider = 0.0, "", ""
        try:
            k, limit, nudged = 0, 3, False
            while k < limit:
                last = k == limit - 1
                k += 1
                resp = office.router.complete(LLMRequest(
                    system, msgs, [] if last else box.specs(), max_tokens=500,
                    agent=AGENT_KEY[agent], project_id=office.project.id))
                total += resp.cost_usd
                provider = resp.provider
                if resp.provider == "offline":
                    return AgentReply(agent, line("offline"), "apologetic", box.created,
                                      offline=True, kind="offline")
                if not resp.tool_calls:
                    if not nudged and not last and self.promises(resp.text) and not box.acted():
                        nudged, limit = True, limit + 1      # said "saya update" but called nothing
                        msgs.append(resp.as_message())
                        msgs.append(Message.user(self.FOLLOW_THROUGH))
                        continue
                    reply_text = resp.text
                    break
                msgs.append(resp.as_message())
                results = []
                for c in resp.tool_calls:                # the office shows each real step
                    office.events.emit("agent_status", agent, status="working",
                                       task=tool_label(c.name, c.args), meeting_id=meeting_id)
                    results.append(ToolResult(c.id, *box.call(c.name, c.args)))
                msgs.append(Message("user", results))
        except AllProvidersFailed:
            return AgentReply(agent, line("offline"), "apologetic", box.created, offline=True,
                              kind="offline")
        finally:
            office.events.emit("agent_status", agent, status="idle", task="")

        mood, reply_text, thought = split_reply(reply_text)
        reply_text = polite(reply_text)
        tables, rest = B.markdown_tables(reply_text)      # tables belong on the board
        for k, t in enumerate(tables):
            box._show(f"Tabel dari {TEAM[agent].name}" + (f" ({k + 1})" if len(tables) > 1 else ""),
                      B.markdown_to_html(t))
        if tables:
            reply_text = (rest + " Detailnya saya tampilkan di papan, Boss.").strip()
        actual = costs.record_actual(office.project, est, total, f"{agent}: {text[:60]}")
        if total > 0:
            office.events.emit("cost_actual", "laras", usd=total, estimate_mid=est.mid_usd,
                               within_range=actual["within_range"], provider=provider)
        return AgentReply(agent, reply_text or line("ack"), mood, box.created,
                          est.to_dict(), actual, thought=thought, provider=provider,
                          board=box.shown)
