# Changelog

All notable changes to this plugin. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

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
