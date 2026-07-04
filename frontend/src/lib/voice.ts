// Voix — capture micro (STT) et lecture des réponses (TTS).
// Deux hooks autonomes : `useVoiceInput` enregistre puis transcrit via le gateway
// (qui proxifie le service `transcription`) ; `useSpeaker` lit du texte via MeloTTS.
// Le navigateur ne parle qu'au gateway — aucun service in-cluster n'est joint direct.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { currentLang } from './i18n'

export type RecState = 'idle' | 'recording' | 'transcribing'

// Enregistre le micro, transcrit à l'arrêt, puis pousse le texte à `onText`.
// Un seul bouton pilote tout le cycle (start → stop → transcribe).
export function useVoiceInput(onText: (text: string) => void) {
  const [state, setState] = useState<RecState>('idle')
  const [error, setError] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  // Démarre la capture : demande le micro, accumule les chunks audio.
  const start = useCallback(async () => {
    setError(false)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      // À l'arrêt : on coupe le micro, on assemble le blob, on transcrit.
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
        if (blob.size === 0) { setState('idle'); return }
        setState('transcribing')
        try {
          const { text } = await api.transcribe(blob)
          if (text) onText(text)
        } catch {
          setError(true)
        } finally {
          setState('idle')
        }
      }
      rec.start()
      recorderRef.current = rec
      setState('recording')
    } catch {
      setError(true)
      setState('idle')
    }
  }, [onText])

  // Arrête la capture → déclenche `onstop` (donc la transcription).
  const stop = useCallback(() => {
    recorderRef.current?.stop()
    recorderRef.current = null
  }, [])

  // Bascule micro on/off ; ignore les clics pendant la transcription.
  const toggle = useCallback(() => {
    if (state === 'recording') stop()
    else if (state === 'idle') start()
  }, [state, start, stop])

  return { state, error, toggle }
}

// Lit du texte à voix haute via le TTS du gateway. File d'attente : les réponses
// successives s'enchaînent sans se chevaucher. `cancel` coupe tout.
export function useSpeaker() {
  const [error, setError] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const queueRef = useRef<string[]>([])
  const playingRef = useRef(false)

  // Joue le prochain texte de la file, puis enchaîne.
  const drain = useCallback(async () => {
    if (playingRef.current) return
    const text = queueRef.current.shift()
    if (!text) return
    playingRef.current = true
    try {
      const lang = currentLang() === 'en' ? 'EN' : 'FR'
      const blob = await api.speak(text, lang)
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audioRef.current = audio
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve()
        audio.onerror = () => resolve()
        audio.play().catch(() => resolve())
      })
      URL.revokeObjectURL(url)
    } catch {
      setError(true)
      queueRef.current = []
    } finally {
      playingRef.current = false
      audioRef.current = null
      if (queueRef.current.length) void drain()
    }
  }, [])

  // Met un texte en file et lance la lecture si rien ne joue.
  const speak = useCallback((text: string) => {
    const t = text.trim()
    if (!t) return
    setError(false)
    queueRef.current.push(t)
    void drain()
  }, [drain])

  // Coupe la lecture en cours et vide la file.
  const cancel = useCallback(() => {
    queueRef.current = []
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null }
    playingRef.current = false
  }, [])

  // Sécurité : on coupe l'audio si le composant se démonte.
  useEffect(() => cancel, [cancel])

  return { speak, cancel, error }
}
