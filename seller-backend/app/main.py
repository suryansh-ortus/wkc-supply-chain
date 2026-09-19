"""
SELLER SERVICE — the supplier side.

    uvicorn app.main:app --reload --port 8002

Owns: seller login, their requests, quotes, negotiation chat. It runs no
agent and reads no analysis tables. When a seller acts, it calls the buyer
service and the agent works there.
"""

from __future__ import annotations

from fastapi import (Depends, FastAPI, File, HTTPException, Query, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from app import auth, config, seller, voice
from app.db import close_pool, get_pool
from app.ws import hub

app = FastAPI(title="WKC Seller Service", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def _shutdown():
    await close_pool()


class LoginBody(BaseModel):
    username: str
    password: str


class QuoteBody(BaseModel):
    invitation_id: str
    response: str
    quoted_price: float | None = None
    expected_delivery_days: int | None = None
    notes: str = ""


class MessageBody(BaseModel):
    message: str
    counter_price: float | None = None
    counter_delivery: int | None = None


class PushBody(BaseModel):
    channel: str
    event: str
    data: dict


class SpeakBody(BaseModel):
    text: str
    language_code: str = "en-IN"


# =============================================================================
# PUBLIC
# =============================================================================

@app.get("/api/health")
async def health():
    pool = await get_pool()
    return {"service": "seller", "ok": await pool.fetchval("SELECT 1") == 1}


@app.get("/api/accounts")
async def accounts():
    """Demo login list - seller emails for the dropdown."""
    pool = await get_pool()
    return await seller.demo_accounts(pool)


@app.post("/api/login")
async def login(body: LoginBody):
    pool = await get_pool()
    return await auth.login(pool, body.username, body.password)


@app.get("/api/me")
async def me(user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.profile(pool, user["seller_id"])


# =============================================================================
# REQUESTS AND QUOTES
# =============================================================================

@app.get("/api/requests")
async def requests(user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.open_requests(pool, user["seller_id"])


@app.post("/api/requests/{invitation_id}/viewed")
async def viewed(invitation_id: str, user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    await seller.mark_viewed(pool, user["seller_id"], invitation_id)
    return {"ok": True}


@app.post("/api/quotes")
async def submit_quote(body: QuoteBody, user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.submit_quote(
        pool, user["seller_id"], body.invitation_id, body.response,
        body.quoted_price, body.expected_delivery_days, body.notes)


@app.get("/api/history")
async def history(user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.history(pool, user["seller_id"])


# =============================================================================
# NEGOTIATION
# =============================================================================

@app.get("/api/negotiations")
async def negotiations(user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.negotiations(pool, user["seller_id"])


@app.get("/api/negotiations/{negotiation_id}")
async def negotiation(negotiation_id: str, user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.negotiation_detail(pool, user["seller_id"], negotiation_id)


@app.post("/api/negotiations/{negotiation_id}/messages")
async def send_message(negotiation_id: str, body: MessageBody,
                       user: dict = Depends(auth.current_seller)):
    pool = await get_pool()
    return await seller.send_message(
        pool, user["seller_id"], negotiation_id, body.message,
        body.counter_price, body.counter_delivery)


# =============================================================================
# VOICE — Sarvam AI. Speak in any language; the agent still reads English.
# =============================================================================

@app.get("/api/voice/languages")
async def voice_languages():
    """Public — the portal needs this before anyone logs in."""
    return {"enabled": voice.enabled(), "languages": voice.LANGUAGES}


@app.post("/api/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...),
                           _: dict = Depends(auth.current_seller)):
    """Recording in (any language), English text out."""
    if not voice.enabled():
        raise HTTPException(503, "Voice is off — set SARVAM_API_KEY to enable it.")
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty recording")
    try:
        return await voice.transcribe(data, audio.content_type or "audio/webm")
    except Exception as exc:
        raise HTTPException(502, f"Sarvam transcribe failed: {exc}")


@app.post("/api/voice/speak")
async def voice_speak(body: SpeakBody, _: dict = Depends(auth.current_seller)):
    """English text in, mp3 of it spoken in their language out."""
    if not voice.enabled():
        raise HTTPException(503, "Voice is off — set SARVAM_API_KEY to enable it.")
    if not body.text.strip():
        raise HTTPException(400, "Nothing to say")
    try:
        mp3 = await voice.speak(body.text, body.language_code)
    except Exception as exc:
        raise HTTPException(502, f"Sarvam speak failed: {exc}")
    return Response(content=mp3, media_type="audio/mpeg")


@app.post("/api/voice/translate")
async def voice_translate(body: SpeakBody, _: dict = Depends(auth.current_seller)):
    """Show the agent's English message in their language, as text."""
    if not voice.enabled():
        return {"text": body.text}
    try:
        return {"text": await voice.translate(body.text, body.language_code)}
    except Exception:
        return {"text": body.text}


# =============================================================================
# INTERNAL — called by the buyer service only
# =============================================================================

@app.post("/internal/push")
async def internal_push(body: PushBody, _: bool = Depends(auth.check_internal)):
    """The agent asking us to deliver an event to a seller's browser."""
    await hub.send(body.channel, body.event, body.data)
    return {"delivered": True}


# =============================================================================
# WEBSOCKET
# =============================================================================

@app.websocket("/ws")
async def websocket(ws: WebSocket, token: str = Query(...)):
    try:
        user = auth.read_token(token)
    except Exception:
        await ws.close(code=4001)
        return
    if user.get("role") != "seller":
        await ws.close(code=4003)
        return

    channel = f"seller:{user['seller_id']}"
    await ws.accept()
    await hub.join(channel, ws)
    await ws.send_json({"event": "connected", "channel": channel,
                        "data": {"service": "seller"}})
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("action") == "join" and \
               str(msg.get("channel", "")).startswith("negotiation:"):
                await hub.join(msg["channel"], ws)
                await ws.send_json({"event": "joined", "channel": msg["channel"],
                                    "data": {}})
            elif msg.get("action") == "ping":
                await ws.send_json({"event": "pong", "channel": channel, "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave_all(ws)
