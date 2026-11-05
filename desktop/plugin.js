// Calling mom, the Desktop half: card badges, the "Calling mom" page, a status-bar chip.
// Plain ESM, no JSX, no build step. It talks to dashboard/plugin_api.py only (ctx.rest), never to app internals.
import * as sdk from '@hermes/plugin-sdk'
import { createElement as h, useState, useSyncExternalStore } from 'react'

const { host, Button, Badge, Codicon, EmptyState, Textarea, ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS } = sdk

const PATH = '/calling-mom'
const POLL_MS = 15000

// ---- state: one store, one poller, shared by the badges, the page and the chip ---------------------------------

let ctx = null
let disposeBadges = null
let paintedKey = ''
let state = { loaded: false, error: null, summary: null, needs: [], cards: {}, kept: [] }
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
    const [summary, needs, cards] = await Promise.all([ctx.rest('/summary?days=7'), ctx.rest('/needs-you'), ctx.rest('/cards')])

    update({ loaded: true, error: null, summary, needs, cards })
    paintBadges()
  } catch (error) {
    update({ loaded: true, error: message(error) })
  }
}

// ---- card badges (Hermes Desktop 0.22.6 and later; an older app loads the page without them) ------------------

const BADGES = {
  mom: { label: 'mom', tone: 'blue', tooltip: 'Mom advised or answered on this card' },
  olymp: { label: 'olymp', tone: 'violet', tooltip: 'Olymp, the stronger model, was asked' },
  held: { label: 'held', tone: 'orange', tooltip: 'Pre-flight HOLD: a risky command stays blocked until you allow it' },
  'for you': { label: 'for you', tone: 'red', tooltip: 'Waiting for your decision' },
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

  if (seconds < 3600) {
    return `${Math.round(seconds / 60)} min`
  }

  return seconds < 86400 ? `${Math.round(seconds / 3600)} h` : `${Math.round(seconds / 86400)} d`
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

function Ladder({ summary, count }) {
  if (!summary) {
    return null
  }

  const { worker, mom, olymp, you } = summary
  const arrow = key => h(Codicon, { key, name: 'arrow-right', style: { ...muted, alignSelf: 'center' } })

  return h(
    'div',
    { style: { display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'stretch' } },
    h(Rung, { name: 'Worker', big: worker.tool_calls, unit: 'tool calls watched', lines: [`${worker.signals} stuck signals`] }),
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
    h(Rung, { name: 'You', big: count, unit: 'waiting for you', accent: count > 0, lines: [`${you.briefs} briefs · ${you.answered} answered`] })
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

function Section({ title, count, children }) {
  return h(
    'section',
    { style: { marginTop: 24, display: 'flex', flexDirection: 'column', gap: 8 } },
    h('h2', { style: { fontSize: '0.8125rem', fontWeight: 600, margin: 0 } }, title, count ? h(Badge, { variant: 'default', style: { marginLeft: 8 } }, count) : null),
    children
  )
}

function Page() {
  const s = useStore()
  const [showKept, setShowKept] = useState(false)
  const open = waiting(s)
  const kept = s.needs.filter(item => s.kept.includes(keepKey(item)))

  return h(
    'div',
    { style: { height: '100%', overflowY: 'auto', padding: '20px 24px 32px' } },
    h('h1', { style: { fontSize: '1.25rem', fontWeight: 600, margin: 0 } }, 'Calling mom'),
    h('div', { style: { ...muted, fontSize: '0.8125rem', marginBottom: 16 } }, 'Worker, then Mom, then Olymp, then you. Your attention is the most expensive model in the stack. Last 7 days.'),
    s.error &&
      h('div', { style: { ...box, marginBottom: 16, fontSize: '0.8125rem' } }, `The Calling mom backend is not reachable (${s.error}). Enable the plugin with "hermes plugins enable calling-mom", then restart the gateway once.`),
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
      s.summary.recent.length > 0 &&
      h(
        Section,
        { title: 'Recent activity' },
        h(
          'div',
          { style: { display: 'grid', gridTemplateColumns: 'max-content 1fr max-content', gap: '4px 16px', fontSize: '0.8125rem' } },
          ...s.summary.recent.flatMap((row, i) => [
            h('div', { key: `${i}-t`, style: muted }, sdk.relativeTime ? sdk.relativeTime(row.ts * 1000) : age(Date.now() / 1000 - row.ts)),
            h('div', { key: `${i}-s` }, sentence(row)),
            h('div', { key: `${i}-i`, style: { ...muted, fontFamily: 'var(--font-mono, monospace)' } }, row.task || '')
          ])
        )
      )
  )
}

// ---- the status-bar chip ---------------------------------------------------------------------------------------

function Chip() {
  const s = useStore()

  if (!s.loaded) {
    return null
  }

  const count = waiting(s).length
  const text = s.error ? 'mom offline' : `mom ${s.summary.today.mom_calls}${count ? ` · ${count} for you` : ''}`

  return h(
    'button',
    {
      type: 'button',
      title: s.error ? 'The Calling mom backend is not reachable' : 'Calling mom: calls today and items waiting for you',
      onClick: () => host.navigate(PATH),
      style: { padding: '0 6px', fontSize: '0.6875rem', color: count ? 'var(--ui-accent)' : 'var(--ui-text-tertiary)' }
    },
    text
  )
}

// ---- registration ----------------------------------------------------------------------------------------------

export default {
  id: 'calling-mom',
  name: 'Calling mom',
  register(context) {
    ctx = context
    update({ kept: ctx.storage.get('kept', []) })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => h(Page) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Calling mom', codicon: 'heart' } },
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
