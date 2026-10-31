# Changelog

All notable changes to this plugin. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.3.1] - 2026-10-31

Catalog-readiness: use only public Hermes surfaces for the advisor comment.

### Changed
- Advisor comments are written through the public `kanban_comment` tool (`ctx.dispatch_tool`) instead of the
  board database, and the write into Hermes' private `tools.kanban_tools._comment_watermark` is gone (catalog
  rule 9). The tool signs with the worker's own profile and Hermes' comment relay skips a worker's own comments,
  so advice still reaches the worker once. Advisor comments are recognised by their text (`[category/signal] run`,
  `[advisor unavailable]`); comments of older versions (author `advisor`) still count. A comment that cannot be
  written never costs the worker its advice.
- `plugin.yaml` declares `requires_hermes: ">=0.22"` (catalog rule 14).

## [1.3.0] - 2026-10-30

### Added
- **Two tiers: mom and olymp** (the names changed during the 1.3.0 preview: `sibling` is now `mom`, the old `mom` is now `olymp`; `setup` rewrites old config). `mom.{model,provider}` is the cheap tier (the old top-level pair still
  works). `olymp.{model,provider}` (empty = off) is the expensive tier. For `olymp.categories` (default
  `architecture`) the mom answers first and writes `ESCALATE: <reason>` only for a rare high-stakes case;
  only then olymp is asked. Olymp is limited by calls (`olymp.max_per_card` 1, `olymp.max_per_day` 6), exempt from the
  USD/token caps (subscription). Model and provider only travel as a pair; a half pair is ignored and logged.
- **`hermes calling-mom setup | status | doctor`**: `setup` lists your configured providers and models,
  grants them in the plugin allowlist (entries only grow), asks before writing and offers the sweep cron job
  (`--preset subscription|openrouter`, `--yes`). `status` shows the effective setup and usage. `doctor` checks
  the Hermes version and internals, the pairs, the allowlist and makes one real call per tier.
- A dead advisor provider is **visible**: one `[advisor unavailable]` comment per card (redacted, short),
  trace row `advisor_error`, metric `calling_mom_advisor_errors_total`; `calling_mom_olymp_calls_total` counts olymp.
- Comments and trace rows name the provider: `model (provider, olymp)`.
- `SECURITY.md` with the threat model, `CONTRIBUTING.md`, issue and PR templates, a compatibility table,
  CI job running the full suite against Hermes `main`.

- **No duplicate sweep cron job.** Cron jobs live per Hermes home (root gateway, each profile), so a profile
  could not see the root job and `setup` created a second one. `setup` now looks in every home and never
  creates another; `doctor` warns about several active sweep jobs (names the ids and the fix) or none.
- A model that just failed is not asked again for 5 minutes (`COOLDOWN_S`): a dead provider no longer delays
  every critical command of a worker (one card saw 7 failing calls in a minute).
- `status` shows today's advisor failures and the last cause.

- **Risk list from existing agents** (`rules.py`, table-tested in `test_rules.py`): download-and-execute, destructive
  git, `curl`/`gh` writes to GitHub (merge, release, dispatch), `terraform`/`kubectl`/`helm`, `DROP`/`TRUNCATE`, broad
  `rm -rf`, secret and IAM writes, `--insecure`, tunnels, the metadata endpoint. Add your own with
  `categories.architecture.commands_extra`.
- **Two risk tiers.** `ssh`/`scp`/`pct`/`qm` are `watch`: one short mom review per card with their own budget, so
  they can no longer use up the critical budget (a real run spent both reviews on ssh reads, then merged and released
  unreviewed). The critical budget is 4 per card (was 2).
- **The completion check runs once per run, not once per card.** A later, bigger run is checked again.

### Changed
- New banner and icon in the Hermes official-plugin style (Thetis answers Achilles, Mount Olympus small and far
  above, reached by a dashed line; tags `STUCK -> MOM .. OLYMP`), shown at the top of the README. Sources and build
  scripts in `assets/banner-src/`; the old SVG generator `tools/make_assets.py` and its assets are removed.
- The `steps` pace signal is logged but no longer calls the advisor.
- `kubectl -n prod delete ...` (flags before the verb) is now a critical command.
- URL credentials (`scheme://user:pass@host`) are scrubbed before anything is sent to a remote model.
- Maintainer scripts (`check.py`, `deploy.sh`, `smoke.py`) no longer contain machine paths: everything comes
  from the environment or the git-ignored `scripts/.deploy.env`.

### Fixed
- `linecov.py` counted lines of any package's `__init__.py` (e.g. `hermes_cli`) as the plugin's, which showed
  100 % while about 20 lines were untested. It now matches the directory and honours `# pragma: no cover`.
- The test matrix no longer leaves `cache-edge.jsonl` in the checkout.

## [1.2.0] - 2026-10-29

Fixes from an audit of four real cards (two advisor answers wrong, one redundant, one wrong
card result closed unchecked).

### Added
- **Completion check** on `kanban_complete` for advisor cards: the strong model compares the
  card with the completion summary once per run. `GAP: ...` blocks the first attempt; the
  second always passes. Own budget (`categories.completion.max`, default 1).
- Critical commands now cover public or irreversible actions: `gh pr merge`,
  `gh release create`, `gh api .../merge|releases|comments`, `git tag`, `git push` to
  `main`/`master` or with `--force`, `gh issue|pr comment`, `ssh`/`pct`/`qm`.
  `execute_code` is checked like `terminal`.
- `calling_mom_advice_feedback_total{verdict}`: the first worker reply after advice is
  counted as `followed` or `rejected` (keyword heuristic, `post_api_request` hook).

### Changed
- `steps` resets on every file edit: it fires only for runs that call tools without
  changing anything.
- The stuck prompt says the card text can hold a wrong guess and allows `NO_ACTION`;
  such answers post no comment and do not interrupt the worker.
- Advice is delivered once: the worker's comment bridge no longer relays the advisor
  comment as an "operator" note (unless an unread human note is waiting).
- Advisor comments name the run (`run 59`) and omit `$0.0000` when the provider reports
  no cost.

## [1.1.1] - 2026-10-28

### Fixed
- `calling_mom_escalations_total` is exported with all three reasons at `0` from the first
  push. Without an existing series `increase()` stays empty, so the
  `CallingMomParkNotEscalated` alert could never fire in the case it exists for.

## [1.1.0] - 2026-10-28

### Added
- Reactive escalation on the core hook `on_kanban_worker_exited`: a card the crash
  circuit-breaker parks is escalated in the same dispatcher tick, without polling.
- `calling_mom_escalations_total{reason}` counter (`crit` / `blocked` / `breaker`) and
  `calling_mom_gave_up_age_seconds{board}` gauge, so an alert can tell "a card parked but
  nothing escalated it" apart from "nothing happened".
- `test_matrix.py`: standardised contract table, one row per case per hook entry point.
- `linecov.py` (stdlib line coverage) and `check.py` (tests + 100 % coverage threshold +
  copy-drift gate), `scripts/smoke.py`, `scripts/deploy.sh`.
- CI and tag-driven release workflow.

### Changed
- The observation log, sweep watermark and spend ledger live under the **shared** Hermes
  root (`$HERMES_HOME` of the installation), not under a profile home. The gateway and the
  cron sweep now write one file instead of one per profile.
- Log is tail-trimmed at 5 MB so the per-minute liveness rows cannot grow without bound.

### Fixed
- `_escalate_parked_task` handed an un-entered context manager (board given) or a closed
  connection (board `None`) to the write, so a parked card could never be escalated.
  Both paths now run inside `with _connect(board) as conn:`.
- `on_parked` is fail-open: a database error is logged, never raised into the dispatcher.
- `_resolve_task` no longer scans every live board; it resolves the board it was given
  (`None` = the active board).

### Removed
- The `kanban_task_parked` core patch route. Deployed Hermes is a Docker image; the core is
  not writable in production. The plugin uses hooks that already exist.
