# Calling Mom 1.4.2: the Dashboard shows the ladder while it is climbing

Status: accepted, for the Hermes Agent Plugin Hackathon (submissions close 2026-11-11, 17:00 EST).
Branching: this work package branches from `main` (which carries 1.4.1) and opens a PR into `main`.
`main` stays the installable hackathon entry (1.4.x) until 12 Nov. 1.5 is not touched.

## 1. Why

1.4 and 1.4.1 give the page a ladder of counts and a "Needs you" list. The gap: the page shows
aggregated numbers and what is waiting, but not what is happening *right now*. A person glancing at the
page wants "what is running, and what did the worker say last?" without opening every card.

1.4.2 closes that gap in the Desktop half only: the page shows active cards with their last comment,
and the existing lists are made more readable and more flexible. No agent-half behaviour changes.
This is the UI the final demo video is recorded from, so it must be stable and on `main` before the
freeze (Monday 2026-11-09).

The guiding idea is the same as 1.4: "the ladder ends with you." The page is the one screen where a
person sees the whole ladder — what is climbing, what is stuck, and what is waiting — without a
second tab.

## 2. Principles (inherited from SPEC-1.4)

- Backend stays read-only over the boards; the only write is `POST /answer`.
- `desktop/plugin.js` talks to `dashboard/plugin_api.py` only, never to app internals or DOM.
- No new record kinds in `logs/calling-mom.jsonl`. We read what is already there (comments, log rows).
- 100% coverage gate on `dashboard/plugin_api.py`; the repo tests are in English.
- Deterministic: on a fixture board, the same output every run.

## 3. Work package (single, this is a hotfix)

### 3.1 Active cards with their last chat entry  *(the core)*

New route `GET /active` in `dashboard/plugin_api.py`:

- For each opted-in board (`_opted_in`), read `kb.list_tasks(conn)` and keep only cards whose `status`
  is `running` or `ready`.
- For each, take `kb.list_comments(conn, task.id)` and the last one. Emit:
  `{board, task_id, title, status, last_comment: {author, body, created_at} | null}`.
- `last_comment` is `null` when the card has no comments (a freshly claimed card).
- Order: by `created_at` of the card's last activity (newest first).
- `body` is truncated to 200 characters (the page is a glance, not a transcript).

**Known assumption (validate before the PR):** the last comment is a technical worker note in some cases,
not a clean status. Take the last comment as-is; if it starts with a known prefix (`[mom answered]`,
`[for you]`, `[pre-flight] HOLD:`, `[you via calling-mom]`), keep it — it is still the last signal. Do not
invent a cleaner "status" field. This is acceptable for the demo and is honest.

### 3.2 Card name + board prominent, ID technical

The Recent-activity list (and the Active list) shows `task_id` as the identity today. Change to:
card **title** + **board** as the visible line, and `task_id` as a small, muted, mono suffix (technical,
not the eye-catcher). The log rows carry only `task_id` + `board`, so build a `task_id -> title` map
from the boards (all slugs, one `_board` pass) and join it. A card that is not found (archived, deleted)
falls back to `task_id`.

### 3.3 Collapsible sections

The page sections (Needs you, Active, Recent activity) collapse on header click, with a `useState`
per section and a chevron indicator. Defaults: Needs you open, Active open, Recent activity collapsed.
This is pure `desktop/plugin.js` (the `Section` component gains a toggle). No backend change.

### 3.4 Time-window selector

The backend `GET /summary` already accepts `days` (1–90). The frontend hardcodes `?days=7`. Add a small
selector (7, 14, 30, 90) in the page header that drives the poll (`/summary?days=<n>`), and change the
subline "Last 7 days" to reflect the selected window. The choice is kept in `ctx.storage` (like `kept`)
so it survives reloads.

## 4. Not in 1.4.2 (and why)

- **Background graphics / "top cards" ranking** — explicitly deferred by Timon. No new sort, no art.
- **Any agent-half behaviour change** — 1.4.2 is Desktop-only; `main`'s agent behaviour is frozen for the hackathon.
- **A cleaner "status" field** — not invented; the last comment is the last signal, as-is.
- **1.5 work** — this is not on `release/1.5.0`.

## 5. Acceptance

- `python scripts/validate_plugin_catalog.py` and `check.py` `gate: PASS` (100% coverage includes `dashboard/plugin_api.py`).
- `GET /active` returns, on a fixture board, the running/ready cards with the last comment, ordered newest first; `null` last_comment on a comment-free card.
- The page shows title + board (not the ID) as the identity in Recent activity and Active.
- Sections collapse/expand on header click with the stated defaults.
- The time-window selector drives the poll and the subline text; the choice persists across reloads.
- `hermes plugins validate --install-deps .` passes on a fresh clone at the merge commit.
- A manual check in Hermes Desktop against the local gateway (the page, the Active list, the selector, the collapse).
- No private Hermes import outside `_core.py`, no raw SQL in Kanban tables, repo text in English.

## 6. Schedule

- Freeze Monday 2026-11-09; video recorded from this UI on Tuesday 2026-11-10.
- Timebox two hours. The card lands on `main` before the freeze.
