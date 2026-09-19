import { useEffect, useState } from 'react'
import { api, getSession, setSession, clearSession, connectSocket } from './api'
import Console from './Console'

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
        <div className="brand-dot">W</div>
        <h1>Buyer Console</h1>
        <span className="badge info">AI procurement agent</span>
        <span className="who">{session.display_name} · {session.email}</span>
        <button className="ghost" onClick={() => { clearSession(); setSess(null) }}>
          Log out
        </button>
      </div>

      <Console events={events} />
    </>
  )
}

function Login({ onDone }) {
  const [username, setUsername] = useState('buyer@mumbairetail.in')
  const [password, setPassword] = useState('demo1234')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('')
    try { onDone(await api.login(username, password)) }
    catch (ex) { setErr(ex.message) } finally { setBusy(false) }
  }

  return (
    <div className="login">
      <div className="card">
        <div className="login-logo">🛒</div>
        <h3>Buyer Console</h3>
        <p className="muted" style={{ marginTop: 2, marginBottom: 22 }}>
          Sign in to review what the agents recommend.
        </p>
        <form onSubmit={submit}>
          <div className="field">
            <label>Email</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={password}
                   onChange={(e) => setPassword(e.target.value)} />
          </div>
          {err && <div className="err">{err}</div>}
          <button disabled={busy} style={{ width: '100%' }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div className="hint">buyer@mumbairetail.in · demo1234</div>
      </div>
    </div>
  )
}
