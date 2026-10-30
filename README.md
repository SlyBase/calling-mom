# Calling mom

A [Hermes](https://hermes-agent.nousresearch.com) plugin that lets a **cheap local model run your Kanban board** and "calls mom" (a stronger model) only where the board shows it is needed.

The local model never judges itself. A weak model does not know when it is out of its depth, so every trigger is a **durable fact from the board or the tool results**, not a self-assessment.

> Status: 1.0.0, private preview. Tested on one setup (local vLLM worker, DeepSeek via OpenRouter as the strong model).

## What it does

| Trigger | Action | Cost |
|---|---|---|
| Worker blocks a card with kind `capability` (or untyped) | Pin the card to the strong model, return it to `ready`, once. A second block parks it for a human. | one strong run |
| The crash circuit breaker parks a card (`gave_up`) | Same, reactively on `on_kanban_worker_exited` (fired by the dispatcher after the breaker accounting). The cron sweep stays as the idempotent fallback for timeout-parked cards and for setups without the hook. Infrastructure failures are skipped. | one strong run |
| `[crit]` in title/body | Pin to the strong model at claim time. | one strong run |
| Stuck signal in a worker run (same error x3, identical call x3, malformed call x3) | **Advisor**: one short question to the strong model with a redacted tail of the run. The answer is appended to the tool result (once: the board does not relay it again as an operator note). `NO_ACTION` answers are dropped. The worker keeps going. | ~300 output tokens |
| Edit of a risky file / risky command (`*.tf`, `migrations/`, `terraform apply`, `gh pr merge`, `gh release create`, `git push … main`, `git tag`, public comments, `ssh`/`pct`/`qm`; `terminal` and `execute_code`) | Same advisor call, category `architecture`. Also reachable as the `second_opinion` tool and as a `SECOND-OPINION:` comment. | ~300 tokens |
| `verify: <cmd>` line on a card | Gates `kanban_complete`: the command must pass. Failures go back to the worker. After `max_fix` failures the advisor looks at it. | free |
| `kanban_complete` on an advisor card | **Completion check**, once per run: card vs. completion summary. `GAP: …` blocks the first attempt with the missing parts; the second attempt always passes. Own budget (1 per card). | ~1 advisor call |
| `[review]` in title/body | On `kanban_complete` the plugin itself calls `kanban_request_review(reviewer=...)`. A separate reviewer profile with the strong model reads the result. | one short review run |
| `ladder.effort_first: true` (off) | First give-up raises `reasoning_effort` on the same model before paying for another one. | free |

Only the **stuck point** goes to the strong model, not the whole turn. Blocks that need a human (`needs_input`, `dependency`) are never escalated.

## Install

```bash
hermes plugins install SlyBase/calling-mom
hermes plugins enable calling-mom
```

Then set the strong model (defaults: `deepseek/deepseek-v4.1-flash-1017` on `openrouter`):

```bash
hermes config set calling_mom.model <model> --force
hermes config set calling_mom.provider <provider> --force
```

### Cron sweep (fallback for breaker-parked cards and metrics push)

```bash
cp ~/.hermes/plugins/calling-mom/scripts/sweep.py "$HERMES_HOME/scripts/calling_mom_sweep.py"
hermes cron create "every 1m" --name calling-mom-sweep --no-agent \
    --script calling_mom_sweep.py --interpreter <hermes venv python> --deliver local
```

### Advisor (opt-in, off by default)

The advisor sends redacted code to a remote model, so it is off until you opt in twice:

```bash
hermes config set calling_mom.advisor.enabled true --force
hermes config set calling_mom.advisor.boards '[my-board]' --force     # or put [advisor] on a card
```

The plugin calls the model through `ctx.llm`, which Hermes only allows for models you grant:

```bash
hermes config set plugins.entries.calling-mom.llm.allow_model_override true --force
hermes config set plugins.entries.calling-mom.llm.allow_provider_override true --force
hermes config set plugins.entries.calling-mom.llm.allowed_models '[<model>]' --force
hermes config set plugins.entries.calling-mom.llm.allowed_providers '[<provider>]' --force
```

### Reviewer profile (for `[review]` cards)

```bash
hermes profile create reviewer --clone-from <your-profile> --no-alias
hermes -p reviewer config set model.default <strong-model> --force
hermes -p reviewer config set model.provider <provider> --force
# remove inherited local endpoint settings: model.base_url, model.api_key, model.api_mode
hermes -p reviewer plugins disable calling-mom
```

## Configuration

All keys live under `calling_mom:` in `config.yaml`. Defaults are in `__init__.py` (`DEFAULTS`).

| Key | Default | Meaning |
|---|---|---|
| `mom.max_per_card`, `mom.max_per_day` | `1` / `6` | Hard call limits for the mom model. It is asked only when the sibling answers `ESCALATE: <reason>` first, and its calls count in calls, not in USD or tokens (subscription). |
| `model`, `provider` | DeepSeek Flash / openrouter | The **sibling** (cheap tier): pinned on escalation, asked by the advisor. |
| `mom.model`, `mom.provider`, `mom.categories` | off / `[architecture]` | The **mom** (expensive tier). Set `model` AND `provider` (a half pair turns mom off, and a half pair in `categories.<name>` is ignored). For the listed categories the sibling answers first and writes `ESCALATE: <reason>` only for a rare high-stakes case; then, and only then, mom is asked. Every other signal stays on the sibling. Own spend is bounded by the per-card budget and the daily caps. A `categories.<name>` entry still wins. |
| `marker` | `[crit]` | Pin the card to the strong model. |
| `local_only_marker` | `[local-only]` | Such cards never send anything to a remote model. Always wins. |
| `limits` | `same_error 3, loop 3, malformed 3, steps 40` | Stuck-signal thresholds. |
| `advisor.*` | disabled | `enabled`, `boards`, `marker`, `max_consults_per_card 3`, `max_output_tokens 700`, `context_chars 6000`, `daily_usd_cap 2.0`, `daily_token_cap 200000`. |
| `categories.<name>` | inherit | Per category (`error`, `loop`, `validation`, `architecture`) its own `model`/`provider`. |
| `verify.*` | see below | `timeout_s`, `max_fix`, `trusted_creators ["user"]`, `allow`. |
| `review.*` | `[review]` / `reviewer` | Marker and reviewer profile (`""` = off). |
| `ladder.effort_first` | `false` | Free rung before the strong model. |
| `metrics.pushgateways` | `[]` | Pushgateway URLs, tried in order. `instance` defaults to `hermes`. |

Kill switch: `CALLING_MOM=off` in the environment.

## Security model

Card text can come from mail or web content, so treat it as untrusted.

- **`verify:`** runs only for cards created by `verify.trusted_creators`, never through a shell (`shlex`), only commands whose first word is in `verify.allow`, with a reduced environment. `python3 -c` is still arbitrary code: the creator check is the real control. Ignored lines are logged as `verify_ignored`.
- **Advisor** is opt-in per board or card. Context is redacted with Hermes' `redact_for_egress`. Board tool results (`kanban_*`) are filtered out of the context. Daily USD and token caps apply.
- **Observation log** (`logs/calling-mom.jsonl`) stores hashes and counts, no content.
- The plugin never edits Hermes config.

## Metrics

`metrics.py` renders Prometheus text from the plugin log, the spend ledger and read-only board DBs. The sweep PUTs it to `/metrics/job/calling_mom/instance/<instance>` on the first reachable Pushgateway. Families: `calling_mom_events_total`, `_advisor_calls_total`, `_advisor_tokens_total`, `_advisor_cost_usd_total`, `_advice_feedback_total{verdict}`, `_stuck_signals_total`, `_verify_total`, `_verify_ignored_total`, `_review_redirects_total`, `_ladder_total`, `_escalations_total{reason}`, `_gave_up_age_seconds{board}`, `_spend_today_{usd,tokens}`, `_spend_cap_{usd,tokens}`, `_cards{board,state}`, `_last_push_timestamp_seconds`.

Two of them exist for alerting:

| Metric | Meaning |
|---|---|
| `calling_mom_escalations_total{reason}` | cards pinned to the strong model — `crit` (marker at claim), `blocked` (worker gave up), `breaker` (crash park) |
| `calling_mom_gave_up_age_seconds{board}` | age of the newest breaker park on that board; absent when the board never parked |

The Pushgateway keeps the last push, so alert on the age of `calling_mom_last_push_timestamp_seconds`, not on absence. A park that nobody escalated shows up as a small `gave_up_age` with no matching increase of `escalations_total`.

### One log for all profiles

The observation log, sweep watermark and spend ledger live under the installation root (`<hermes root>/logs/`), not under a profile home: the gateway escalates reactively, the cron sweeps, and both must land in ONE file or the metrics only see half the story. `CALLING_MOM_LOG`, `CALLING_MOM_STATE`, `CALLING_MOM_SPEND` still override the paths. The log is tail-trimmed at 5 MB.

## What we measured, and what we did not

24 runs (6 hidden-test coding tasks x 4 arms: local, local + high reasoning effort, local + advisor, strong model). **All 24 were correct.** The local model solved every task alone, so this set shows **no case for escalation**: it is too easy to find the capability boundary. `ladder.effort_first` stays off because high effort was slower for no gain. Build a harder set (multi-file, ambiguous spec) before tuning any threshold.

Known limits:

- The advisor was exercised live once; the stuck detectors fired one false positive (fixed: the identical-call history now resets on `write_file`/`patch`).
- `claim_review_task` fires no `kanban_task_claimed` hook, so the strong model cannot be pinned per review run. That is why review uses a separate profile.
- `model_override` is per card, not per run.
- The plugin imports Hermes internals (`hermes_cli.kanban_db`, `kanban_db_connect`, `kanban_db_boards`). `hermes plugins validate` passes but warns about them; a Hermes update can break the plugin. The tests catch that.
- The dispatcher tick hook did not fire in the gateway we tested. The cron sweep covers it.
- The reactive hook `on_kanban_worker_exited` only fires in the process that runs the dispatcher,
  and only for the crash path. Timeout parks and spawn-failure parks emit no hook, so the cron
  sweep stays as the idempotent fallback for them.

## Development

Needs a Hermes checkout/venv (the tests use the real `kanban_db`). No pytest required:

```bash
python -W error::ResourceWarning -m unittest test_signals test_calling_mom test_metrics test_matrix
```

Tests write to a sandbox, never to your real log.

### Deploy and release

```bash
scripts/deploy.sh            # sync both plugin copies, gate, smoke test
scripts/deploy.sh --restart  # same, then restart the gateway so it reloads the plugin
```

Release: bump `version` in `plugin.yaml`, add the CHANGELOG section, commit, tag `v<version>`.
The `release` workflow refuses a tag that does not match the manifest, builds
`calling-mom-<version>.zip` from the tracked files and publishes it as a GitHub release.
CI (`ci.yml`) checks compile, manifest, hook/manifest agreement and the CHANGELOG entry —
the full suite needs a Hermes venv and runs on the host via `check.py`.

### Release gate

One command, before every release (and daily as the `calling-mom-check` cron job):

```bash
/opt/hermes/.venv/bin/python check.py          # exit 1 on any failure
/opt/hermes/.venv/bin/python check.py --sync   # make the deployed copy match
```

It runs three things:

| Part | What it proves |
|---|---|
| `unittest` (178 tests) | the behaviour is unchanged |
| line coverage over `__init__.py`, `metrics.py`, `signals.py` | **100 % threshold**; a new branch without a test fails the gate (`linecov.py`, stdlib only) |
| copy drift vs. `/opt/data/plugins/calling-mom` | the deployed copy is byte-identical to the canonical one |

`test_matrix.py` is the standardised contract: one row per case (entry point, prepared
card, call, expected effect). A new guard branch needs a new row — that is what keeps the
coverage gate honest.

## License

MIT
