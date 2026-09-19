const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8002'
const WS = BASE.replace(/^http/, 'ws')

export function getSession() {
  try { return JSON.parse(localStorage.getItem('wkc_seller') || 'null') } catch { return null }
}
export function setSession(s) { localStorage.setItem('wkc_seller', JSON.stringify(s)) }
export function clearSession() { localStorage.removeItem('wkc_seller') }

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
  accounts: () => req('/api/accounts'),
  login: (username, password) =>
    req('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  me: () => req('/api/me'),
  requests: () => req('/api/requests'),
  submitQuote: (body) =>
    req('/api/quotes', { method: 'POST', body: JSON.stringify(body) }),
  negotiations: () => req('/api/negotiations'),
  negotiation: (id) => req(`/api/negotiations/${id}`),
  send: (id, body) =>
    req(`/api/negotiations/${id}/messages`, { method: 'POST', body: JSON.stringify(body) }),
  history: () => req('/api/history'),

  // --- voice (Sarvam AI) ----------------------------------------------------
  voiceLanguages: () => req('/api/voice/languages'),

  // Recording in (any language) -> English text out.
  transcribe: async (blob) => {
    const session = getSession()
    const form = new FormData()
    form.append('audio', blob, 'speech.webm')
    const res = await fetch(BASE + '/api/voice/transcribe', {
      method: 'POST',
      headers: session ? { Authorization: `Bearer ${session.token}` } : {},
      body: form,                       // no Content-Type: the browser sets the boundary
    })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail || detail } catch {}
      throw new Error(detail)
    }
    return res.json()
  },

  // English text in -> mp3 spoken in their language out.
  speak: async (text, language_code) => {
    const session = getSession()
    const res = await fetch(BASE + '/api/voice/speak', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
      },
      body: JSON.stringify({ text, language_code }),
    })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail || detail } catch {}
      throw new Error(detail)
    }
    return URL.createObjectURL(await res.blob())
  },

  translate: (text, language_code) =>
    req('/api/voice/translate', {
      method: 'POST', body: JSON.stringify({ text, language_code }),
    }),
}

export function getLanguage() {
  return localStorage.getItem('wkc_lang') || 'en-IN'
}
export function setLanguage(code) {
  localStorage.setItem('wkc_lang', code)
}

export function connectSocket(onEvent) {
  const session = getSession()
  if (!session) return null
  const ws = new WebSocket(`${WS}/ws?token=${session.token}`)
  ws.onmessage = (e) => { try { onEvent(JSON.parse(e.data)) } catch {} }
  return ws
}

export const PAY_BASE = import.meta.env.VITE_PAY_URL || 'http://localhost:8001'
export const payUrl = (poNumber) => `${PAY_BASE}/pay/${poNumber}`

export const money = (n) =>
  n == null ? '-' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 })
