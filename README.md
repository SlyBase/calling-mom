# Calling mom

A [Hermes](https://hermes-agent.nousresearch.com) plugin that lets a **cheap model run your Kanban board** and "calls mom" only where the board or the tool results show it is needed.

Two tiers, named after the family:

| | Role | Typical choice |
|---|---|---|
| **Sibling** | the cheap model that answers first and gets pinned on a give-up | Sonnet, DeepSeek Flash, a local model |
| **Mom** | the expensive model, asked only for rare high-stakes edge cases | Opus on a subscription |

The worker never judges itself. A weak model does not know when it is out of its depth, so every trigger is a **durable fact** (a typed block, a marker, a failure streak, a risky command), not a self-assessment.

> Status: 1.3.0. Tested with Hermes 0.22.5 (see [Compatibility](#compatibility)). The plugin imports Hermes internals; run `hermes calling-mom doctor` after every Hermes update.

## Quick start

```bash
hermes plugins install SlyBase/calling-mom
hermes plugins enable calling-mom
hermes calling-mom setup        # pick sibling and mom from YOUR configured providers/models
hermes calling-mom doctor       # one real call to each model, tells you what is missing
```

`setup` lists the providers and models Hermes already knows, asks which board may use the advisor, grants both models in the plugin allowlist (existing grants stay), and offers the 1-minute sweep cron job. It shows what it will write and asks before writing. No questions: `hermes calling-mom setup --preset subscription` (or `openrouter`).

`hermes calling-mom status` shows the effective setup, today's mom calls against the limit, and spend. A dead key or missing login is **not silent**: the card gets one `[advisor unavailable]` comment and `doctor` names the cause.

## What it does

| Trigger | Action | Cost |
|---|---|---|
| Worker blocks a card with kind `capability` (or untyped) | Pin the card to the sibling model, return it to `ready`, once. A second block parks it for a human. | one sibling run |
| The crash circuit breaker parks a card (`gave_up`) | Same. Infrastructure failures are skipped. | one sibling run |
| `[crit]` in title/body | Pin to the sibling model at claim time. | one sibling run |
| Stuck signal (same error x3, identical call x3, malformed call x3) | **Advisor**: one short question to the sibling with a redacted tail of the run. The answer is appended to the tool result. | ~300 output tokens |
| Risky file or command (`*.tf`, `migrations/`, `terraform apply`, `kubectl ... apply/delete`, `gh pr merge`, `git push ... main`, `git tag`, `ssh`, ...) | Advisor, category `architecture`: the sibling answers first. **Only if it writes `ESCALATE: <reason>` is mom asked.** Also reachable as `second_opinion` tool and `SECOND-OPINION:` comment. | sibling call, rarely a mom call |
| `verify: <cmd>` line on a card | Gates `kanban_complete`: the command must pass. | free |
| `kanban_complete` on an advisor card | **Completion check** once per run: card vs. summary; `GAP: ...` blocks the first attempt. | ~1 sibling call |
| `[review]` in title/body | A separate reviewer profile reads the result on completion. | one short review run |

Which model answers what:

| Question | Model |
|---|---|
| stuck, validation, completion | sibling |
| risky change / `SECOND-OPINION` | sibling first, **mom only on `ESCALATE`** |
| `steps` pace signal | nobody (logged only) |

Mom is limited by calls (`mom.max_per_card` 1, `mom.max_per_day` 6), not by dollars, so a subscription needs no USD cap. The advice arrives **after** the tool ran (`transform_tool_result`): it warns, it cannot prevent a command.

## Manual configuration

Everything `setup` writes can be set by hand. Keys live under `calling_mom:` in `config.yaml`:

```yaml
calling_mom:
  sibling: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}   # a pair: both keys or the block is ignored
  mom:     {provider: claude-subscription-directsdk-experimental, model: claude-opus-5-5}   # empty = no mom
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
| `sibling.model`, `sibling.provider` | DeepSeek Flash / openrouter | The cheap tier. The older top-level `model`/`provider` pair still works; `sibling` wins. |
| `mom.model`, `mom.provider`, `mom.categories` | off / `[architecture]` | The expensive tier. A half pair turns mom off; a half pair in `categories.<name>` is ignored. |
| `mom.max_per_card`, `mom.max_per_day` | `1` / `6` | Hard call limits for mom. |
| `marker` | `[crit]` | Pin the card to the sibling model. |
| `local_only_marker` | `[local-only]` | Such cards never send anything to a remote model. Always wins. (The dashboard field `completion_contract: local-only` is a different thing: the board default.) |
| `limits` | `same_error 3, loop 3, malformed 3, steps 40` | Stuck-signal thresholds. |
| `advisor.*` | disabled | `enabled`, `boards`, `marker`, `max_consults_per_card 3`, `max_output_tokens 700`, `context_chars 6000`, `daily_usd_cap 2.0`, `daily_token_cap 200000` (caps count the sibling only). |
| `categories.<name>` | inherit | Per category (`error`, `loop`, `validation`, `architecture`) its own `model` AND `provider`. |
| `verify.*` | see below | `timeout_s`, `max_fix`, `trusted_creators ["user"]`, `allow`. |
| `review.*` | `[review]` / `reviewer` | Marker and reviewer profile (`""` = off). |
| `ladder.effort_first` | `false` | Free rung before the sibling. |
| `metrics.pushgateways` | `[]` | Pushgateway URLs, tried in order. |

Kill switch: `CALLING_MOM=off` in the environment.

### Reviewer profile (for `[review]` cards)

```bash
hermes profile create reviewer --clone-from <your-profile> --no-alias
hermes -p reviewer config set model.default <strong-model> --force
hermes -p reviewer config set model.provider <provider> --force
# remove inherited local endpoint settings: model.base_url, model.api_key, model.api_mode
hermes -p reviewer plugins disable calling-mom
```

## Compatibility

| Plugin | Hermes | Notes |
|---|---|---|
| 1.3.x | 0.22.5 tested, `>= 0.22` required | `doctor` checks the version and that the kanban internals import. |

The plugin uses `hermes_cli.kanban_db`, `kanban_db_connect` and the private comment watermark of `tools.kanban_tools`. A Hermes update can break it; the CI runs the full suite against Hermes `main`.

## Security model

Card text can come from mail or web content, so treat it as untrusted.

- **`verify:`** runs only for cards created by `verify.trusted_creators`, never through a shell (`shlex`), only commands whose first word is in `verify.allow`, with a reduced environment. `python3 -c` is still arbitrary code: the creator check is the real control. Ignored lines are logged as `verify_ignored`.
- **Advisor** is opt-in per board or card. Context is redacted with Hermes' `redact_for_egress`. Board tool results (`kanban_*`) are filtered out of the context. Daily USD and token caps apply.
- **Observation log** (`logs/calling-mom.jsonl`) stores hashes and counts, no content.
- The plugin never edits Hermes config.

## Metrics

`metrics.py` renders Prometheus text from the plugin log, the spend ledger and read-only board DBs. The sweep PUTs it to `/metrics/job/calling_mom/instance/<instance>` on the first reachable Pushgateway. Families: `calling_mom_events_total`, `_advisor_calls_total`, `_advisor_errors_total` (dead key, no login), `_mom_calls_total`, `_advisor_tokens_total`, `_advisor_cost_usd_total`, `_advice_feedback_total{verdict}`, `_stuck_signals_total`, `_verify_total`, `_verify_ignored_total`, `_review_redirects_total`, `_ladder_total`, `_escalations_total{reason}`, `_gave_up_age_seconds{board}`, `_spend_today_{usd,tokens}`, `_spend_cap_{usd,tokens}`, `_cards{board,state}`, `_last_push_timestamp_seconds`.

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

Two-tier routing was exercised live with real models (sibling and mom on a subscription): routine and even severe incidents were answered by the sibling alone and mom was never asked; the mom leg was proven with a forced `ESCALATE`. **No natural `ESCALATE` has been observed yet**, so how often a weaker sibling escalates is unmeasured.

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
python -m unittest test_signals test_calling_mom test_metrics test_matrix test_cli
```

Tests write to a sandbox, never to your real log. Test-first: a new branch needs a test, `linecov.py` (stdlib only) fails the gate otherwise. `# pragma: no cover` on an `except` or a `def` line excludes that block, use it only for glue that needs a live Hermes.

### Deploy and release (maintainers)

`scripts/deploy.sh` copies the checkout to the plugin directories named in `scripts/.deploy.env` (git-ignored, see the script header), runs the gate and a smoke test. Release: bump `version` in `plugin.yaml`, add the CHANGELOG section, commit, push tag `v<version>`; the `release` workflow builds `calling-mom-<version>.zip`. Do not create the GitHub release by hand first.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT
