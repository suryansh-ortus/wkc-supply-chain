import { useCallback, useEffect, useRef, useState } from 'react'
import { api, getLanguage, money, payUrl } from './api'
import Thumb from './Thumb'
import { LanguagePicker, MicButton, SpeakButton, useLanguages } from './Voice'

export default function Portal({ session, events }) {
  const [tab, setTab] = useState('requests')
  return (
    <>
      <div className="tabs">
        {[['requests', 'Requests'],
          ['negotiations', 'Negotiations'],
          ['history', 'History']].map(([k, label]) => (
          <button key={k} className={'tab' + (tab === k ? ' on' : '')}
                  onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <div className="wrap">
        {tab === 'requests' && <Requests events={events} />}
        {tab === 'negotiations' && <Negotiations events={events} />}
        {tab === 'history' && <History />}
      </div>
    </>
  )
}

/* ------------------------------------------------------------------ */

function Requests({ events }) {
  const [rows, setRows] = useState([])
  const load = useCallback(async () => setRows(await api.requests()), [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (events[0]?.event === 'rfq.invited') load()
  }, [events, load])

  if (rows.length === 0) return (
    <div className="empty">
      <span className="icon">📭</span>
      No open requests right now. New ones appear here instantly.
    </div>
  )

  return (
    <div className="grid">
      {rows.map((r) => <RequestCard key={r.invitation_id} row={r} onDone={load} />)}
    </div>
  )
}

function RequestCard({ row, onDone }) {
  const [mode, setMode] = useState(null)      // null | 'yes' | 'no'
  const [price, setPrice] = useState('')
  const [days, setDays] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    setBusy(true); setErr('')
    try {
      const out = await api.submitQuote({
        invitation_id: row.invitation_id,
        response: mode,
        quoted_price: mode === 'yes' ? Number(price) : null,
        expected_delivery_days: mode === 'yes' ? Number(days) : null,
        notes,
      })
      if (out.negotiations_started) {
        alert('All sellers have answered — the buyer has opened negotiations.')
      }
      onDone()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <div className="spread">
        <div className="item-head">
          <Thumb productId={row.product_id} size={56} />
          <div className="meta">
            <h3>{row.product_name}</h3>
            <div className="faint">
              {row.quantity_needed} units · reply by{' '}
              {new Date(row.response_deadline).toLocaleDateString()}
            </div>
          </div>
        </div>
        <span className={'badge ' + (
          row.urgency === 'critical' ? 'bad' : row.urgency === 'high' ? 'warn' : 'info'
        )}>{row.urgency}</span>
      </div>

      <p style={{ background: 'var(--bg)', padding: '14px 16px',
                  borderRadius: 'var(--r)', border: '1px solid var(--line-soft)',
                  fontSize: 13.5, whiteSpace: 'pre-wrap', color: 'var(--text-dim)' }}>
        {row.message}
      </p>

      {!mode ? (
        <div className="row">
          <button className="good" onClick={() => setMode('yes')}>Quote for this</button>
          <button className="ghost" onClick={() => setMode('no')}>Decline</button>
        </div>
      ) : mode === 'yes' ? (
        <div>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <div style={{ flex: 1 }}>
              <label>Price per unit (₹)</label>
              <input type="number" step="0.01" value={price} autoFocus
                     onChange={(e) => setPrice(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label>Delivery in (days)</label>
              <input type="number" value={days}
                     onChange={(e) => setDays(e.target.value)} />
            </div>
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <label>Notes (optional)</label>
            <input value={notes} onChange={(e) => setNotes(e.target.value)}
                   placeholder="Payment terms, minimum order…" />
          </div>
          {price && days && (
            <div className="muted" style={{ marginBottom: 12 }}>
              Total for {row.quantity_needed} units:{' '}
              <b>{money(Number(price) * row.quantity_needed)}</b>
            </div>
          )}
          {err && <div className="err">{err}</div>}
          <div className="row">
            <button disabled={busy || !price || !days} onClick={submit}>
              {busy ? 'Sending…' : 'Send quote'}
            </button>
            <button className="ghost" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div>
          <div className="field">
            <label>Reason (optional)</label>
            <input value={notes} onChange={(e) => setNotes(e.target.value)}
                   placeholder="Out of stock this week" />
          </div>
          {err && <div className="err">{err}</div>}
          <div className="row">
            <button className="bad" disabled={busy} onClick={submit}>
              Confirm decline
            </button>
            <button className="ghost" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Negotiations({ events }) {
  const [rows, setRows] = useState([])
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => setRows(await api.negotiations()), [])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const e = events[0]?.event
    if (e === 'negotiation.started' || e === 'negotiation.buyer_replied') load()
  }, [events, load])

  if (open) return <Chat id={open} events={events} onBack={() => { setOpen(null); load() }} />
  if (rows.length === 0) return (
    <div className="empty">
      <span className="icon">💬</span>
      No negotiations yet. Quote for a request and the buyer may open one.
    </div>
  )

  return (
    <div className="grid">
      {rows.map((n) => (
        <div className="card" key={n.negotiation_id}>
          <div className="spread">
            <div className="item-head">
              <Thumb productId={n.product_id} size={48} />
              <div className="meta">
                <h3>{n.product_name}</h3>
                <div className="faint">
                  {n.quantity_needed} units · round {n.current_round}/{n.max_rounds}
                  {n.final_price ? ` · settled ${money(n.final_price)}` : ''}
                </div>
              </div>
            </div>
            <div className="row">
              <span className={'badge ' + (n.status === 'accepted' ? 'good'
                : n.status === 'active' ? 'info' : '')}>{n.status}</span>
              <button onClick={() => setOpen(n.negotiation_id)}>
                {n.last_sender === 'buyer' && n.status === 'active' ? 'Reply' : 'View'}
              </button>
            </div>
          </div>
          {n.last_message && (
            <p className="muted" style={{ marginBottom: 0 }}>
              {n.last_sender === 'buyer' ? 'Buyer: ' : 'You: '}
              {n.last_message.slice(0, 150)}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}

function Chat({ id, events, onBack }) {
  const [data, setData] = useState(null)
  const [text, setText] = useState('')
  const [price, setPrice] = useState('')
  const [days, setDays] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const bottom = useRef(null)

  // Sarvam: which language this seller wants to speak and listen in.
  const { enabled: voiceOn, languages } = useLanguages()
  const [lang, setLang] = useState(getLanguage())

  const load = useCallback(async () => {
    setData(await api.negotiation(id))
  }, [id])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (events[0]?.event === 'negotiation.buyer_replied') load()
  }, [events, load])
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [data])

  const send = async () => {
    setBusy(true); setErr('')
    try {
      await api.send(id, {
        message: text,
        counter_price: price ? Number(price) : null,
        counter_delivery: days ? Number(days) : null,
      })
      setText(''); setPrice(''); setDays('')
      await load()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  if (!data) return <div className="empty">Loading…</div>
  const { negotiation: n, messages } = data
  const closed = n.status !== 'active'

  return (
    <div className="grid">
      <div className="row">
        <button className="ghost" onClick={onBack}>← Back</button>
        <Thumb productId={n.product_id} size={40} radius={10} />
        <h3 style={{ margin: 0 }}>{n.product_name}</h3>
        <span className="badge">{n.quantity_needed} units</span>
        <span className={'badge ' + (n.status === 'accepted' ? 'good' : 'info')}>
          {n.status} · round {n.current_round}/{n.max_rounds}
        </span>
        {voiceOn && (
          <div className="row" style={{ marginLeft: 'auto', gap: 8 }}>
            <span className="faint">Voice</span>
            <LanguagePicker languages={languages} value={lang} onChange={setLang} />
          </div>
        )}
      </div>

      <div className="chat">
        {messages.map((m, i) => (
          <div key={i} className={'msg ' + m.sender}>
            <div className="meta">
              {m.sender === 'buyer' ? 'Buyer' : 'You'} · round {m.round}
              {voiceOn && m.sender === 'buyer' && (
                <SpeakButton text={m.body} language={lang} />
              )}
            </div>
            {m.body}
            {(m.proposed_price || m.proposed_delivery) && (
              <div className="terms">
                {m.proposed_price ? `Offer ${money(m.proposed_price)}` : ''}
                {m.proposed_delivery ? ` · ${m.proposed_delivery} days` : ''}
              </div>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      {closed ? (
        <div className="card">
          {n.status === 'accepted'
            ? <>Deal agreed at <b>{money(n.final_price)}</b>
                {n.final_delivery ? ` with ${n.final_delivery} day delivery.` : '.'}</>
            : <span className="muted">This negotiation is {n.status}.</span>}
        </div>
      ) : (
        <div className="card">
          <div className="field">
            <div className="spread" style={{ marginBottom: 6 }}>
              <label style={{ margin: 0 }}>Your reply</label>
              {voiceOn && (
                <span className="faint">
                  Speak in any language — it reaches the buyer in English
                </span>
              )}
            </div>
            <div className="row" style={{ alignItems: 'flex-start' }}>
              <textarea rows="3" style={{ flex: 1 }} value={text}
                        onChange={(e) => setText(e.target.value)}
                        placeholder="We can do this price if you confirm today…" />
              {voiceOn && (
                <MicButton disabled={busy}
                           onText={(t) => setText((p) => (p ? p + ' ' + t : t))} />
              )}
            </div>
          </div>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <div style={{ flex: 1 }}>
              <label>Counter price (₹, optional)</label>
              <input type="number" step="0.01" value={price}
                     onChange={(e) => setPrice(e.target.value)} />
            </div>
            <div style={{ flex: 1 }}>
              <label>Delivery days (optional)</label>
              <input type="number" value={days}
                     onChange={(e) => setDays(e.target.value)} />
            </div>
            <button disabled={busy || !text.trim()} onClick={send}>
              {busy ? 'Sending…' : 'Send'}
            </button>
          </div>
          {err && <div className="err">{err}</div>}
          <div className="muted" style={{ marginTop: 8 }}>
            The buyer's agent answers immediately.
          </div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function History() {
  const [rows, setRows] = useState([])
  useEffect(() => { api.history().then(setRows) }, [])

  return (
    <div className="card">
      <table>
        <thead>
          <tr>
            <th>Product</th><th>Answer</th><th className="num">Your price</th>
            <th className="num">Score</th><th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.quote_id}>
              <td>{r.product_name}</td>
              <td>{r.response === 'yes'
                ? <span className="badge good">quoted</span>
                : <span className="badge bad">declined</span>}</td>
              <td className="num">{r.quoted_price ? money(r.quoted_price) : '—'}</td>
              <td className="num">{r.score ?? '—'}</td>
              <td>{r.po_number
                ? <span className="row" style={{ gap: 8 }}>
                    <span className="badge good">won · {money(r.total_value)}</span>
                    <a className="btn-link" target="_blank" rel="noreferrer"
                       href={payUrl(r.po_number)}>Payment QR</a>
                  </span>
                : <span className="muted">{r.negotiation_status || r.request_status}</span>}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan="5" className="empty">
              <span className="icon">📄</span>Nothing yet.
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
