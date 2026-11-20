"""
FastAPI + WebSockets Backend Server for the QuantPilot Autonomous Crypto Trading Desk.
Serves REST API, real-time WebSocket stream (`/ws`), QuantPilot MCP OAuth2 login/callback endpoints,
and the dark-mode React + TradingView Lightweight Charts Command Center on `localhost:8000`.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import DESK_CONFIG
from backend.feeds.hyperliquid_stream import HyperliquidDataFeed
from backend.mcp.quantpilot_client import QuantPilotMCPClient
from backend.engine.paper_matching_engine import DualBookPaperMatchingEngine
from backend.agents.orchestrator import MultiAgentDeskOrchestrator


hl_feed = HyperliquidDataFeed()
mcp_client = QuantPilotMCPClient(hl_feed)
matching_engine = DualBookPaperMatchingEngine()
orchestrator = MultiAgentDeskOrchestrator(hl_feed, mcp_client, matching_engine)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, symbol: str = "BTC") -> None:
        await websocket.accept()
        self.active_connections[websocket] = symbol.upper()

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    def set_symbol(self, websocket: WebSocket, symbol: str) -> None:
        if websocket in self.active_connections:
            self.active_connections[websocket] = symbol.upper()

    async def broadcast_snapshots(self) -> None:
        for ws, sym in list(self.active_connections.items()):
            try:
                snap = orchestrator.get_desk_snapshot(selected_symbol=sym)
                await ws.send_json({"type": "DESK_SNAPSHOT", "data": snap})
            except Exception:
                self.disconnect(ws)


ws_manager = ConnectionManager()
_bg_tasks: List[asyncio.Task] = []


async def continuous_desk_loop() -> None:
    """24/7 autonomous multi-agent scan, cartography, backtest, and execution loop."""
    while True:
        try:
            await orchestrator.run_single_desk_cycle()
            await ws_manager.broadcast_snapshots()
        except Exception as exc:
            print(f"[DeskLoop] Cycle error: {exc}")
        await asyncio.sleep(10.0)


async def fast_mark_to_market_loop() -> None:
    """High-frequency 3-second mark-to-market price & lifecycle updater."""
    while True:
        try:
            if orchestrator.cycle_count > 0:
                await hl_feed.fetch_universe_and_contexts()
                flow_map = {
                    s: d.get("order_flow", {})
                    for s, d in orchestrator.symbol_dossiers.items()
                }
                orchestrator.engine.mark_to_market_and_manage_lifecycle(hl_feed.markets, flow_map)
                await ws_manager.broadcast_snapshots()
        except Exception:
            pass
        await asyncio.sleep(3.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await mcp_client.probe_mcp_server()
    await orchestrator.run_single_desk_cycle()
    t1 = asyncio.create_task(continuous_desk_loop())
    t2 = asyncio.create_task(fast_mark_to_market_loop())
    _bg_tasks.extend([t1, t2])
    yield
    for t in _bg_tasks:
        t.cancel()
    await hl_feed.close()


app = FastAPI(
    title="QuantPilot Autonomous Crypto Trading Desk",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModeChangeRequest(BaseModel):
    mode: str  # "AUTO_EXECUTE" | "PAUSE" | "KILL_SWITCH"


class MCPTokenRequest(BaseModel):
    token: str
    refresh_token: Optional[str] = ""


class MCPLoginUrlRequest(BaseModel):
    redirect_uri: Optional[str] = "http://localhost:8000/api/mcp/oauth/callback"
    client_id: Optional[str] = "https://chatgpt.com/.well-known/oauth-client.json"


class MCPToolCallRequest(BaseModel):
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None


@app.get("/api/snapshot")
async def get_snapshot(symbol: str = Query("BTC")) -> Dict[str, Any]:
    sym = symbol.upper()
    if sym not in orchestrator.symbol_dossiers and sym in hl_feed.markets:
        await orchestrator.run_agent_2_cartographer(hl_feed.markets[sym])
    return orchestrator.get_desk_snapshot(selected_symbol=sym)


@app.get("/api/dossier/{symbol}")
async def get_symbol_dossier(symbol: str, interval: str = Query("15m")) -> Dict[str, Any]:
    sym = symbol.upper()
    if not hl_feed.markets:
        await hl_feed.fetch_universe_and_contexts()
    if sym not in hl_feed.markets:
        raise HTTPException(status_code=404, detail=f"Symbol {sym} not found on Hyperliquid")

    dossier = await orchestrator.run_agent_2_cartographer(hl_feed.markets[sym])
    if interval in ("1m", "5m", "15m", "1h", "4h", "1d"):
        candles = await hl_feed.fetch_candles(sym, interval=interval, lookback_bars=140)
        dossier["active_chart_interval"] = interval
        dossier["active_chart_candles"] = candles
    return dossier


@app.post("/api/mode")
async def set_execution_mode(req: ModeChangeRequest) -> Dict[str, Any]:
    mode = req.mode.upper()
    if mode not in ("AUTO_EXECUTE", "PAUSE", "KILL_SWITCH"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use AUTO_EXECUTE, PAUSE, or KILL_SWITCH.")

    matching_engine.execution_mode = mode
    if mode == "AUTO_EXECUTE":
        matching_engine.circuit_breaker_tripped = False
        matching_engine.circuit_breaker_reason = ""
        matching_engine.day_high_equity_usd = matching_engine.get_current_equity()
        orchestrator._emit_consensus_event(
            "agent_5_cro",
            "Chief Risk Officer (Sentinel)",
            "DESK",
            "SYSTEM_CONTROL",
            100.0,
            "AUTO_EXECUTE_ENABLED",
            "Master execution switch set to AUTO-EXECUTE. Autonomous 24/7 paper trading active.",
        )
    elif mode == "PAUSE":
        orchestrator._emit_consensus_event(
            "agent_5_cro",
            "Chief Risk Officer (Sentinel)",
            "DESK",
            "SYSTEM_CONTROL",
            100.0,
            "DESK_PAUSED",
            "Master execution switch set to PAUSE. Existing positions managed; new entries halted.",
        )
    elif mode == "KILL_SWITCH":
        closed = matching_engine.close_all_positions(hl_feed.markets, reason="MANUAL_KILL_SWITCH")
        orchestrator._emit_consensus_event(
            "agent_5_cro",
            "Chief Risk Officer (Sentinel)",
            "DESK",
            "SYSTEM_CONTROL",
            100.0,
            "KILL_SWITCH_ENGAGED",
            f"EMERGENCY KILL-SWITCH ENGAGED: Flattened {len(closed)} active positions at market and halted new entries.",
        )

    await ws_manager.broadcast_snapshots()
    return {"ok": True, "execution_mode": matching_engine.execution_mode, "ledger": matching_engine.get_ledger_state()}


@app.post("/api/positions/{position_id}/close")
async def close_single_position(position_id: str) -> Dict[str, Any]:
    pos = matching_engine.active_positions.get(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found or already closed.")
    sym = pos["symbol"]
    px = float(hl_feed.markets.get(sym, {}).get("mark_px", pos["current_px"]))
    ev = matching_engine._close_position(position_id, px, "MANUAL_DESK_CLOSE", is_maker=False)
    await ws_manager.broadcast_snapshots()
    return {"ok": True, "event": ev, "ledger": matching_engine.get_ledger_state()}


@app.post("/api/cycle/trigger")
async def trigger_manual_cycle() -> Dict[str, Any]:
    snap = await orchestrator.run_single_desk_cycle()
    await ws_manager.broadcast_snapshots()
    return snap


@app.post("/api/ledger/reset")
async def reset_ledger_endpoint() -> Dict[str, Any]:
    matching_engine.reset_paper_ledger()
    orchestrator._emit_consensus_event(
        "agent_5_cro",
        "Chief Risk Officer (Sentinel)",
        "DESK",
        "SYSTEM_CONTROL",
        100.0,
        "LEDGER_RESET_$500",
        "Paper ledger reset to clean $500.00 starting balance (6 dynamic slots unlocked). Re-evaluating live market confluence.",
    )
    snap = await orchestrator.run_single_desk_cycle()
    await ws_manager.broadcast_snapshots()
    return snap


# =============================================================================
# QuantPilot MCP Authentication & Interactive Tool Endpoints
# =============================================================================
@app.get("/api/mcp/status")
async def get_mcp_status() -> Dict[str, Any]:
    probe = await mcp_client.probe_mcp_server()
    return {
        "probe": probe,
        "auth_mode": mcp_client.auth_mode,
        "remote_connected": mcp_client.remote_connected,
        "tools": mcp_client.remote_tools_discovered,
        "recent_calls": mcp_client.mcp_call_log[:25],
    }


@app.post("/api/mcp/login-url")
async def create_mcp_login_url(req: MCPLoginUrlRequest) -> Dict[str, Any]:
    return mcp_client.generate_oauth_login_url(
        redirect_uri=req.redirect_uri or "http://localhost:8000/api/mcp/oauth/callback",
        client_id=req.client_id or "https://chatgpt.com/.well-known/oauth-client.json",
    )


@app.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
async def mcp_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
) -> HTMLResponse:
    if error:
        html = f"""<!DOCTYPE html>
<html><body style="background:#0a0e17;color:#f8fafc;font-family:sans-serif;padding:32px;">
<h3>QuantPilot OAuth Notice: {error}</h3>
<p>{error_description or ''}</p>
<p>You can close this window and paste your QuantPilot Bearer token directly in the Command Center MCP modal.</p>
<script>
if (window.opener) {{
    window.opener.postMessage({{ type: "QUANTPILOT_MCP_AUTH_ERROR", error: "{error}" }}, "*");
}}
</script>
</body></html>"""
        return HTMLResponse(content=html)

    if code:
        res = await mcp_client.exchange_oauth_code(code=code, state=state or "")
        await ws_manager.broadcast_snapshots()
        status_txt = "AUTHENTICATED" if res.get("ok") else f"Token Exchange Response ({res.get('error', '')})"
        html = f"""<!DOCTYPE html>
<html><body style="background:#0a0e17;color:#10b981;font-family:sans-serif;padding:32px;">
<h3>QuantPilot MCP Connection: {status_txt}</h3>
<p>Returning to Desk Command Center...</p>
<script>
if (window.opener) {{
    window.opener.postMessage({{ type: "QUANTPILOT_MCP_AUTH_DONE", ok: {str(bool(res.get('ok'))).lower()} }}, "*");
    setTimeout(() => window.close(), 1200);
}}
</script>
</body></html>"""
        return HTMLResponse(content=html)

    return HTMLResponse(content="<html><body>Missing OAuth code parameter.</body></html>")


@app.post("/api/mcp/token")
async def set_mcp_bearer_token(req: MCPTokenRequest) -> Dict[str, Any]:
    probe = await mcp_client.set_bearer_token(req.token, req.refresh_token or "")
    await ws_manager.broadcast_snapshots()
    return {
        "ok": probe.get("authenticated", False),
        "probe": probe,
        "auth_mode": mcp_client.auth_mode,
    }


@app.post("/api/mcp/call-tool")
async def call_mcp_tool_endpoint(req: MCPToolCallRequest) -> Dict[str, Any]:
    args = req.arguments or {}
    t = req.tool_name
    if t == "List markets":
        return await mcp_client.list_markets(min_volume_usd=float(args.get("min_volume_usd", DESK_CONFIG.min_24h_volume_usd)))
    elif t == "Search symbols":
        return await mcp_client.search_symbols(query=str(args.get("query", "BTC")))
    elif t == "QuantScript v2 docs":
        return await mcp_client.get_quantscript_v2_docs()
    elif t == "Validate a strategy":
        sym = str(args.get("symbol", "BTC")).upper()
        dossier = orchestrator.symbol_dossiers.get(sym) or next(iter(orchestrator.symbol_dossiers.values()), {})
        script = str(args.get("script") or dossier.get("quantscript_v2") or "")
        return await mcp_client.validate_strategy(script)
    elif t == "Run a backtest":
        sym = str(args.get("symbol", "BTC")).upper()
        dossier = orchestrator.symbol_dossiers.get(sym)
        if not dossier and sym in hl_feed.markets:
            dossier = await orchestrator.run_agent_2_cartographer(hl_feed.markets[sym])
        if not dossier:
            raise HTTPException(status_code=404, detail=f"Symbol {sym} not loaded")
        bt = await orchestrator.run_agent_3_researcher(dossier)
        return bt
    elif t == "Get backtest orders":
        bt_id = str(args.get("backtest_id", ""))
        if not bt_id and orchestrator.drift_telemetry["recent_backtests"]:
            bt_id = orchestrator.drift_telemetry["recent_backtests"][0]["backtest_id"]
        return await mcp_client.get_backtest_orders(bt_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown QuantPilot MCP tool: {t}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket, symbol="BTC")
    try:
        # Send immediate initial snapshot
        await websocket.send_json(
            {"type": "DESK_SNAPSHOT", "data": orchestrator.get_desk_snapshot(selected_symbol="BTC")}
        )
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")
            if action == "SELECT_SYMBOL":
                sym = str(msg.get("symbol", "BTC")).upper()
                ws_manager.set_symbol(websocket, sym)
                if sym not in orchestrator.symbol_dossiers and sym in hl_feed.markets:
                    await orchestrator.run_agent_2_cartographer(hl_feed.markets[sym])
                await websocket.send_json(
                    {"type": "DESK_SNAPSHOT", "data": orchestrator.get_desk_snapshot(selected_symbol=sym)}
                )
            elif action == "PING":
                await websocket.send_json({"type": "PONG", "timestamp": time.time()})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# Serve React frontend build if available
FRONTEND_DIST = "/home/antigravity/quantpilot_desk/frontend/dist"
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = os.path.join(FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
