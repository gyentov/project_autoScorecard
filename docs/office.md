# The office (Phase 2a + 2b)

The team works the project stage by stage. Every decision is yours, taken in a
meeting with a choice or by typing, and recorded in an emailed MOM and the
audit log. The engine does every calculation; the team proposes and explains.

Code: `autoscorecard/office/`. Try it: `.venv/bin/python scripts/office_cli.py --demo`.

## The team

| | role | character | handles |
|---|---|---|---|
| **Laras** | Partner / project lead | warm, organised, honest about cost and schedule | kickoff, timeline, meetings, MOMs, cost estimates |
| **Bening** | Prepper | meticulous, a little fussy about missing dictionaries | tables, joins, target, data dictionary |
| **Citra** | Modeller | calm, numbers first, pushes back politely, then follows your decision | waterfall plan, cuts, bins, proposals |
| **Kirana** | Deck creator | cheerful, thinks about the audience | decks, from Phase 3 |

They speak Bahasa Indonesia and call you **Boss**. They are AI colleagues and
never claim otherwise. Personality changes wording, never numbers. Each has a
fixed set of portrait moods: neutral, happy, proud, thinking, worried,
apologetic, excited, serious, surprised.

## The flow

| milestone | who | what happens | your decision |
|---|---|---|---|
| Data first | Laras | before anything starts she confirms the data: files already in the project (e.g. the demo data) must be confirmed in 📁 Data; with no files she asks for an upload. "Mulai kickoff" opens that window when needed and starts kickoff right after the handover | confirm / upload |
| Kickoff & timeline | Laras | team introduction, timeline (Gantt slide), working rules | ACC timeline |
| Data & join | Bening | no meeting: at her desk she profiles every table you handed over, picks the base table, tries the candidate keys for each table and keeps the best by rule (one-to-many tables are aggregated); the "Catatan join data" memo lists what she used and what else she tried, and the Cut 1 meeting reports what each table brought to the model | none; say so if you want another join |
| Target definition | Bening | **vintage analysis first** when there is a monthly performance table (see below): cumulative 30+/60+/90+ DPD curves per booking quarter by months on book, the window where the curve flattens and the strictest threshold with enough bads; she proposes "ever T+ DPD within W months" from that history (immature accounts excluded) and offers the summary column as an alternative. Without history: proposes `bad` from summary columns (outcome-table columns first), bad rate overall and per quarter, out-of-time split (≈ latest 15%), candidate features with reasons for every excluded column | ACC, or pick an offered alternative |
| Data dictionary | Bening | memo listing missing descriptions / guessed types; does not block modelling | upload a dictionary any time |
| Waterfall plan | Citra | the plan diagram and criteria; "✏ Edit rencana waterfall sendiri" opens the editor | edit it yourself, then ACC plan → Cut 1 is built |
| Cut 1 | Citra | build runs in the background (live progress), results email + presentation | type feedback, ACC proposals, go to Cut 2 |
| Cut 2 (final) | Citra | build with the ACC'd changes, comparison with Cut 1 | confirm final, or extend |
| Cut 3+ | Citra | only on extension; your typed feedback is the recorded reason | confirm or extend again |
| Deck | Kirana | Phase 3: HTML draft first, PDF or PPTX only after your ACC | |

The recommendations (base table, keys, target, split, exclusions) come from
fixed rules in `office/prep.py`, not from the LLM. They are reproducible, cost
nothing, and still work when no LLM is available.

**Work happens at the desks first.** After you ACC something, the next owner works
at her desk for a short while (her status and steps show over her head, her desk
props move) before Laras schedules the next meeting.

The timeline is an **order of milestones** (no dates). Each milestone has an
owner and a status (not started / in progress / waiting for Boss / done). An
extension adds Cut N+1 before the deck.

## Bening documents the variables before Citra starts

After the target ACC, Bening works out what every variable and table means
(`office/meaning.py`): the bank's dictionary first, then the join (source column and
aggregation, "per nasabah"), then the name and value range, and one batched LLM guess
(names and aggregate statistics only) for what is still unclear. Each meaning says where
it came from and how sure she is. English entries from the bank's dictionary are
translated into Indonesian (one LLM call; offline she says it still needs translating).
The result, `artifacts/kamus_variabel_bening.md` (tables and variables), goes to the
Boss's inbox, fills empty descriptions in the data dictionary, and is handed to Citra
with the dataset; the plan meeting shows it.

**Laras reviews every version** (`office/dictreview.py`, $0): a meaning that is empty, only
repeats the name, is an untranslated bank description, a low-confidence guess not marked
as one, a derived variable without its formula, or an unknown table blocks it. Bening fixes
what Laras found (an LLM read when there is one, else an honest "perlu konfirmasi" mark)
and Laras checks again, up to three rounds; her memo lists what was found and fixed. Only
an approved version (`work/variable_meanings_approved.json`) is documentation: the model
documentation, the deck, Citra's report and `fitur_terpilih.html` read only that. Every
teammate can read it with the `dictionary` tool.

**Meanings change with the discussion.** "arti el = exposure limit" (or Bening's
`update_meaning` tool) records the new meaning for a variable or a table, keeps the old one
in its history, and sends the new version to Laras's review. Meanings settled this way
survive when Bening works the dictionary out again.

**Derived variables, at any time** (`office/derived.py`). "variabel baru dti =
loan_amount / cust_income" ($0), or Bening's `propose_derived_variable` tool: ratio,
difference, sum, product, log, flag (a comparison) or a missing flag. Bening shows a
preview on the training sample (IV, strength, missing rate); after the Boss's ACC the
variable is added to the dataset every time it is loaded, joins the dictionary (meaning =
its formula, reviewed by Laras), and is a candidate in the next cut (also after Cut 1).

## Citra's report at every cut

**Every results meeting presents Kirana's deck for that cut** on the projector (the
consulting format below, English), saved as `artifacts/<cut>/deck_<cut>.html` and attached
to the results email. The projector has two tabs: **Deck** (the prepared slides, ‹ › to
browse) and **Papan** (what the Boss asks to see during the meeting, plus working material
such as the WoE correlation, the join and the timeline). A query never goes into the deck.

Citra reads every model variable **bin by bin** (each bin's share of accounts, bad rate,
risk index = bin bad rate ÷ portfolio bad rate, WoE and points; the riskiest and safest
bin, thin bins, the trend and where it breaks), one line per variable on its deck slide.

**Score bands: from 20 to the ideal number** (`engine/risk.py`). The train score is cut
into 20 equal-population bands (any under 5% of accounts merged first); then the two
neighbouring bands that are least different are merged one at a time (a reversal first,
else the highest two-proportion p-value), recording every step. The ideal number is the
largest where the bad rate falls band after band in train, test and OOT, every
neighbouring pair differs significantly (p < 0.05) and every band holds at least 5% of
accounts. The **KS** of the banded score is recorded at each step, and a KS table per band
(cumulative bads and goods) is made for every sample. The VL–VH levels are built from the
ideal bands (with fewer than five bands, the levels still span VH to VL). Files:
`risk_bands_20.csv`, `band_path.csv`, `risk_bands.csv` (ideal), `ks_table.csv`,
`band_choice.json`, `score_contribution.csv`.

**Hyperparameter tuning when the model is finalised** (`engine/tuning.py`, config
`modelling.tuning`). On the final cut only, the training sample is split again (75% fit,
25% validation; test and OOT never used to choose) and every combination of the smallest
bin, the most bins and the stepwise / forward entry p-value re-runs the approved
waterfall. Chosen: the best validation Gini after a penalty for a fit–validation gap above
0.05; near-ties go to fewer variables, then to the planned settings. The final model uses
the chosen settings; the deck and the documentation show every combination
(`tuning.json`, `tuning.csv`).

In the final cut Citra proposes **VL, L, M, H, VH**: contiguous groups of bands from the
lowest scores (VH) up, about a fifth of accounts each, and says she wants to discuss it.
The Boss can ACC it, or open the editor (🎚 Ubah batas kategori risiko) and set each
band's level; the order must run VH → VL. Citra repeats the result in the meeting
(`risk_levels.csv`).

## Orders, messages and the MOM

- **Orders.** Anything the Boss asks a teammate to do (not a question, not a $0 shortcut)
  becomes a numbered order with owner and status (baru, dikerjakan, selesai, terhambat),
  listed under 📋 Perintah. The owner sees her open orders every time she answers and
  reports with `update_order`. Laras's briefing names the open ones.
- **Orders the office can do itself** run at once: documenting variables and tables
  (Bening), the model documentation (Laras), the deck draft (Kirana). If one cannot run
  yet, it is blocked with the reason and retried automatically after every cut and at
  the final confirmation.
- **Messages.** "Tolong sampaikan ke Citra …", "titip pesan buat Bening …": the messenger
  walks it over, the recipient confirms, the Boss gets a memo, and it is an open order
  for the recipient.
- **The MOM is acted on.** After a meeting with the Boss's input, Laras (agent
  `partner_mom`, Claude Sonnet in `config.yaml`) turns each direction into actions:
  proposals for ACC, tasks for teammates (`assign_task`), data on the board. She mails
  "Tindak lanjut MOM" with what she did and what it cost. After an adjournment her
  proposals appear as choices in the follow-up meeting. Without an LLM she forwards each
  direction to the right teammate and says so.
- **Manners.** No "lu/gua" from the team, to the Boss or each other (a rule in the prompt
  and a filter on every reply); the Boss may.

## Teamwork and the timeline

- **Relays.** An order that names several teammates in turn ("kamus suruh Bening kerjakan,
  kasih ke Citra untuk dipasangkan ke fitur terpilih lalu berikan ke Kirana") becomes a
  relay ($0, parsed by rule): a parent order for Laras with one step per teammate. Each
  step starts when the one before is done, and Laras reports every step to the Boss (a
  line in the office and a memo). Steps the office can do run by themselves (Bening's
  dictionary, Citra's `fitur_terpilih.html` mapping the dictionary to the model's
  variables, Kirana's deck); a blocked step is retried when the project moves on. The
  orders panel shows the steps and where the relay is.
- **Delegating and asking.** Every agent can `assign_task` (the teammate starts on it at
  once, with her own tools, and closes it with `update_order`), `plan_relay`, and
  `ask_teammate` (the answer comes back to her, so a question is not bounced back to the
  Boss). A consulted teammate cannot consult or delegate further, so there are no loops.
- **Checking a teammate's work.** Every agent has `check_teammate_work` (one teammate, the
  whole team, or one order/relay number): open tasks with their latest notes, what was
  finished last, where a relay stands, and the owner's open milestones. Agents are told to
  check before saying where a teammate's work is, or before waiting on it. The Boss's own
  question ("Bening lagi ngerjain apa?", "alur #5 sampai mana?", "progres tim?") is
  answered from the same records by rule ($0, no model call), by whoever was addressed.
- **The timeline is Laras's.** She can change a milestone's status, note, title, owner or
  length, add a milestone (after one, optionally before another) and remove one she
  added. Milestones decided by meetings (kickoff, data, target, waterfall plan, cuts)
  cannot be closed by hand; she says why. Every change puts the new Gantt on the board
  (the projector, in a meeting) and is audited.
- **Timeline instructions are done, not promised.** "Tandai kamus dan deck selesai di
  timeline" or "kenapa timeline masih menunggu Boss, kan sudah done" changes the
  milestones at once by rule ($0; named milestones, else the ones waiting for the Boss),
  shows the new Gantt and closes the matching open order. Any agent who says "saya update
  sekarang" without calling a tool is sent back once to call it or say honestly why not.
- **Turns in meetings.** In a coordination or casual meeting every attendee answers the
  Boss in turn (each hears what the others just said); naming one person asks only her.
  In stage meetings the host answers unless the Boss asks the others ("yang lain juga",
  "masing-masing", "gantian") or names several. Nobody can call a second meeting from
  inside one, and only the Boss closes it. At the desks Laras stays the coordinator.
- **Meetings on request.** "Yuk meeting" makes Laras call a coordination meeting (topic,
  attendees, the timeline, and the deck draft when the topic is the deck); the Boss closes
  it with "Selesai", and their directions go through the MOM as usual.
- **Memory at the desk.** Each answer at a desk sees the last four exchanges with that
  teammate, so "2" or "yang tadi" are understood. Agents are told never to claim work no
  tool did, and to act on clear directions instead of asking again.

## Deliverables

`office/deliverables.py`, from the confirmed (else latest) cut: the **model
documentation** (`office/report.py`: an Indonesian report in the style of a bank or
consulting model report, one HTML file that prints to A4: cover, contents, executive
summary with key figures and a recommendation, then numbered chapters on data, bad
definition and vintage, the approved dictionary, the development process, tuning,
performance, every variable bin by bin, score bands 20 → ideal with the KS table, VL–VH
levels with illustrative policy, decisions and the audit trail, and a methodology
appendix; numbered figures and tables with sources, and "Arti bagi bisnis" at the end of
each chapter) and Kirana's **deck draft** (English, 16:9 HTML slides). Both are made
when the final model is confirmed, or on request, and mailed with the file; the deck
waits for the Boss's review before a PDF / PPTX export (not built yet).

The deck (`office/deck.py`, charts in `office/deckviz.py`) is written consulting style:
every slide title states the takeaway, the chart sits beside its "so what", and every
slide names its source. Charts follow Knaflic: the message in one accent colour, the rest
grey, labels on the data instead of legends. Slides: cover, executive summary with a
recommendation, background, bad definition (each DPD threshold with its bads and
window, and why the chosen one wins), vintage analysis, development process and samples,
model waterfall (each rule and what it removed or added), performance, score drivers,
recommended score bands (reversals flagged), risk levels (proposal or agreed, with an
illustrative policy), one transparent slide per variable (every bin's accounts, bad rate,
WoE and points), and a methodology appendix. All text comes from the numbers by rule, so
it costs nothing and never claims more than the build shows: a weak Gini is called weak,
and a variable with no business meaning is flagged for removal.

## Editing the waterfall

📐 Waterfall in the ribbon (also a choice in the plan meeting, and in Citra's chat)
opens the editor: one card per step; drag a card by ⠿ (or ↑/↓) to reorder, switch a
step on or off, edit its parameters, remove it, or add a step type that is missing.
Saving validates the plan (a bad order such as Finalization not last is refused with
the reason). The Boss's own edit is a decision, so it applies at once, no ACC: Citra
says what changed in words ("Missing rate: max 0.3 → 0.2; Forward selection dimatikan;
urutan: …"); in the plan meeting the projector's plan slides are rebuilt on the spot;
after Cut 1 the change is used from the next cut and named in its comparison
(`state.plan_edits`). A plan-change proposal ACC'd in the plan meeting also refreshes
the slides. Citra's `show_waterfall` tool puts the current plan on the board.

## Who speaks in a meeting

Only the people invited answer in a meeting. A question that would go to someone
absent (e.g. data for Bening in the plan meeting) is taken by the host, who has the
same data tools. The whole team, Kirana included, is at the kickoff.

## The Boss's words are summarised

MOMs, follow-up meetings, adjournments, extensions and the timeline carry the Boss's
input as one summarised action sentence (`office/summary.py`: one tiny LLM call when
available, else a topic summary such as "Tinjau ulang timeline sesuai arahan Boss."),
not word for word. The exact words stay in the conversation log for audit.

## Vintage analysis

`engine/vintage.py`. A performance-history table is found by its shape: several rows
per account key of the base table, a months-on-book column (`mob`, `month_on_book`,
`bulan_ke`, ...) or a snapshot date, and a DPD column. It is never joined as features
(it would leak the outcome); the join memo says so.

- **Curves:** for 30+, 60+ and 90+ DPD, the share of accounts that ever reached it by
  each MOB, per booking quarter and overall. Months not observed yet are censored, not
  counted as good (a life table), so young cohorts do not drag the curve down; and
  ever 30+ ≥ ever 60+ ≥ ever 90+ is kept. Charts drop each line's thin tail (fewer than
  half its accounts observed).
- **Window:** the first standard MOB (6, 9, 12, 18, 24) where the overall curve reaches
  85% of its last well-observed value; if fewer than 60% of accounts are that old, the
  longest window that keeps 60% (Bening says it is a compromise).
- **Threshold:** the strictest of 90+, 60+, 30+ with at least 300 bads and a 2% bad rate
  at that window.
- **Target:** `vin_ever{T}_mob{W}` is added to the dataset: 1 = reached T+ within W
  months, 0 = observed W months and never did, empty = immature (not modelled). It and
  every outcome-table column are always excluded from the candidate features. The OOT
  split is taken among accounts with a known outcome.

The demo data has `performance_monthly` (DPD per month on book up to 24, observed to
2025-12), exactly consistent with `max_dpd_12m`, with some defaults after month 12: its
vintage says ever 90+ within 18 months.

## Meetings

Each meeting is a visual-novel script, `{speaker, mood, slide, text}`, played
over slides on the meeting-room projector, followed by:

- **choices**: the decision buttons for this meeting. You are never forced to ACC:
  the kickoff, target and plan meetings also offer **"Belum ACC: bubarkan rapat,
  kerjakan masukan saya dulu"**. Type what you want first; the meeting ends (the MOM
  says it was not ACC'd and lists your input), the owner reworks at her desk, and
  Laras calls a follow-up meeting that opens with your input. Changes you ACC'd as
  proposals (e.g. another target) are kept in the redo.
- **a free-text box**, shown once the script has been played and it is your turn:
  ask questions or give instructions (see below).

Results are presented in the shortest form: features kept, Gini test/OOT, PSI,
highest WoE correlation, forced features, and from Cut 2 on, what changed since
the previous cut. When nothing you asked for became an ACC'd change, Citra says
so plainly.

When the meeting ends, Laras emails the **MOM**: attendees, agenda, discussion,
Q&A, decisions, next steps, LLM cost of the meeting, and the timeline status.

## Typing to the team

1. **Routing ($0):** `@citra …` goes to Citra. Otherwise keywords pick the
   agent (bins/Gini/model → Citra, join/data/target → Bening, cost/timeline →
   Laras).
2. **Commands ($0, no LLM):**
   `paksa <feature>` · `batal paksa <feature>` · `buang <feature>` ·
   `kembalikan <feature>` · `bins <feature> 25 35 45`.
   Each becomes a proposal. Unknown column names get a "did you mean" answer.
3. **Questions (LLM):** the agent answers with tools that return aggregates
   only (profiles, bin tables, IV, metrics, what-if bins), never rows. At most
   3 LLM calls per question.

**Data goes on the board, not in the chat.** Every teammate can show data when asked:

| tool | what goes on the board | what the model gets back |
|---|---|---|
| `show_aggregate` | a group-by with count, mean, sum, min, max, median, nunique, missing_rate or bad_rate; numeric columns are cut into quantile bands ("21 – 30"); optional filters; a bar chart plus the table | the aggregated table |
| `show_sample` | up to 20 sample rows (random), chosen columns, optional filters | only how many rows and which columns; never the values |
| `show_columns` | the column profile of a table: type, missing rate, distinct values | the profile |
| `show_vintage` | the vintage curves (per cohort for one threshold, and 30+/60+/90+ overall) with the window marked, and the recommendation table | the recommendation and the per-threshold table |

The data is the joined dataset (with `bad` once the target exists), or any handed-over
table by name; before the join the main table. In a meeting the item becomes the next
projector slide and the speaker's line points at it (it is also attached to the MOM);
at a desk the **board window** opens, and 🖥 Papan in the ribbon pages through
everything shown so far (`work/board/`). The rules tell the agents to put detail on the
board and keep the spoken answer to the conclusion; if a model writes a table into its
answer anyway, the server moves it to the board. Free shortcuts, no LLM: `sampel 10`
(`sampel 5 dari customers`), `vintage` / `vintage 60`, `bad rate per cust_age`, `jumlah per channel`,
`rata-rata income per cust_province`, `missing per province dari customers`. A
mistyped column gets the nearest real names ("mungkin maksudnya: province").

**Never just "…".** The moment the Boss asks, the page asks the server (`/office/route`,
free and instant) who will answer; she steps forward and says a short line in
character ("Sebentar ya Boss, saya tarik datanya dulu."). Each real step she takes
appears under it as it happens ("▸ menghitung bad_rate per cust_age", from an
`agent_status` event per tool call), with a "masih saya kerjakan" note if nothing has
happened for 8 seconds. Anything she puts on the board shows on the projector (or opens
the board window) the moment it is made, before she finishes talking. The page polls the
event stream every 0.3 s while someone is working, every 1.5 s otherwise.

**Slides are always light.** The projector is a white screen: slide pages ignore the
computer's dark theme, and SVG slides lose their dark-mode block, so text is never dark
on dark (`meetings.light_only`). Report files keep both themes.

**Only the answer reaches the Boss.** The model writes its spoken words inside
`<ucap>…</ucap>` and may think first inside `<batin>…</batin>`. The screen, the
bubbles, the meeting transcript and the events show only the `<ucap>` part.
Anything else the model wrote (its `<batin>`, or planning written outside the tags)
is kept as the exchange's `thought` in the conversation log, for audit.

## The conversation log

`projects/<id>/conversations.jsonl` records every time the Boss types, at a desk or
in a meeting: what the Boss said, who answered, the answer, the mood, the real cost
in USD, Laras's upper estimate, the provider, what kind of answer it was (LLM,
greeting $0, command $0, cost question, offline) and the hidden `thought`. Handing
data to Laras is logged too. `GET /api/projects/{id}/conversations` returns the log
without thoughts (only `has_thought`); `?audit=true` includes them; `?since=` limits
it to a time.

## Handing data to Laras

Click **📁 Data** in the bar (or "Serahkan data" in Laras's chat). A window lists the
project's files with their size in rows and columns only, never their rows; add
files from the computer (CSV, Excel, Parquet) or drag them in, tick the ones to use,
and press **Serahkan ke Laras**. Files that came with a demo project are tagged **data
default (demo)** with a note that they are synthetic, not the Boss's; each file has 🗑
and there is "Hapus semua data default". Removed files go to the project's
`work/removed/` (recoverable), only while the data is not locked yet. Laras thanks the Boss, says what she received
(tables and data dictionary) and walks it over to Bening. Only the ticked files are
used from then on (`work/office.json: data_files`). The choice can change until the
data stage starts; after that the data feeds the model, so it is locked (a new
project is the way to use other data). A new project from the picker starts empty,
and the window opens by itself after the briefing.

**Proposals.** Nothing that changes the model happens without your ACC. A
proposal (forced feature, exclusion, bin edit, WoE override, plan change,
target) appears as an ACC / reject choice in the meeting. ACC'd changes apply
from the next cut. The same proposal made twice is kept once.

## Cost honesty

Before an LLM answer, Laras estimates a **range** from the actual prompt size
and the config prices: input = characters/4 to characters/3 tokens, up to 3
calls, tool results counted, no cache discount (so the top is conservative).

- Free or offline providers: she says it's $0.
- Upper end above `budget.ask_above_usd` (default $0.02): she asks first.
  The Boss can click Yes or type "ya"/"ok" (the waiting question then runs); a typed
  complaint about the cost gets a free explanation. One yes holds for 30 minutes for
  questions up to twice that estimate, so she does not ask before every message.
  Status questions ("update #3"), thanks and praise are answered for $0.
- Over the remaining budget: she holds the question.

Afterwards she reports the actual cost and whether it fell inside the range.
After 3 paid answers in a project, estimates are scaled by the median
actual/estimate ratio, and she says they are calibrated.

## Inbox

MOMs, memos (handoffs, dictionary gaps, failures), meeting invites and results
emails, with attachments (reports, slides). Raw data is never attached.

## Events

`projects/<id>/events.jsonl` is the ordered record of what actually happened:
agent status, job progress, handoffs, emails, meetings and dialogue lines,
proposals, approvals, milestones, cost estimates and actuals. The 2b office
replays it; there are no filler events. The needs-driven life simulation
(coffee, snacks, the dispenser) is cosmetic, runs in the browser, and never
delays real work.

## The pixel office (Phase 2b)

```bash
.venv/bin/uvicorn autoscorecard.server.app:app      # then open http://127.0.0.1:8000/
```

The page is read fresh from disk, but the Python side only changes when uvicorn
restarts: after an update, stop it (Ctrl+C) and start it again (or run it with
`--reload`). The page warns when the server is older than the page.

**Projects.** The picker opens a project, makes a new one (empty, or with the
synthetic demo data), or deletes one: 🗑 moves its folder to `projects/.trash/`, so
it can be restored by moving it back.

One screen (28 x 15 tiles), a modern corporate office with Japanese touches, in 3/4
top-down pixel art: a glass meeting room carpeted up to its glass wall, with walnut
slat walls and a projector; the work area on ash wood floor under a whiteboard (the
live timeline) between city-view windows with bamboo blinds; a walled pantry on a
terrazzo floor, entered from the work area through a sliding door (water dispenser, coffee
machine, microwave, sink, vending machine, air-con, wall clock); a raised tatami room with a
kotatsu (staff sit on the floor there, legs under the futon); and the office front:
a tall feature wall (walnut slats, a stone panel with the backlit logo, downlights),
marble floor with a gold inlay, a marble-topped walnut reception counter with an
orchid and a bell, armchairs, a sofa, and glass doors with gold frames. The Boss's
desk is right next to Laras's, with the red post box beside it.

**Arriving.** The Boss enters at the office front. Laras is waiting by reception,
greets the Boss, bows, and walks with them to their desk; the Boss sits down and she
gives the briefing standing beside it. By then the day has started: most of the
team sit at their desks, and one or two may be at the pantry, the whiteboard, the
lounge or the huddle table.

**The tour.** The first time each project is opened, Laras then shows the office, step by step, with an
arrow at each thing: the Boss desk and its log drawer, 📁 Data, the mailbox, the
whiteboard, the meeting-room door, proposals, chatting with the team, the cost
corner, music and the help signs, and Mulai kickoff. It is a fixed script ($0).
Continue with the button, a click on the box, Enter or Space; skip it any time; type
a question at any step (a normal chat with Laras) and carry on after her answer. ⚠
Petunjuk → "Ulangi tutorial Laras" replays it.

**Desks show the work.** Each desk carries the tools of its owner's job, next to the
PC, and while she works they move: Bening's sheets travel from the RAW tray to the
OK tray while her screen scrolls through rows of data; Citra's score curve draws
itself and her calculator blinks; Kirana sketches on her drawing tablet and her
slides change; Laras's plan bars fill in and her phone blinks. Seated staff are
shown from behind, from the waist up over the chair's backrest; anyone standing on
or next to a chair is drawn in front of it. Desks stand in joined rows like a real
office (Bening, Citra and Kirana on one bench; Laras's desk joined to the Boss's), in
3/4 view with a lit top, an apron, legs and a floor shadow.

**What can be clicked** carries a small bobbing tag (✉ mailbox, 📊 whiteboard, 🗄 the
Boss desk drawer, 🚪 the meeting-room door when a meeting is ready), and hovering
anything clickable, teammates included, shows a label and a hand cursor.

**Controls:** WASD or the arrow keys to walk (the last key pressed wins, so you can
turn while another key is still held), or click the floor to walk there. Walk back
to your desk (or press E there) to sit down. Click
a teammate (or stand next to her and press E) to chat. Click the mailbox for the
inbox and the whiteboard for the timeline.

**The ribbon** at the top right, next to "Masuk rapat", holds every feature: 📁 Data,
✉ Inbox, 📝 Usulan, 📊 Timeline, 🗄 Log, 🎵 Musik, ⚠ Petunjuk (road signs explaining
every control, and the tour replay) and ⌂ Proyek.

**Meetings** play as a visual novel. Everyone attending stands on stage the whole
meeting, each on a fixed side (the host on the left, the others alternating right,
left, right). On each side the latest speaker stands in front and the other steps
back. The speaker is lit, the others are dimmed, and her standing art
(`<pose>_<mood>` from the script line) changes with each line.
Click or press Space to advance. At the end come the choices, plus a text box to
type anything. Laras's cost question ("Lanjut, Boss?") appears as a yes/no choice.
Walk into the meeting room, or press "Masuk rapat", to join.

**Music.** 🎵 in the ribbon turns the background music on or off, picks the mood
(Pagi di kantor, Fokus kerja, Lembur malam lo-fi) or your own music file, and sets
the volume. It is composed live in the browser (WebAudio, no files, $0); it starts on
the first click, because browsers block sound before that, and the setting is
remembered in this browser.

**Top-left corner.** 💬 shows what this session cost and its last two exchanges;
click it, 🗄 Log, or the Boss's desk (its drawer) to open the full log with the price
of every exchange. An agent's hidden thinking appears there only behind a
closed "🔒 Isi hati (audit)" line.

**Desk chat** (`candid`) uses the same rules as meetings (numbers only from tools,
changes only as proposals, honest about being an AI), in a friendlier tone.
Greetings, thanks and "apa kabar" are answered from templates ($0, no LLM).

**What moves and why:**

| On screen | Caused by (real event) |
|---|---|
| typing at the desk / "❗ menunggu Boss" | `agent_status` working / waiting_boss |
| walking over with a 📄 memo | `handoff` |
| an envelope flying to the mailbox, a toast | `email_sent` |
| walking to the Boss's desk with 📝 | `proposal` |
| the big ACC / DITOLAK stamp | `approval` |
| the host at the whiteboard, the team gathering in the meeting room, the door glowing | `meeting_scheduled` (until `meeting_ended`) |
| ⚙️, step counts floating above Citra, 🎉 | `job_started`, `job_progress`, `job_finished` |
| 🧮 and the cost floating above Laras | `cost_estimate`, `cost_actual` |
| whiteboard dots (green done, blue in progress, orange waiting for the Boss) | `milestone` / the timeline |

The **life simulation** is the only thing not driven by events. Each teammate gets
hungry, thirsty and sleepy over time and acts it out in character: Laras makes
tea at the dispenser, Bening gets water, Citra gets coffee, and Kirana buys a
frozen meal and heats it in the microwave. Meals are eaten at the pantry table or
on the tatami, and a sleepy teammate either gets coffee or rests on a cushion. It only happens when she isn't in
a meeting or waiting for the Boss, it is purely visual, and it never delays real
work.

**Art files** (replaceable, same names and sizes):
- `assets/office/office.png`, `chair_back.png` + `layout.json`: the map;
  `scripts/art/office.py` draws all three, and the browser uses `layout.json` for
  walking, seats, the arrival and where to animate each desk.
- `assets/sprites/<id>.png`: 4 x 2 frames of 48 x 64 (down, left, right, up;
  standing, then mid-stride). Made by `scripts/art/map_sprites.py`: one
  gpt-image-2 call per character (about $0.055) draws a pixel-art turnaround from
  the character's reference in `assets/characters/_references/`, and `cut` slices
  it into the sheet. Walking alternates the two rows; sitting, drinking, bowing
  and cheering are small tweens plus emote bubbles.
- `assets/characters/`: the standing art; see `docs/character_art.md`.

The front end is plain files in `autoscorecard/server/static/` (Phaser 3 from a CDN,
no build step).

## Code layout

`office/workflow.py` defines `Office` as a composition of mixins, one per domain, in
`office/flow/`: `data` (dataset, summaries for the tools), `handover` (the Boss's actions,
data handover, messages), `relays` (orders dispatched, relays, ad hoc meetings),
`conversation` (what the Boss types, the free rule-based replies, meeting turns),
`stages` (kickoff, data, target, vintage), `deliverables_orders`, `dictionary` (versions,
review, meanings, derived variables), `plan` (waterfall plan), `cuts` (builds, results
meeting) and `score_report` (contribution, bands, KS, risk levels); `flow/common.py` holds
the shared helpers. A new domain is a new mixin added to `Office`'s bases.

## API

| endpoint | purpose |
|---|---|
| `POST /api/projects/{id}/office/start` | kickoff |
| `GET /api/projects/{id}/office` | stage, open meeting, cuts, timeline, unread count, pending proposals, cost |
| `GET /api/projects/{id}/office/briefing` | Laras's briefing lines |
| `GET /api/projects/{id}/office/events?since=N` | events after seq N |
| `GET /api/projects/{id}/office/timeline.svg` | the Gantt slide |
| `GET /api/projects/{id}/meetings`, `…/meetings/{mid}` | meetings, script, slides, choices, transcript |
| `POST /api/projects/{id}/meetings/{mid}/choose/{choice}` | take a decision |
| `POST /api/projects/{id}/office/say` `{text, meeting_id?, to?, cost_ok?, candid?}` | type to the team (`candid`: relaxed desk chat) |
| `POST /api/demo` | new project with the synthetic demo data |
| `GET /api/projects/{id}/conversations?since=&audit=` | the conversation log and its cost |
| `GET /api/projects/{id}/data` | the project's files: kind, rows, columns, chosen or not (shapes cached in `work/data_shapes.json`) |
| `DELETE /api/projects/{id}` | move the project to `projects/.trash/` |
| `GET /api/projects/{id}/board` | everything shown on the board |
| `POST /api/projects/{id}/office/route` `{text, meeting_id?, to?}` | who will answer, and her first line ($0, instant) |
| `POST /api/projects/{id}/data/remove` `{files}` | take files out of the project (to `work/removed/`) |
| `POST /api/projects/{id}/data/handover` `{files}` | hand the chosen files to Laras |
| `POST /api/projects/{id}/proposals/{pid}` `{accept}` | ACC or reject a proposal |
| `GET /api/projects/{id}/inbox`, `…/inbox/{eid}` | emails (reading marks as read) |
| `GET /api/projects/{id}/files/{path}` | attachments and slides (artifacts and meeting folders only) |
