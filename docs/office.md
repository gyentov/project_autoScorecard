# The office (Phase 2a)

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
| Kickoff & timeline | Laras | team introduction, timeline (Gantt slide), working rules | ACC timeline |
| Data & join | Bening | profiles every uploaded table, picks the base table, proposes keys; one-to-many tables are aggregated | ACC join plan |
| Target definition | Bening | proposes `bad` (outcome-table columns first), bad rate overall and per quarter, out-of-time split (≈ latest 15%), candidate features with reasons for every excluded column | ACC, or pick an offered alternative |
| Data dictionary | Bening | memo listing missing descriptions / guessed types; does not block modelling | upload a dictionary any time |
| Waterfall plan | Citra | the plan diagram and criteria | ACC plan → Cut 1 is built |
| Cut 1 | Citra | build runs in the background (live progress), results email + presentation | type feedback, ACC proposals, go to Cut 2 |
| Cut 2 (final) | Citra | build with the ACC'd changes, comparison with Cut 1 | confirm final, or extend |
| Cut 3+ | Citra | only on extension; your typed feedback is the recorded reason | confirm or extend again |
| Deck | Kirana | Phase 3: HTML draft first, PDF or PPTX only after your ACC | |

The recommendations (base table, keys, target, split, exclusions) come from
fixed rules in `office/prep.py`, not from the LLM. They are reproducible, cost
nothing, and still work when no LLM is available.

The timeline is an **order of milestones** (no dates). Each milestone has an
owner and a status (not started / in progress / waiting for Boss / done). An
extension adds Cut N+1 before the deck.

## Meetings

Each meeting is a visual-novel script, `{speaker, mood, slide, text}`, played
over slides on the meeting-room projector, followed by:

- **choices**: the decision buttons for this meeting;
- **a free-text box**: ask questions or give instructions (see below).

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
- Over the remaining budget: she holds the question.

Afterwards she reports the actual cost and whether it fell inside the range.
After 3 paid answers in a project, estimates are scaled by the median
actual/estimate ratio, and she says they are calibrated.

## Inbox

MOMs, memos (handoffs, dictionary gaps, failures), meeting invites and results
emails, with attachments (reports, slides). Raw data is never attached.

## Events (for the 2b pixel office)

`projects/<id>/events.jsonl` is the ordered record of what actually happened:
agent status, job progress, handoffs, emails, meetings and dialogue lines,
proposals, approvals, milestones, cost estimates and actuals. The 2b office
replays it; there are no filler events. The needs-driven life simulation
(coffee, snacks, the dispenser) is cosmetic, runs in the browser, and never
delays real work.

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
| `POST /api/projects/{id}/office/say` `{text, meeting_id?, to?, cost_ok?}` | type to the team |
| `POST /api/projects/{id}/proposals/{pid}` `{accept}` | ACC or reject a proposal |
| `GET /api/projects/{id}/inbox`, `…/inbox/{eid}` | emails (reading marks as read) |
| `GET /api/projects/{id}/files/{path}` | attachments and slides (artifacts and meeting folders only) |
