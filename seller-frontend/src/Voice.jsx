import { useEffect, useRef, useState } from 'react'
import { api, getLanguage, setLanguage } from './api'

/**
 * Two buttons and a picker, all talking to Sarvam AI through our backend.
 *
 *   MicButton    hold the mic, speak Tamil, get English text back
 *   SpeakButton  play the agent's message out loud in your language
 *   LanguagePicker  which language "your language" means
 */

// ---------------------------------------------------------------------------

export function useLanguages() {
  const [state, setState] = useState({ enabled: false, languages: [] })
  useEffect(() => {
    api.voiceLanguages().then(setState).catch(() => {})
  }, [])
  return state
}

export function LanguagePicker({ languages, value, onChange }) {
  if (!languages.length) return null
  return (
    <select className="lang-picker" value={value}
            onChange={(e) => { setLanguage(e.target.value); onChange(e.target.value) }}
            title="Speak and listen in this language">
      {languages.map((l) => (
        <option key={l.code} value={l.code}>{l.label}</option>
      ))}
    </select>
  )
}

// ---------------------------------------------------------------------------

const MAX_SECONDS = 60

export function MicButton({ onText, disabled }) {
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [secs, setSecs] = useState(0)
  const [err, setErr] = useState('')
  const recorder = useRef(null)
  const chunks = useRef([])
  const ticker = useRef(null)

  const stop = () => {
    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.stop()
    }
  }

  // count up while recording, and stop on its own so nothing runs forever
  useEffect(() => {
    if (!recording) {
      clearInterval(ticker.current)
      setSecs(0)
      return
    }
    ticker.current = setInterval(() => {
      setSecs((s) => {
        if (s + 1 >= MAX_SECONDS) stop()
        return s + 1
      })
    }, 1000)
    return () => clearInterval(ticker.current)
  }, [recording])

  const start = async () => {
    setErr('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunks.current = []
      mr.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        const blob = new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' })
        if (blob.size < 1200) return               // a stray tap, not speech
        setBusy(true)
        try {
          const out = await api.transcribe(blob)
          if (out.text) onText(out.text, out.detected_language)
          else setErr('Nothing was picked up — try again')
        } catch (e) { setErr(e.message) } finally { setBusy(false) }
      }
      recorder.current = mr
      mr.start()
      setRecording(true)
    } catch {
      setErr('Microphone blocked')
    }
  }

  const mmss = `0:${String(secs).padStart(2, '0')}`

  // While recording, the round button becomes the stop, AND there is a plain
  // labelled button next to it. No guessing which thing ends the recording.
  return (
    <div className="mic-wrap">
      <button type="button"
              className={'mic' + (recording ? ' rec' : '')}
              disabled={disabled || busy}
              onClick={recording ? stop : start}
              title={recording ? 'Stop and send' : 'Speak in any language'}>
        {busy ? '…' : recording ? '⏹' : '🎤'}
      </button>

      {recording && (
        <button type="button" className="mic-stop" onClick={stop}>
          Stop &amp; send · {mmss}
        </button>
      )}
      {busy && <span className="faint">Transcribing…</span>}
      {err && <span className="faint">{err}</span>}
    </div>
  )
}

// ---------------------------------------------------------------------------

export function SpeakButton({ text, language }) {
  const [busy, setBusy] = useState(false)
  const audio = useRef(null)

  const play = async () => {
    if (audio.current) { audio.current.pause(); audio.current = null }
    setBusy(true)
    try {
      const url = await api.speak(text, language || getLanguage())
      const el = new Audio(url)
      audio.current = el
      el.onended = () => { audio.current = null }
      await el.play()
    } catch { /* voice off or Sarvam hiccup — silence is fine here */ }
    finally { setBusy(false) }
  }

  return (
    <button type="button" className="speak" onClick={play} disabled={busy}
            title="Listen in your language">
      {busy ? '…' : '🔊'}
    </button>
  )
}
