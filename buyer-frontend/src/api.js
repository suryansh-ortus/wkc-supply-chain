const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8001'
const WS = BASE.replace(/^http/, 'ws')

export function getSession() {
  try { return JSON.parse(localStorage.getItem('wkc_buyer') || 'null') } catch { return null }
}
export function setSession(s) { localStorage.setItem('wkc_buyer', JSON.stringify(s)) }
export function clearSession() { localStorage.removeItem('wkc_buyer') }

async function req(path, options = {}) {
  const session = getSession()
  const res = await fetch(BASE + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
      ...options.headers,
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch {}
    throw new Error(detail)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  login: (username, password) =>
    req('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  recommendations: (status = 'pending_approval') =>
    req(`/api/recommendations?status=${status}`),
  approve: (id, quantity) =>
    req(`/api/recommendations/${id}/approve`, {
      method: 'POST', body: JSON.stringify({ quantity }) }),
  reject: (id, note) =>
    req(`/api/recommendations/${id}/reject`, {
      method: 'POST', body: JSON.stringify({ note }) }),
  runStn: () => req('/api/run-stn', { method: 'POST', body: JSON.stringify({}) }),
  runLtn: () => req('/api/run-ltn', { method: 'POST', body: JSON.stringify({}) }),
  dailyCheck: () => req('/api/daily-check', { method: 'POST' }),
  dailyPreview: () => req('/api/daily-check/preview'),
  requests: () => req('/api/requests'),
  requestDetail: (id) => req(`/api/requests/${id}`),
  award: (id) => req(`/api/negotiations/${id}/award`, { method: 'POST' }),
  stock: () => req('/api/stock'),
  holds: () => req('/api/holds'),
  payUrl: (poNumber) => `${BASE}/pay/${poNumber}`,
  releaseHold: (pid) => req(`/api/holds/${pid}`, { method: 'DELETE' }),
}

export function connectSocket(onEvent) {
  const session = getSession()
  if (!session) return null
  const ws = new WebSocket(`${WS}/ws?token=${session.token}`)
  ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)) } catch {} }
  return ws
}

export const money = (n) =>
  n == null ? '-' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 })
