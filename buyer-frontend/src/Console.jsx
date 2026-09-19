import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, money } from './api'
import Thumb from './Thumb'
import { ENGINES, engineOf } from './engines'

export default function Console({ events }) {
  const [tab, setTab] = useState('recommendations')
  return (
    <>
      <div className="tabs">
        {[['recommendations', 'Recommendations'],
          ['requests', 'Requests & Negotiations'],
          ['holds', 'Holds']].map(([k, label]) => (
          <button key={k} className={'tab' + (tab === k ? ' on' : '')}
                  onClick={() => setTab(k)}>{label}</button>
        ))}
        <div id="toolbar-slot" className="toolbar-slot" />
      </div>
      <div className="wrap">
        {tab === 'recommendations' && <Recommendations events={events} />}
        {tab === 'requests' && <Requests events={events} />}
        {tab === 'holds' && <Holds />}
      </div>
    </>
  )
}

/* ------------------------------------------------------------------ *
 * Renders its children into the tab strip, so the run buttons stay on
 * screen without a second sticky bar overlapping the cards.
 * ------------------------------------------------------------------ */

function Toolbar({ children }) {
  const [slot, setSlot] = useState(null)
  useEffect(() => { setSlot(document.getElementById('toolbar-slot')) }, [])
  if (!slot) return null
  return createPortal(children, slot)
}

function Recommendations({ events }) {
  const [rows, setRows] = useState([])
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      const all = await api.recommendations('pending_approval')
      // a hold is not a recommendation - only show what needs buying
      setRows(all.filter((r) => r.recommended_quantity > 0))
    } catch (e) { setErr(e.message) }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const last = events[0]
    if (!last) return
    if (last.event === 'analysis.progress') setProgress(last.data)
    if (last.event === 'analysis.completed') { setRunning(false); setProgress(null); load() }
    if (last.event === 'analysis.failed') { setRunning(false); setErr(last.data.error) }
  }, [events, load])

  const runStn = async () => {
    setRunning(true); setErr('')
    try { await api.runStn() } catch (e) { setErr(e.message); setRunning(false) }
  }

  const runLtn = async () => {
    setRunning(true); setErr('')
    try { await api.runLtn() } catch (e) { setErr(e.message); setRunning(false) }
  }

  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState(null)

  const dailyCheck = async () => {
    setChecking(true); setErr(''); setCheckResult(null)
    try {
      const out = await api.dailyCheck()
      setCheckResult(out)
      await load()
    } catch (e) { setErr(e.message) } finally { setChecking(false) }
  }

  const approve = async (row, qty) => {
    setBusy(true); setErr('')
    try {
      const out = await api.approve(row.result_id, qty)
      alert(`RFQ sent to ${out.sellers_invited} sellers for ${out.quantity} units`)
      await load()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const reject = async (row) => {
    setBusy(true)
    try { await api.reject(row.result_id, 'Not needed'); await load() }
    catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="grid">
      <div className="page-head">
        <h2>Recommendations</h2>
        <p>Nothing is ordered until you approve it.</p>
      </div>

      <Toolbar>
        <button className="ghost" onClick={dailyCheck} disabled={checking}>
          {checking ? 'Checking…' : '🛡️ Stock Watch'}
        </button>
        <button onClick={runStn} disabled={running} title={ENGINES.stn.blurb}>
          {running ? 'Running…' : `${ENGINES.stn.icon} ${ENGINES.stn.name}`}
        </button>
        <button onClick={runLtn} disabled={running} title={ENGINES.ltn.blurb}>
          {running ? 'Running…' : `${ENGINES.ltn.icon} ${ENGINES.ltn.name}`}
        </button>
      </Toolbar>

      {progress && (
        <div className="card">
          <div className="spread" style={{ marginBottom: 10 }}>
            <div className="muted">
              {engineOf(progress.engine || 'stn').name} · {progress.product_name}
              {progress.skipped ? ' — on hold, skipped' : ''}
              {progress.sources ? ` · ${progress.sources} sources` : ''}
            </div>
            <div className="muted mono">{progress.pct}%</div>
          </div>
          <div className="progress"><div style={{ width: progress.pct + '%' }} /></div>
        </div>
      )}
      {err && <div className="err">{err}</div>}

      <Stock events={events} />

      {checkResult && <DailyCheck result={checkResult} />}

      <h3 style={{ marginTop: 8 }}>Waiting for your approval</h3>

      {rows.length === 0
        ? <div className="empty">
            <span className="icon">✓</span>
            Nothing to order. Every product has enough cover for now.
          </div>
        : <div className="grid">
            {rows.map((r) => (
              <RecommendationCard key={r.result_id} row={r} busy={busy}
                                  onApprove={approve} onReject={reject} />
            ))}
          </div>}
    </div>
  )
}

function Stock({ events }) {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try { setRows(await api.stock()) } catch (e) { setErr(e.message) }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    // refresh whenever anything happened that could move stock
    const e = events[0]?.event
    if (e === 'analysis.completed' || e === 'po.issued') load()
  }, [events, load])

  // never render nothing - an empty panel looks like a broken app
  if (err) return (
    <>
      <h3>Stock on hand</h3>
      <div className="err">Could not load stock: {err}</div>
    </>
  )
  if (rows.length === 0) return (
    <>
      <h3>Stock on hand</h3>
      <div className="empty">
        <span className="icon">📦</span>
        No products came back. Check the buyer backend is restarted and that
        ONLY_PRODUCTS is blank in its .env.
      </div>
    </>
  )

  return (
    <>
      <h3>Stock on hand</h3>
      <div className="cards">
        {rows.map((r) => <StockCard key={r.product_id} row={r} />)}
      </div>
    </>
  )
}

function StockCard({ row }) {
  const tone = row.level === 'CRITICAL' ? 'bad'
             : row.level === 'URGENT'   ? 'warn' : 'good'
  const label = row.level === 'OK' ? 'healthy' : row.level.toLowerCase()

  // how full the runway bar is, capped at a month
  const pct = Math.max(4, Math.min(100, Math.round((row.runway_days / 30) * 100)))

  return (
    <div className="card">
      <div className="spread">
        <div className="item-head">
          <Thumb productId={row.product_id} size={48} />
          <div className="meta">
            <h3>{row.product_name}</h3>
            <div className="faint">{row.daily_rate}/day · {row.lead_time_days}d lead</div>
          </div>
        </div>
        <span className={'badge ' + tone}>{label}</span>
      </div>

      <div className="row" style={{ margin: '14px 0 10px', gap: 22 }}>
        <div>
          <label style={{ marginBottom: 2 }}>In stock</label>
          <div className="big">{row.stock}</div>
        </div>
        <div>
          <label style={{ marginBottom: 2 }}>Days of cover</label>
          <div className="big">{row.runway_days}</div>
        </div>
      </div>

      <div className="progress">
        <div style={{ width: pct + '%',
                      background: tone === 'bad' ? 'var(--bad)'
                                : tone === 'warn' ? 'var(--warn)' : 'var(--good)' }} />
      </div>
    </div>
  )
}

function DailyCheck({ result }) {
  const acted = result.findings.filter((f) => f.level !== 'OK')
  return (
    <div className="card">
      <div className="spread">
        <div>
          <h3>Daily stock check</h3>
          <div className="muted">
            {result.checked} products · {result.critical} critical ·
            {' '}{result.urgent} urgent · {result.awaiting_approval} awaiting your approval
            {result.holds_overridden > 0 &&
              ` · ${result.holds_overridden} hold${result.holds_overridden > 1 ? 's' : ''} overridden`}
          </div>
        </div>
        <span className={'badge ' + (result.awaiting_approval ? 'warn' : 'good')}>
          {result.awaiting_approval ? 'needs approval' : 'all healthy'}
        </span>
      </div>

      {acted.length === 0
        ? <div className="muted">Every product has more cover than its lead time.</div>
        : (
          <table>
            <thead>
              <tr>
                <th>Product</th><th className="num">Stock</th><th className="num">Per day</th>
                <th className="num">Runway</th><th className="num">Lead</th>
                <th></th><th className="num">Suggested</th>
              </tr>
            </thead>
            <tbody>
              {acted.map((f) => (
                <tr key={f.product_id}>
                  <td>
                    <div className="item-head">
                      <Thumb productId={f.product_id} size={34} radius={9} />
                      <span>{f.product_name}</span>
                    </div>
                  </td>
                  <td className="num">{f.stock}</td>
                  <td className="num">{f.daily_rate}</td>
                  <td className="num">{f.runway_days}d</td>
                  <td className="num">{f.lead_time_days}d</td>
                  <td>
                    <span className={'badge ' + (f.level === 'CRITICAL' ? 'bad' : 'warn')}>
                      {f.level}
                    </span>
                    {f.was_held && <span className="badge" style={{ marginLeft: 6 }}>
                      hold overridden</span>}
                  </td>
                  <td className="num">
                    {f.quantity > 0 ? `${f.quantity} units` : '—'}
                    {f.shelf_life_cap != null &&
                      <div className="muted" style={{ fontSize: 11 }}>
                        capped at {f.shelf_life_cap} by shelf life
                      </div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </div>
  )
}

function RecommendationCard({ row, busy, onApprove, onReject }) {
  const [qty, setQty] = useState(row.recommended_quantity)
  const value = qty * Number(row.cost_price)
  const held = !row.recommended_quantity && row.hold_days

  return (
    <div className="card">
      <div className="spread">
        <div className="item-head">
          <Thumb productId={row.product_id} size={54} />
          <div className="meta">
            <h3>{row.product_name}</h3>
            <div className="faint">
              {engineOf(row.engine).icon} {engineOf(row.engine).name} · as of{' '}
              {row.as_of_date} · {row.seller_count} sellers
            </div>
          </div>
        </div>
        {held
          ? <span className="badge warn">hold {row.hold_days}d</span>
          : <span className="badge good">order {row.recommended_quantity}</span>}
      </div>

      {row.reasoning && (
        <p className="muted" style={{ marginBottom: 12 }}>{row.reasoning}</p>
      )}

      {row.recommended_quantity > 0 ? (
        <div className="row">
          <div style={{ width: 120 }}>
            <label>Quantity</label>
            <input type="number" value={qty}
                   onChange={(e) => setQty(Number(e.target.value))} />
          </div>
          <div>
            <label>Order value</label>
            <div className="big">{money(value)}</div>
          </div>
          <div style={{ marginLeft: 'auto' }} className="row">
            <button className="ghost" disabled={busy} onClick={() => onReject(row)}>
              Reject
            </button>
            <button className="good" disabled={busy || qty <= 0}
                    onClick={() => onApprove(row, qty)}>
              Approve & send RFQ
            </button>
          </div>
        </div>
      ) : (
        <div className="muted">
          No order needed — held for {row.hold_days} days.
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Requests({ events }) {
  const [rows, setRows] = useState([])
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => setRows(await api.requests()), [])
  useEffect(() => { load() }, [load, events.length])

  if (open) return <RequestDetail id={open} onBack={() => { setOpen(null); load() }} />

  // Only the newest request. The API returns them created_at DESC.
  const latest = rows.slice(0, 1)

  return (
    <div className="card">
      <div className="spread" style={{ marginBottom: 12 }}>
        <h3>Latest request</h3>
        {rows.length > 1 && (
          <span className="faint">{rows.length - 1} earlier hidden</span>
        )}
      </div>
      <table>
        <thead>
          <tr>
            <th>Product</th><th className="num">Qty</th><th>Status</th>
            <th className="num">Replied</th><th></th>
          </tr>
        </thead>
        <tbody>
          {latest.map((r) => (
            <tr key={r.request_id}>
              <td>
                <div className="item-head">
                  <Thumb productId={r.product_id} size={36} radius={9} />
                  <span>{r.product_name}</span>
                </div>
              </td>
              <td className="num">{r.quantity_needed}</td>
              <td><span className={'badge ' + statusColor(r.status)}>{r.status}</span></td>
              <td className="num">{r.replied}/{r.invited}</td>
              <td className="num">
                <button className="ghost" onClick={() => setOpen(r.request_id)}>Open</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan="5" className="empty">
              <span className="icon">📋</span>No requests yet.
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

const statusColor = (s) =>
  s === 'awarded' ? 'good' : s === 'negotiating' ? 'info'
  : s === 'cancelled' ? 'bad' : 'warn'

function RequestDetail({ id, onBack }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try { setData(await api.requestDetail(id)) } catch (e) { setErr(e.message) }
  }, [id])
  useEffect(() => { load() }, [load])

  const award = async (nid) => {
    try {
      const po = await api.award(nid)
      load()
      window.open(api.payUrl(po.po_number), '_blank')
    } catch (e) { setErr(e.message) }
  }

  if (!data) return <div className="empty">Loading…</div>
  const { request, quotes, negotiations } = data

  return (
    <div className="grid">
      <div className="row">
        <button className="ghost" onClick={onBack}>← Back</button>
        <Thumb productId={request.product_id} size={40} radius={10} />
        <h3 style={{ margin: 0 }}>{request.product_name}</h3>
        <span className="badge">{request.quantity_needed} units</span>
        <span className={'badge ' + statusColor(request.status)}>{request.status}</span>
      </div>

      <div className="card">
        <h3>Quotes received</h3>
        <table>
          <thead>
            <tr>
              <th>Seller</th><th className="num">Price</th><th className="num">Delivery</th>
              <th className="num">Reliability</th><th className="num">Score</th><th></th>
            </tr>
          </thead>
          <tbody>
            {quotes.map((q) => (
              <tr key={q.quote_id}>
                <td>{q.company_name}</td>
                <td className="num">{q.response === 'yes' ? money(q.quoted_price) : '—'}</td>
                <td className="num">{q.expected_delivery_days || '—'}</td>
                <td className="num">{Number(q.reliability_score).toFixed(0)}%</td>
                <td className="num">{q.score ?? '—'}</td>
                <td>{q.response === 'no'
                  ? <span className="badge bad">declined</span>
                  : <span className="badge good">quoted</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {negotiations.map((n) => (
        <div className="card" key={n.negotiation_id}>
          <div className="spread">
            <div>
              <h3>{n.company_name}</h3>
              <div className="muted">
                round {n.current_round}/{n.max_rounds} ·
                opened at {money(n.opening_price)}
                {n.final_price ? ` · settled at ${money(n.final_price)}` : ''}
              </div>
            </div>
            <div className="row">
              <span className={'badge ' + (n.status === 'accepted' ? 'good' : 'info')}>
                {n.status}
              </span>
              {n.status === 'accepted' && request.status !== 'awarded' && (
                <button className="good" onClick={() => award(n.negotiation_id)}>
                  Award PO
                </button>
              )}
              {request.status === 'awarded' && n.status === 'accepted' && (
                <a className="btn-link" target="_blank" rel="noreferrer"
                   href={api.payUrl(request.po_number)}>Pay</a>
              )}
            </div>
          </div>
        </div>
      ))}

      {err && <div className="err">{err}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Holds() {
  const [rows, setRows] = useState([])
  const load = useCallback(async () => setRows(await api.holds()), [])
  useEffect(() => { load() }, [load])

  return (
    <div className="card">
      <h3>Products on hold</h3>
      <div className="muted" style={{ marginBottom: 16 }}>
        Skipped by the agents until the hold expires — no LLM call is made for these.
      </div>
      <table>
        <thead>
          <tr><th>Product</th><th>Placed by</th><th className="num">Days</th>
              <th>Until</th><th>Why</th><th></th></tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.product_id}>
              <td>
                <div className="item-head">
                  <Thumb productId={h.product_id} size={36} radius={9} />
                  <span>{h.name}</span>
                </div>
              </td>
              <td className="muted">
                {engineOf(h.created_by).icon} {engineOf(h.created_by).short}
              </td>
              <td className="num">{h.hold_days}</td>
              <td>{h.hold_until}</td>
              <td className="muted">{(h.reason || '').slice(0, 90)}</td>
              <td className="num">
                <button className="ghost"
                        onClick={async () => { await api.releaseHold(h.product_id); load() }}>
                  Release
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan="6" className="empty">
              <span className="icon">⏸</span>Nothing on hold.
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
