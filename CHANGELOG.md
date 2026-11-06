# Changelog

All notable changes to this plugin. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.4.2] - 2026-11-06

### Added
- **The page shows what is climbing right now**: a new `Active` section (between "Needs you" and "Recent activity") lists the running/ready cards of your opted-in boards with their last comment, newest last-activity first. A card with no comment shows "No comment yet". New read-only route `GET /active` (the last comment is the last signal, as-is; the body is cut to 200 chars).
- **Card identity in the lists**: "Recent activity" (and the Active list) show the card **title + board** as the visible line; the `task_id` is now a small, muted, mono suffix. A card that is no longer on the board falls back to its `task_id`.
- **A time-window selector** (7 / 14 / 30 / 90 days) in the page header drives the poll (`/summary?days=<n>`) and the "Last N days" subline; the choice is kept in `ctx.storage` and survives reloads.
- **Collapsible sections**: each page section (Needs you, Active, Recent activity) toggles on header click (chevron). Defaults: Needs you open, Active open, Recent activity collapsed.

### Changed
- The `GET /summary` recent rows carry a `title` (joined from the boards), so the page can show the card name instead of its id.

## [1.4.1] - 2026-11-06

### Changed
- **Display name is "Calling Mom"** (was "Calling mom"): plugin label, Desktop page and sidebar entry, status-bar chip
  tooltip, error texts, `hermes calling-mom report` header, README and docs. Identifiers stay `calling-mom` (commands,
  config keys, folders, log files, card comment prefixes and badges).
- The sidebar entry uses the `call-outgoing` codicon instead of `heart`.
- README screenshots recorded again with 1.4.1.

### Added
- README and TECHNICAL.md: "Desktop on another machine" (the Desktop app loads Desktop plugins from its own disk, so
  a Desktop that connects to a remote gateway needs `desktop/plugin.js` copied by hand).
- `hermes calling-mom setup` and `doctor` print one line that points to that section (in `doctor` an info line, never
  a failure).

## [1.4.0] - 2026-11-06

### Added
- **Demo kit** (`scripts/demo.py --assignee <profile> [--board B] [--reset]`): four cards on a board of their own, in a
  scratch folder with a bare **local** git remote, that make every rung fire: the same failing command three times and
  Mom's tip, a question Mom answers, a rewrite of shared history that pre-flight holds, and a `verify:` card. It
  watches the log and prints when each trigger fired. `--reset` removes everything it made.
- **README rewrite**: the ladder (worker, Mom, Olymp, you) and the tagline first, a one-minute quick start, "What you
  will see" with screenshots (`assets/screenshots/`), real numbers from the author's log, the updated "Your money and
  your data". `docs/TECHNICAL.md`: "New in 1.4 at a glance" (options, record kinds, the hook budget rule) and "Demo
  kit". `SECURITY.md`: a table of every data flow.
- **Pre-flight: a critical command is asked before it runs.** Before a Kanban worker runs a command that the risk
  list marks critical (force push, branch delete, release or tag publish, `terraform apply`, `kubectl delete`,
  `DROP TABLE`, a broad `rm -rf`, the rest of `CRITICAL_COMMANDS`), `pre_tool_call` asks Mom once (one synchronous
  call, deadline `min(preflight.timeout_s, 0.4 x hook budget)`, default 12 s), active only where the advisor is and
  never on `[local-only]` cards, non-critical commands, or outside Kanban workers. **GO** allows the command.
  **HOLD** blocks it with the reason and a safer alternative, posts the card comment `[pre-flight] HOLD: ...`, and
  persists the hold (task, normalized command hash) so a later run of the card is held without another model call.
  Only a human release lifts a hold (WP3/WP4); a timeout, a down provider, an unparseable answer or the cap
  (`preflight.max_per_card`, default 4) lets the command run and the 1.3 post-hoc review runs as today.
  Config: `calling_mom.preflight: {enabled: true, timeout_s: 12, max_per_card: 4}`.

- **Mom answers first.** A question that a worker raises itself (`kanban_block`, kind `needs_input`) on a board in
  `calling_mom.answer.boards` goes to Mom before it reaches a person. She replies `ANSWER: <text>` (the card gets
  `[mom answered]` and continues), `ASK_HUMAN:` with a four-line brief (`Question:`, `Options:`, `Recommendation:`,
  `If nobody answers:`; the card gets `[for you]` and stays blocked) or `ESCALATE: <reason>` (Olymp, 1 per card and 6
  per day, answers or writes the brief). The fast path decides without a model: `[local-only]` and `[human]` cards,
  blocks that Hermes itself re-kinded to `needs_input`, the per-card cap (`answer.max_per_card`, default 2) and the
  advisor's daily cap are skipped; a question or card that matches the critical risk list, the new
  `rules.HUMAN_TOPICS` (credentials and secrets, payments and money, legal, personal data, publishing in the owner's
  name) or an active pre-flight HOLD gets a brief, never an answer. An error or timeout leaves the card unchanged.
  **Off by default**: `hermes calling-mom setup` asks "Let Mom answer worker questions before they reach you?"
  (`--answer` / `--no-answer`). New setting `human_marker` (`[human]`).
- `answer_card(board, task_id, text=None, *, by, accept=False, allow_once=False)` and
  `hermes calling-mom answer <task> [--board B] [--accept | --text "..." | --allow-once]`: a person answers a brief
  (`[you via calling-mom] ...`; `--accept` takes Mom's recommendation) or releases a pre-flight HOLD once
  (`--allow-once`), and the card continues. `status` shows whether answers are on.
- New log rows `question` (`outcome`, `by`, `reason`, `ms`, tokens, `usd`) and `human_answer` (`via`, `accepted`,
  `allow_once`, `latency_s`; `via=card` when a card with a brief is claimed again after someone else unblocked it).

- **Desktop half** (Hermes Desktop; badges need 0.22.6 or later). `desktop/plugin.js` (plugin SDK only, no JSX, no
  build) adds card badges (`mom`, `olymp`, `held`, `for you`, `verified`, `verify failed`, at most two per card), the
  sidebar page "Calling mom" (the ladder with 7-day counts, **Needs you** with *Accept recommendation*, *Answer...*,
  *Allow once*, *Keep blocked*, recent activity) and a status-bar chip. Backend `dashboard/plugin_api.py` (gateway
  process): `GET /summary?days=7`, `GET /cards?board=`, `GET /needs-you`, `POST /answer` (`answer_card(by="desktop")`).
  The 100 % coverage gate counts the backend (`test_dashboard.py`).

- **Attention report.** `hermes calling-mom report [--days 7] [--board X] [--json]` shows, per rung, what happened
  and what it cost: Worker (tool calls, runs, stuck signals, API calls and tokens), Mom (calls, questions answered,
  pre-flight checks and holds, verify results, tips followed or rejected), Olymp (calls against the daily cap), You
  (briefs, answers, median time to answer, holds released) and Cost (Mom's dollars and tokens, Olymp's calls). When
  Mom reports a price, it adds what running every worker call on Mom would have cost. One function (`report.build`)
  is behind the CLI and `GET /summary`. New log row `worker_usage` (`tokens_in`, `tokens_out` per worker API
  request, no text); `preflight` rows that reached the model now carry `tokens_in`, `tokens_out`, `usd`.

- **Precedents: your answers become Mom's rules.** When you answer one of Mom's `[for you]` briefs (CLI, Desktop, or a
  card comment followed by an unblock), the plugin stores `{id, board, question, answer, task, ts}` as text in
  `logs/calling-mom-precedents.jsonl` (redacted, at most 500 characters each, local only). Mom's question prompt gets
  up to three entries with the most shared words (same board first on a tie) and cites one as "as decided on t_...".
  `hermes calling-mom precedents [list | forget <id> | clear]`; setting `answer.precedents` (default `true`, meaningful
  only where answers are on). Owner-only topics and `[local-only]` cards are never stored. This is the one prose file
  of the plugin; it is disclosed in README and SECURITY.md.

### Fixed
- No hook reaches Hermes' hook timeout any more (`plugins.hook_callback_timeout`, default 30 s). Before, a slow
  Mom (40 s), Mom plus Olymp (about 80 s) or a `verify:` command (120 s) inside `pre_tool_call` could time out; a
  timed-out `pre_tool_call` blocked every tool call of the worker for a minute. All waits in the bounded hooks now stay
  under 40 % of the configured timeout (`hook_budget()` in `_core.py`).
- A chat session no longer gets the calling-mom tip in its system prompt; only Kanban workers do.
- An untyped block on a card without assignee (a human to-do) is no longer escalated to Mom's model.
- The regex timing test measures CPU time, not wall time, so `check.py` passes on a busy machine.

### Changed
- **Advice is asynchronous.** The question to Mom (and the Olymp step) runs on a background thread, one per run. The
  tool result waits at most `min(8 s, 0.25 x budget)`; a later answer goes to the card and is appended to the next tool
  result as `[late advice]` (new log row `advice_late`). A late completion check is a card comment, it cannot block.
- **`verify:` is asynchronous.** The command runs on a background thread (own process group, killed on timeout).
  `kanban_complete` is blocked with `verify running` until the result is in; a result is valid only for the workspace
  state it ran on (git HEAD plus `git status` hash, otherwise no file edit since the start). Default `verify.timeout_s`
  is now 600 (was 120). The `verify` log row has a `state` (`started`, `pass`, `fail`, `timeout`, `stale`) and `ms`.

- **Pre-flight holds a history rewrite on a shared branch even when the card asks for it.** In three demo runs the
  reviewer answered GO to `git push --force[-with-lease] origin main` because the card named it, so the HOLD never
  fired. Its prompt now says that a force push to main, master or a shared branch is a HOLD whatever the task says
  (alternative: a normal commit or a branch push); a person can still release it (`--allow-once`). With this prompt
  all four demo triggers fired in two runs in a row. Other critical commands stay a model judgement.

### Compatibility
- `requires_hermes` stays `>=0.22`. The agent half (hooks, pre-flight, Mom answers, precedents, report, CLI) runs on
  0.22. Only the card badges need the Desktop app 0.22.6 (the badge slot `KANBAN_CARD_AREAS.badges` is not in
  0.22.5): `desktop/plugin.js` checks for the slot and an older app loads the page without badges. Raising the floor
  to 0.22.6 would lock out 0.22.x users of the agent half to protect one cosmetic feature.
- The release archive now ships `desktop/` and `dashboard/`; CI compiles every module and script and checks that the
  archive list names every imported module and plugin directory.

## [1.3.2] - 2026-11-03

Review fixes for the Hermes plugin catalog (NousResearch/hermes-agent#147276).

### Removed
- The optional ladder rung `ladder.effort_first` (retry once with more reasoning effort before the strong model). It
  needed a raw `UPDATE tasks SET block_recurrences = 0` on Hermes' Kanban table. The plugin no longer writes that table
  directly: a card that gave up is pinned to the strong model once, and the board's own recurrence breaker decides what
  happens after that. The metric `calling_mom_ladder_total` is gone with it. The setting was off by default; a leftover
  `calling_mom.ladder` key in `config.yaml` is ignored.

### Fixed
- Unbounded `.*` gaps in the default risk list made one long line very slow (about 10 s for 4 KB of `git push `).
  Every gap is now at most 256 characters, stays on one line and stops at the next copy of its own command word, so
  matching time grows linearly. At most the first 4000 characters of a command are classified. Ceiling: a risky
  command whose words are more than 256 characters apart, or that starts after character 4000, is not flagged.
  A timing test (12 KB, single line and multi-line, under 50 ms) guards this.

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
- **Easier setup.** `hermes calling-mom setup` now uses Hermes' own selection list (arrow keys, `/` filters by
  name) and falls back to the numbered list without a terminal. When the *Advanced Delegation* plugin has model
  routes (`delegation.backends.model_map`), `setup` offers them as the source for Mom and Olymp (values are copied
  once, nothing is linked). New flags run it without any question: `--mom`, `--olymp` (`provider/model`,
  `route:NAME`, `none`), `--boards`, `--cron/--no-cron`. Without a terminal and without the flags it exits with
  code 2 instead of waiting for input. The final "Write it? [y/N]" question is gone: you have just chosen the
  values and the summary is shown, the flags are the consent in script mode. The sweep cron job is no longer
  asked about either: `setup` says it is creating it (skip with `--no-cron`; never a second one). `--yes` is
  accepted and does nothing. The job is now named `calling-mom: sweep - escalate parked cards, push metrics`, so
  it is recognisable in `hermes cron list` (cron jobs have no owner field, the name is the link to the plugin).
  A job created by an earlier version under the name `calling-mom-sweep` is not recognised any more: rename it
  (`hermes cron edit <id> --name "calling-mom: sweep - escalate parked cards, push metrics"`) or remove it before
  running `setup`, otherwise `setup` creates a second one.
- Picking `*` (all boards) in `setup` now replaces single board names in the list instead of sitting next to them.
- **CI is faster where it matters.** The slow full suite against Hermes main now runs on `main`, by hand
  (`workflow_dispatch`) and on pull requests that are not drafts; a draft PR only runs the 10-second static
  checks, and a newer push cancels the run still going. `check.py` runs the test suite once (under the coverage
  tracer, which also reports failures) instead of twice.
- `setup` stops for good on ESC, Ctrl+C or end of input ("Cancelled. Nothing was written.", exit code 1); before,
  the menu handed back its default row and the questions went on. When the Advanced Delegation plugin is enabled
  but has no usable routes, `setup` says why nothing is offered and how to create routes.
- **One door to Hermes internals.** Hermes offers plugins no public Kanban API yet (pin a card to a model, unblock
  from a hook, read boards and events, egress redaction), so those private imports now live in a single file,
  `_core.py`, and a test fails if any other plugin file imports `hermes_cli`, `agent`, `tools` or `gateway`.
  If a Hermes update removes something, the plugin still loads, registers no hooks, logs the missing module once
  and `hermes calling-mom doctor` says so, instead of failing at import time.

## [1.3.0] - 2026-10-30

### Added
- **Two tiers: mom and olymp**. `mom.{model,provider}` is the cheap tier (the old top-level pair still
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
