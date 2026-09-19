"""
Sarvam AI — the seller talks, in whatever language they like.

WHO DOES THE TRANSLATING: Sarvam does, both ways. Our agent only ever sees
and writes English, so nothing in the buyer service changes.

  Seller speaks Tamil
      -> speech_to_text.transcribe(mode="translate")   ONE call: Saaras
         listens, works out the language itself, and hands back ENGLISH
      -> that English text goes into the chat box and on to the agent

  Agent replies in English
      -> text.translate(...)        Mayura turns it into their language
      -> text_to_speech.convert(...)  Bulbul reads it aloud
      -> mp3 plays in their browser

If SARVAM_API_KEY is missing the endpoints say so politely and the portal
falls back to typing, which is exactly how it works today.

Docs: https://docs.sarvam.ai/
"""

from __future__ import annotations

import base64

from app import config

# Bulbul (text-to-speech) covers these. The picker in the portal shows the
# same list, so we never ask it for a language it cannot read out.
LANGUAGES = [
    {"code": "en-IN", "label": "English",   "speaker": "anushka"},
    {"code": "hi-IN", "label": "हिन्दी",      "speaker": "shubh"},
    {"code": "bn-IN", "label": "বাংলা",       "speaker": "anushka"},
    {"code": "gu-IN", "label": "ગુજરાતી",     "speaker": "anushka"},
    {"code": "kn-IN", "label": "ಕನ್ನಡ",       "speaker": "anushka"},
    {"code": "ml-IN", "label": "മലയാളം",     "speaker": "anushka"},
    {"code": "mr-IN", "label": "मराठी",       "speaker": "shubh"},
    {"code": "od-IN", "label": "ଓଡ଼ିଆ",        "speaker": "anushka"},
    {"code": "pa-IN", "label": "ਪੰਜਾਬੀ",       "speaker": "shubh"},
    {"code": "ta-IN", "label": "தமிழ்",       "speaker": "anushka"},
    {"code": "te-IN", "label": "తెలుగు",       "speaker": "anushka"},
]

_BY_CODE = {l["code"]: l for l in LANGUAGES}

# What the browser's MediaRecorder actually produces, mapped to what Sarvam
# calls it.
_CODECS = {"webm": "webm", "ogg": "ogg", "mp4": "mp4", "mpeg": "mp3",
           "wav": "wav", "x-wav": "wav", "flac": "flac", "aac": "aac"}


def enabled() -> bool:
    return bool(config.SARVAM_API_KEY)


def _client():
    from sarvamai import AsyncSarvamAI
    return AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)


def _codec(content_type: str) -> str:
    """'audio/webm;codecs=opus' -> 'webm'"""
    sub = (content_type or "").split("/")[-1].split(";")[0].strip().lower()
    return _CODECS.get(sub, "webm")


# =============================================================================
# SPEECH -> ENGLISH TEXT
# =============================================================================

async def transcribe(audio: bytes, content_type: str = "audio/webm") -> dict:
    """
    Any Indian language in, English out, in a single API call.

    mode="translate" is what does it — Saaras transcribes and translates in
    one pass. language_code="unknown" lets it detect the language, which it
    reports back so the portal can reply in the same one.
    """
    client = _client()
    response = await client.speech_to_text.transcribe(
        file=("speech." + _codec(content_type), audio),
        model="saaras:v4",
        mode="translate",
        language_code="unknown",
        input_audio_codec=_codec(content_type),
    )
    detected = getattr(response, "language_code", None)
    return {
        "text": (response.transcript or "").strip(),
        "detected_language": detected if detected in _BY_CODE else None,
    }


# =============================================================================
# ENGLISH TEXT -> SPOKEN AUDIO
# =============================================================================

async def speak(text: str, language_code: str) -> bytes:
    """English in, mp3 of that sentence in their language out."""
    lang = _BY_CODE.get(language_code) or _BY_CODE["en-IN"]
    client = _client()

    spoken = text.strip()
    # Mayura only needs to run when we are not already in English.
    if lang["code"] != "en-IN":
        translation = await client.text.translate(
            input=spoken,
            source_language_code="en-IN",
            target_language_code=lang["code"],
            model="mayura:v1",
            mode="modern-colloquial",
        )
        spoken = translation.translated_text

    # Bulbul is happier with shorter passages; chat lines are short anyway.
    audio = await client.text_to_speech.convert(
        text=spoken[:1500],
        language_code=lang["code"],
        speaker=lang["speaker"],
        model="bulbul:v3",
        pace=1.0,
        output_audio_codec="mp3",
    )
    return b"".join(base64.b64decode(chunk) for chunk in audio.audios)


# =============================================================================
# TEXT -> TEXT (used to show the agent's message in their language too)
# =============================================================================

async def translate(text: str, language_code: str) -> str:
    if language_code == "en-IN" or language_code not in _BY_CODE:
        return text
    client = _client()
    response = await client.text.translate(
        input=text.strip(),
        source_language_code="en-IN",
        target_language_code=language_code,
        model="mayura:v1",
        mode="modern-colloquial",
    )
    return response.translated_text
