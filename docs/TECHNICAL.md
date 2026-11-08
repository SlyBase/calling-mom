# Calling Mom: technical reference

The everyday guide is the [README](../README.md). This page holds the details: every signal, every config key, security model,
metrics, how the risk list was built, what was measured, and how to develop and release.

## New in 1.4 and 1.5 at a glance

One page for what 1.4 and 1.5 added. The details are in the sections named in each row.

**Options** (under `calling_mom:` in `config.yaml`; defaults in `__init__.py` `DEFAULTS`):

| Key | Default | What it does | Section |
|---|---|---|---|
| `preflight.enabled` | `true` | Mom checks a critical command before it runs. Active only where the advisor is. | Hook deadlines |
| `preflight.timeout_s` | `12` | Deadline of that one synchronous question. The effective deadline is `min(timeout_s, 0.4 x hook budget)`. | Hook deadlines |
| `preflight.max_per_card` | `4` | Model calls per card for pre-flight. Past it, critical commands run. | Hook deadlines |
| `answer.enabled` | `false` | Mom answers a worker's `needs_input` question first. `setup` asks. | Mom answers first |
| `answer.boards` | `[]` | Boards where she may. `"*"` = all. | Mom answers first |
| `answer.max_per_card` | `2` | Answers and briefs per card. | Mom answers first |
| `answer.precedents` | `true` | Keep your answers to briefs as text, show Mom up to three. | Precedents |
| `human_marker` | `[human]` | A card with it is for a person. Mom never answers its questions. | Mom answers first |
| `verify.timeout_s` | `600` (was 120) | `verify:` runs in the background, so a long command is fine. | Hook deadlines |
| `advisor.timeout_s` | `40` | Limit for one advice call. It runs off the hot path, see the budget rule. | Hook deadlines |

Options added in 1.5:

| Key | Default | What it does | Section |
|---|---|---|---|
| `count.chat_waits` | `false` | Count the time you wait on a prompt in chat (kind, seconds, outcome; never the text). `setup --chat-waits`. | Attention report |
| `mom_fallback` | `{provider: "", model: ""}` | Answers when Mom's provider is limited or down. `setup --fallback PROVIDER/MODEL\|route:NAME\|none`. Olymp has none. | Quota, fallback, stalled cards, pace |
| `stall_minutes` | `15` | A running card without a heartbeat this long gets one `[stalled]` comment per run (`0` = off). Nothing is reclaimed. | Quota, fallback, stalled cards, pace |
| `guard_minutes` | `30` | A `ready` card the dispatcher's respawn guard holds back this long gets one `[guarded]` comment per streak (`0` = off). The guard is never bypassed. | Guarded cards |

Settings read from Hermes, not from this plugin: `plugins.hook_callback_timeout` (default 30 s) is the hook budget.

**Commands** added in 1.5: `hermes calling-mom pace --board B --max N [--dry-run] [--undo]`, `hermes calling-mom report --since/--until`.

**Commands** added in 1.4: `hermes calling-mom answer <task>` (`--accept`, `--text`, `--allow-once`, `--board`),
`hermes calling-mom precedents [list | forget <id> | clear]`, `hermes calling-mom report [--days N] [--board X] [--json]`,
`hermes calling-mom setup --answer / --no-answer`. `scripts/demo.py --assignee <profile> [--board B] [--root DIR]
[--wait MIN] [--log FILE] [--reset]` makes the demo cards (see "Demo kit").

**Log record kinds** (`logs/calling-mom.jsonl`, one JSON object per line, no prose; every row has `ts`, `kind`, `pid`,
and `task`, `board`, `run` where known). New or changed in 1.4:

| kind | Fields | Written when |
|---|---|---|
| `preflight` | `verdict` (`go`, `hold`, `held_again`, `allowed`, `skipped`), `reason` (`timeout`, `down`, `cap`, `parse`, `no_rationale`, `allowance`, `local_only`), `rationale` (on `hold`: redacted, 160 characters), `cmd_hash`, `model`, `tokens_in`, `tokens_out`, `usd` | A critical command met the pre-flight check. The command text is never stored. |
| `question` | `outcome` (`answered`, `human`, `skipped`, `error`), `by` (`mom`, `olymp`), `reason`, `ms`, `tokens_in`, `tokens_out`, `usd` | A worker's `needs_input` question was handled. |
| `human_answer` | `via` (`cli`, `desktop`, `card`), `accepted`, `allow_once`, `latency_s` | A person answered a brief or released a hold. |
| `verify` | `state` (`started`, `pass`, `fail`, `timeout`, `stale`), `ok`, `fails`, `exit`, `ms` | The background `verify:` command changed state. |
| `advice_late` | `signal`, `ms` | An advice missed the wait and reached the worker on a later tool result. |
| `web_verify` | `query_hash`, `results` (hits that reached the judge) | A web search was made for a claim (see "Web evidence"). The query text is never stored. |
| `worker_usage` | `tokens_in`, `tokens_out` | One worker API request (`post_api_request`). Counts only, no text. |

Added in 1.5 (WP1), all additive:

| kind | Fields | Written when |
|---|---|---|
| `touch` | `src` (`comment`, `event`, `chat`), `what` (`comment`, `link`, `unlink`, `reclaim`, `unblock`, `edit`, `archive`, `assign`, `model`, `effort`, `changes`, `triage`, `to_person`, `chat_wait`), `author` (comments: profile or surface id), `actor` (`other`, `unattributed`), `store_id` (dedupe key with `board` and `src`; the last row of a key wins), `wait_s` (triage, to_person, chat waits; absent while still waiting), `outcome` (chat), `wait` (chat: `clarify`, `approval`, `sudo`, `other`) | The sweep found outside help on an opted-in board (`ts` is the time of the event), or a prompt wait in chat ended. `wait` is an addition to the spec's field list: the spec asks for the kind of wait, and `kind` is taken by the row kind. |
| `own_write` | `what` (`pin`, `unblock`, `link`, `unlink`), `store_id` | calling-mom wrote to a board itself; the sweep takes that event out of the outside-help count. |
| `advice_feedback` | `result` (`followed`, `acted`, `ignored`, `unknown`), `anchors` (count), `match` (hash prefix of the matched anchor or null) | An advice was judged: at the first matching tool call, when the run ends, or at once for advice without anchors. 1.4 rows have `verdict` instead of `result`. |

Added in 1.5 (WP2), all additive:

| kind | Fields | Written when |
|---|---|---|
| `quota` | `provider`, `model`, `profile`, `state`, `until` (epoch seconds, or null while the reset time is a guess), `error_class` (`rate_limit`, `auth`, `key_limit`, `other`), `src` (`api_error`, `exit`, `call`), `task`, `board`, `run`, `tier` | `state` `limited` / `down` / `recovered`: the health of a provider changed (one row per change). The spec lists these three; WP2 adds `skipped` (a Mom or Olymp call was not made, `tier` says which), `fallback` (the fallback answered) and `paused` (a `[quota]` comment was written for `task`, `src` `exit`) so the report can count them. |
| `pace` | `board`, `max` (null for `--undo`), `added`, `removed`, `skipped_running` | A person ran `pace` or `pace --undo` (not for `--dry-run`). |
| `stall` | `last_heartbeat` (epoch seconds), `minutes` (silent when flagged), `task`, `board`, `run` | The sweep flagged a running card; one row per run. |

State in `calling-mom-state.json` (additive): `health` (`{profile: {provider: {state, until, class, known}}}`, `known` false when the reset time
is the 30 minute guess), `pace` (`{board: [[parent, child], ...]}`: the links to undo), `stalled` (`{task: run}` already commented),
`quota_cards` (`{task: limit window}` already commented). Comment prefixes: `[quota]`, `[stalled]` (author `calling-mom`).

Unchanged kinds: tool rows (no `kind`: `tool`, `ok`, `sig`, `signals`), `advisor`, `advisor_error`,
`escalated`, `review_redirect`, `verify_ignored`, `tick`. Precedents are a separate file (see "Precedents").
`hermes calling-mom report` and `GET /summary` only count these rows.

**The hook budget rule.** Hermes cuts a bounded hook off after `plugins.hook_callback_timeout` (the budget, default 30 s) and then skips that
callback for 60 s. So no synchronous wait inside a bounded hook may take more than **40 % of the budget**:

| Where | Wait | At 30 s budget |
|---|---|---|
| Advice in `transform_tool_result` | `min(8 s, 0.25 x budget)`, then it goes late | 7.5 s |
| Pre-flight in `pre_tool_call` | `min(preflight.timeout_s, 0.4 x budget)` | 12 s |
| `verify:` result in `kanban_complete` | `0.3 x budget` per call, the command runs in the background | 9 s |
| ESCALATE to Olymp, completion check, question handling | no wait: background or unbounded hook | - |

`pre_tool_call` fails closed, so a hook that waits too long freezes every tool call of the worker for a minute.
Everything else fails open. See "Hook deadlines".

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
| `mom.escalate_below`, `mom.brief_short_from` | `4` / `7` | Mom's confidence (1-10, see "Mom answers first"): below the first, olymp is asked (inside his limits); from the second, a brief for the human is kept short (160 characters a line, not 400). |
| `olymp.model`, `olymp.provider`, `olymp.categories` | off / `[architecture]` | The expensive tier. A half pair turns olymp off; a half pair in `categories.<name>` is ignored. |
| `olymp.max_per_card`, `olymp.max_per_day` | `1` / `6` | Hard call limits for olymp. |
| `categories.architecture.max`, `.commands_extra`, `categories.watch.max` | `4`, `[]`, `1` | Critical reviews per card; your own risky-command regexes (added to the defaults, nothing to replace); watch reviews per card. |
| `marker` | `[crit]` | Pin the card to the mom model. |
| `local_only_marker` | `[local-only]` | Such cards never send anything to a remote model. Always wins. (The dashboard field `completion_contract: local-only` is a different thing: the board default.) |
| `limits` | `same_error 3, loop 3, malformed 3, steps 40` | Stuck-signal thresholds. |
| `advisor.*` | disabled | `enabled`, `boards`, `marker`, `max_consults_per_card 3`, `max_output_tokens 700`, `context_chars 6000`, `daily_usd_cap 2.0`, `daily_token_cap 200000` (caps count the mom only), `extract_state false` (see "State first"). |
| `web_verify.*` | disabled, `max_per_card 1`, `max_per_day 20`, `results 3`, `result_chars 200`, `timeout_s 8` | A web search for the claim of a `[crit]` / `[review]` card or a `verify-web:` line, before the judge decides (see "Web evidence"). Own switch; needs the advisor. |
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


## Demo kit

`scripts/demo.py` (stdlib only) makes a scratch tree and four cards on the board `calling-mom-demo`, then watches
the log until each rung has fired:

| Card | Workspace | Trigger | Proof in the log |
|---|---|---|---|
| `[demo] make the flaky build pass` | `build/` | `build.sh` fails the same way three times (`out` is a file, not a folder). | `advisor` row with `signal` `same_error` or `loop` |
| `[demo] set the greeting` | `config/` | The worker asks which file to change. The card holds the facts, so Mom can answer. | `question` row, `outcome` `answered`, `by` `mom` |
| `[demo] publish the release notes` | `repo/` | The worker must replace `main` on a shared remote. | `preflight` row, `verdict` `hold` |
| `[demo] write the notes` | `verify/` | The card carries `verify: python3 check_notes.py`. | `verify` row, `state` `pass` |

Safe by construction: everything lives under `--root` (default `<tmp>/calling-mom-demo`, marked by a `.calling-mom-demo`
file; the script never deletes a folder without that mark). The "remote" is `remote.git`, a bare repository in that folder, and the
script refuses to go on if `origin` points anywhere else. Each run archives the cards of the last run and builds the tree
again, so it can run twice in a row. `--reset` does only that. The board must be opted in
(`hermes calling-mom setup --boards calling-mom-demo --answer`) and a gateway must run, because the gateway starts the worker.
The log it watches is the one of the installation: `CALLING_MOM_LOG`, else `<Hermes root>/logs/calling-mom.jsonl`
(`--log` overrides). The exit code is 0 only when all four triggers fired within `--wait` minutes (default 15).

Limits: a model decides whether the stuck card gets stuck (it must repeat the failing command), and whether Mom says
HOLD, so a run can miss a trigger. The script reports it as `NOT FIRED`; run it again. Run it with a worker profile you
can throw away: in one test Hermes' own approval gate blocked the force push (Mom had said GO; since 1.4.0 her prompt holds a force push to a shared branch whatever the card says), and the worker then ran
`hermes config set approvals.single_query_mode approve` on its own profile. Mom's pre-flight held the retry, and the
worker reverted the setting. Measured on 2026-11-05 with a local 27B worker (Swift-27B), Mom = Sonnet: all four triggers
fired at +7:44 and +7:31 in two runs in a row. With the first, looser card texts one run missed the stuck card and one
HOLD came only at +12:26. Four workers on one local model are slow: in one run a pre-flight check timed out (12 s) and the
command was allowed, as designed.

## Where the risk list comes from

`rules.py` is built from what existing agents already decided, not invented: the **Claude Code** auto-mode classifier publishes its "blocked by default" list (production deploys, force push, destructive git, `terraform destroy`, secret-manager writes, IAM grants, merging unapproved PRs, bot commands in comments, `--insecure`, tunnels, the cloud metadata endpoint); **OpenAI Codex** flags forced `rm` and looks through `sudo`/`env`/`bash -c`; Claude's docs warn that prefix rules miss `/usr/bin/curl` and `git -C . push`, so we match a regex anywhere in the command. The **DeepSeek Harness** relies on a sandbox and approval policy, not on a command list. `test_rules.py` is the table (critical / watch / read-only / routine, including false-positive guards such as `git push origin feat/x` or `rm -f one-file`).

1.5 added three critical patterns after an audit (a worker quit a production app and installed a plugin over `ssh` with no question
first): `osascript ... quit`, `killall`/`pkill`, and `cp`/`mv`/`rsync`/`scp`/`ditto`/`ln` with a `Library/` path as a target (not as the
source). `watch` is for writes only: `rules.read_only()` splits the line with `shlex`, looks through `ssh host '...'`, `pct exec N -- ...` and
`timeout N`, and calls it read-only when every simple command starts with a reading word and no redirect writes a file. A `#` is a word, not a
comment (a comment must not hide a later write). Known gaps: `make install` and `installer -pkg` (the target is not in the command),
redirects and downloads into a Library folder (`> ~/Library/x`, `curl -o`), and `kill <pid>`. The replay of the audited runs is in the PR of WP3; no shipped code reads transcripts.


## Compatibility

| Plugin | Hermes | Notes |
|---|---|---|
| 1.4.x | 0.22.7 tested, `>= 0.22` required | The agent half (hooks, pre-flight, answers, CLI, report) runs on 0.22. The card badges need the Desktop app 0.22.6 or later; `desktop/plugin.js` checks for the badge slot and, on an older app, loads the page without badges. `doctor` checks the version and that the kanban internals import. |
| 1.3.x | 0.22.5 tested, `>= 0.22` required | `doctor` checks the version and that the kanban internals import. |

The plugin uses `hermes_cli.kanban_db`, `kanban_db_connect` and the private comment watermark of `tools.kanban_tools`. A Hermes update can break it; the CI runs the full suite against Hermes `main`.


## Security model

Card text can come from mail or web content, so treat it as untrusted.

- **`verify:`** runs only for cards created by `verify.trusted_creators`, never through a shell (`shlex`), only commands whose first word is in `verify.allow`, with a reduced environment. `python3 -c` is still arbitrary code: the creator check is the real control. Ignored lines are logged as `verify_ignored`.
- **Advisor** is opt-in per board or card. Context is redacted with Hermes' `redact_for_egress`. Board tool results (`kanban_*`) are filtered out of the context. Daily USD and token caps apply.
- **Pre-flight** is active only where the advisor is (board opt-in or card marker) and never on `[local-only]` cards. The card and the command are redacted with `redact_for_egress` before the question leaves. A HOLD is a persistent block that only a human release lifts; a model never releases its own hold. The log stores only the command hash, not the command.
- **Answers** (`answer.enabled`) are a second, separate opt-in: Mom answers a worker's question and unblocks the card. The fast path (no model call) skips `[local-only]` and `[human]` cards and sends critical, `rules.HUMAN_TOPICS` and held cards to a brief-only prompt. The question prompt is redacted with `redact_for_egress` plus the URL-credential scrub. Mom cannot release a hold.
- **Precedents** (`answer.precedents`) are the one prose store: redacted answers to briefs in `logs/calling-mom-precedents.jsonl`, listed and deleted with `hermes calling-mom precedents`. See "Precedents".
- **Observation log** (`logs/calling-mom.jsonl`) stores hashes and counts, no content. The 1.5 `touch` rows keep author ids and event kinds, never text.
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
- **A HOLD carries its rationale (refute filter).** The pre-flight prompt asks for `HOLD: <what is not verified, one concrete
  sentence>. Instead: ...`. A reason shorter than 4 words ("too risky") is downgraded: the command runs, nothing is
  recorded as a hold, the row says `allowed` / `no_rationale`. That is the fail-open path of an unparseable answer, so no
  hold exists that a model could release (re-asking was dropped: one more call per vague answer, same result). The reason
  shows in the card comment and, redacted and cut to 160 characters, as `rationale` in the `hold` row.

New log rows: `verify` has `state` (`started`, `pass`, `fail`, `timeout`, `stale`), `ms` and `exit`;
`advice_late` has `signal` and `ms`; `preflight` has `verdict` (`go`, `hold`, `held_again`, `allowed`, `skipped`),
`reason` (`timeout`, `down`, `parse`, `no_rationale`, `cap`, `allowance`, `local_only`) and `cmd_hash`. Metrics count only finished verify rows.

## Counter-claim first

The prompts of the completion check (`completion`) and of the review of a critical change (`critical_change`) ask for one
first line, `COUNTER: <the most plausible way the claim is wrong>` (a check that never ran, a fix that is not in the
delivered change, a summary that differs from what was done), then the normal answer after it ("Explore" stage of SMART,
see References). No extra model call. `_counter` takes the line off the reply, like `_confidence`, so the output contract
is unchanged: the card comment, the `GAP:` block message and the `ESCALATE:` check see only the answer. Fail-open: a reply
without the line is handled as before. The `advisor` row carries `contra` (the counter-claim, redacted, 160 characters)
when the model wrote one. The other prompts (`stuck`, `watch_change`, `second_opinion`) are unchanged.

Measured on 5 recorded completion checks (3 `GAP`, 2 `DONE`), same model as recorded, one sample per arm, without the
tool history of the run: the verdict was identical with and without the line in 5 of 5 (and equal to the recorded
verdict); all 5 replies parsed. The line appeared in 5 of 5 replies with the new prompt. No false `GAP` on the two `DONE`
cards, and no extra catch either: this set shows **no gain in verdicts**, only a logged reason and sharper `GAP` text on
one card. Build a set with known wrong summaries before claiming more.

## State first

With `advisor.extract_state: true` a completion check and a review of a critical change make one extra call before the
judging call ("Identify" stage of SMART, see References). The call gets the same redacted context as the judge, with the
prompt `_STATE_ASK`: list at most 5 stateful things the claim depends on (CI, tests, PR, DB schema, API, deploy), one line
each, 3 to 5 facts per line, `unknown` for a fact the text does not give. `_state` keeps the first 5 lines that start with
`-` (300 characters each, redacted again) and the judge reads them as an `Extracted state` block above the raw context,
which stays. Stuck signals, `watch_change` and `second_opinion` never extract: they would pay a call for a short answer.
`[crit]` and `[review]` cards reach the feature through these two checks (the review of a critical change, the completion).

- **Same rules as the judge.** The call runs inside `_advise`, after the opt-in and `[local-only]` checks, so a card that
  may not leave the machine sends nothing. It uses Mom's route (or the category override), never Olymp: when Mom then
  escalates, Olymp reads the same state. Its tokens and cost go to the daily caps; it writes no comment, so the per-card
  budgets (one completion check per run, `max` of `architecture`) are unchanged. The `advisor` row carries `state_tokens`.
- **Fail-open for the content.** A reply without an entity line (empty, prose, garbled) is dropped and the judge reads the
  raw context as before. A model-call error is NOT swallowed: the judge goes to the same model and would fail the same
  way, so the provider is called and reported once (`[advisor unavailable]`), not twice.
- **Off by default.** It adds a call and its latency to a check that already runs close to the hook deadline
  (`_advise_soon` waits at most `min(8 s, 0.25 x budget)`; a late verdict still reaches the card and the worker).

Measured on the same 5 recorded completion checks as "Counter-claim first" (3 `GAP`, 2 `DONE`), `claude-sonnet-5-5` as recorded,
one sample per arm, no tool history, thinking off; the arms are the raw context (A) and extraction + judge (B). Input
tokens are estimated from the prompt text (characters / 4: the CLI used to call the model adds a variable cache overhead),
output tokens and wall time are as reported, the CLI start-up is part of the time:

| | A: raw | B: state first | B / A |
|---|---|---|---|
| input tokens, 5 cards | ~3 700 | ~8 100 | 2.2x |
| output tokens, 5 cards | 4 400 | 8 500 (of that 4 700 extraction) | 1.9x |
| wall time, 5 cards | 48 s | 90 s (of that 45 s extraction) | 1.9x |
| verdict = A | | 5 of 5 | |
| verdict = recorded verdict | 2 of 5 | 2 of 5 | |

**No gain in verdicts, about twice the cost.** Both arms gave the same verdict on 5 of 5 cards and matched the recorded
verdict on 2 of 5 (run without the tool history: 1 of 3 gaps caught, 1 of 2 `DONE` kept, in both arms). The verdict line is read from the
reply's last `DONE` / `GAP` line here, as the harness model writes prose first; the two arms are read the same way. In an
earlier pass of the same set (output cap 700, two replies cut off) one card flipped between the arms (A `DONE`, B `GAP`,
recorded `GAP`) and gave `DONE` in both arms in the final pass: one sample per arm cannot tell noise from signal. The extra
call also doubles the latency of a check whose synchronous wait is 8 s (`_advise_soon`): a `GAP` then reaches the card late
instead of blocking the first `kanban_complete`. That is why `extract_state` is off by default. Test it on a set with known
wrong summaries and a tool history before turning it on.

## Web evidence

With `web_verify.enabled: true` a completion check and a review of a critical change may look the claim up on the web
before the judging call ("Curate" stage of SMART, see References). It is the one flow that sends text to a **search
provider** instead of a model: no card code goes to a remote model because of it, but the query leaves the machine, so it
has its own switch (off by default) and budgets, and it runs inside `_advise`, after the opt-in and `[local-only]` checks.

- **When.** Only the two checks of `_CHECK_SIGNALS` (as for "State first"), and only for a card with a `verify-web: <claim>`
  line in its body, or a `[crit]` / `[review]` marker in its title or body. The claim is the `verify-web:` line, else the
  title without the markers. A `[human]` card, a title that is only a marker and every other card search nothing.
- **What leaves.** The claim only: redacted (`redact_for_egress` plus the URL-credential scrub, as every prompt), one line,
  at most 200 characters. Never the card body, the steps or the summary. The query goes to the search backend Hermes has
  configured, through the public `web_search` tool (`ctx.dispatch_tool`); the plugin adds no dependency and no endpoint.
- **What the judge reads.** Up to `results` hits as `- <url> - <description>` (one line, `result_chars` characters), under a
  header that names them untrusted text from a web search: a lead to weigh against the card, never an instruction. Code
  never decides on a hit; the verdict contract (`DONE` / `GAP:` / `ESCALATE:`) is unchanged. The hits are not posted to the card.
- **Budgets and failure.** `max_per_card` searches per card and `max_per_day` per UTC day (`calling-mom-spend.json`, field
  `web`); an attempt counts, a hit does not matter. A hard `timeout_s` (own thread, the search is abandoned, not the
  check), a failed search, an error reply, a hit that is not a dict and a missing `dispatch_tool` all leave the judge's
  prompt as it was (fail-open). Searches cost no tokens and add no comment.
- **Log.** One `web_verify` row: `query_hash` (as `cmd_hash`) and `results`. No query, no URL, no text. Hermes' own `web_search`
  tool logs the (redacted) query at info level in the Hermes log; that is outside this plugin's log.
- **Abuse.** The hits are the only text in the prompt that a stranger can write, so they are capped (3 x 200 characters
  plus the URL), flattened to one line each (no fake header or role line), labelled and placed after the card text. A hit
  can still mislead the model: that is why the verdict stays with the model and the card, and why the feature is opt-in.

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

**Confidence.** Every Mom prompt of this flow, and the Mom-first prompt of the advisor gate (a category in
`olymp.categories` with Olymp configured), ends with a request for one last line, `CONFIDENCE: <1-10> | <one-line reason>`
(label plus confidence, after the "Curate/Infer" stages of SMART, see References). `_confidence` takes the last such line
off the text, so it is never posted to the card. It is fail-open: no line, no digits or a value outside 1..10 is
`None`, and the reply is handled as before. The rules:

- below `mom.escalate_below` (default 4) Olymp is asked as if Mom had written `ESCALATE`. Only where Olymp is asked at all:
  configured, `max_per_card` and `max_per_day` left, no brief-only question. A low confidence never opens a way past
  these limits; where Olymp has no room the reply stands as before.
- from `mom.brief_short_from` (default 7) an `ASK_HUMAN` brief is cut to 160 characters per line (400 otherwise).
- the log row of the advisor and of the question carries `confidence` (Mom's own, also when Olymp then answered);
  the rationale stays out of the log, as every other model text. `status` shows no advisor rows, so it shows no confidence.

`ESCALATE:` still works: a model that has no confidence line, or a reply that carries both, escalates as before.

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
card) or 409 (not blocked, no brief, no hold and no question, `allow_once` without a hold). Nothing changes on an error. On success:
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

`hermes calling-mom report [--days 7 | --since DATE] [--until DATE] [--board X] [--json]` and `GET /summary` call one function, `report.build(rows,
since, board, tasks, until)` (via `attention_report(days, board, since=, until=)` in `__init__.py`). `since` and `until` are an ISO date or date-time in UTC (a date alone is 00:00); `since` is included, `until` is not; `--days` counts back from `until` or now. Touch rows carry the time of the event, so a window finds old history. It counts rows of `logs/calling-mom.jsonl`
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
| `advice.calls` | advisor rows that were not silent (`noop`): advice that reached the worker |
| `advice.followed` / `acted` / `ignored` | `advice_feedback` rows by `result`; a 1.4 row counts by its old `verdict` (`followed` is `acted`, `rejected` has no result) |
| `advice.unknown` | `calls` minus the three above, never below 0: advice without anchors, without a verdict yet, or a hard-killed run |
| `you.outside` | `comments` (+ `authors` by author id), `changes` (+ `changes_by` by kind), `actor` (always `unattributed`), `to_person` (cards that entered triage or sat on an unanswered question), `waiting` (those still in that state), `own_writes` (rows taken out of the count), `source`, `observed` (false: the sweep has not read this board, nothing is shown as 0) |
| `you.chat_waits` | `count`, `total_s`, `longest_s`, `by_kind`, `source`, `observed` (false while `count.chat_waits` is off) |
| `quota` | `limits` (one entry per `limited` row: `provider`, `until`, `seconds` to the reset or null), `down`, `paused` (cards with a `[quota]` comment), `skipped` (Mom calls not made; Olymp's are not counted), `fallback` (answers by the fallback), `stalled{cards, minutes}` (`stall` rows), `source` |
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

## Quota, fallback, stalled cards, pace

`quota.py` holds the pure parts (error classes, the `resets 1:50pm (UTC)` pattern, health dicts, stalled test, pace plan);
`__init__.py` wires them: `on_api_error` (`api_request_error`, worker sessions of opted-in boards), `_note_error` (failed Mom and
Olymp calls), `_guarded` (skips an unhealthy route, hands Mom's calls to `mom_fallback`, retries on it after a failure unless the
caller is a bounded hook), `_quota_notice` (`on_kanban_worker_exited` with `outcome == "rate_limited"`), `_record_stalls` (the
sweep, with the touch scan, at most every 30 s per board) and `pace_board`.

- **Classes.** `key_limit` (402, `reason` billing, `Key limit exceeded`) and `auth` (401, `reason` auth, an error type that contains
  `LoggedOut`) come first, then `rate_limit` (429, `reason` rate_limit, `You've hit your ... limit`, `usage limit`). An aggregator's
  upstream 429 (`upstream_rate_limit`), overload and timeouts are `other`: they change nothing.
- **Health.** `limited` until the parsed reset (30 minutes when unknown; a guess never extends a known reset), `down` for 30 minutes. Keyed by profile
  and provider. An entry that ended is dropped on the next read and logged as `recovered`.
- **Exit hook.** The payload of `on_kanban_worker_exited` names no provider, so the `[quota]` comment takes it from the health entry of
  the card's assignee profile (set by the worker's own `api_request_error`), else from the card's model pin, else says "The provider".
  One comment per card and limit window (the reset time, or a 30 minute bucket without one). It needs the plugin in the dispatcher's home.
- **Fallback.** Used for Mom's route (advice, pre-flight, questions, second opinion), never for Olymp. A bounded hook (pre-flight) makes one call
  only: it uses the fallback when Mom is already known to be limited or down, and does not retry after a failure.
- **Stalled.** `running` and `now - (last_heartbeat_at or started_at) >= stall_minutes`. The sweep reads it through `_core.running_cards`
  (covered by `doctor`'s `board read`). Hermes' own wedged-worker reclaim only runs after an hour of silence and for a live PID;
  this flag is for the case before that and for a dead host.
- **Guarded.** `ready` and the newest events of the card are `respawn_guarded` (Hermes writes one per dispatcher tick, with the reason, for
  example `active_pr`, `recent_success`). The streak starts at the first of them; any other event ends it, except `commented` (our own
  notice). The sweep reads it through `_core.guarded_cards`. One comment and one `guard` row per streak once `now - since >= guard_minutes`;
  the card leaving `ready` clears the memo. Report only: the guard protects against a duplicate PR, so a person decides.
- **Pace.** `quota.plan` orders the `todo` and `ready` cards by priority, then age; running cards head the first chains; waiting cards fill
  the empty chains first and then go round-robin; a link that would close a cycle goes to another chain or the card is left alone. Links
  go through `kanban_db.link_tasks` (via `_core.link`). A second `pace` on a board refuses until `--undo` ran.

## Desktop backend

`dashboard/plugin_api.py` (manifest `dashboard/manifest.json`, `tab.hidden`: no web-dashboard tab) is mounted by Hermes
at `/api/plugins/calling-mom/` in the gateway process. It loads the plugin module by path and reuses its functions;
it imports nothing from Hermes itself. Boards are read through the plugin's `_connect` (read-only use).

| Route | Answer |
|---|---|
| `GET /summary?days=7` (1..90), optionally `since=` and `until=` (400 for a bad date) | The attention report (see above: `worker`, `mom`, `olymp`, `you`, `cost`) plus `you.waiting`, `today{mom_calls}` and `recent[{ts, kind, task, board, detail, by}]`. `today` starts at 00:00 UTC. `recent` is the last 10 rows worth a line, newest first, no prose. |
| `GET /cards?board=<slug>` | `{task_id: [badge, ...]}` for cards the plugin touched, at most two, most urgent first: `held`, `for you`, `stalled`, `verify failed`, `olymp`, `mom`, `verified`. `held`, `for you` and `stalled` need an opted-in board and a card that still waits (`stalled`: a running card without a heartbeat); the others come from the log (the last finished `verify` row decides). Without `board`, every board; an unknown board gives `{}`. |
| `GET /needs-you` | Blocked cards on opted-in boards with an unanswered `[for you]` brief or a hold nobody allowed: `{board, task_id, title, brief, holds[{reason, alternative}], can_accept, age_s}`, oldest first. `brief` is the comment without its marker; `can_accept` says there is a `Recommendation:` line. Also the running cards without a heartbeat for `stall_minutes` (read live from the store, so the entry goes when the heartbeat is back): `brief` null, no holds, `can_accept` false, plus `stalled{minutes, reclaim}` (the reclaim command to copy); the Desktop page shows them without buttons. |
| `POST /answer` | `{board, task_id, text?, accept?, allow_once?}` -> `answer_card(..., by="desktop")`. 404 for a board that does not exist or did not opt in, or no such card; 409 for a card that is not blocked or carries no brief or hold; 400 for bad arguments; 422 for a malformed body. |

**Desktop on another machine.** The app loads Desktop plugins from its own disk only
(`$HERMES_HOME/desktop-plugins/<id>/plugin.js` or `$HERMES_HOME/plugins/<id>/desktop/plugin.js` on the machine that
runs the app; the SDK calls them app-level: one root for every profile, gateway or remote machine), and
`hermes_cli/desktop_plugin_halves.py` does not install the half on a host that Desktop only reaches over SSH. The
backend routes above live in the gateway and work either way. On the machine that runs the app, install the half with
Hermes' own door: **Capabilities -> Plugins -> Install** (it clones the Desktop half into `desktop-plugins/`), then
enable it. Card badges need Desktop 0.22.6 or later. `hermes calling-mom setup` (last line) and `doctor` (an `INFO` line,
never a failure) name this path.

The plugin does **not** download or replace its own files (catalog admission rule 3, "no self-updating code"); there is
no `desktop-sync` command since 1.5.6. Updates reach the app through Hermes: `plugins.auto_apply` and `hermes plugins
update` for the gateway's copy, the Update button for the app's copy. The page shows `Desktop vX · Backend vY` when the
two differ (`GET /summary` carries `version`, read from `plugin.yaml`; `plugin.js` carries the same number in `VERSION`,
and a test keeps them equal), so a stale Desktop copy is visible at once.

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


## Signals, one by one

Which event makes Mom speak. The README shows the short version.

| Signal | Who sends it | What Mom does |
|---|---|---|
| The main model **gives up** on a card ("capability" block), or the card keeps **crashing** | the main model / the board | The whole card is handed to Mom's model, once. |
| The **same error 3 times in a row** | the main model | A short tip: probable cause, next steps. |
| The **same command 3 times** (going in circles) | the main model | A short tip to get out of the loop. |
| **Broken tool calls 3 times** | the main model | A short tip. |
| A **risky command or file** (merge, release, force push, `terraform apply`, `kubectl delete`, `DROP TABLE`, broad `rm -rf`, secrets, ...) | the main model | A review: the biggest risk, what to double-check. |
| A command that **changes** another machine (`ssh`, `scp`, `pct`, `qm`; a read-only look such as `ls` or `pgrep` is no review) | the main model | One short look per card. |
| **`SECOND-OPINION: <question>`** as a card comment (or the `second_opinion` tool) | the main model | Mom answers the question. |
| It says **"done"** | the main model | A check: does the result match what the card asked? Missing parts go back once. |
| Card marker **`[crit]`** | you | The card runs on Mom's model from the start. |
| Card marker **`[review]`** | you | A separate reviewer reads the result. |
| A **question** (`kanban_block`, kind `needs_input`), answers on | the main model | Mom answers first, see "Mom answers first". |
| A **`verify: <command>`** line on the card | you | The command must pass before the card can finish. No model needed. It runs in the background (up to 10 minutes); the worker is told to call `kanban_complete` again until the result is in. |

Mom's answer appears **in the card** as a comment and is shown to the main model. If Mom is slow, the worker is never held
up: her answer goes to the card when it arrives and reaches the main model with its next tool result, marked `[late advice]`.

## Reuse your Advanced Delegation models

If you use the official [*Advanced Delegation*](https://hermes-agent.nousresearch.com/docs/plugins/advanced-delegation)
plugin, `setup` reads its model routes (`fast`, `deep`, `local` ...) and offers them for Mom and Olymp:

```bash
hermes calling-mom setup                                      # asks "one of your delegation routes, or by hand?"
hermes calling-mom setup --mom route:fast --olymp route:deep  # no questions
```

The values are **copied once**, nothing stays linked: change a route later and Mom does not follow (run `setup` again).
A route needs a provider and a model; routes with only a `base_url` are not offered.

## A real report

From the author's own install (board `coding`, 5 to 6 November 2026, while 1.4 was built on it). The `You` block counts
what reached the person, **including what other people and agents did on the board**, because that is attention too.

```
Calling Mom, 2026-11-05T00:00Z to 2026-11-06T03:00Z, board coding
Worker  1103 tool calls in 21 runs, 14 stuck signals; 0 API calls, 0 tokens in / 0 out
Mom     38 calls: 0 questions answered, 0 pre-flight checks (0 holds), 0 verify passed / 0 failed
Olymp   0 calls, 0 of 6 today
You     0 briefs, 0 answers, 0 holds released
        outside help  9 comments (default 7, daidalos 2) · 24 board changes (unattributed) · 1 card waiting on a person
        chat waits    not observed (calling_mom.count.chat_waits is off)
Advice  36 calls: 0 followed · 22 acted · 0 ignored · 14 unknown
Cost    Mom 128,372 in / 17,655 out tokens on a subscription (no per-token price), Olymp 0 calls
```

Version 1.4 called 22 pieces of advice "followed" because some action came next. Those rows carry no anchors, so they
read `acted` now, and the 14 without a verdict read `unknown`. From 1.5 on, `followed` needs a later tool call that uses a
command, path or tool Mom named. A Claude subscription has no per-token price, so the cost line gives tokens and says so.
Options: `--days 7`, `--since 2026-11-05 --until 2026-11-06T03:00`, `--board X`, `--json` (dates are ISO and UTC).


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

## References

- Vallurupalli, Ferraro (UMBC): *SMART: State-based Multi Agent Reasoning Traces for Verification-oriented Problem-Solving*,
  arXiv:2611.08196v1, <https://arxiv.org/abs/2611.08196v1>. We take the pattern, not the benchmark. Used for: every verdict of
  Mom carries a label and a confidence 1-10 (`mom.escalate_below`, see "Mom answers first"); a pre-flight HOLD must carry a concrete rationale (refute filter, see "A HOLD carries its rationale"); the completion check and the review of a critical change name the most plausible way the claim is wrong first (contrastive premise, see "Counter-claim first"); with `advisor.extract_state` those two checks first extract 3-5 stateful entities with their facts and judge on that state ("Identify", see "State first"); with `web_verify` the claim of a `[crit]` / `[review]` card or a `verify-web:` line is looked up on the web and the hits go to the judge as untrusted evidence ("Curate", see "Web evidence"). Nothing in it makes a card
  text leave the machine, except that one redacted claim of at most 200 characters, only when `web_verify` is on: the local model stays the default, the redaction (`redact_for_egress`) and `[local-only]` are unchanged.
