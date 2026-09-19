import { useEffect, useState } from 'react'
import { api, getSession, setSession, clearSession, connectSocket } from './api'
import Portal from './Portal'

export default function App() {
  const [session, setSess] = useState(getSession())
  const [events, setEvents] = useState([])

  useEffect(() => {
    if (!session) return
    const ws = connectSocket((msg) =>
      setEvents((p) => [{ ...msg, at: new Date().toLocaleTimeString() }, ...p].slice(0, 40)))
    return () => ws && ws.close()
  }, [session])

  if (!session) return <Login onDone={(s) => { setSession(s); setSess(s) }} />

  return (
    <>
      <div className="topbar">
        <div className="brand-dot">S</div>
        <h1>Seller Portal</h1>
        <span className="who">
          <b style={{ color: 'var(--text)' }}>{session.company_name}</b>
          {session.location ? ' · ' + session.location : ''}
        </span>
        <button className="ghost" onClick={() => { clearSession(); setSess(null) }}>
          Log out
        </button>
      </div>

      <Portal session={session} events={events} />
    </>
  )
}

function Login({ onDone }) {
  const [accounts, setAccounts] = useState([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('demo1234')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.accounts()
      .then((list) => {
        setAccounts(list)
        if (list.length) setUsername(list[0].email)
      })
      .catch(() => {})
  }, [])

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try { onDone(await api.login(username, password)) }
    catch (ex) { setErr(ex.message) } finally { setBusy(false) }
  }

  return (
    <div className="login">
      <div className="card">
        <div className="login-logo">🏭</div>
        <h3>Seller Portal</h3>
        <p className="muted" style={{ marginTop: 2, marginBottom: 22 }}>
          Sign in to see requests and quote for them.
        </p>
        <form onSubmit={submit}>
          <div className="field">
            <label>Account</label>
            {accounts.length > 0 ? (
              <select value={username} onChange={(e) => setUsername(e.target.value)}>
                {accounts.map((a) => (
                  <option key={a.seller_id} value={a.email}>
                    {a.company_name} — {a.email}
                  </option>
                ))}
              </select>
            ) : (
              <input value={username} onChange={(e) => setUsername(e.target.value)}
                     placeholder="email address" autoFocus />
            )}
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={password}
                   onChange={(e) => setPassword(e.target.value)} />
          </div>
          {err && <div className="err">{err}</div>}
          <button disabled={busy || !username} style={{ width: '100%' }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div className="hint">
          Demo accounts, password <b>demo1234</b>
        </div>
      </div>
    </div>
  )
}
