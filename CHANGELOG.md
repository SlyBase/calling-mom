# Changelog

All notable changes to this plugin. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.3.0] - 2026-10-30

### Added
- **Two-tier advisor**: `mom.model`/`mom.provider` (empty = off) name the "mom", a very strong model
  that is asked only for `mom.categories` (default `architecture`: critical files and
  commands, `SECOND-OPINION`). Stuck, validation and completion questions stay on the sibling (`model`/`provider`).

### Changed
- The `steps` pace signal is logged but no longer calls the advisor (it produced a call that
  answered `NO_ACTION`).

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
