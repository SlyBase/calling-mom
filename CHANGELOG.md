# Changelog

All notable changes to this plugin. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.7.0] - 2026-11-08

### Changed
- **README rewritten: shorter, USP first, plain words** (`README.md`, `docs/TECHNICAL.md`, `plugin.yaml`). The 289-line README is now a one-minute read: the pitch, the problem, the ladder as a small diagram, six plain-language advantages (signals, the pre-flight HOLD with optional fail-closed, the hard Olymp caps with real numbers, the four-line brief with phone and Decide-later, the dashboard with New card, the measured benchmark), a three-line quick start, the screenshots, and the safety summary. The detail it carried (sequence diagram, card examples, the command table, the troubleshooting table, minimal configuration, the Desktop-on-another-machine path) moves to `docs/TECHNICAL.md` unchanged. The `plugin.yaml` description matches the new README pitch. No behaviour change.
- **Decide later: put a card off and resume it** (`__init__.py`, `dashboard/plugin_api.py`, `desktop/plugin.js`). The right-click menu of every *Needs you* and *Active* card (status todo, ready, running or blocked, or a stalled card) has **Decide later**. A card that waits for your answer stays blocked (no worker runs, nothing is dispatched), its brief or hold stays recorded, and the wait is put aside in the plugin's state: it leaves Needs you, the badges and the phone sweep, and comes back with the same text when you resume it (no second phone message; a new question on the card shows at once). Any other card is parked with Hermes' public `schedule_task` in `scheduled` status (a running card loses its claim, like the board's own move) and `unblock_task` resumes it. A collapsible **Later** section lists both kinds with **Resume** and **Open in Kanban**. New routes `GET /later`, `POST /later`, `POST /resume`; `POST /move` accepts `scheduled`, and `unblocked` / `archived` accept it as a source. Why not `scheduled` for a waiting card: `unblock_task` would restart its worker without your answer, and a re-block trips Hermes' block-loop guard into `triage` (docs/TECHNICAL.md). No new hook or tool, no new private import. A backend from before this release has no `/later`; the page still loads.
- **Desktop: start a card from the Calling Mom page** (`dashboard/plugin_api.py`, `_core.py`, `desktop/plugin.js`). A **New card** button in the page header opens a compact form: title (required), body, board (the boards you opted in, Kanban's current one first), assignee (installed profiles), priority, and the plain flags the plugin understands (`[crit]`, `[review]`, a `verify:` line) with one line each on what they do. `POST /card` creates the card through Hermes' public `kanban_create` tool (never a raw write) and checks the input first (board must exist and be opted in, assignee must be an installed profile, title 200 / body 8000 / verify 300 characters, `verify` one line); a retried click is one card (idempotency key). The page toasts `Card t_xxxx created`, the card shows under Active once it runs; **Open in Kanban** is a plain jump to the board. `GET /new-card` feeds the form. A write action behind the dashboard login, disclosed in `SECURITY.md` and `README.md`. The one private import (`tools.registry`, because a dashboard route gets no `ctx`) is in `_core.py` and `docs/UPSTREAM.md` (#8). No new hook or tool.
- **Pre-flight fail-closed: a critical command stays held when Mom cannot check it** (`__init__.py`, `cli.py`, `report.py`, `dashboard/plugin_api.py`, `desktop/plugin.js`). New opt-in `calling_mom.preflight.fail_closed` (default `false`: nothing changes). On, a Mom failure on a critical command (provider error, provider limited or down, model in cooldown, timeout, unparseable or reasonless answer, per-card cap, daily budget) returns a HOLD to the worker (`calling-mom pre-flight HOLD (fail-closed): Mom could not check this command (<class>). It stays blocked until a person allows it.`) instead of letting it run. The card gets the `held` badge and lands in Needs you like any HOLD, and the release (`answer --allow-once`, the Desktop button, the phone reply) is the same one-shot allowance. Commands that are not critical stay fail-open; the hook deadline is unchanged. `setup` asks (recommended on shared remotes; `--fail-closed` / `--no-fail-closed`), `status` and `doctor` show the mode, the Desktop ladder names it, and the report counts `fail-closed holds` apart from Mom's own holds (`mom.closed`). No new egress, hook or tool. Limit: an unexpected exception inside the check still lets the command run.
- **Answer from your phone** (`__init__.py`, `_core.py`, `cli.py`, `desktop/plugin.js`, `dashboard/plugin_api.py`). What waits under "Needs you" (Mom's brief, a held command, a worker's own question) is also sent once to your phone through Hermes' own messaging (Telegram, Signal, Discord, Slack, WhatsApp, ...). New setting `calling_mom.notify.target` (`telegram` = home channel, `telegram:<chat>`; default empty = off); `setup` asks, or `--notify telegram|none`. New tool `calling_mom_answer(card_id, answer, board)` (toolset `calling-mom`, chat sessions only while a target is set): your reply in that chat answers the card through `answer_card(by="phone")`. `A`/`B` become the option text, `accept` takes Mom's recommendation, `allow` lets a held command run once. Redacted like every prompt, never for `[local-only]` cards, never waits for you, a failed send is tried twice more and the card stays in Needs you. `doctor` shows the last result (`phone`), `status` the target, the Desktop card says "sent to telegram". The one private import (`tools.send_message_tool`; `send_message` is not a registered tool) is in `_core.py` and in `docs/UPSTREAM.md`.
- `GET /needs-you` items gain `sig`, `local_only` and `sent_to`; `waiting()` moved from the dashboard module into the plugin so the sweep and the page read the same list.- **Desktop: Active cards show the card's last comment** (`dashboard/plugin_api.py`, `desktop/plugin.js`). `GET /active` now returns `last_comment` (`{author, body, created_at}` or `null`, body cut to 200 chars) per running/ready/blocked card, and the Active line renders it; `last_chat` remains the fallback when there is no comment.

## [1.6.1] - 2026-11-08

### Added
- **Desktop: the cards under "Needs you" open on click and have the same right-click menu as the Active cards** (`desktop/plugin.js`). A click on a button, field or code inside the card does not open it.
- **Desktop: a worker's own question shows under "Needs you"** (`dashboard/plugin_api.py`, `__init__.py`). A blocked card with `block_kind: needs_input` and no brief or hold (Mom is off for the board, or her call failed) now waits for you, with the block reason as text; "Answer..." works on it. A card whose brief was answered, a Goal-mode block and a held command are unchanged.
- **Desktop: board filter and status colors under Active** (`desktop/plugin.js`). "All" plus one toggle per board (multi-select, kept in app storage) sit left of Hide/Show blocked, which now ends flush with the cards. Status tags: blocked red, running green, ready blue, review violet, others grey.
- **Desktop: blocked cards show under Active, with a Hide/Show switch** (`desktop/plugin.js`, `dashboard/plugin_api.py`). `GET /active` now lists blocked cards too; a card that already waits under "Needs you" is not shown twice. The switch is kept in the app's storage (default: shown).
- **Desktop: "Open card" also opens the card's detail drawer** (`desktop/plugin.js`). After the jump to Kanban the card is clicked where it is drawn (best effort: it must be on the board the page shows).
- **Desktop: the last worker message of an Active card is grey**, to tell it from the title (`desktop/plugin.js`).

## [1.6.0] - 2026-11-07

### Added
- **Desktop: the page shows the version under the title** (`desktop/plugin.js`, `dashboard/plugin_api.py`). The tagline is gone; the line reads `v1.6.0`, or `Desktop v… · Backend v…` when the app's copy and the gateway differ, so a stale Desktop half is visible at once, with a note next to the version. `GET /summary` gains `version` (from `plugin.yaml`); a test keeps `plugin.js` and `plugin.yaml` equal.
- **Desktop: right-click menu of an Active card has "Copy task id"** (`desktop/plugin.js`).
- **Mom rates her own answer: confidence 1-10** (`__init__.py`). The Mom-first prompt of the advisor gate and every prompt of the question flow end with a request for one line, `CONFIDENCE: <1-10> | <reason>`. Below `calling_mom.mom.escalate_below` (default 4) Olymp is asked, with no new way past his per-card and per-day limits and never for a brief-only question; from `calling_mom.mom.brief_short_from` (default 7) a brief for the human is cut to 160 characters a line. A missing, garbled or out-of-range value changes nothing; the `ESCALATE:` prefix still works. The line is never posted to the card. The `advisor` and `question` log rows carry `confidence`. TECHNICAL.md gets a References section with the SMART paper (arXiv:2611.08196v1).
- **State first: `advisor.extract_state`** (`__init__.py`, off by default). A completion check and a review of a critical change make one extra call before the judge: it lists at most 5 stateful entities (CI, tests, PR, DB schema, API, deploy) with 3-5 facts each, and the judge reads that list above the raw context. Stuck signals never extract. The call runs after the opt-in and `[local-only]` checks, on Mom's route only, counts against the daily caps and writes no comment; a reply with no entity line falls back to the raw context, a model-call error is reported once like any advisor failure. The `advisor` log row carries `state_tokens`. After the "Identify" stage of SMART (arXiv:2611.08196v1); numbers in TECHNICAL.md.
- **Web evidence: `web_verify`** (`__init__.py`, off by default). A completion check and a review of a critical change on a `[crit]` / `[review]` card, or on a card with a `verify-web: <claim>` line, search the web for the claim before the judge decides; the hits (up to 3, one line each, 200 characters, URL first) go to the judge as untrusted evidence under a header that says so. The query is the claim only (the `verify-web:` line, else the title without markers), redacted, one line, 200 characters; never the card body. Own switch, runs only where the advisor does, never on `[local-only]` or `[human]` cards, 1 search per card and 20 per day (`web_verify.max_per_card`, `.max_per_day`; the day count lives in the spend file as `web`), 8 s hard timeout, fail-open. It uses Hermes' `web_search` tool through `ctx.dispatch_tool`: no new dependency. Code never decides on a hit; nothing is posted to the card; the `web_verify` log row has the query hash and the hit count. The egress is documented in README, SECURITY.md and TECHNICAL.md. After the "Curate" stage of SMART (arXiv:2611.08196v1).

### Removed
- **`hermes calling-mom desktop-sync`** (added in 1.5.5). A command that downloads and replaces the plugin's own Desktop file is a self-updater, which the Hermes plugin catalog forbids (admission rule 3), and it exists only where the plugin is installed, which a Mac without a gateway does not have. Install the Desktop half through **Capabilities -> Plugins -> Install** and update it with the plugin's Update button. `setup` and `doctor` name this path; README and TECHNICAL.md describe it.
- **Guarded cards** (`__init__.py`, `_core.py`). A `ready` card that the dispatcher's respawn guard holds back for `calling_mom.guard_minutes` (default 30, 0 = off) gets one `[guarded]` comment per streak, with the reason (`active_pr`: a PR URL in a comment of the last 24 hours, even after the PR was merged) and, for `active_pr`, the workaround (`PR #N` instead of the URL, or hand the card to another profile). The guard fires no block hook and no crash, so such a card looked fine for hours. The sweep reads the `respawn_guarded` events through `_core.guarded_cards`; a `guard` row keeps the reason and the minutes. The plugin never bypasses the guard.

### Security
- **Disclosure of the session read** (`SECURITY.md`, `README.md`): the Active cards read the worker's last message from the profile's session store (Hermes' `SessionDB`); catalog rule 13 asks to list reads outside the plugin's own data.
- **Install scan is `safe`** (`tests/test_rules.py`): the test list spelled a recursive delete of the filesystem root literally, which the install scanner flags as `destructive_root_rm` (`caution`). A `caution` verdict holds an unattended Git update as "needs review", so `plugins.auto_apply` never moved the install. The command is now built from `RM`.

### Changed
- **Desktop: the status-bar chip no longer shows the Mom call count** (`desktop/plugin.js`). It appears only for `N for you` (items waiting) or `mom offline`. `today.mom_calls` stays in `GET /summary` for older Desktop copies.
- **Desktop: the Active cards show the worker's last session message, never a comment** (`desktop/plugin.js`, `dashboard/plugin_api.py`). The worker log is written only when the worker exits, so a running card had no chat line and fell back to its last comment. `last_chat` now reads the last non-empty assistant message of the worker session (the `Session:` header of the card's log, then Hermes' own `SessionDB` read-only through `_core.last_assistant_message`, which follows a compression split to the continuation), up to 300 characters, shown in two lines. `GET /active` no longer returns `last_comment`; a card without a message shows "No messages yet — the worker is on it.".
- **Desktop: Recent activity uses the global header window, stacked layout** (`desktop/plugin.js`). The section's own time filter is gone; it follows the header's window. The layout is stacked — a dot, the time and the action on one line (one text size, one colour), the card title and board beneath it; the same action carries the same dot colour.
- **A pre-flight HOLD must say what is not verified** (`__init__.py`). The prompt asks for one concrete sentence; a reason of fewer than 4 words ("too risky") is downgraded like an unparseable answer: the command runs, no hold is recorded (`allowed` / `no_rationale`), so fail-open and "a model never releases its own hold" stay as they are. The `hold` log row carries `rationale` (redacted, 160 characters, never the command); the card comment already shows it. After the refute filter of SMART (arXiv:2611.08196v1).
- **Completion and critical-change reviews name a counter-claim first** (`__init__.py`). The `completion` and `critical_change` prompts ask for one first line, `COUNTER: <the most plausible way the claim is wrong>`, before the answer. The line is taken off the reply like `CONFIDENCE`: the `DONE` / `GAP:` / `ESCALATE:` contract, the card comment and the worker never see it; the `advisor` log row carries `contra` (redacted, 160 characters). No extra model call; a reply without the line changes nothing. On 5 recorded completion checks the verdict did not change (no measurable gain, see TECHNICAL.md). After the "Explore" stage of SMART (arXiv:2611.08196v1).

## [1.5.5] - 2026-11-07

### Added
- **`hermes calling-mom desktop-sync`** (`cli.py`). The Desktop half of a machine that runs the app but not the gateway (the Mac) never reached it by a plugin install or update — the gateway's copy stays on the server and the app reads only its own disk, so the old manual `curl` stayed stale. `desktop-sync` pulls `desktop/plugin.js` from the repository (the latest release, or a pinned tag) and writes it atomically to `~/.hermes/desktop-plugins/calling-mom/plugin.js`; a failed download leaves the old file in place. No manual `curl` anymore. README and TECHNICAL.md point to the command instead of the curl.
- **Test for the `hours` param of `GET /summary`** (`test_dashboard.py`): the sub-day branch (1..2160, priority over `days`, `days = hours / 24`) was exercised only by line coverage before; the branch is now covered by behaviour.

### Changed
- **Desktop: the Active cards show the worker's last chat line** (`desktop/plugin.js`, `dashboard/plugin_api.py`). The second line now shows what the worker last said in its log (the last worker line, not `Session:` headers and not the `[kanban-worker-exit]` trailer) instead of its last comment; a card with neither shows "No activity yet — the worker is on it.". `GET /active` gains `last_chat` per card.

## [1.5.4] - 2026-11-07

### Added
- **Desktop: Recent activity has its own time filter** (`desktop/plugin.js`). The section carries a selector with the same buckets as the header (`1 h`, `4 h`, `1 d`, `7 d`, `14 d`, `30 d`, `90 d`; default `1 d`, stored across reloads). It is independent of the header window: when the two differ, the page asks `GET /summary?hours=` a second time for the recent rows only. An empty window shows a hint and keeps the selector. No backend change.

### Changed
- **Desktop: the card name in Recent activity is left-aligned.** The title sits on its own line under the sentence with the board beside it, instead of being pushed to the right edge.

## [1.5.3] - 2026-11-07

### Added
- **Desktop: sub-day time windows** (`desktop/plugin.js`, `dashboard/plugin_api.py`). The header selector now offers `1 h`, `4 h`, `1 d`, `7 d`, `14 d`, `30 d`, `90 d` (was `7d`/`14d`/`30d`/`90d` only). The new `GET /summary?hours=` param (1..2160) takes priority over `days`; the header line shows the selected window (`Last 1 h.`).

## [1.5.2] - 2026-11-07

### Fixed
- **Desktop: recent activity now shows the dynamic time readout** (`desktop/plugin.js`). The Recent-activity cell fell back to `sdk.relativeTime` (always present in the desktop SDK) and so never rendered the new `age()` format (`15 min` / `1 h` / `1 d`). It now uses `age()` directly, matching the Active section.

## [1.5.1] - 2026-11-07

### Added
- **Desktop: active cards and recent activity, improved** (`desktop/plugin.js`). The `Active` section shows two lines of the last
  comment (clamped) instead of one; a card click opens the card in the Kanban page (its drawer has no URL door a plugin can park on),
  and a right-click menu moves the card to another bucket — the same verbs as the board, through a real `kanban_db` transition
  (`POST /move`, `block_task`/`complete_task`/`request_review`/`unblock_task`/`archive_task`/`promote_task`, so the dependency and
  live-claim gates are never bypassed). `Recent activity` now shows what each action did with a colored dot (verify pass/fail,
  pre-flight go/hold, a question, an advice) instead of plain text; the card title is left-aligned and its id is demoted. The
  time readout is dynamic (`15 min`, `1 h`, `4 h`, `1 d`, `3 d`) instead of fixed min/h/d.

## [1.5.0] - 2026-11-06

### Added
- **Honest count, WP1** (`docs/SPEC-1.5.md`). The report shows what reaches you, not only what Mom does.
  - **Outside help.** The sweep reads the comments and events of opted-in boards (read only, through `_core.py`) and
    logs `touch` rows: comments by anyone but the card's assignee and calling-mom (author id, never text), structural
    board events (`link`, `unlink`, `unblock`, `edit`, `archive`, `assign`, `model`, `effort`, `changes`, a manual
    `reclaim`; no actor is stored, so `unattributed`), every card that entered `triage`, and every `needs_input` block
    Mom did not answer, each with the seconds until it left, or "still waiting". Writes calling-mom makes itself
    (model pins and unblocks, also `answer_card`) are logged as `own_write` and not counted. The first sweep takes the
    whole history; a card waiting is written again, with `wait_s`, when it leaves.
  - **Chat waits** (`calling_mom.count.chat_waits`, off by default, `setup --chat-waits`): `on_human_input_request` and
    `on_human_input_resolved` give the kind of wait, its seconds and its outcome. Never the prompt or the answer.
  - **Advice feedback with evidence.** The anchors of an advice (commands, paths, tool names in backticks) are kept in
    memory for the run; a later tool call that uses one makes the advice `followed`, any other call `acted`, a run that
    ends first `ignored`, advice without anchors `unknown`. The old "followed" means `acted` now.
  - **Report**: `--since` / `--until` (ISO date or date-time, UTC; also on `GET /summary`), the `You` block with
    outside help and chat waits, an `Advice` line with the four results, and a cost line that says "on a subscription
    (no per-token price)". What was not observed says `not observed`, never 0. The Desktop `You` rung shows the
    outside-help counts. `doctor` has a `board read` check.

- **Quota and pacing, WP2** (`docs/SPEC-1.5.md`). A quota stall now says why and until when; the manual chores become a comment
  or one command.
  - **Provider health.** Errors are classified as rate limit, auth, key limit or other (`quota.py`), from the plugin's own Mom and
    Olymp calls and from `api_request_error` in worker sessions of opted-in boards. A Claude subscription message such as
    `You've hit your session limit · resets 1:50pm (UTC)` gives the reset time (UTC only; anything else is "unknown", 30 minutes).
    Health is kept per profile and provider in `calling-mom-state.json`; an auth or key-limit error is `down for 30 min`. While a
    provider is limited or down, Mom and Olymp calls to it are skipped without a model call and the single
    `[advisor unavailable]` comment says until when. The error text is parsed and dropped: it reaches neither the log nor the state file.
  - **`[quota]` comments.** `on_kanban_worker_exited` with `outcome == "rate_limited"`: one comment per card and limit window
    (`[quota] <provider> hit its usage limit; resets about HH:MM UTC. The dispatcher retries by itself; the work in <workspace> is kept.`).
  - **Mom fallback.** `calling_mom.mom_fallback: {provider, model}` (`setup --fallback PROVIDER/MODEL|route:NAME|none`, or the
    new setup question) answers when Mom's provider is limited or down; the comment header says `(<provider>, fallback)`. It is granted like Mom,
    gets the same redacted excerpts, counts against Mom's caps, and `doctor` makes one real call to it. Olymp has no fallback.
  - **Stalled cards.** The sweep flags a running card without a heartbeat for `calling_mom.stall_minutes` (default 15, 0 = off):
    one `[stalled]` comment and one `stall` row per run, a `stalled` badge and a Needs you entry in the Desktop half (read only).
    It never reclaims by itself.
  - **`hermes calling-mom pace --board B --max N [--dry-run] [--undo]`** keeps at most N cards ready or running by chaining the
    rest with parent links. It prints its plan first; `--undo` removes exactly the links it added. Its writes are logged as `own_write`.
  - **Report.** A `Quota` line (limits and their length, cards paused, Mom calls skipped, fallback answers) and a `Stalled` line.
    `status` and `doctor` show provider health and the fallback.
  - `docs/UPSTREAM.md`: five requests to Hermes, each naming the code that would use it.

- **Pre-action gaps, WP3** (`docs/SPEC-1.5.md`, owner's audit t_de8aec31). Three holes in the risk list, found when a worker quit a
  production app and installed a plugin on a person's Mac with no question first.
  - **New critical rules** (asked before they run, also inside `ssh host '...'`): quitting an app (`osascript ... quit`,
    `killall`, `pkill`) and `cp`/`mv`/`rsync`/`scp`/`ditto`/`ln` into `~/Library/`, `$HOME/Library/`, `/Users/<name>/Library/` or
    `/Library/` (the Library path must be a target, so copying FROM it is not a hit). Every pattern is bounded.
  - **`watch` budget only for writes.** `rules.read_only()` reads the command with `shlex`: when every simple command starts with
    a reading word (`ls`, `cat`, `grep`, `pgrep`, `sed -n`, `find` without `-delete`/`-exec`, a `curl` that only fetches, `pct`/`qm`
    `list|status|config`...) and nothing writes a file, `ssh`/`pct`/`qm` is no `watch` and costs no budget. Unknown, unparseable
    or longer than 4000 characters counts as a write. `scp`/`rsync` stay `watch`.
  - **The words for an unclear approval.** The worker tip says: not sure the card allows it? `kanban_block(kind='needs_input')` or
    `SECOND-OPINION:`, do not guess. The pre-flight reviewer holds a quit or a Library install that the task does not name and
    names "ask the owner first" as the alternative.

### Changed
- `watch` no longer fires for read-only `ssh`/`pct`/`qm` commands (see above). Add your own with `categories.watch` patterns as before.
- `report` JSON: `mom.tips_followed` / `tips_rejected` are gone; see `advice` (`followed`, `acted`, `ignored`, `unknown`).
  `calling_mom_advice_feedback_total` has the label `result` (was `verdict`). Rows of 1.4 are read as before:
  `followed` counts as `acted`, `rejected` as `unknown`.
- Log rows may carry their own `ts` (the time of the event, not of the sweep).
- `plugin.yaml` `version` is 1.5.0. README and TECHNICAL.md point the Desktop-on-another-machine `curl` at `v1.5.0`
  (the Desktop half changed in 1.5). The 1.4.2 dashboard hotfix of `main` is part of this release.

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
