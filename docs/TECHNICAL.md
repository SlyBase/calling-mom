# Calling mom: technical reference

The everyday guide is the [README](../README.md). This page holds the details: every config key, security model,
metrics, how the risk list was built, what was measured, and how to develop and release.

## Manual configuration

Everything `setup` writes can be set by hand. Keys live under `calling_mom:` in `config.yaml`:

```yaml
calling_mom:
  mom: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}   # a pair: both keys or the block is ignored
  olymp:     {provider: claude-subscription-directsdk-experimental, model: claude-opus-5-5}   # empty = no olymp
  advisor: {enabled: true, boards: [coding]}                                  # opt-in: nothing leaves the machine otherwise
plugins:
  entries:
    calling-mom:
      llm:                                                                      # Hermes grants model overrides only on request
        allow_model_override: true
        allow_provider_override: true
        allowed_models: [deepseek/deepseek-v4.1-flash-1017, claude-opus-5-5]
        allowed_providers: [openrouter, claude-subscription-directsdk-experimental]
```

| Key | Default | Meaning |
|---|---|---|
| `mom.model`, `mom.provider` | DeepSeek Flash / openrouter | The cheap tier. The older top-level `model`/`provider` pair still works; `mom` wins. |
| `olymp.model`, `olymp.provider`, `olymp.categories` | off / `[architecture]` | The expensive tier. A half pair turns olymp off; a half pair in `categories.<name>` is ignored. |
| `olymp.max_per_card`, `olymp.max_per_day` | `1` / `6` | Hard call limits for olymp. |
| `categories.architecture.max`, `.commands_extra`, `categories.watch.max` | `4`, `[]`, `1` | Critical reviews per card; your own risky-command regexes (added to the defaults, nothing to replace); watch reviews per card. |
| `marker` | `[crit]` | Pin the card to the mom model. |
| `local_only_marker` | `[local-only]` | Such cards never send anything to a remote model. Always wins. (The dashboard field `completion_contract: local-only` is a different thing: the board default.) |
| `limits` | `same_error 3, loop 3, malformed 3, steps 40` | Stuck-signal thresholds. |
| `advisor.*` | disabled | `enabled`, `boards`, `marker`, `max_consults_per_card 3`, `max_output_tokens 700`, `context_chars 6000`, `daily_usd_cap 2.0`, `daily_token_cap 200000` (caps count the mom only). |
| `preflight.*` | enabled, `timeout_s 12`, `max_per_card 4` | One synchronous question to Mom before a critical command runs (active only where the advisor is). The deadline is `min(timeout_s, 0.4 x hook budget)`; the cap counts model calls per card; a timeout, a down provider, an unparseable answer or the cap lets the command run. |
| `answer.*` | disabled, `boards []`, `max_per_card 2` | Mom answers a worker's `needs_input` question before it reaches you (see "Mom answers first"). `setup` asks. Model calls use `advisor.timeout_s`, `advisor.max_output_tokens`, `advisor.context_chars` and the daily caps of the advisor. |
| `answer.precedents` | `true` | Keep a person's answers to `[for you]` briefs as text and show Mom up to three (see "Precedents"). Meaningful only where `answer` is on. |
| `human_marker` | `[human]` | A card with this marker is for a person: Mom never answers its questions (no brief either). |
| `categories.<name>` | inherit | Per category (`error`, `loop`, `validation`, `architecture`) its own `model` AND `provider`. |
| `verify.*` | see below | `timeout_s 600`, `max_fix`, `trusted_creators ["user"]`, `allow`. The command runs in the background, see "Hook deadlines". |
| `review.*` | `[review]` / `reviewer` | Marker and reviewer profile (`""` = off). |
| `metrics.pushgateways` | `[]` | Pushgateway URLs, tried in order. |

Kill switch: `CALLING_MOM=off` in the environment.

**Run `hermes calling-mom doctor` with the environment your workers have.** Workers can run with another `HOME` than your shell; a provider that logs in per `HOME` (e.g. a Claude subscription login) then looks fine in your shell and is "logged out" in the worker. Point the provider at the login explicitly (Claude subscription plugin: `plugins.entries.claude-subscription-directsdk-experimental.settings.claude_config_dir`).

### Reviewer profile (for `[review]` cards)

```bash
hermes profile create reviewer --clone-from <your-profile> --no-alias
hermes -p reviewer config set model.default <strong-model> --force
hermes -p reviewer config set model.provider <provider> --force
# remove inherited local endpoint settings: model.base_url, model.api_key, model.api_mode
hermes -p reviewer plugins disable calling-mom
```


## Where the risk list comes from

`rules.py` is built from what existing agents already decided, not invented: the **Claude Code** auto-mode classifier publishes its "blocked by default" list (production deploys, force push, destructive git, `terraform destroy`, secret-manager writes, IAM grants, merging unapproved PRs, bot commands in comments, `--insecure`, tunnels, the cloud metadata endpoint); **OpenAI Codex** flags forced `rm` and looks through `sudo`/`env`/`bash -c`; Claude's docs warn that prefix rules miss `/usr/bin/curl` and `git -C . push`, so we match a regex anywhere in the command. The **DeepSeek Harness** relies on a sandbox and approval policy, not on a command list. `test_rules.py` is the table (critical / watch / routine, including false-positive guards such as `git push origin feat/x` or `rm -f one-file`).


## Compatibility

| Plugin | Hermes | Notes |
|---|---|---|
| 1.3.x | 0.22.5 tested, `>= 0.22` required | `doctor` checks the version and that the kanban internals import. |

The plugin uses `hermes_cli.kanban_db`, `kanban_db_connect` and the private comment watermark of `tools.kanban_tools`. A Hermes update can break it; the CI runs the full suite against Hermes `main`.


## Security model

Card text can come from mail or web content, so treat it as untrusted.

- **`verify:`** runs only for cards created by `verify.trusted_creators`, never through a shell (`shlex`), only commands whose first word is in `verify.allow`, with a reduced environment. `python3 -c` is still arbitrary code: the creator check is the real control. Ignored lines are logged as `verify_ignored`.
- **Advisor** is opt-in per board or card. Context is redacted with Hermes' `redact_for_egress`. Board tool results (`kanban_*`) are filtered out of the context. Daily USD and token caps apply.
- **Pre-flight** is active only where the advisor is (board opt-in or card marker) and never on `[local-only]` cards. The card and the command are redacted with `redact_for_egress` before the question leaves. A HOLD is a persistent block that only a human release lifts; a model never releases its own hold. The log stores only the command hash, not the command.
- **Answers** (`answer.enabled`) are a second, separate opt-in: Mom answers a worker's question and unblocks the card. The fast path (no model call) skips `[local-only]` and `[human]` cards and sends critical, `rules.HUMAN_TOPICS` and held cards to a brief-only prompt. The question prompt is redacted with `redact_for_egress` plus the URL-credential scrub. Mom cannot release a hold.
- **Precedents** (`answer.precedents`) are the one prose store: redacted answers to briefs in `logs/calling-mom-precedents.jsonl`, listed and deleted with `hermes calling-mom precedents`. See "Precedents".
- **Observation log** (`logs/calling-mom.jsonl`) stores hashes and counts, no content.
- The plugin never edits Hermes config.


## Hook deadlines

Hermes cuts a bounded hook off after `plugins.hook_callback_timeout` (default 30 s) and then skips that callback for
60 s. `transform_tool_result` and `post_api_request` fail open (a late result is dropped); `pre_tool_call` fails
closed (a timeout blocks the tool, so a slow check would freeze the whole worker for a minute). The plugin reads the
value through `_core.hook_budget()` (1..600 s) and keeps every synchronous wait inside these hooks below 40 % of it:

- **Advice runs in the background.** On a stuck signal, a risky command, a `SECOND-OPINION:` comment or a completion
  check, the question runs on its own thread (one in flight per run). The hook waits at most `min(8 s, 0.25 x budget)`.
  If the answer is ready it is appended to the tool result as before. If not, the answer is written to the card when
  it arrives and appended to the next tool result of the same run as `[late advice]`, once (log row `advice_late`).
  The Olymp step after an `ESCALATE` always runs in the background. Advice still pending when the run ends reaches the
  card only. A late completion check cannot block `kanban_complete` any more; it is a card comment.
- **`verify:` runs in the background.** The first `kanban_complete` (or `kanban_request_review`) starts the command
  (own process group, scrubbed environment, `verify.timeout_s`) and waits up to 0.3 x budget. No result yet: the call is
  blocked with `verify running: ... Do not edit files. Call kanban_complete again`. Every later call waits again.
  PASS lets the card finish; FAIL keeps the `max_fix` path with the output tail. A timeout kills the whole process tree.
- **A result counts only for the workspace it ran on.** In a git workspace the state is HEAD plus a hash of
  `git status --porcelain` taken when the command ended (files the command writes itself do not make its result stale).
  Without git, or before the first commit, any `write_file`/`patch` call since the start makes it stale. A stale result
  is dropped (`verify` row with `state: stale`) and the command runs again. Limit: an edit made by a terminal command
  during the run, in a workspace without git, is not seen.
- **The prompt tip** (`SECOND-OPINION`, `verify:`) is added to Kanban worker sessions only (`HERMES_KANBAN_TASK`).
- **Untyped blocks** are escalated only on cards with an assignee; a card nobody works on is a human to-do.
- **Pre-flight is bounded and fail-open.** A critical command gets ONE synchronous question, on its own thread, with
  a deadline of `min(preflight.timeout_s, 0.4 x budget)` (default 12 s). A timeout, a down provider, an unparseable
  answer or the per-card cap (`preflight.max_per_card`) lets the command run and the post-hoc review happens as before.
  A HOLD persists in the plugin state (task, normalized command hash) and blocks later runs of the card without a
  model call; only a human release removes it (WP3/WP4).

New log rows: `verify` has `state` (`started`, `pass`, `fail`, `timeout`, `stale`), `ms` and `exit`;
`advice_late` has `signal` and `ms`; `preflight` has `verdict` (`go`, `hold`, `held_again`, `allowed`, `skipped`),
`reason` (`timeout`, `down`, `parse`, `cap`, `allowance`, `local_only`) and `cmd_hash`. Metrics count only finished verify rows.

## Mom answers first

`on_question` listens to `kanban_task_blocked` (not bounded by Hermes). It acts only when the worker itself blocked its
own card (`HERMES_KANBAN_TASK` equals the task id), the card is `blocked` with kind `needs_input` and the board is in
`answer.boards`. Order of the checks, each logged as `question outcome=skipped reason=...` unless silent:

| Check | Reason | Note |
|---|---|---|
| kind is not `needs_input`, status is not `blocked` (block loop: Hermes parks the card in `triage`), board not listed, switch off | (silent) | Not a question for Mom. |
| the latest `blocked` event has `rekind_reason` (a `dependency` block with no open parent) | `rekind` | Hermes made it `needs_input`; nobody asked. |
| the reason starts with `Goal-mode` (exhausted goal budget or judge verdict) | `goal` | Today Hermes blocks these untyped, so the kind check already skips them. |
| `[local-only]` | `local_only` | Nothing leaves the machine. |
| `[human]` (`human_marker`) | `human_marker` | The card is for a person. |
| comments `[mom answered]`, `[olymp answered]`, `[for you]` >= `answer.max_per_card` | `cap` | |
| the advisor's daily USD or token cap is used up | `cap_daily` | |
| no plugin LLM context | `no_llm` | |

Then the **risk** check. A held command (`holds_for`), a critical command in the question or card (`rules.classify`,
first 4000 characters) or an owner-only topic (`rules.HUMAN_TOPICS`, `rules.human_topic`) turn the question into
**brief-only**: the prompt says "Do NOT answer", and a reply other than `ASK_HUMAN` is an error row (`reason=parse`),
never an answer. Reason of the `human` row: `hold`, `critical` or `human_topic`.

Mom replies with exactly `ANSWER: <text>` (kept to 1500 characters), `ASK_HUMAN:` plus `Question:`, `Options:`,
`Recommendation:`, `If nobody answers:` (re-shaped to four lines of at most 400 characters; a missing line is an
error), or `ESCALATE: <reason>`. `ESCALATE` is offered only when Olymp is configured and has room (1 per card, 6 per
day; his comments `[olymp answered]` and the advisor's `, olymp)` comments count). Olymp answers `ANSWER` or
`ASK_HUMAN`. When Mom escalates and Olymp has no room, the card gets a brief written from the question
(`reason=olymp_cap` or `no_olymp`).

Effects: `[mom answered] <text>` (or `[olymp answered]`) is posted through the public `kanban_comment` tool, then the
card is unblocked with `kb.unblock_task` (the path of the capability escalation); if the comment fails the card is not
unblocked (`reason=comment`). `[for you] <brief>` leaves the card blocked. Model error, timeout (`advisor.timeout_s`,
enforced on a thread), cooldown or an unparseable reply: nothing changes, one `error` row (`down`, `timeout`,
`cooldown`, `parse`, `comment`, `unblock`), no retry.

Hermes limit: `block_recurrences` survives an unblock and the limit is 2, so the second `needs_input` block of the
same card goes to `triage` and reaches a person directly. `answer.max_per_card` matters only if that changes.

### `answer_card`

`answer_card(board, task_id, text=None, *, by, accept=False, allow_once=False) -> dict` is the entry point of the CLI
(`by="cli"`) and the Desktop page (`by="desktop"`). It returns `{"ok": True, "task_id", "text", "unblocked"}` or
`{"ok": False, "status", "error"}`, with `status` 400 (bad arguments: nothing to do, text and `accept` together, text
empty or over 2000 characters, unknown `by`, `accept` without a recommendation), 404 (board not opted in, no such
card) or 409 (not blocked, no brief and no hold, `allow_once` without a hold). Nothing changes on an error. On success:
comment `[you via calling-mom] <text>` (`accept` copies the `Recommendation:` line of the newest `[for you]` brief;
`allow_once` alone writes "Allowed once..."), an allowance for every held command of the card through `allow_once()`
of the pre-flight, `kb.unblock_task`, and a `human_answer` row. "Opted in" means the board is in `answer.boards` or
`advisor.boards`, so a pre-flight hold can be released on a board where answers are off.

A card that carries a `[for you]` brief and is claimed again without an `answer_card` call (somebody unblocked it in
the dashboard) gets one `human_answer via=card` row per brief.

New log rows: `question` has `outcome` (`answered`, `human`, `skipped`, `error`), `by` (`mom`, `olymp`), `reason`,
`ms` and, after a model call, `tokens_in`, `tokens_out`, `usd` (Mom's; Olymp is counted in calls). `human_answer` has
`via` (`cli`, `desktop`, `card`), `accepted`, `allow_once` and `latency_s` (brief or hold to answer).
`worker_usage` (one per worker API request) has `task`, `run`, `tokens_in`, `tokens_out`; `preflight` rows that
reached the model also carry `tokens_in`, `tokens_out`, `usd`.

## Precedents

`precedents.py` (stdlib) keeps one JSON object per line in `logs/calling-mom-precedents.jsonl`, next to the log:
`{id, board, question, answer, task, ts}`. `id` is a hash of board, card and brief comment id, so one brief is stored
once whichever path reports it.

Capture, when `answer.precedents` is on, `answer` is on for the board, the card is not `[local-only]`, and neither text
matches `rules.HUMAN_TOPICS`: `answer_card` (CLI, Desktop; the typed text, or Mom's recommendation for `accept`; an
`allow_once` without text stores nothing) and the claim hook `_note_card_answer` (the newest comment after the brief
that is not a plugin comment: `[for you]`, `[mom answered]`, `[olymp answered]`, `[you via calling-mom]`). The
question is the `Question:` line of the newest brief. Both texts are redacted (`redact_for_egress` plus the
URL-credential scrub), joined to one line and cut at 500 characters. A failure is logged and never blocks the answer.

Use: `_answer_question` ranks the stored entries by the number of shared words (four letters or more) between the new
question plus card title and the stored question, at least one; on a tie the same board, then the newer entry. The
top three go into the prompt under "Earlier answers" (not for brief-only questions), together with the instruction to
cite one as "as decided on <card id>". `hermes calling-mom precedents [list | forget <id> | clear]` manages the file.

## Attention report

`hermes calling-mom report [--days 7] [--board X] [--json]` and `GET /summary` call one function, `report.build(rows,
since, board, tasks)` (via `attention_report(days, board)` in `__init__.py`). It counts rows of `logs/calling-mom.jsonl`
and nothing else, so the same log gives the same numbers. `--board` keeps the rows that name the board and the rows of
its cards (tool and advice rows carry a task id, not a board; the cards come from a read-only list of the board).

JSON fields (`days` and `board` are added by `attention_report`; `olymp.today` and `olymp.cap` too):

| Field | Meaning |
|---|---|
| `worker.tool_calls` / `runs` / `signals` | tool-result rows / distinct (card, run) pairs among them / stuck signals fired |
| `worker.api_calls` / `tokens_in` / `tokens_out` | `worker_usage` rows (one per worker API request, `post_api_request`) and their tokens; `tokens_in` includes cache reads and writes |
| `mom.calls` | advisor rows (an Olymp advice was asked of Mom first) + pre-flight checks that reached the model + questions she answered or briefed (`outcome` answered/human) |
| `mom.answered` | questions with `outcome=answered`, `by=mom` |
| `mom.checked` / `holds` | pre-flight `go` + `hold` rows / `hold` rows |
| `mom.verified` / `verify_failed` | finished `verify` rows: `pass` / `fail` + `timeout` (`started` and `stale` are not results) |
| `mom.tips_followed` / `tips_rejected` | `advice_feedback` rows by verdict |
| `olymp.calls` | Olymp advice rows + questions with `by=olymp`; `today` and `cap` are the daily count and `olymp.max_per_day` |
| `you.briefs` | questions with `outcome=human` |
| `you.answered` / `holds_released` | `human_answer` rows (briefs and holds, any `via`) / those with `allow_once` |
| `you.median_answer_s` | median `latency_s` of the answers, `null` without any |
| `cost.mom_usd` / `mom_tokens_in` / `mom_tokens_out` | the cost rows of Mom alone: her advisor rows, her pre-flight rows, her questions (not those finished by Olymp, which mix both models) |
| `cost.olymp_calls` | same as `olymp.calls` (a subscription: calls, not dollars) |
| `cost.worker_tokens` | `worker.tokens_in` + `tokens_out` |
| `cost.all_on_mom_usd` | `worker_tokens` x (`mom_usd` / Mom's tokens): the worker tokens at Mom's observed blended price. `null` unless Mom reports a price (a provider that reports `usd: 0` has none) |

Limits: Mom's cost is a lower bound, because the first, escalating call of an Olymp advice is not logged; the price is
one blended number for input and output; worker tokens count cache reads at full weight; the log is trimmed to its last 1000 rows when it passes 5 MB, so a busy install reports fewer days than asked. `GET /summary` adds
`you.waiting`, `today.mom_calls` and `recent` to this report.

## Desktop backend

`dashboard/plugin_api.py` (manifest `dashboard/manifest.json`, `tab.hidden`: no web-dashboard tab) is mounted by Hermes
at `/api/plugins/calling-mom/` in the gateway process. It loads the plugin module by path and reuses its functions;
it imports nothing from Hermes itself. Boards are read through the plugin's `_connect` (read-only use).

| Route | Answer |
|---|---|
| `GET /summary?days=7` (1..90) | The attention report (see above: `worker`, `mom`, `olymp`, `you`, `cost`) plus `you.waiting`, `today{mom_calls}` and `recent[{ts, kind, task, board, detail, by}]`. `today` starts at 00:00 UTC. `recent` is the last 10 rows worth a line, newest first, no prose. |
| `GET /cards?board=<slug>` | `{task_id: [badge, ...]}` for cards the plugin touched, at most two, most urgent first: `held`, `for you`, `verify failed`, `olymp`, `mom`, `verified`. `held` and `for you` need an opted-in board and a card that still waits; the others come from the log (the last finished `verify` row decides). Without `board`, every board; an unknown board gives `{}`. |
| `GET /needs-you` | Blocked cards on opted-in boards with an unanswered `[for you]` brief or a hold nobody allowed: `{board, task_id, title, brief, holds[{reason, alternative}], can_accept, age_s}`, oldest first. `brief` is the comment without its marker; `can_accept` says there is a `Recommendation:` line. |
| `POST /answer` | `{board, task_id, text?, accept?, allow_once?}` -> `answer_card(..., by="desktop")`. 404 for a board that does not exist or did not opt in, or no such card; 409 for a card that is not blocked or carries no brief or hold; 400 for bad arguments; 422 for a malformed body. |

The Desktop half polls the three GET routes every 15 s (not while the window is hidden) and keeps "Keep blocked" in
`ctx.storage`: it hides an item on this machine only, the card stays blocked and the backend is not told.

## Metrics

`metrics.py` renders Prometheus text from the plugin log, the spend ledger and read-only board DBs. The sweep PUTs it to `/metrics/job/calling_mom/instance/<instance>` on the first reachable Pushgateway. Families: `calling_mom_events_total`, `_advisor_calls_total`, `_advisor_errors_total` (dead key, no login), `_olymp_calls_total`, `_advisor_tokens_total`, `_advisor_cost_usd_total`, `_advice_feedback_total{verdict}`, `_stuck_signals_total`, `_verify_total`, `_verify_ignored_total`, `_review_redirects_total`, `_escalations_total{reason}`, `_gave_up_age_seconds{board}`, `_spend_today_{usd,tokens}`, `_spend_cap_{usd,tokens}`, `_cards{board,state}`, `_last_push_timestamp_seconds`.

Two of them exist for alerting:

| Metric | Meaning |
|---|---|
| `calling_mom_escalations_total{reason}` | cards pinned to the strong model — `crit` (marker at claim), `blocked` (worker gave up), `breaker` (crash park) |
| `calling_mom_gave_up_age_seconds{board}` | age of the newest breaker park on that board; absent when the board never parked |

The Pushgateway keeps the last push, so alert on the age of `calling_mom_last_push_timestamp_seconds`, not on absence. A park that nobody escalated shows up as a small `gave_up_age` with no matching increase of `escalations_total`.

### One log for all profiles

The observation log, sweep watermark and spend ledger live under the installation root (`<hermes root>/logs/`), not under a profile home: the gateway escalates reactively, the cron sweeps, and both must land in ONE file or the metrics only see half the story. `CALLING_MOM_LOG`, `CALLING_MOM_STATE`, `CALLING_MOM_SPEND` still override the paths. The log is tail-trimmed at 5 MB.


## What we measured, and what we did not

24 runs (6 hidden-test coding tasks x 4 arms: local, local + high reasoning effort, local + advisor, strong model). **All 24 were correct.** The local model solved every task alone, so this set shows **no case for escalation**: it is too easy to find the capability boundary. The free rung "more reasoning effort first" was removed in 1.3.2: it needed a write into Hermes' Kanban table, and high effort was slower for no gain. Build a harder set (multi-file, ambiguous spec) before tuning any threshold.

Two-tier routing was exercised live with real models (mom and olymp on a subscription): routine and even severe incidents were answered by the mom alone and olymp was never asked; the olymp leg was proven with a forced `ESCALATE`. **No natural `ESCALATE` has been observed yet**, so how often a weaker mom escalates is unmeasured.

Known limits:

- The advice arrives after the tool ran; it cannot prevent a command (a `pre_tool_call` gate would be a separate design).

- The advisor was exercised live once; the stuck detectors fired one false positive (fixed: the identical-call history now resets on `write_file`/`patch`).
- `claim_review_task` fires no `kanban_task_claimed` hook, so the strong model cannot be pinned per review run. That is why review uses a separate profile.
- `model_override` is per card, not per run.
- The plugin imports Hermes internals (`hermes_cli.kanban_db`, `kanban_db_connect`, `kanban_db_boards`). `hermes plugins validate` passes but warns about them; a Hermes update can break the plugin. The tests catch that.
- The dispatcher tick hook did not fire in the gateway we tested. The cron sweep covers it.
- The reactive hook `on_kanban_worker_exited` only fires in the process that runs the dispatcher,
  and only for the crash path. Timeout parks and spawn-failure parks emit no hook, so the cron
  sweep stays as the idempotent fallback for them.


## Development

Needs a Hermes checkout/venv (the tests use the real `kanban_db`):

```bash
python check.py        # tests + 100 % line coverage + optional drift check
python -m unittest test_signals test_calling_mom test_metrics test_matrix test_cli test_rules test_core test_dashboard
```

Every import of Hermes internals (`hermes_cli`, `agent`, ...) lives in `_core.py`; `test_core.py` fails if another file adds one. Hermes has no public Kanban API for plugins yet, so this keeps a Hermes update to one place to fix.

Tests write to a sandbox, never to your real log. Test-first: a new branch needs a test, `linecov.py` (stdlib only) fails the gate otherwise. `# pragma: no cover` on an `except` or a `def` line excludes that block, use it only for glue that needs a live Hermes.

### Deploy and release (maintainers)

`scripts/deploy.sh` copies the checkout to the plugin directories named in `scripts/.deploy.env` (git-ignored, see the script header), runs the gate and a smoke test. Release: bump `version` in `plugin.yaml`, add the CHANGELOG section, commit, push tag `v<version>`; the `release` workflow builds `calling-mom-<version>.zip`. Do not create the GitHub release by hand first.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

