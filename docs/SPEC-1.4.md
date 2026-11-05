# Calling mom 1.4: the ladder ends with you

Status: accepted, built for the Hermes Agent Plugin Hackathon (submissions close 2026-11-11, 17:00 EST).
Branching: every work package branches from `release/1.4.0` and opens a draft PR into it.
`release/1.4.0` merges into `main` once, as the 1.4.0 release, so `main` stays the installable 1.3.2 until then.

## 1. Why

Agents finish most single tasks on their own now. The bottleneck is the person who has to unstick them.
A Kanban worker pulls a human in five ways: it loops, it gives up, it asks a question, it does something
risky, or it says "done" when it is not. Calling mom 1.3 sends four of these to a stronger model. Three gaps remain:

- A worker question (`kanban_block(kind="needs_input")`) still goes straight to the human.
- Risky commands are reviewed only after they ran. The advice warns, it cannot prevent.
- Nothing is visible outside card comments and a JSONL file.

1.4 closes the loop. Every path from a stuck worker to a human passes Mom first:

```
worker  ->  Mom  ->  Olymp  ->  you
(does)     (answers   (rare,     (decides,
            often)     capped)    signs off)
```

Your attention is the most expensive model in the stack. Calling mom calls it last.

This is the watchdog and the two-stage filter from DeepSeek Harness's "Exploring Super-agent Design"
(sections 3.1 and 6), built as a Hermes plugin for Hermes Kanban: deterministic signals first (fast path,
zero tokens), a model only where code cannot decide (slow path), and the human only for decisions and
sign-off (section 8.2).

Evidence from the author's board, 2026-10-27 to 2026-11-05 (`logs/calling-mom.jsonl`): 1,567 worker tool
calls observed, 24 calls to Mom (1.5 %), 0 calls to Olymp, 11 of 11 tips with feedback were followed.
In the same nine days no `verify:` gate ran, and the one `needs_input` question on the board waited for a human.

## 2. Principles for every work package

1. **Signals, not self-judgment.** A rung is entered by a measurable event (block kind, error streak, risk
   class, verify result), never because a model says it needs help.
2. **Each rung answers or escalates, it never skips one.** Mom never overrules her own HOLD. Only a human
   releases a HOLD.
3. **Fast path first.** Code decides what it can (markers, risk class, caps, `[local-only]`) before a model is called.
4. **Hard caps** on every model rung, per card and per day. Olymp is capped by call count.
5. **Never worse than 1.3.** When a model is slow, down or over budget, behaviour falls back to 1.3: the card
   carries on, advice arrives late or not at all. A plugin failure never blocks a worker.
6. **No hook ever reaches Hermes's hook timeout** (WP1).
7. **Opt-in.** Behaviour that changes what a card does without a human (WP3 answers) has its own switch,
   off by default; `setup` offers it.
8. **Catalog rules hold** (8, 9, 11, 12, 13): public surfaces only; private Hermes imports only in `_core.py`;
   no raw SQL writes into Kanban tables; the Desktop half uses the plugin SDK only; no approval is bypassed;
   every new data flow is disclosed in README, SECURITY.md and the catalog entry.
9. **The log stays prose-free:** hashes, counts, ids, verdicts. Text lives in card comments. WP5 precedents are
   the one exception, and they are disclosed.
10. **Gate:** `python check.py` ends with `gate: PASS` (100 % line coverage), CI is green,
    `hermes plugins validate --install-deps .` passes, and every repo text is English.

## 3. Work packages

| WP | What | Priority | Depends on |
|---|---|---|---|
| WP1 | Reliability: hook deadlines, async verify, worker-only prompt, load-proof timing tests | must | none |
| WP2 | Pre-flight: Mom checks critical commands before they run | must | WP1 |
| WP3 | Mom answers first: `needs_input` questions and decision briefs | must | WP1 |
| WP4 | Desktop half: card badges, "Calling mom" page with Needs you and one-tap sign-off, status chip | must | WP1 (wires WP3 last) |
| WP5 | Precedents: your answers become Mom's rules | should | WP3 |
| WP6 | Attention report (CLI and backend route) | must | WP2, WP3 |
| WP7 | Demo kit, README rewrite, screenshots | must | WP2, WP3, WP4, WP6 |
| WP8 | Release 1.4.0 and catalog pin | must | WP7 |

If time runs short, cut in this order: WP5, then the one-tap actions of WP4 (the page stays read-only),
then the Desktop header of WP6 (the CLI report stays).

### WP1: Reliability

Facts (Hermes 0.22.7, `hermes_cli/plugins_dispatch.py`): `transform_tool_result` and `post_api_request` are
bounded by `plugins.hook_callback_timeout` (default 30 s) and fail open, so a late result is dropped.
`pre_tool_call` is bounded and fails closed, so a timeout blocks the tool. After a timeout the same callback is
suppressed for 60 s. For `pre_tool_call` that means every tool call of the worker is blocked for a minute.
`kanban_task_*` and `on_kanban_*` hooks are not bounded.

Today Mom runs with `advisor.timeout_s: 40`, Mom plus ESCALATE plus Olymp can take about 80 s, and
`verify.timeout_s: 120` runs inside `pre_tool_call`. Any of these can trip the timeout.

Do:

1. **Hook budget.** `_core.hook_budget()` returns the configured `plugins.hook_callback_timeout`
   (default 30, clamped to 1..600). No synchronous work inside a bounded hook may take more than 40 % of it.
2. **Advice off the hot path.** In `transform_tool_result`, run `_advise` on a background thread, one in flight per
   (task, run). Wait at most `min(8 s, 0.25 x budget)`. If the advice is ready, append it as today. Otherwise
   return at once, append it to the next tool result of the same run ("late advice") and post the card comment
   when it arrives. ESCALATE to Olymp always runs in the background. Advice still pending when the run ends
   goes to the card only.
3. **Async verify.** `kanban_complete` on a card with a `verify:` line starts the command on a background thread
   (no shell, scrubbed environment, `verify.timeout_s`, default raised to 600) and blocks `kanban_complete` with:
   "verify running: `<cmd>`. Do not edit files. Call kanban_complete again; each call waits up to N s."
   Each later `kanban_complete` waits up to `0.3 x budget` for the result. PASS allows it, FAIL keeps today's
   path (`max_fix`, output tail). A result counts only for the workspace state it ran on: in a git workspace,
   HEAD plus a hash of `git status --porcelain`; otherwise, no file-editing tool call since the start.
4. **Prompt section only for Kanban workers.** Register it with callable content that returns `""` unless
   `HERMES_KANBAN_TASK` is set. Today it lands in every session, chats included.
5. **Untyped blocks.** Escalate an untyped block only when the card has an assignee. Human to-do cards stay
   untouched (the author's default board holds 33 of them).
6. **Load-proof timing tests.** `check.py` once ended with `gate: FAIL` while `hermes plugins validate` ran in
   parallel, and passed when run alone. Reproduce under load, then make the regex timing tests independent of
   machine load (for example a calibrated baseline loop, or `time.process_time()` with a generous bound).

Accept:

- Fault-injection tests: a Mom stub that sleeps 45 s lets `on_tool_result` return in under 0.4 x budget, and the
  advice shows up on the next tool result and on the card. `verify: sleep 40` blocks the first `kanban_complete`
  with "running" and allows a later one after PASS; a file edit in between forces a new run.
- A real worker run on a scratch board with a `verify:` command that takes more than 30 s completes, and the
  worker log has no `pre_tool_call plugin callback timed out`.
- A chat session's system prompt has no calling-mom section; a Kanban worker session has it.

### WP2: Pre-flight

When a Kanban worker on an opted-in board is about to run a command that `rules.classify` marks `critical`
(force push or branch delete, release or tag publish, `terraform apply`, `kubectl delete`, `DROP TABLE`,
broad `rm -rf`, the rest of `CRITICAL_COMMANDS`), `pre_tool_call` asks Mom first. Critical file writes stay
with the post-hoc review in 1.4.

- One synchronous call with deadline `min(preflight.timeout_s, 0.4 x budget)`, `timeout_s` default 12.
  Mom gets the card title and body head, the command and the last steps, all redacted. She answers exactly
  `GO` or `HOLD: <reason>. Instead: <safer alternative>`.
- **GO**: allow. Log `preflight verdict=go`.
- **HOLD**: block with "calling-mom pre-flight HOLD: <reason>. Instead: <alternative>. This exact command stays
  blocked on this card. Change the approach, or call kanban_block(kind='needs_input') if a person must decide."
  Post the card comment `[pre-flight] HOLD: ...`. Persist the hold (task id, normalized command hash) in the
  plugin state, so a later run of the same card is held too, without another model call.
- **Release**: only a human can, through a WP3 brief answer with `allow once` or the WP4 button. That records an
  allowance for (task, command hash). Olymp never releases a hold.
- **Fail open**: timeout, provider down, an unparseable answer or the cap (`preflight.max_per_card`, default 4)
  allow the command, and the 1.3 post-hoc review runs as today. Log the reason.
- Never for `[local-only]` cards, non-critical commands, or outside Kanban workers.
- Config: `calling_mom.preflight: {enabled: true, timeout_s: 12, max_per_card: 4}`, active only where the advisor is.

Accept: tests for GO, HOLD (block, comment, identical retry held without a model call, also in a new run),
allowance, fail-open, cap, `[local-only]`, non-critical commands. README "What you will see" shows a HOLD.

### WP3: Mom answers first

Trigger: `kanban_task_blocked` where the worker itself called `kanban_block(kind="needs_input")`, on a board in
`answer.boards`. Config: `calling_mom.answer: {enabled: false, boards: [], max_per_card: 2}`. `setup` asks
"Let Mom answer worker questions before they reach you?". Blocks that Hermes itself re-kinds to `needs_input`
(a dependency on an unfinished parent, an exhausted goal budget, a detected block loop; see
`hermes_cli/kanban_db.py` and `tools/kanban_tools.py`) are not worker questions and are skipped.

Fast path, no model call (log the skip reason):

- `[local-only]` or `[human]` on the card: skip.
- The question or the card matches the critical risk list, or the new `rules.HUMAN_TOPICS` (credentials and
  secrets, payments and money, legal and contracts, personal data, publishing in the owner's name): Mom does not
  answer. She may still write a brief, except on `[local-only]` cards.
- The card has an active pre-flight HOLD: brief for you. Mom never overrules herself.
- Per-card cap and the daily cap shared with the advisor budget.

Slow path: Mom gets the card (title, body head), the question (block reason), the last comments and, with WP5,
up to three precedents. She answers with exactly one of:

- `ANSWER: <text>`, only when it follows from the card, the repository, earlier comments or a precedent,
  and a wrong answer is cheap to undo.
- `ASK_HUMAN:` followed by a brief of four lines: `Question:`, `Options:` (A/B, optional C),
  `Recommendation:`, `If nobody answers:`.
- `ESCALATE: <reason>`. Olymp, when configured and within his caps (1 per card, 6 per day), gets the same
  question and answers `ANSWER` or `ASK_HUMAN`.

Effects:

- ANSWER: comment `[mom answered] <text>` (or `[olymp answered]`), unblock through `_core` (the same path as the
  capability escalation), log `question outcome=answered`.
- ASK_HUMAN: comment `[for you] <brief>`, the card stays blocked, log `question outcome=human`.
- Error or timeout: the card stays blocked and unchanged, log `question outcome=error`. No retry, the card waits
  for a human as in 1.3.
- Shared entry point for WP4 and the CLI:
  `answer_card(board, task_id, text=None, *, by, accept=False, allow_once=False) -> dict`. It checks that the card
  is blocked, on an opted-in board and carries a brief or a hold, writes the human's answer as a comment
  (`[you via calling-mom] ...`; `accept=True` copies Mom's recommendation), records an allowance when asked,
  unblocks the card and logs `human_answer`.
- When a card that had a `[for you]` brief is claimed again after somebody else unblocked it, log
  `human_answer via=card`. WP5 and WP6 build on this.

Accept: tests for every fast-path skip, ANSWER, ASK_HUMAN (exact shape), ESCALATE (caps), the error path,
redaction of the outbound prompt, and `answer_card` validation. One real run on a scratch board: a worker asks
"Which config file should I change, A or B?", Mom answers, the card continues.

### WP4: Desktop half

Layout, a unified package (hermes-agent `website/docs/developer-guide/desktop-plugin-sdk.md`,
"One package, both SDKs"):

```
desktop/plugin.js          ESM, no JSX; imports only @hermes/plugin-sdk, react, react/jsx-runtime
dashboard/manifest.json    {"name": "calling-mom", "api": "plugin_api.py"}
dashboard/plugin_api.py    FastAPI router, mounted at /api/plugins/calling-mom/
```

Backend, in the gateway process. It reads the plugin's own logs and, read-only, the Kanban boards.

- `GET /summary?days=7`: counts per rung (WP6).
- `GET /cards?board=<slug>`: `{task_id: [badge, ...]}` for cards with plugin activity. No prose.
- `GET /needs-you`: blocked cards on opted-in boards that carry a `[for you]` brief or a HOLD, with board, id,
  title, the brief text from the card comment, and age.
- `POST /answer` with `{board, task_id, text?, accept?, allow_once?}`: calls `answer_card(..., by="desktop")`.
  Answers 404 or 409 for cards that are not blocked, not on an opted-in board, or have no brief or hold.

UI:

- **Card badges** (`KANBAN_CARD_AREAS.badges`, in Hermes 0.22.6 and later): at most two per card, text only,
  at most 24 characters: `mom` (advised or answered), `olymp`, `held`, `for you`, `verified` or
  `verify failed`, each with a fixed tone. Data comes from `/cards`, polled every 15 s while a board is visible;
  re-register the contribution to repaint.
- **Page "Calling mom"** with a sidebar entry: the ladder (worker, Mom, Olymp, you) with 7-day counts; a
  "Needs you" list showing each brief with the buttons "Accept recommendation" and "Answer...", for holds
  "Allow once" and "Keep blocked"; recent activity.
- **Status bar chip**: Mom calls today and items waiting for you. A click opens the page.
- Feature-detect newer SDK parts (`import * as sdk from '@hermes/plugin-sdk'`, then check
  `sdk.KANBAN_CARD_AREAS`), so an older app loads the page without badges instead of failing to link.
- No DOM reach-in, no app stores, no `window.hermesDesktop`.

Accept: `hermes plugins validate` passes, including the desktop surface check; backend route tests with a
temporary `HERMES_HOME` and a fixture board (the 100 % coverage gate includes `dashboard/plugin_api.py`);
a manual check in Hermes Desktop against a local gateway and against a remote one (Desktop half installed with
"Install from Git" and the Desktop target). Screenshots go to WP7.

### WP5: Precedents (should)

- When a human answers a brief (Desktop, CLI, or a card comment followed by an unblock), store
  `{id, board, question, answer, task, ts}` in `logs/calling-mom-precedents.jsonl`, redacted, each text at most
  500 characters. Local only.
- WP3 adds up to three precedents with the highest word overlap to Mom's prompt, same board first. An answer
  based on one cites it ("as decided on t_...").
- `hermes calling-mom precedents [list | forget <id> | clear]`.
- `answer.precedents: true`, meaningful only when `answer` is on. Disclosure: the plugin stores your answers to
  worker questions as text in that file.

Accept: capture from all three paths, ranking, redaction, the cap of three, forget and clear, prompt inclusion.

### WP6: Attention report

`hermes calling-mom report [--days 7] [--board X] [--json]`, with the same numbers behind `GET /summary`:

- Worker: tool calls observed, runs, signals fired.
- Mom: calls, tips followed (`advice_feedback`), questions answered, pre-flight checks and holds, verify runs and results.
- Olymp: calls, against the daily cap.
- You: briefs created and answered, median time to answer, holds released.
- Cost: Mom's USD and tokens, Olymp's calls. Comparison: the worker tokens seen by `post_api_request`, priced at
  Mom's observed price per token, is what running every call on Mom would have cost. Shown only when Mom
  reports a price.

Accept: deterministic output on fixture logs, JSON fields documented in TECHNICAL.md, numbers equal to a hand count.

### WP7: Demo kit and docs

- `scripts/demo.py` (`--reset` removes everything it created) makes a board `calling-mom-demo`, a scratch git
  repository with a bare local remote, and cards that trigger reliably:
  1. the same error three times, Mom's tip, fixed;
  2. a `needs_input` question that Mom answers, and the card continues;
  3. a card that leads to `git push --force origin main`, the pre-flight HOLD, and a branch push instead;
  4. a card with a `verify:` line.
  It never touches a real remote.
- README rewrite: the ladder and the tagline first, then a one-minute quick start, "What you will see" with
  screenshots, the install link `hermes://plugin/install?repo=SlyBase/calling-mom&enable=1`, the updated
  "Your money and your data" section, and one line crediting the Super-agent article.
- TECHNICAL.md: the new options, record kinds and the hook budget rule.
- `assets/screenshots/*.png`: the Desktop page, card badges, Needs you, a HOLD on a card.

Accept: on a fresh `HERMES_HOME` with one worker profile, `scripts/demo.py` fires all four triggers within
15 minutes, twice in a row. The times go into the PR.

### WP8: Release and catalog

- `plugin.yaml` 1.4.0. `provides_*` match the registrations. `requires_hermes` is the lowest version the shipped
  features need; the agent half runs on 0.22, the badges need the 0.22.6 Desktop app. Decide on evidence and say so.
- CHANGELOG, the archive list in the release workflow (add `desktop/` and `dashboard/`), the CI compile list.
- Release PR `release/1.4.0` into `main`, tag `v1.4.0`, GitHub release.
- Catalog: if NousResearch/hermes-agent#147276 (1.3.2) is merged, open a bump PR (sha, version, screenshots,
  disclosure). If it is still open on Monday 2026-11-09, re-pin #147276 to 1.4.0 and comment the delta for the reviewer.

## 4. Data contract

New record kinds in `logs/calling-mom.jsonl`. Every row has `ts`, `kind`, and `task`, `board`, `run` where known. No prose.

| kind | fields |
|---|---|
| `preflight` | `verdict` (go, hold, held_again, allowed, skipped), `reason` (timeout, down, cap, parse, local_only), `cmd_hash`, `ms`, `model`, `provider` |
| `question` | `outcome` (answered, human, skipped, error), `by` (mom, olymp), `reason`, `precedents` (ids), `ms`, `tokens_in`, `tokens_out`, `usd` |
| `human_answer` | `via` (desktop, cli, card), `accepted` (bool), `allow_once` (bool), `latency_s` (brief to answer) |
| `verify` | `state` (started, pass, fail, timeout, stale), `ms`, `exit` |
| `advice_late` | `signal`, `ms` (advice delivered on a later tool result) |

Card comment prefixes, which the Desktop half keys on: `[mom answered]`, `[olymp answered]`, `[for you]`,
`[pre-flight] HOLD:`, `[you via calling-mom]`.

## 5. Not in 1.4

Signals in ordinary chat sessions (outside Kanban), one-word answers over Telegram or other messengers,
a time-based progress watchdog, learning across boards beyond WP5.

## 6. Schedule (CET)

| Day | Goal |
|---|---|
| Thu 5 Nov | spec, `release/1.4.0`, WP1 |
| Fri 6 Nov | WP2, WP3, WP4 backend |
| Sat 7 Nov | WP3, WP4 UI, WP6 |
| Sun 8 Nov | WP5, WP7 demo kit, end-to-end run |
| Mon 9 Nov | README, screenshots, feature freeze at 20:00 |
| Tue 10 Nov | release 1.4.0, catalog pin, demo video |
| Wed 11 Nov | X post and submission form by 18:00; the deadline is 23:00 CET (17:00 EST) |
