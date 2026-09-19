"""
BUYER SERVICE — the AI side.

    uvicorn app.main:app --reload --port 8001

Owns: STN/LTN engines, holds, recommendations, approval, RFQ creation,
the negotiation agent, purchase orders. Sellers never touch this service.
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import Depends, FastAPI, Form, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import agent, auth, config, daily, holds, ltn, payments, stn
from app.db import close_pool, get_pool
from app.ws import hub

app = FastAPI(title="WKC Buyer Service", version="1.0")

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


class ApproveBody(BaseModel):
    quantity: int | None = None
    urgency: str = "normal"


class RejectBody(BaseModel):
    note: str = ""


class RunBody(BaseModel):
    as_of: str | None = None


class PushBody(BaseModel):
    channel: str
    event: str
    data: dict


class QuotesCompleteBody(BaseModel):
    request_id: str


class SellerRepliedBody(BaseModel):
    negotiation_id: str


# =============================================================================
# PUBLIC
# =============================================================================

@app.get("/api/health")
async def health():
    pool = await get_pool()
    return {"service": "buyer", "ok": await pool.fetchval("SELECT 1") == 1}


@app.post("/api/login")
async def login(body: LoginBody):
    pool = await get_pool()
    return await auth.login(pool, body.username, body.password)


# =============================================================================
# ANALYSIS AND APPROVAL
# =============================================================================

@app.get("/api/recommendations")
async def recommendations(status: str = "pending_approval",
                          _: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await agent.list_recommendations(pool, status)


@app.post("/api/recommendations/{result_id}/approve")
async def approve(result_id: str, body: ApproveBody,
                  user: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await agent.approve(pool, result_id, body.quantity, user["uid"], body.urgency)


@app.post("/api/recommendations/{result_id}/reject")
async def reject(result_id: str, body: RejectBody,
                 user: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await agent.reject(pool, result_id, body.note, user["uid"])


@app.post("/api/run-stn")
async def run_stn(body: RunBody, user: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    as_of = date.fromisoformat(body.as_of) if body.as_of else config.SIMULATED_TODAY

    async def progress(pct: int, r: dict):
        await hub.send("buyer", "analysis.progress", {
            "pct": pct, "product_name": r.get("product_name"),
            "skipped": r.get("skipped", False),
            "quantity": r.get("recommended_quantity"),
            "hold_days": r.get("hold_days")})

    async def job():
        try:
            run_id = await stn.run(pool, as_of=as_of, triggered_by=user["uid"],
                                   on_progress=progress)
            await hub.send("buyer", "analysis.completed", {"run_id": run_id})
        except Exception as exc:
            await hub.send("buyer", "analysis.failed", {"error": str(exc)})

    asyncio.create_task(job())
    return {"started": True, "as_of": as_of.isoformat()}


@app.post("/api/run-ltn")
async def run_ltn(body: RunBody, user: dict = Depends(auth.current_buyer)):
    """Long-term agent: market research via Bright Data, then a decision."""
    pool = await get_pool()
    as_of = date.fromisoformat(body.as_of) if body.as_of else config.SIMULATED_TODAY

    async def progress(pct: int, r: dict):
        await hub.send("buyer", "analysis.progress", {
            "pct": pct, "engine": "ltn", "product_name": r.get("product_name"),
            "skipped": r.get("skipped", False),
            "quantity": r.get("recommended_quantity"),
            "sources": r.get("source_count", 0)})

    async def job():
        try:
            run_id = await ltn.run(pool, as_of=as_of, triggered_by=user["uid"],
                                   on_progress=progress)
            await hub.send("buyer", "analysis.completed",
                           {"run_id": run_id, "engine": "ltn"})
        except Exception as exc:
            await hub.send("buyer", "analysis.failed", {"error": str(exc)})

    asyncio.create_task(job())
    return {"started": True, "engine": "ltn", "as_of": as_of.isoformat()}


@app.post("/api/daily-check")
async def daily_check(_: dict = Depends(auth.current_buyer)):
    """Arithmetic safety net. Overrides holds and orders when stock runs low."""
    pool = await get_pool()
    return await daily.check(pool, place_orders=True, created_by="daily-check")


@app.get("/api/stock")
async def stock(_: dict = Depends(auth.current_buyer)):
    """Every product with its stock, sales rate and runway. Read-only."""
    pool = await get_pool()
    summary = await daily.check(pool, place_orders=False)
    return summary["findings"]


@app.get("/api/daily-check/preview")
async def daily_check_preview(_: dict = Depends(auth.current_buyer)):
    """Same checks, but places nothing."""
    pool = await get_pool()
    return await daily.check(pool, place_orders=False)


# =============================================================================
# PROCUREMENT
# =============================================================================

@app.get("/api/requests")
async def requests(_: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await agent.list_requests(pool)


@app.get("/api/requests/{request_id}")
async def request_detail(request_id: str, _: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await agent.request_detail(pool, request_id)


@app.post("/api/negotiations/{negotiation_id}/award")
async def award(negotiation_id: str, user: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await agent.award(pool, negotiation_id, user["uid"])


@app.get("/api/holds")
async def list_holds(_: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return await holds.list_holds(pool)


@app.delete("/api/holds/{product_id}")
async def release_hold(product_id: int, _: dict = Depends(auth.current_buyer)):
    pool = await get_pool()
    return {"released": await holds.release_hold(pool, product_id)}


# =============================================================================
# PAYMENT — public page, meant to be opened on a phone and scanned
# =============================================================================

@app.get("/pay/{po_number}", response_class=HTMLResponse)
async def pay_page(po_number: str):
    pool = await get_pool()
    data = await payments.get_or_create(pool, po_number)
    return payments.page_html(data["payment"], data["po"])


@app.post("/pay/{po_number}/paid", response_class=HTMLResponse)
async def pay_confirm(po_number: str, utr: str = Form(default="")):
    pool = await get_pool()
    await payments.mark_paid(pool, po_number, "payer", utr)
    data = await payments.get_or_create(pool, po_number)
    return payments.page_html(data["payment"], data["po"])


@app.get("/api/payments/{po_number}")
async def payment_status(po_number: str):
    pool = await get_pool()
    data = await payments.get_or_create(pool, po_number)
    return {"payment": data["payment"], "po": data["po"],
            "pay_url": "/pay/" + po_number}


# =============================================================================
# INTERNAL — called by the seller service only
# =============================================================================

@app.post("/internal/quotes-complete")
async def internal_quotes_complete(body: QuotesCompleteBody,
                                   _: bool = Depends(auth.check_internal)):
    """Every seller has answered -> the agent opens a negotiation."""
    pool = await get_pool()
    started = await agent.open_negotiations(pool, body.request_id)
    return {"negotiations_started": len(started), "sellers": started}


@app.post("/internal/seller-replied")
async def internal_seller_replied(body: SellerRepliedBody,
                                  _: bool = Depends(auth.check_internal)):
    """A seller posted a message -> the agent decides what to say back."""
    pool = await get_pool()
    return await agent.respond_to_seller(pool, body.negotiation_id)


@app.post("/internal/push")
async def internal_push(body: PushBody, _: bool = Depends(auth.check_internal)):
    """The seller service asking us to deliver an event to the buyer console."""
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
    if user.get("role") != "buyer":
        await ws.close(code=4003)
        return

    await ws.accept()
    await hub.join("buyer", ws)
    await ws.send_json({"event": "connected", "channel": "buyer",
                        "data": {"service": "buyer"}})
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("action") == "ping":
                await ws.send_json({"event": "pong", "channel": "buyer", "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave_all(ws)
