// Calling Mom, the Desktop half: card badges, the "Calling Mom" page, a status-bar chip.
// Plain ESM, no JSX, no build step. It talks to dashboard/plugin_api.py only (ctx.rest), never to app internals.
import * as sdk from '@hermes/plugin-sdk'
import { createElement as h, useEffect, useState, useSyncExternalStore } from 'react'

const { host, Button, Badge, Codicon, EmptyState, Textarea, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS } = sdk

const VERSION = '1.6.1' // must equal plugin.yaml (a test checks it): the page shows which copy runs
const PATH = '/calling-mom'
const POLL_MS = 15000

// ---- state: one store, one poller, shared by the badges, the page and the chip ---------------------------------

let ctx = null
let disposeBadges = null
let paintedKey = ''
const WINDOW = [
  { hours: 1, label: '1 h' }, { hours: 4, label: '4 h' }, { hours: 24, label: '1 d' },
  { hours: 168, label: '7 d' }, { hours: 336, label: '14 d' }, { hours: 720, label: '30 d' }, { hours: 2160, label: '90 d' }
]  // the time windows the header selector offers (hours; the last four were days)
let state = { loaded: false, error: null, summary: null, needs: [], cards: {}, active: [], kept: [], hours: 24, recent: [], showBlocked: true }
const listeners = new Set()

const subscribe = fn => (listeners.add(fn), () => listeners.delete(fn))
const useStore = () => useSyncExternalStore(subscribe, () => state)

function update(patch) {
  state = { ...state, ...patch }
  listeners.forEach(fn => fn())
}

const message = error => String((error && error.message) || error)

async function refresh() {
  if (!ctx) {
    return
  }

  try {
    const hours = state.hours || 24
    const [summary, needs, cards, active] = await Promise.all([
      ctx.rest(`/summary?hours=${hours}`),
      ctx.rest('/needs-you'),
      ctx.rest('/cards'),
      ctx.rest('/active')
    ])

    update({ loaded: true, error: null, summary, needs, cards, active, recent: summary.recent })
    paintBadges()
  } catch (error) {
    update({ loaded: true, error: message(error) })
  }
}

function setHours(hours) {
  ctx.storage.set('hours', hours)   // kept like `kept`, so the window survives reloads
  update({ hours })
  refresh()
}

// The card's board door: the Kanban page opens its own drawer, and the drawer has no URL
// door a plugin can park on, so a card opens there, not here.
// The drawer is the Kanban page's own state, so after the jump the card is clicked where it is drawn:
// its footer shows the short id (best effort; the card must sit on the board the page shows).
function openCard(item) {
  host.navigate('/kanban')

  const short = item.task_id.replace(/^t_/, '').slice(0, 6)
  let tries = 0
  const timer = setInterval(() => {
    const footer = [...document.querySelectorAll('span.font-mono')].find(el => el.textContent === short)
    const card = footer && footer.closest('[draggable="true"]')

    if (card) {
      card.click()
    }

    if (card || ++tries > 20) {
      clearInterval(timer)
    }
  }, 150)
}

function moveCard(item, to) {
  ctx.rest('/move', { method: 'POST', body: { board: item.board, task_id: item.task_id, to } })
    .then(() => refresh())
    .catch(error => host.notify({ kind: 'error', message: message(error) }))
}

// The card's bucket moves, per its current status: the same verbs as the Kanban board's menu,
// only the ones a public transition can do (ready→todo stays in the board's drag).
const MOVES = {
  ready: [['blocked', 'To blocked'], ['archived', 'Archive']],
  todo: [['ready', 'To ready']],
  running: [['blocked', 'Block'], ['done', 'Complete'], ['review', 'To review'], ['archived', 'Archive']],
  blocked: [['unblocked', 'Unblock'], ['archived', 'Archive']],
  review: [['done', 'Complete'], ['archived', 'Archive']],
  done: [['archived', 'Archive']],
  triage: [['archived', 'Archive']]
}

// ---- card badges (Hermes Desktop 0.22.6 and later; an older app loads the page without them) ------------------

const BADGES = {
  mom: { label: 'mom', tone: 'blue', tooltip: 'Mom advised or answered on this card' },
  olymp: { label: 'olymp', tone: 'violet', tooltip: 'Olymp, the stronger model, was asked' },
  held: { label: 'held', tone: 'orange', tooltip: 'Pre-flight HOLD: a risky command stays blocked until you allow it' },
  'for you': { label: 'for you', tone: 'red', tooltip: 'Waiting for your decision' },
  stalled: { label: 'stalled', tone: 'orange', tooltip: 'Running, but no heartbeat for a while: the worker is probably gone' },
  verified: { label: 'verified', tone: 'green', tooltip: 'The verify command passed' },
  'verify failed': { label: 'verify failed', tone: 'red', tooltip: 'The verify command failed' }
}

function paintBadges() {
  const area = sdk.KANBAN_CARD_AREAS
  const key = JSON.stringify(state.cards)

  if (!area || !area.badges || key === paintedKey) {
    return
  }

  paintedKey = key

  if (disposeBadges) {
    disposeBadges()
  }

  // Registering the same id again replaces the old contribution, so the board repaints with the new data.
  disposeBadges = ctx.register({
    id: 'badges',
    area: area.badges,
    data: { decorate: card => (state.cards[card.id] || []).map(name => BADGES[name]).filter(Boolean) }
  })
}

// ---- helpers ---------------------------------------------------------------------------------------------------

const keepKey = item => JSON.stringify([item.task_id, item.holds, item.brief])
const waiting = s => s.needs.filter(item => !s.kept.includes(keepKey(item)))

function age(seconds) {
  if (seconds < 60) {
    return 'just now'
  }

  const h = seconds / 3600
  const d = seconds / 86400

  return h < 0.5 ? `${Math.round(seconds / 60)} min` : h < 36 ? `${Math.round(h)} h` : `${Math.round(d)} d`
}

const BRIEF_LINE = /^(Question|Options|Recommendation|If nobody answers):\s*(.*)$/

function briefRows(text) {
  const rows = String(text || '')
    .split('\n')
    .map(line => BRIEF_LINE.exec(line.trim()))
    .filter(Boolean)
    .map(m => ({ label: m[1], value: m[2] }))

  return rows.length ? rows : [{ label: 'Brief', value: String(text || '') }]
}

const PREFLIGHT = { go: 'Pre-flight: Mom said go', hold: 'Pre-flight HOLD on a risky command', held_again: 'A held command was tried again' }
const VERIFY = { pass: 'verify passed', fail: 'verify failed', timeout: 'verify timed out' }

// The action of a recent row, colored by what it did: the dot says it at a glance,
// the sentence stays the text.
function actionDot(row) {
  const { kind, detail } = row
  if (kind === 'verify') {
    return detail === 'pass' ? 'var(--ui-accent-green, #22c55e)' : 'var(--ui-text-destructive, #ef4444)'
  }

  if (kind === 'preflight') {
    return detail === 'go' ? 'var(--ui-accent-green, #22c55e)' : detail === 'hold' ? 'var(--ui-accent-amber, #f59e0b)' : muted.color
  }

  if (kind === 'question') {
    return detail === 'answered' ? 'var(--ui-accent-violet, #8b5cf6)' : 'var(--ui-text-secondary)'
  }

  if (kind === 'advisor') {
    return 'var(--ui-accent, #3b82f6)'
  }

  return kind === 'human_answer' ? 'var(--ui-text-secondary)' : 'var(--ui-accent-amber, #f59e0b)'
}

function sentence(row) {
  const who = row.by === 'olymp' ? 'Olymp' : 'Mom'

  switch (row.kind) {
    case 'advisor':
      return `${who} advised (${row.detail || 'stuck'})`
    case 'question':
      return row.detail === 'answered' ? `${who} answered a worker question` : `${who} wrote a brief for you`
    case 'preflight':
      return PREFLIGHT[row.detail] || 'Pre-flight check'
    case 'verify':
      return VERIFY[row.detail] || 'verify ran'
    case 'human_answer':
      return `You answered (${row.detail})`
    case 'escalated':
      return 'A card was handed to the stronger model'
    default:
      return row.kind
  }
}

const muted = { color: 'var(--ui-text-tertiary)' }
const box = { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 8, padding: '12px 14px' }

// ---- the page --------------------------------------------------------------------------------------------------

function Rung({ name, big, unit, lines, accent }) {
  return h(
    'div',
    { style: { ...box, flex: '1 1 150px', minWidth: 150 } },
    h('div', { style: { ...muted, fontSize: '0.6875rem', letterSpacing: '0.08em', textTransform: 'uppercase' } }, name),
    h('div', { style: { fontSize: '1.875rem', fontWeight: 600, lineHeight: 1.2, color: accent ? 'var(--ui-accent)' : undefined } }, big),
    h('div', { style: { ...muted, fontSize: '0.75rem' } }, unit),
    ...lines.filter(Boolean).map(line => h('div', { key: line, style: { fontSize: '0.75rem', marginTop: 4 } }, line))
  )
}

function outsideLine(outside) {
  return !outside || outside.observed === false
    ? 'outside help: not observed'
    : `outside help: ${outside.comments} comments · ${outside.changes} board changes`
}

function quotaLine(quota) {
  const parts = quota
    ? [
        quota.limits.length ? `${quota.limits.length} quota limit${quota.limits.length === 1 ? '' : 's'}` : null,
        quota.stalled.cards ? `${quota.stalled.cards} stalled` : null
      ].filter(Boolean)
    : []

  return parts.join(' · ')
}

function Ladder({ summary, count }) {
  if (!summary) {
    return null
  }

  const { worker, mom, olymp, you } = summary
  const arrow = key => h(Codicon, { key, name: 'arrow-right', style: { ...muted, alignSelf: 'center' } })

  return h(
    'div',
    { style: { display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'stretch' } },
    h(Rung, { name: 'Worker', big: worker.tool_calls, unit: 'tool calls watched', lines: [`${worker.signals} stuck signals`, quotaLine(summary.quota)] }),
    arrow('a1'),
    h(Rung, {
      name: 'Mom',
      big: mom.calls,
      unit: 'calls',
      lines: [
        `${mom.answered} answered · ${mom.checked} checked · ${mom.holds} held`,
        mom.verified + mom.verify_failed ? `${mom.verified} verified · ${mom.verify_failed} verify failed` : null
      ]
    }),
    arrow('a2'),
    h(Rung, { name: 'Olymp', big: olymp.calls, unit: 'calls', lines: [`${olymp.today} of ${olymp.cap} today`] }),
    arrow('a3'),
    h(Rung, { name: 'You', big: count, unit: 'waiting for you', accent: count > 0, lines: [`${you.briefs} briefs · ${you.answered} answered`, outsideLine(you.outside)] })
  )
}

function Item({ item }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [writing, setWriting] = useState(false)
  const [draft, setDraft] = useState('')

  async function send(body) {
    setBusy(true)
    setError(null)

    try {
      await ctx.rest('/answer', { method: 'POST', body: { board: item.board, task_id: item.task_id, ...body } })
      update({ needs: state.needs.filter(other => other.task_id !== item.task_id) })
      host.notify({ kind: 'success', message: 'Sent. The card continues.' })
      refresh()
    } catch (e) {
      setError(message(e))
      setBusy(false)
    }
  }

  function keep() {
    const kept = [...state.kept, keepKey(item)]

    ctx.storage.set('kept', kept)
    update({ kept })
  }

  const button = (key, label, props) => h(Button, { key, size: 'sm', disabled: busy, ...props }, label)
  const holds = item.holds.length > 0

  return h(
    'div',
    { style: { ...box, display: 'flex', flexDirection: 'column', gap: 8 } },
    h(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', gap: 8 } },
      h('div', { style: { fontWeight: 600 } }, item.title),
      h('div', { style: { ...muted, fontSize: '0.75rem', whiteSpace: 'nowrap' } }, `${item.task_id} · ${item.board} · waiting ${age(item.age_s)}`)
    ),
    item.brief &&
      h(
        'div',
        { style: { display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '2px 12px', fontSize: '0.8125rem' } },
        ...briefRows(item.brief).flatMap(({ label, value }) => [
          h('div', { key: `${label}-l`, style: muted }, label),
          h('div', { key: `${label}-v`, style: { whiteSpace: 'pre-wrap' } }, value)
        ])
      ),
    item.stalled &&
      h(
        'div',
        { style: { fontSize: '0.8125rem' } },
        h(Badge, { variant: 'warn' }, 'STALLED'),
        ` No heartbeat for ${item.stalled.minutes} min: the worker is probably gone. If so, run: `,
        h('code', { style: { fontFamily: 'var(--font-mono, monospace)', userSelect: 'all' } }, item.stalled.reclaim)
      ),
    ...item.holds.map((hold, i) =>
      h(
        'div',
        { key: `hold-${i}`, style: { fontSize: '0.8125rem' } },
        h(Badge, { variant: 'warn' }, 'HOLD'),
        ` ${hold.reason}. `,
        h('span', { style: muted }, `Instead: ${hold.alternative || 'a safer equivalent'}`)
      )
    ),
    writing &&
      h(Textarea, { value: draft, autoFocus: true, placeholder: 'Your answer goes to the worker as a card comment', onChange: e => setDraft(e.target.value) }),
    error && h('div', { className: 'text-destructive', style: { fontSize: '0.75rem' } }, error),
    !item.stalled &&
      h(
        'div',
        { style: { display: 'flex', flexWrap: 'wrap', gap: 6 } },
        ...(writing
          ? [
              button('send', 'Send answer', { disabled: busy || !draft.trim(), onClick: () => send({ text: draft.trim() }) }),
              button('cancel', 'Cancel', { variant: 'ghost', onClick: () => setWriting(false) })
            ]
          : [
              item.can_accept && button('accept', 'Accept recommendation', { onClick: () => send({ accept: true }) }),
              item.brief && button('answer', 'Answer...', { variant: 'secondary', onClick: () => setWriting(true) }),
              holds && button('allow', 'Allow once', { variant: item.brief ? 'secondary' : 'default', onClick: () => send({ allow_once: true }) }),
              holds && button('keep', 'Keep blocked', { variant: 'ghost', onClick: keep })
            ].filter(Boolean))
      )
  )
}

function Section({ title, count, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)   // a state per section; the header toggles it
  return h(
    'section',
    { style: { marginTop: 24, display: 'flex', flexDirection: 'column', gap: 8 } },
    h(
      'h2',
      {
        style: { fontSize: '0.8125rem', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' },
        onClick: () => setOpen(!open)
      },
      h(Codicon, { name: open ? 'chevron-down' : 'chevron-right', style: { ...muted } }),
      title,
      count ? h(Badge, { variant: 'default', style: { marginLeft: 8 } }, count) : null
    ),
    open ? children : null
  )
}

function windowLabel(hours) {
  const w = WINDOW.find(w => w.hours === hours)
  return w ? w.label : `${hours} h`
}

function WindowSelector({ value, onPick, ...props }) {
  const button = w => h(
    Button,
    {
      key: w.hours,
      size: 'sm',
      variant: w.hours === value ? 'default' : 'ghost',
      onClick: () => onPick(w.hours)
    },
    w.label
  )
  return h('div', { ...props, style: { display: 'flex', gap: 4 } }, WINDOW.map(button))
}

function copyTaskId(item) {
  navigator.clipboard
    .writeText(item.task_id)
    .then(() => host.notify({ kind: 'success', message: `Copied ${item.task_id}` }))
    .catch(error => host.notify({ kind: 'error', message: message(error) }))
}

function ActiveItem({ item }) {
  const { ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem, ContextMenuSeparator } = sdk
  const moves = MOVES[item.status] || []
  const card = h(
    'div',
    { style: { ...box, display: 'flex', flexDirection: 'column', gap: 6, cursor: 'pointer' }, onClick: () => openCard(item) },
    h(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' } },
      h(
        'div',
        { style: { display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' } },
        h('span', { style: { fontWeight: 600 } }, item.title),
        h(Badge, { variant: item.status === 'running' || item.status === 'blocked' ? 'warn' : 'default' }, item.status),
        h('span', { style: muted }, item.board)
      ),
      h('span', { style: { ...muted, fontSize: '0.6875rem' } }, item.task_id)
    ),
    item.last_chat
      ? h(
          'div',
          { style: { ...muted, fontSize: '0.8125rem', whiteSpace: 'pre-wrap', display: '-webkit-box', overflow: 'hidden', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' } },
          item.last_chat
        )
      : h('div', { style: { ...muted, fontSize: '0.8125rem' } }, 'No messages yet — the worker is on it.')
  )
  return ContextMenu
    ? h(
        ContextMenu,
        null,
        h(ContextMenuTrigger, { asChild: true }, card),
        h(
          ContextMenuContent,
          null,
          h(ContextMenuItem, { onSelect: () => openCard(item) }, 'Open card'),
          h(ContextMenuItem, { onSelect: () => copyTaskId(item) }, 'Copy task id'),
          moves.length > 0 && h(ContextMenuSeparator),
          moves.map(([to, label]) => h(ContextMenuItem, { key: to, onSelect: () => moveCard(item, to) }, label))
        )
      )
    : card
}

function Page() {
  const s = useStore()
  const [showKept, setShowKept] = useState(false)

  useEffect(() => void refresh(), []) // fresh numbers the moment the page opens, not up to 15 s later

  const open = waiting(s)
  const kept = s.needs.filter(item => s.kept.includes(keepKey(item)))
  const asking = new Set(s.needs.map(item => item.task_id)) // already shown under "Needs you"
  const active = s.active.filter(item => item.status !== 'blocked' || (s.showBlocked && !asking.has(item.task_id)))
  const toggleBlocked = () => {
    ctx.storage.set('showBlocked', !s.showBlocked)
    update({ showBlocked: !s.showBlocked })
  }

  return h(
    'div',
    { style: { height: '100%', overflowY: 'auto', padding: '20px 24px 32px' } },
    h(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 } },
      h('h1', { style: { fontSize: '1.25rem', fontWeight: 600, margin: 0 } }, 'Calling Mom'),
      h(WindowSelector, { value: s.hours, onPick: setHours })
    ),
    h('div', { style: { ...muted, fontSize: '0.8125rem', margin: '4px 0 16px' } }, h(VersionLine, { backend: s.summary && s.summary.version })),
    s.error &&
      h('div', { style: { ...box, marginBottom: 16, fontSize: '0.8125rem' } }, `The Calling Mom backend is not reachable (${s.error}). Enable the plugin with "hermes plugins enable calling-mom", then restart the gateway once.`),
    !s.loaded && h('div', { style: muted }, 'Loading...'),
    h(Ladder, { summary: s.summary, count: open.length }),
    s.summary &&
      h(
        Section,
        { title: 'Needs you', count: open.length },
        open.length === 0
          ? h(EmptyState, { title: 'Nothing is waiting for you.', description: 'Mom and Olymp handled everything that reached them.' })
          : open.map(item => h(Item, { key: item.task_id, item })),
        kept.length > 0 && h(Button, { variant: 'text', size: 'inline', onClick: () => setShowKept(!showKept) }, showKept ? 'Hide kept blocked' : `Show ${kept.length} kept blocked`),
        showKept && kept.map(item => h(Item, { key: item.task_id, item }))
      ),
    s.summary &&
      h(
        Section,
        { title: 'Active', count: active.length },
        h(Button, { variant: 'text', size: 'inline', onClick: toggleBlocked }, s.showBlocked ? 'Hide blocked' : 'Show blocked'),
        active.length === 0
          ? h(EmptyState, { title: 'Nothing is climbing right now.', description: 'No running, ready or blocked card on the boards you opted in.' })
          : active.map(item => h(ActiveItem, { key: item.task_id, item }))
      ),
    s.summary &&
      h(
        Section,
        { title: 'Recent activity', count: s.recent.length, defaultOpen: false },
        s.recent.length === 0
          ? h('div', { style: { ...muted, fontSize: '0.8125rem' } }, `No activity in the last ${windowLabel(s.hours)}.`)
          : h(
              'div',
              { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
              ...s.recent.map((row, i) =>
                h(
                  'div',
                  { key: i, style: { display: 'flex', flexDirection: 'column', gap: 4 } },
                  h(
                    'div',
                    { style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8125rem' } },
                    h('span', { style: { width: 8, height: 8, borderRadius: '50%', backgroundColor: actionDot(row), flex: 'none' } }),
                    h('span', { style: muted }, age(Date.now() / 1000 - row.ts)),
                    h('span', null, sentence(row))
                  ),
                  h(
                    'div',
                    { style: { display: 'flex', gap: 8, alignItems: 'baseline', fontSize: '0.8125rem', marginLeft: 16 } },
                    h('span', { style: muted }, row.title || row.task),
                    h('span', { style: muted }, row.board || '')
                  )
                )
              )
            )
      )
  )
}

// ---- the status-bar chip ---------------------------------------------------------------------------------------

function newer(a, b) {
  const [x, y] = [a, b].map(v => v.split('.').map(Number))
  const i = x.findIndex((n, k) => n !== (y[k] || 0))

  return i >= 0 && x[i] > (y[i] || 0)
}

function VersionLine({ backend }) {
  if (!backend || backend === VERSION) {
    return h('span', null, `v${VERSION}`)
  }

  const behind = newer(backend, VERSION)

  return h(
    'span',
    null,
    `Desktop v${VERSION} · Backend v${backend} `,
    h('span', { style: { color: 'var(--ui-accent)' } }, behind
      ? '— this app has an older Calling Mom: Capabilities → Plugins → Update.'
      : '— the gateway has an older Calling Mom: run "hermes plugins update calling-mom" there.')
  )
}

function Chip() {
  const s = useStore()

  if (!s.loaded) {
    return null
  }

  const count = waiting(s).length
  const text = s.error ? 'mom offline' : `${count} for you`

  if (!s.error && !count) {
    return null
  }

  return h(
    'button',
    {
      type: 'button',
      title: s.error ? 'The Calling Mom backend is not reachable' : 'Calling Mom: items waiting for you',
      onClick: () => host.navigate(PATH),
      style: { padding: '0 6px', fontSize: '0.6875rem', color: count ? 'var(--ui-accent)' : 'var(--ui-text-tertiary)' }
    },
    text
  )
}

// ---- registration ----------------------------------------------------------------------------------------------

export default {
  id: 'calling-mom',
  name: 'Calling Mom',
  register(context) {
    ctx = context
    update({ kept: ctx.storage.get('kept', []), hours: ctx.storage.get('hours', 24), showBlocked: ctx.storage.get('showBlocked', true) })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => h(Page) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Calling Mom', codicon: 'call-outgoing' } },
      { id: 'chip', area: STATUSBAR_AREAS.right, order: 140, render: () => h(Chip) }
    ])
    refresh()
    ctx.setInterval(() => {
      if (!document.hidden) {
        refresh()
      }
    }, POLL_MS)
  }
}
