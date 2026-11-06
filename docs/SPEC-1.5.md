# Calling Mom 1.5: count what reaches you, keep the board moving

Status: direction approved by Timon on 2026-11-06. This spec is the source of truth for the 1.5 work packages; a card's
text never overrides it. If the spec is wrong, the worker comments on the card and blocks with kind `needs_input`
instead of improvising.

## 1. Why

1.4 shields your attention from the workers. Building 1.4 on its own board (`coding`, watched by calling-mom 1.3.1)
showed what calling-mom still cannot see. Numbers from `hermes calling-mom report --days 2 --board coding`, run with
1.4.0 on 2026-11-06:

| | |
|---|---|
| Worker | 21 runs, 1,103 tool calls, 14 stuck signals |
| Mom | 38 calls, 128,372 tokens in / 17,655 out, "tips followed 22 / rejected 0" |
| Olymp | 0 calls |
| You | 0 briefs, 0 answers |

What really happened on those cards:

1. **Outside help is invisible.** The orchestrator wrote 7 comments, added 7 parent links, removed 2, reclaimed a
   running card, and one card (t_83bf91fe) ended in triage waiting for the owner. The report says "You: 0".
2. **"Followed" is not followed.** The heuristic counts advice as followed when any next action happens. The owner's
   audit (card t_2590fcab) found 4 of 4 "followed" while 3 were not acted on.
3. **A quota stall is silent.** A Claude subscription session limit stopped every worker and Mom for almost two hours
   (2026-11-05 11:19-13:10 UTC). No card said why or until when. Mom was also down for other reasons: an OpenRouter key
   over its limit, `ClaudeCodeLoggedOut` in a default-profile worker. Each case produced the same
   `[advisor unavailable]` comment and nothing else.
4. **Pacing is manual.** Running one card at a time took 9 link changes and a reclaim by hand.
5. **Subscription cost reads "no price reported".** True, and useless.
6. **A dead worker looks busy.** On 2026-11-06 a deploy recreated the Hermes container at 11:03 UTC. The worker of card
   t_6159c155 died with it, but the card stayed `running` with a stale claim until the orchestrator reclaimed it at
   13:06 UTC. Its `max_runtime` (3 h) would have released it at 13:58. Nothing on the board said so.

1.5 measures first. Every outside touch, every quota stall and every stalled card becomes a number with a named source,
and the manual chores (quota recovery, pacing, spotting dead workers) become a comment or one command. Card routing (Mom picks the tier and writes the card
contract) is planned for 1.6 and needs these numbers to show that it saves attention.

## 2. Principles

SPEC-1.4 section 2 holds in full. In addition:

1. **Count, don't guess.** Every number in the report comes from a recorded event, and the JSON names its source. When a
   source is not observed (the plugin is not enabled where the action happened, or the store does not record who acted),
   the report says `not observed` or `unattributed`, never 0.
2. **Observers only.** New inputs are documented observer hooks (`api_request_error`, `on_kanban_worker_exited`,
   `on_human_input_request`, `on_human_input_resolved`) or read-only reads of the Kanban store through the functions
   `_core.py` already wraps. Nothing in 1.5 vetoes a claim, answers a prompt or touches Hermes approvals.
3. **No new private imports outside `_core.py`.** A new Kanban read goes into `_core.py` with a doctor check, like the
   existing ones.
4. **Board writes only on request.** The one new board write (`pace`) runs only when a person runs the command, shows its
   plan with `--dry-run`, and `--undo` removes exactly the links it added. Writes calling-mom makes itself (model pins,
   unblocks, pace links) are logged as `own_write` and never counted as outside help.
5. **Error text never reaches the log or the state file.** `api_request_error` messages can contain provider or user
   data; the plugin keeps only what it parses (provider, model, reset time, error class).

## 3. Work packages

| WP | What | Priority | Depends on |
|---|---|---|---|
| WP1 | Honest count: outside help, chat waits, evidence-based advice feedback, subscription cost line | must | none |
| WP2 | Quota and pacing: `[quota]` comments, provider health, Mom fallback, stalled cards, `pace`, upstream seam drafts | must | WP1 |
| WP3 | Pre-action gaps from the owner's audit (card t_2590fcab) | should | WP2 |
| WP4 | Release candidate 1.5.0, review by Kore; not merged into `main` before 2026-11-12 | must | WP3 |

Branch model as in 1.4: every WP branches from `release/1.5.0`, opens a PR into it, squash-merges it itself once CI is
green, and verifies `MERGED` before completing. `main` stays 1.4.0 (the hackathon entry) until WP4 is approved.

### WP1: Honest count

**Outside help on a board.** For every opted-in board, the sweep reads the Kanban store (read-only, dedupe by store id)
and records `touch` rows:

- Comments whose author is neither the card's assignee nor calling-mom. calling-mom comments are recognised by their
  prefixes (the existing `[<category>/<signal>] run N ·`, `[advisor unavailable]`, `[pre-flight] HOLD:`,
  `[mom answered]`, `[olymp answered]`, `[for you]`, and WP2's `[quota]` and `[stalled]`) and by the plugin's own authors (`advisor`,
  `escalation`). Keep the author id (a profile or surface id such as `default`, `desktop`, `user`), never the text. A
  `[you via calling-mom]` comment is an answer (already counted), not outside help.
- Structural events: `linked`, `unlinked`, `reclaimed` (only `manual: true`), `unblocked`, `edited`, `archived`,
  `assigned`, `model_override_set`, `reasoning_effort_set`, `changes_requested`. The store records no actor for these, so
  they count as `unattributed`, minus every write calling-mom logged as `own_write`. Writes made by calling-mom before
  1.5 have no `own_write` row; the report cannot tell them apart and shows them as unattributed.
- Cards that reached a person: every card that entered `triage`, and every `needs_input` block that Mom did not answer
  (from the existing `question` rows), with the time until it left that state, or "still waiting".

**Chat waits** (switch `calling_mom.count.chat_waits`, off by default, `setup` offers it): `on_human_input_request` and
`on_human_input_resolved` in any session of a profile with calling-mom enabled give the kind of wait (clarify, approval,
sudo, other, from the documented payload), its duration and its outcome. Never the prompt, never the answer.

**Advice feedback with evidence.** When Mom's advice is delivered, extract its anchors (commands, paths and tool names in
backticks) and keep their hashes for that run. An anchor matches a later tool call of the same run when the call's
normalised command starts with the anchor (commands) or contains it (paths, tool names). A match makes the advice
`followed`; any other next action makes it `acted`; a run that ends first makes it `ignored`; advice without anchors
stays `unknown`. The old meaning of "followed" becomes `acted`. The report shows all four.

**Report** (CLI and `GET /summary`, both additive; `--since` and `--until` take an ISO date or date-time). Shape, numbers
illustrative:

```
You     0 briefs, 0 answers, 0 holds released
        outside help  7 comments (default 7) · 10 board changes (unattributed) · 1 card waiting on a person
        chat waits    not observed (calling_mom.count.chat_waits is off)
Advice  38 calls: 6 followed · 14 acted · 2 ignored · 16 unknown
Cost    Mom 128,372 in / 17,655 out tokens on a subscription (no per-token price), Olymp 0 calls
```

The Desktop page shows the outside-help counts on the "You" rung (plugin SDK only, as in 1.4).

**Accept**

- Table tests for every author and event class, the dedupe, `own_write`, and the four feedback results (advice naming
  `` `git status` `` followed by `git status --short` is `followed`; followed by `ls` is `acted`).
- Real check against the real store, read-only: `hermes calling-mom report --board coding --since 2026-11-05 --until
  2026-11-06T03:00` must show at least the outside help on the 1.4 build cards (t_0700ec9b, t_4f534667, t_4ead7b98,
  t_6473e3bf, t_4bad620a, t_215c6fc4, t_40c5abec, t_a53a687a, t_e7d72b8a, t_3ddcc2a8, t_83bf91fe): 7 comments by
  `default`, 7 links, 2 unlinks, 1 manual reclaim, and t_83bf91fe in triage. Paste the output into the PR and the card
  comment. Explain every other counted event from the store (kind and card). Never tune the code to the expected number.
- README ("Your money and your data"), SECURITY.md (data-flow table) and TECHNICAL.md name the new reads: authors of
  board comments and board events (counts and ids only), and the optional chat-wait counting.

### WP2: Quota and pacing

**Rate-limit awareness.**

- Errors are classified as rate limit, auth, key limit or other, from two sources: the plugin's own Mom and Olymp call
  errors (today's `advisor_error`), and `api_request_error` in worker sessions of opted-in boards. For Claude
  subscription messages such as `You've hit your session limit · resets 1:50pm (UTC)` the reset time is parsed with a
  bounded pattern (UTC only; anything else is "unknown").
- Provider health lives in `calling-mom-state.json`, keyed by profile and provider, because one profile's broken login
  must not silence another profile's Mom: `limited until <reset>` for a rate limit (30 minutes when the reset is
  unknown), `down for 30 min` for auth and key-limit errors. Every change is a `quota` row.
- `on_kanban_worker_exited` with `outcome == "rate_limited"`: one comment per card and limit window,
  `[quota] <provider> hit its usage limit; resets about HH:MM UTC. The dispatcher retries by itself; the work in
  <workspace> is kept.` (Without a known reset: "reset time unknown".) A crash stays a crash: the existing breaker
  escalation is unchanged and still only acts on blocked cards.
- While a provider is limited or down, Mom and Olymp calls to it are skipped without a model call, and the existing
  single `[advisor unavailable]` comment says until when.

**Mom fallback.** New optional `calling_mom.mom_fallback: {provider, model}` (or `route:NAME`), offered by `setup`,
granted like Mom (`plugins.entries.calling-mom.llm.*`), checked by `doctor` with one real call. When Mom's provider is
limited or down, the fallback answers and the comment header names it (`… (fallback)`). Mom's caps apply to it. Olymp
has no fallback: an expensive model is never replaced silently.

**`hermes calling-mom pace --board B --max N [--dry-run] [--undo]`.** Keeps at most N cards of the board ready or
running. Running cards count against N and head the chains. The `todo` and `ready` cards are ordered by priority, then
age, keep their existing parents, and are chained with parent links (N chains, round-robin) through the Kanban functions
`_core.py` already uses, never raw SQL. `triage` and `blocked` cards are skipped. The plan is printed first,
`--dry-run` stops there, and `--undo` removes exactly the links `pace` added (recorded in `calling-mom-state.json`).

**Stalled cards.** The sweep flags a card that is `running` without a heartbeat for `calling_mom.stall_minutes`
(default 15, 0 = off), read through `_core.py`. One comment per run: `[stalled] no heartbeat since HH:MM UTC (run N).
The worker is probably gone, for example after a gateway restart. If so: hermes kanban --board B reclaim <id> --reason
stalled`, and a `stall` row. calling-mom never reclaims by itself (principle 4). The Desktop half shows a `stalled` badge
for this prefix (the existing prefix table) and lists stalled cards in Needs you, read-only. The report counts stalled
cards and their minutes.

**Status, doctor, report.** `status` and `doctor` show provider health and the fallback. The report gains one line:
`Quota   1 limit (claude-subscription-directsdk-experimental, 1 h 51 min) · 3 cards paused · 0 Mom calls skipped · fallback used 0×`.

**Upstream seams.** Write `docs/UPSTREAM.md` with five Hermes requests, each naming the calling-mom code path that would
use it: (1) a dispatcher veto or a per-profile/per-provider concurrency limit (would replace `pace`), (2) a public read
API for Kanban events and comments (would replace the private reads in `_core.py`), (3) a way for a plugin to offer an
answer to a human-input request that the person can still override (Mom answers first in chat sessions), (4) read access
to subscription usage windows (share of the 5-hour window instead of tokens), (5) release a running card's claim when
the dispatcher host that holds it is gone (a recreated gateway container left t_6159c155 `running` for two hours; would
make the stalled-card comment rare). Timon files them.

**Accept**

- Recorded-payload tests with the real message shapes above, an OpenRouter `403 Key limit exceeded`, and
  `ClaudeCodeLoggedOut`; one comment per card per window; no message text anywhere in the log or the state file;
  per-profile health (a down provider in profile A does not skip calls in profile B).
- Real check on a scratch board (`calling-mom-demo` or a new scratch board, never `coding`): Mom pointed at a provider
  that fails, the fallback answers and the header says so; `pace --dry-run` with 4 cards prints the plan, `pace --max 1`
  leaves exactly one card ready, `--undo` restores the original links. Evidence in the PR and the card comment.
- Stalled cards: table tests (no heartbeat for 20 min: one `[stalled]` comment for that run; 10 min: none; a second sweep
  in the same run: no second comment; a new run may comment again). Real check on the same scratch board with
  `stall_minutes: 1`: suspend a running card's worker with `kill -STOP` (the PID stays alive, so the dispatcher keeps
  the claim), wait two minutes, see one comment and the badge, then `kill -CONT`. Evidence in the PR and the card comment.
- README, SECURITY.md and TECHNICAL.md: `pace` writes card links when a person runs it; the fallback receives the same
  redacted excerpts as Mom.

### WP3: Pre-action gaps (owner's audit)

Card t_2590fcab (created by Timon on 2026-11-01) is the brief. Implement its items 1, 2, 4 and 5; item 3 (feedback) is
WP1. Since 1.4, "critical" means "asked before it runs" (pre-flight), so the new critical rules (GUI-app quit via
`osascript … quit`, `killall`, `pkill`; copy or install into `~/Library/` or `/Library/`, also over `ssh`) get a
pre-action check. Patterns stay bounded (catalog review of 1.3.2).

The replay comes first and is a development analysis, not plugin code: take the real command texts of the audited runs
from the worker transcripts (read-only), run old and new rules over them, and report false positives and negatives in
the PR before the rules change. No shipped code reads transcripts.

**Accept:** as in t_2590fcab plus the gate; the replay numbers in the PR and the card comment; when merged, a comment on
t_2590fcab with the PR link (Kore closes it).

### WP4: Release candidate 1.5.0

As WP8 of 1.4: fresh clone at the tip of `release/1.5.0`, gate twice (once under parallel load),
`hermes plugins validate --install-deps .`, version 1.5.0 in `plugin.yaml`, `provides_*` equal to the registrations,
CHANGELOG `## [1.5.0] - <date>`, README, TECHNICAL.md and SECURITY.md current, release PR `release/1.5.0` → `main` titled
"Release 1.5.0: count what reaches you", ready, CI green, **not merged**. Draft catalog entry in the card comment (no
upstream PR). Then `kanban_request_review`. Kore merges not before 2026-11-12.

## 4. Data contract (additive)

New record kinds in `logs/calling-mom.jsonl`. Every row has `ts`, `kind`, and `task`, `board`, `run` where known. No prose.

| kind | fields |
|---|---|
| `touch` | `src` (comment, event, chat), `what` (comment, link, unlink, reclaim, unblock, edit, archive, assign, model, effort, changes, triage, to_person, chat_wait), `author` (comments only: profile or surface id), `actor` (other, unattributed), `store_id` (dedupe), `wait_s` (triage, to_person, chat waits), `outcome` (chat waits) |
| `own_write` | `what` (pin, unblock, link, unlink), `store_id` when known |
| `advice_feedback` | existing; new `result` (followed, acted, ignored, unknown), `anchors` (count), `match` (hash prefix of the matched anchor, or null) |
| `quota` | `provider`, `model`, `profile`, `state` (limited, down, recovered), `until` (ts or null), `error_class` (rate_limit, auth, key_limit, other), `src` (api_error, exit, call) |
| `pace` | `board`, `max`, `added`, `removed`, `skipped_running` (counts) |
| `stall` | `last_heartbeat` (ts), `minutes` |

Card comment prefixes: `[quota]`, `[stalled]`. State in `calling-mom-state.json` (existing file): provider health with
expiry, pace links, stalled runs already commented. Config: `calling_mom.count.chat_waits` (bool, default false),
`calling_mom.mom_fallback` (optional), `calling_mom.stall_minutes` (int, default 15, 0 = off).

## 5. Not in 1.5

Card routing and the card contract (1.6), Mom-first handoff review and a board digest (1.6), a daily attention budget
for the human rung (after 1.5's numbers exist), the ladder in ordinary chat sessions, answers over messengers
(kanban-empowered already covers that channel), and anything that needs a Hermes seam from `docs/UPSTREAM.md`.

## 6. Schedule (CET)

| Day | Goal |
|---|---|
| Fri 6 Nov | dogfood upgrade to 1.4.0 and demo video (separate cards), this spec, WP1 starts |
| Sat 7 - Sun 8 Nov | WP1, WP2 |
| Mon 9 - Tue 10 Nov | WP3; anything the hackathon entry needs goes first |
| Wed 11 Nov | hackathon submission with 1.4.0 |
| from Thu 12 Nov | WP4, Kore's review, merge into `main` |

One card runs at a time on board `coding`.
