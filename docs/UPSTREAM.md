# Requests to Hermes

Eight things calling-mom works around because Hermes has no public seam for them. Each section says what is missing,
what calling-mom does today, which file and function would use the seam, and what the seam would remove. They are drafts
for an upstream issue each; Timon files them. Every private import stays in `_core.py` (`test_core.OneDoorTest`), so a
seam replaces code in one place.

## 1. A dispatcher veto, or a per-profile / per-provider concurrency limit

**Missing.** The dispatcher starts every ready card. A user who wants one card at a time (a Claude subscription window
holds one worker, not four) can only chain the cards with parent links by hand.

**Today.** `hermes calling-mom pace --board B --max N` plans the chains and writes the parent links through
`kanban_db.link_tasks`; `--undo` removes exactly those links.

**Would use it.** `__init__.py` `pace_board` and `quota.py` `plan` (and `cli.py` `cmd_pace`).

**Removes.** The whole `pace` command and the links it leaves on the board. A limit in `config.yaml`
(for example `kanban.max_running_per_profile` or `per_provider`) does the same without touching the cards. A veto hook
(`on_kanban_before_claim`, return `"defer"`) would let calling-mom hold cards while a provider is `limited`.

## 2. A public read API for Kanban events and comments

**Missing.** A plugin cannot list the comments, events, links or heartbeats of a board without importing
`hermes_cli.kanban_db` and `kanban_db_connect`, which can change with any release.

**Today.** `_core.py` wraps the reads: `board_activity` (comments and events of every card), `board_cards` (cards and
parent links), `running_cards` (heartbeat of running cards), `last_event_id`. `doctor` has a `board read` check that fails by
name when Hermes changed one of them.

**Would use it.** `__init__.py` `_record_touches` (outside help) and `_record_stalls` (stalled cards),
`dashboard/plugin_api.py` `_board` and `_stalled`, `_core.py` `check_board_read`.

**Removes.** The private imports in `_core.py` and the `board read` doctor check. A read-only
`ctx.kanban.list_tasks / list_comments / list_events / list_links` with a stable shape would be enough. The store records
no actor for board events (link, unlink, reclaim, assign, ...); an `actor` field would turn "unattributed" into a name in
`report.py` `_outside`.

## 3. A plugin can offer an answer to a human-input request, and the person can still override it

**Missing.** `on_human_input_request` is an observer. A plugin sees that a chat session waits for the person, but it cannot
offer an answer that the person confirms or replaces.

**Today.** `__init__.py` `on_human_input_request` and `on_human_input_resolved` only time the wait (kind, seconds,
outcome) when `calling_mom.count.chat_waits` is on. Mom answers worker questions on Kanban cards (`_answer_question`), not in
chat.

**Would use it.** A new hook return value, for example `{"suggest": "...", "auto_after_s": 60}`, read by
`on_human_input_request`; the answer would come from `_answer_question`'s logic, with the same risk rules
(`_question_risk`).

**Removes.** The gap between the ladder on cards (Worker, Mom, Olymp, you) and ordinary chat sessions, which have no ladder
in 1.5.

## 4. Read access to subscription usage windows

**Missing.** A Claude subscription limits by a time window (the 5-hour session). Hermes shows the gauge in its own UI
(`agent/usage_gauges.py`), but a plugin cannot read it. The only signal is the error text
`You've hit your session limit · resets 1:50pm (UTC)`.

**Today.** `quota.py` `classify` and `parse_reset` read that text (UTC only, bounded pattern, nothing stored), then
`__init__.py` `_set_health` keeps `limited until HH:MM`. `report.py` `_quota` can only count limits and cards paused, and the
cost line gives tokens.

**Would use it.** `__init__.py` `_set_health` and `_quota_notice` (before the limit: share of the window used),
`report.py` `_quota` and `render` (share of the window instead of tokens), `__init__.py` `_guarded` (skip Mom before
the wall, not after the first failure).

**Removes.** The text parsing, and the first failed call of every window.

## 5. Release the claim of a running card when its dispatcher host is gone

**Missing.** A deploy that recreates the gateway container kills the dispatcher and its workers, but the cards stay
`running` with a claim. On 2026-11-06 a deploy at 11:03 UTC left card t_6159c155 `running` until the orchestrator reclaimed it at
13:06 UTC. Its `max_runtime` (3 h) would have released it at 13:58. Nothing on the board said so.

**Today.** `__init__.py` `_record_stalls` writes one `[stalled]` comment per run and a `stall` row when a running card has no
heartbeat for `calling_mom.stall_minutes` (default 15); the Desktop half shows a `stalled` badge and lists the card in Needs
you (`dashboard/plugin_api.py` `_stalled`). calling-mom never reclaims by itself: a person runs
`hermes kanban --board B reclaim <id> --reason stalled`.

**Would use it.** `__init__.py` `_record_stalls`: with the seam the plugin would only report the cases Hermes could not
settle.

**Removes.** Most `[stalled]` comments. On start, a dispatcher would release the claims whose `claim_lock` names a host or
process that is gone (`on_kanban_worker_stale_claim` exists as an observer; the missing part is the release itself).

## 6. A public read of a session's last message

**Missing.** A plugin cannot ask "what did this session last say?" without opening Hermes' session store. The REST route
`GET /api/sessions/{id}/messages` needs the user's login, which a plugin's backend route does not hold, and the SDK offers
session rows with a preview of the first user message only.

**Today.** `_core.py` `last_assistant_message` opens `hermes_state.SessionDB` read-only for the worker profile, follows the
compression split with `resolve_resume_session_id` and returns the newest assistant message with text. The Desktop page
shows it on every Active card (`dashboard/plugin_api.py` `_last_chat`). It is a private import and a read outside the
plugin's own data, so it is disclosed in `SECURITY.md` (catalog rule 13).

**Would use it.** `_core.py` `last_assistant_message` only.

**Removes.** The `hermes_state` import and the `profile_home` helper. A `ctx.sessions.last_message(session_id, profile=None,
role="assistant")` returning `{text, at}` or `None`, with the same profile scoping as `host.requestProfile`, would be enough.

## 7. A public way to send a message, and to receive the reply

**Missing.** `send_message` is deliberately not a registered tool, so `ctx.dispatch_tool("send_message", ...)` cannot deliver a
message to the user's phone. A plugin that wants to say "this card needs you" on Telegram, Signal or Discord has no public door.
The reply is the second gap: nothing connects "the person answered a plugin's message" to the plugin; today the chat agent has to
choose to call a plugin tool.

**Today.** `_core.py` `send_message` runs the public `hermes send` CLI as a subprocess (`hermes send --to <target> --json <text>`,
`SEND_TIMEOUT_S` = 90 s, the JSON result parsed from the output). No Hermes module is imported for it any more. The reply goes through
the plugin tool `calling_mom_answer`, which the chat agent calls when the person answers the mirrored message; the tool answers only in
the session the notice went to (the notify target's platform, and its chat when the target names one). The text that leaves is redacted
first.

**Would use it.** `__init__.py` `_send_notice` and `notify_waiting` (send), `handle_answer` (reply).

**Removes.** The CLI subprocess (one Hermes start per notice). A `ctx.send_message(target, text, *, reply_to=None) -> {ok, id}` with the
same target syntax as `hermes send` would be enough, and `ctx.on_reply(message_id, handler)` (called with the person's text when they
reply to that message, authorised by the platform's own allow-list) would remove the dependency on the chat agent choosing the tool.

## 8. A public way for a dashboard route to call a tool, and to open a Kanban card

**Missing.** `ctx.dispatch_tool(...)` exists only on the `PluginContext` that `register()` receives; a dashboard route
(`dashboard/plugin_api.py`) is loaded by path in the dashboard process and gets no `ctx`. The Desktop SDK has no verb that opens a
card's detail drawer (`host.openKanbanCard(board, id)`), so after creating a card the page can only `host.navigate('/kanban')`.

**Today.** `_core.py` `dispatch_tool` imports `tools.registry.registry` (the registry `ctx.dispatch_tool` calls) and runs the public
`kanban_create` tool; the **New card** form shows the id and a plain **Open in Kanban** button. `desktop/plugin.js` `openCard` no
longer reads the app's markup (it used to poll `span.font-mono` and click the tile): it navigates to the board and names the card id,
since the SDK has no verb for the drawer. Private import, listed in `SECURITY.md`.

**Would use it.** `dashboard/plugin_api.py` `create_card` (tool call), `desktop/plugin.js` `NewCardForm` (open the new card).

**Removes.** The `tools.registry` import: a `ctx` argument for plugin routes (`def create_card(body, ctx=Depends(plugin_ctx))`) would be enough.
