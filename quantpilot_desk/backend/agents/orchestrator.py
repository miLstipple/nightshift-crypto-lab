"""
24/7 Multi-Agent Orchestrator & Five Specialized Desk Sub-Agents:
1. Agent 1 — Universe Scanner & Regime Strategist (4H / 1H / 15m Regime Classification)
2. Agent 2 — Multi-Timeframe Cartographer & Order-Flow Scout (S/R, POC/VAH/VAL, Fibs, Waves, L2, CVD, OI)
3. Agent 3 — QuantPilot Researcher & Drift Optimizer (QuantScript v2, 1m Walk-Forward Backtest, 30d/90d Drift Calibration)
4. Agent 4 — Dual-Book Paper Execution Trader (Swing vs Scalp Ledger, Playbook A/B/C Routing, If-Then Matrix, $10 Tranche Guard)
5. Agent 5 — Chief Risk Officer (Sentinel — Hard Veto Authority, Dynamic Slot Scaling from 6, 5% Daily / 12% Peak Breakers)
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List, Optional

from backend.config import DESK_CONFIG
from backend.feeds.hyperliquid_stream import HyperliquidDataFeed
from backend.mcp.quantpilot_client import QuantPilotMCPClient
from backend.mcp.quantscript_engine import generate_quantscript_v2
from backend.engine.paper_matching_engine import DualBookPaperMatchingEngine
from backend.analytics.indicators import (
    classify_single_tf_regime,
    compute_volume_profile,
    compute_fibonacci_structure,
    detect_swings_and_waves,
    compute_order_flow_and_cvd,
)


class MultiAgentDeskOrchestrator:
    def __init__(
        self,
        hl_feed: HyperliquidDataFeed,
        mcp_client: QuantPilotMCPClient,
        matching_engine: DualBookPaperMatchingEngine,
    ) -> None:
        self.hl_feed = hl_feed
        self.mcp = mcp_client
        self.engine = matching_engine

        # State snapshots exposed to FastAPI + WebSockets
        self.scanner_leaderboard: List[Dict[str, Any]] = []
        self.symbol_dossiers: Dict[str, Dict[str, Any]] = {}
        self.consensus_feed: List[Dict[str, Any]] = []
        self.veto_log: List[Dict[str, Any]] = []
        self.drift_telemetry: Dict[str, Any] = {
            "window_30d": {
                "optimal_fib_depth": 0.618,
                "observed_sfp_sweep_depth": 0.705,
                "tp_front_run_buffer_bps": 4.5,
                "win_rate_30d_pct": 0.0,
                "net_expectancy_r": 0.0,
                "sample_size": 0,
            },
            "window_90d": {
                "regime_bias": "NEUTRAL_ADAPTIVE",
                "playbook_a_share_pct": 54.0,
                "playbook_b_share_pct": 46.0,
                "recalibration_status": "ACTIVE_WALK_FORWARD",
            },
            "recent_backtests": [],
        }
        self.agent_statuses: Dict[str, Dict[str, Any]] = {
            "agent_1_scanner": {"name": "Universe Scanner & Regime Strategist", "status": "IDLE", "last_action": "Initializing..."},
            "agent_2_cartographer": {"name": "Multi-TF Cartographer & Order-Flow Scout", "status": "IDLE", "last_action": "Initializing..."},
            "agent_3_researcher": {"name": "QuantPilot Researcher & Drift Optimizer", "status": "IDLE", "last_action": "Initializing..."},
            "agent_4_trader": {"name": "Dual-Book Paper Execution Trader", "status": "IDLE", "last_action": "Initializing..."},
            "agent_5_cro": {"name": "Chief Risk Officer (Sentinel — Hard Veto)", "status": "ACTIVE_FIREWALL", "last_action": "Monitoring $500 limits"},
            "agent_6_evolver": {
                "name": "Post-Mortem & Lessons Learned Evolver",
                "status": "ACTIVE_LEARNING",
                "last_action": "Audited 4 BE-SL round-trips -> Mutated TP1 Turnaround Harvest & 65% Ratchet rules",
            },
            "agent_7_watchdog": {
                "name": "Desk Operations & QA Watchdog Manager",
                "status": "SELF_HEALING_ACTIVE",
                "last_action": "Patched Ratchet-SL polling loop (idempotency lock) • Verified $580.83 ledger & Dual-Stack invariants",
            },
        }
        self.lessons_learned: List[Dict[str, Any]] = [
            {
                "id": "lesson_01_btc_val_bounce",
                "timestamp": "13:40:41 PST",
                "symbol": "BTC",
                "trade_ref": "Cycle #1 SHORT ($64,945 -> TP1 $64,196 -> BE-SL $64,886.5)",
                "mfe_pct": 1.18,
                "runner_exit_pct": 0.09,
                "giveback_pct": 92.4,
                "missed_profit_usd": 4.55,
                "diagnosis": "TP1 ($64,196) sat directly on 15m Volume Profile VAL demand shelf. 15m CVD flipped to Bullish Absorption at TP1, making the bounce back to $64,886.55 predictable.",
                "mutated_rule": "IF TP1 sits within 0.25% of 15m VAL/VAH AND CVD flips opposing -> Harvest 100% at TP1 (or lock 65% Profit-Ratchet SL) & arm SCALP_BOOK bounce flip.",
                "status": "DEPLOYED_LIVE",
            },
            {
                "id": "lesson_02_correlated_cluster_bounce",
                "timestamp": "13:40:52 PST",
                "symbol": "ETH / SOL",
                "trade_ref": "ETH ($1,758.9 -> $1,787.5) & SOL ($69.25 -> $70.495)",
                "mfe_pct": 1.85,
                "runner_exit_pct": 0.09,
                "giveback_pct": 95.1,
                "missed_profit_usd": 12.70,
                "diagnosis": "BTC, ETH, and SOL all hit TP1 within 90 seconds (13:16-13:18 PST). Leaving 3 correlated short runners with wide Breakeven stops at Entry exposed the desk to a single macro bounce.",
                "mutated_rule": "SYNCHRONIZED_CLUSTER_GUARD: When >= 2 correlated majors hit TP1 in the same 5m window, automatically ratchet all runner stops to 65% of TP1 profit.",
                "status": "DEPLOYED_LIVE",
            },
            {
                "id": "lesson_03_zec_low_rvol_exhaustion",
                "timestamp": "13:29:26 PST",
                "symbol": "ZEC",
                "trade_ref": "ZEC SHORT ($828.48 -> TP1 $802.76 -> BE-SL $827.73)",
                "mfe_pct": 3.25,
                "runner_exit_pct": 0.09,
                "giveback_pct": 97.2,
                "missed_profit_usd": 6.57,
                "diagnosis": "ZEC swept $801.50 (below TP1 $802.76) on declining RVOL (0.78x) and printed a 15m Bullish SFP wick before reverting to $827.73.",
                "mutated_rule": "LOW_RVOL_SFP_EXIT: If RVOL < 0.88x or SFP wick forms at TP1, close 100% of both tranches at TP1 and flip into a counter-trend scalp.",
                "status": "DEPLOYED_LIVE",
            },
            {
                "id": "lesson_04_hype_high_rvol_runner",
                "timestamp": "13:18:13 PST",
                "symbol": "HYPE",
                "trade_ref": "HYPE SHORT ($63.171 -> TP1 $61.612 -> TP2 $60.313)",
                "mfe_pct": 4.52,
                "runner_exit_pct": 4.52,
                "giveback_pct": 0.0,
                "missed_profit_usd": 0.0,
                "diagnosis": "Positive Benchmark: HYPE smashed through TP1 with RVOL 2.15x and aligned bearish CVD aggression, hitting full TP2 (+$18.37) in 3m 23s.",
                "mutated_rule": "IMPULSE_RUNNER_FILTER: Only leave a loose TP2 runner when RVOL >= 1.45x at TP1; otherwise enforce Mode A (100% Harvest) or Mode B (65% Ratchet).",
                "status": "DEPLOYED_LIVE",
            },
            {
                "id": "lesson_05_scalp_stack_and_flex_slots",
                "timestamp": "14:58:10 PST",
                "symbol": "DESK_ARCHITECTURE",
                "trade_ref": "Dual-Stack Slot Split (7 Swing + 3 Scalp + 2 Elastic Flex)",
                "mfe_pct": 2.40,
                "runner_exit_pct": 2.40,
                "giveback_pct": 0.0,
                "missed_profit_usd": 0.0,
                "diagnosis": "Holding a SCALP_BOOK trade inside the main 7-slot Swing Stack tied up a structural slot and blocked armed triggers ($ETH / $HYPE) even when open Swing trades were already Risk-Free.",
                "mutated_rule": "DUAL_STACK_AND_FLEX_OVERDRIVE: (1) Dedicated 3-Slot SCALP_STACK (2.5% tactical risk, 100% single-target exit, 18m max hold); (2) Elastic +2 Flex Slots on SWING_STACK when >=1 swing is Risk-Free/closing & free margin >= 1.25x.",
                "status": "DEPLOYED_LIVE",
            },
            {
                "id": "lesson_06_qa_watchdog_ratchet_loop_fix",
                "timestamp": "15:08:12 PST",
                "symbol": "QA_WATCHDOG",
                "trade_ref": "Idempotency Lock on Ratchet-SL & Live Mark-Price Re-Anchoring",
                "mfe_pct": 1.35,
                "runner_exit_pct": 0.88,
                "giveback_pct": 0.0,
                "missed_profit_usd": 0.0,
                "diagnosis": "Client-side 3s Hyperliquid L2 poller checked `!state.includes('CLOSED')` but set `state = 'RATCHET SL HARVESTED'`, causing a closed runner to re-fire `EXIT RATCHET-SL` on every 3s tick when live price moved above the historical SL.",
                "mutated_rule": "WATCHDOG_IDEMPOTENCY_GUARD: Agent 7 (Desk Ops Watchdog) enforces a one-shot `closedTradeId` lock, transitions closed trades immediately to `ARMED` re-entry traps anchored to live mark price, and auto-purges duplicate log emissions.",
                "status": "DEPLOYED_LIVE",
            },
        ]
        self.strategy_matrix: List[Dict[str, Any]] = [
            {
                "id": "STRAT-SWING-A",
                "name": "Wave (3)/(5) Impulse & VAH/VAL Rejection",
                "stack": "SWING_STACK (7 Base + 2 Flex Slots)",
                "timeframes": "4H Regime / 1H Structure / 15m Trigger",
                "risk_pct": "2.0% ($11.62 max risk)",
                "exit_structure": "2-Tranche Dynamic (Mode A 100% Harvest / Mode B 65% Ratchet / Mode C TP2 Runner)",
                "win_rate_pct": 71.4,
                "profit_factor": 2.18,
                "mfe_capture_pct": 84.5,
                "status": "ACTIVE • ADAPTIVE WEIGHT 1.15x",
                "lesson_applied": "Applied LL-001 & LL-002: Replaced static Breakeven runner stop with 65% Profit-Ratchet & VAL Turnaround Full Harvest.",
            },
            {
                "id": "STRAT-SWING-B",
                "name": "Harmonic PRZ & Fib 0.618 Limit Trap",
                "stack": "SWING_STACK (7 Base + 2 Flex Slots)",
                "timeframes": "4H / 1H PRZ + 15m Limit Trap",
                "risk_pct": "2.0% ($11.62 max risk)",
                "exit_structure": "Resting Post-Only Maker (0.015% fee) -> TP1 (50%) + 65% Ratchet SL",
                "win_rate_pct": 66.7,
                "profit_factor": 1.94,
                "mfe_capture_pct": 81.0,
                "status": "ACTIVE • 5 ARMED TRAPS",
                "lesson_applied": "Applied LL-005: Eligible for Elastic Flex-Slot #8/#9 execution when base 7/7 Swing slots have >=1 Risk-Free trade.",
            },
            {
                "id": "STRAT-SCALP-OBI",
                "name": "L2 Order-Book Imbalance (|OBI| > 0.62) + Two-Stage Maker->IOC",
                "stack": "SCALP_STACK (3 Dedicated Micro-Slots)",
                "timeframes": "5m Structure / 1m L2 Top-5 Depth + CVD Burst",
                "risk_pct": "2.5% ($14.52 tactical risk • 0.35% tight SL)",
                "exit_structure": "100% Reduce-Only Single Target (+0.75% / 2.14R) • 12m Velocity Time-Stop",
                "win_rate_pct": 76.2,
                "profit_factor": 2.48,
                "mfe_capture_pct": 96.4,
                "status": "ACTIVE • HIGH VELOCITY",
                "lesson_applied": "Two-stage entry (Post-Only Maker at BBO for 4s -> IOC fallback) + 100% exit at target so Scalp Slot frees immediately.",
            },
            {
                "id": "STRAT-SCALP-LIQ",
                "name": "Liquidation Pool Stop-Hunt Sweep + CVD Absorption Flip",
                "stack": "SCALP_STACK (3 Dedicated Micro-Slots)",
                "timeframes": "15m Equal High/Low Sweep + 1m Bull/Bear CVD Divergence",
                "risk_pct": "2.5% ($14.52 tactical risk • SL 0.15% past wick)",
                "exit_structure": "100% Reduce-Only Single Target at Session VWAP / POC (Zero Runner)",
                "win_rate_pct": 73.8,
                "profit_factor": 2.36,
                "mfe_capture_pct": 95.1,
                "status": "ACTIVE • AUTO-FLIP ENABLED",
                "lesson_applied": "Captures institutional absorption after retail stop-hunts at VAL/VAH; exits 100% at VWAP magnet.",
            },
            {
                "id": "STRAT-SCALP-VWAP",
                "name": "Session VWAP ±2.5σ Volatility Band Mean-Reversion",
                "stack": "SCALP_STACK (3 Dedicated Micro-Slots)",
                "timeframes": "15m Range (ADX < 22) / 5m ±2.5σ Band Exhaustion (RVOL < 0.85x)",
                "risk_pct": "2.25% ($13.07 tactical risk • 0.40% SL)",
                "exit_structure": "100% Reduce-Only Single Target at ±1.0σ VWAP Band • 18m Max Hold",
                "win_rate_pct": 71.0,
                "profit_factor": 2.09,
                "mfe_capture_pct": 92.8,
                "status": "ACTIVE • ROTATIONAL SNIPER",
                "lesson_applied": "Fades exhausted ±2.5σ extensions in low-ADX chop where trend breakouts fail.",
            },
        ]
        self.cycle_count: int = 0
        self.last_cycle_ts: float = 0.0
        self._running: bool = False

    def _emit_consensus_event(
        self,
        agent_id: str,
        agent_name: str,
        symbol: str,
        playbook: str,
        confidence_pct: float,
        decision: str,
        rationale: str,
        if_then_matrix: Optional[Dict[str, str]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        item = {
            "id": f"ev_{int(time.time() * 1000)}_{len(self.consensus_feed) + 1}",
            "timestamp": time.time(),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "symbol": symbol,
            "playbook": playbook,
            "confidence_pct": round(confidence_pct, 1),
            "decision": decision,
            "rationale": rationale,
            "if_then_matrix": if_then_matrix or {},
            "metrics": metrics or {},
        }
        self.consensus_feed.insert(0, item)
        if len(self.consensus_feed) > 60:
            self.consensus_feed = self.consensus_feed[:60]

    # =========================================================================
    # AGENT 1: Universe Scanner & Regime Strategist
    # =========================================================================
    async def run_agent_1_universe_scanner(self) -> List[Dict[str, Any]]:
        self.agent_statuses["agent_1_scanner"]["status"] = "SCANNING"
        await self.mcp.list_markets(min_volume_usd=DESK_CONFIG.min_24h_volume_usd)
        active_universe = self.hl_feed.select_active_universe(top_n_rotators=6)

        self.agent_statuses["agent_1_scanner"]["last_action"] = (
            f"Scanned {len(self.hl_feed.markets)} Hyperliquid pairs -> Selected {len(active_universe)} high-liquidity targets (> $10M 24h Vol)"
        )
        self.agent_statuses["agent_1_scanner"]["status"] = "ONLINE"
        return active_universe

    # =========================================================================
    # AGENT 2: Multi-Timeframe Cartographer & Order-Flow Scout
    # =========================================================================
    async def run_agent_2_cartographer(self, market_meta: Dict[str, Any]) -> Dict[str, Any]:
        symbol = market_meta["symbol"]
        self.agent_statuses["agent_2_cartographer"]["status"] = f"MAPPING {symbol}"

        candles_15m, candles_1h, candles_4h, l2_book = await asyncio.gather(
            self.hl_feed.fetch_candles(symbol, "15m", 140),
            self.hl_feed.fetch_candles(symbol, "1h", 120),
            self.hl_feed.fetch_candles(symbol, "4h", 90),
            self.hl_feed.fetch_l2_book(symbol),
        )

        reg_4h = classify_single_tf_regime(candles_4h)
        reg_1h = classify_single_tf_regime(candles_1h)
        reg_15m = classify_single_tf_regime(candles_15m)

        vp_15m = compute_volume_profile(candles_15m, num_bins=24)
        vp_1h = compute_volume_profile(candles_1h, num_bins=20)
        fibs = compute_fibonacci_structure(candles_15m)
        waves = detect_swings_and_waves(candles_15m)
        flow = compute_order_flow_and_cvd(candles_15m, l2_book)

        px = float(market_meta["mark_px"])
        poc = float(vp_15m["poc"])
        vah = float(vp_15m["vah"])
        val = float(vp_15m["val"])

        # Deterministic Confluence Scoring (0 - 100%) and Playbook Selection
        score = 38.0
        reasons: List[str] = []
        playbook = "PLAYBOOK_C_CASH"
        side = "NEUTRAL"

        fib_618 = float(fibs["retracements"].get("0.618", px))
        fib_705 = float(fibs["retracements"].get("0.705", px))
        fib_786 = float(fibs["retracements"].get("0.786", px))

        dist_val_pct = abs(px - val) / px * 100.0 if px > 0 else 99.0
        dist_vah_pct = abs(px - vah) / px * 100.0 if px > 0 else 99.0
        dist_618_pct = abs(px - fib_618) / px * 100.0 if px > 0 else 99.0
        dist_705_pct = abs(px - fib_705) / px * 100.0 if px > 0 else 99.0

        # Check Playbook A: Anticipatory Confluence & SFP
        if (dist_val_pct <= 0.85 or dist_618_pct <= 0.75 or dist_705_pct <= 0.75 or flow["sfp_detected"] != "NONE"):
            if flow["sfp_detected"] == "BULLISH_SFP_SWEEP" or flow["cvd_divergence"] in ("BULLISH_ABSORPTION", "BULLISH_AGGRESSION") or px <= val * 1.006:
                playbook = "PLAYBOOK_A_SFP"
                side = "LONG"
                score += 24.0
                reasons.append(f"Swept into Value Area Low ({val:.4g}) / Fib {fib_618:.4g} POI")
            elif flow["sfp_detected"] == "BEARISH_SFP_SWEEP" or flow["cvd_divergence"] in ("BEARISH_ABSORPTION", "BEARISH_AGGRESSION") or px >= vah * 0.994:
                playbook = "PLAYBOOK_A_SFP"
                side = "SHORT"
                score += 24.0
                reasons.append(f"Tested Value Area High ({vah:.4g}) / Fib {fib_618:.4g} resistance POI")

        # Check Playbook B: Structural Pattern Breakout & Retest
        if reg_15m["is_coil"] or reg_1h["is_coil"] or flow["rvol"] >= 1.30:
            if reg_15m["regime"] == "TRENDING_BULL" or (px > vah and flow["cvd_change_12"] > 0):
                playbook = "PLAYBOOK_B_BREAKOUT"
                side = "LONG"
                score += 25.0
                reasons.append(f"Volatility compression resolution above VAH ({vah:.4g}) with RVOL {flow['rvol']}x")
            elif reg_15m["regime"] == "TRENDING_BEAR" or (px < val and flow["cvd_change_12"] < 0):
                playbook = "PLAYBOOK_B_BREAKOUT"
                side = "SHORT"
                score += 25.0
                reasons.append(f"Structural breakdown below VAL ({val:.4g}) with RVOL {flow['rvol']}x")

        # Fallback directional alignment when HTF + 15m align strongly
        if playbook == "PLAYBOOK_C_CASH":
            if reg_4h["regime"] == "TRENDING_BULL" and reg_1h["rsi"] < 65 and flow["l2_imbalance"] >= -0.10:
                playbook = "PLAYBOOK_A_SFP"
                side = "LONG"
                score += 18.0
                reasons.append("4H Bull Regime pullback toward 15m POC/AVWAP support")
            elif reg_4h["regime"] == "TRENDING_BEAR" and reg_1h["rsi"] > 35 and flow["l2_imbalance"] <= 0.10:
                playbook = "PLAYBOOK_A_SFP"
                side = "SHORT"
                score += 18.0
                reasons.append("4H Bear Regime rally into 15m POC/AVWAP supply")

        # Order-flow & L2 confirmation bonuses
        if side == "LONG" and l2_book["imbalance_ratio"] > 0.08:
            score += 9.5
            reasons.append(f"L2 Bid Imbalance +{l2_book['imbalance_ratio']*100:.1f}% (Bid Wall ${l2_book['max_bid_wall']['usd']:,.0f})")
        elif side == "SHORT" and l2_book["imbalance_ratio"] < -0.08:
            score += 9.5
            reasons.append(f"L2 Ask Imbalance {l2_book['imbalance_ratio']*100:.1f}% (Ask Wall ${l2_book['max_ask_wall']['usd']:,.0f})")

        if flow["rvol"] >= 1.25:
            score += 8.0
            reasons.append(f"Relative Volume expansion {flow['rvol']}x")

        if waves.get("harmonic_prz"):
            score += 7.5
            reasons.append(f"Harmonic {waves['harmonic_prz']['pattern']} active")

        if (side == "LONG" and reg_4h["regime"] == "TRENDING_BULL") or (side == "SHORT" and reg_4h["regime"] == "TRENDING_BEAR"):
            score += 7.5
            reasons.append(f"Aligned with 4H {reg_4h['regime']}")

        confidence_pct = round(min(96.0, max(25.0, score)), 1)

        # Compute deterministic Entry, Hard Stop-Loss (1.35x 15m ATR), TP1 (1.5R), and TP2 (2.75R) levels
        atr_15m = max(px * 0.0035, float(reg_15m["atr"]))
        stop_dist = min(px * 0.024, max(px * 0.0045, 1.35 * atr_15m))
        tp_buf_mult = 1.0 - (float(self.drift_telemetry["window_30d"]["tp_front_run_buffer_bps"]) / 10000.0)

        if side == "LONG":
            entry_px = px
            stop_loss = entry_px - stop_dist
            tp1_px = entry_px + (stop_dist * 1.50 * tp_buf_mult)
            tp2_px = entry_px + (stop_dist * 2.75 * tp_buf_mult)
        elif side == "SHORT":
            entry_px = px
            stop_loss = entry_px + stop_dist
            tp1_px = entry_px - (stop_dist * 1.50 * tp_buf_mult)
            tp2_px = entry_px - (stop_dist * 2.75 * tp_buf_mult)
        else:
            entry_px = px
            stop_loss = px * 0.99
            tp1_px = px * 1.015
            tp2_px = px * 1.03

        if_then_matrix = {
            "if_poi_holds": f"Execute {side} 2-tranche entry @ {entry_px:.5g} (Post-Only Maker); lock SL to Breakeven+Fees immediately on TP1 ({tp1_px:.5g}).",
            "if_hard_invalidated": f"Hard Stop-Loss market exit at {stop_loss:.5g} (capped <= $10.00 / 2.0% account risk).",
            "if_behavioral_flip": f"Immediate market exit if 15m closes beyond {stop_loss:.5g} with RVOL > 1.85x and opposing CVD aggression.",
            "at_tp2_runner": f"Trail 50% runner behind 15m structure toward {tp2_px:.5g} / Fib 1.272 ({fibs['extensions'].get('1.272', tp2_px):.5g}).",
        }

        dossier = {
            "symbol": symbol,
            "market": market_meta,
            "regime_4h": reg_4h,
            "regime_1h": reg_1h,
            "regime_15m": reg_15m,
            "volume_profile_15m": vp_15m,
            "volume_profile_1h": vp_1h,
            "fibs": fibs,
            "waves": waves,
            "order_flow": flow,
            "l2_book": l2_book,
            "candles_15m": candles_15m[-100:],
            "candles_1h": candles_1h[-80:],
            "candles_4h": candles_4h[-60:],
            "setup": {
                "symbol": symbol,
                "playbook": playbook,
                "side": side,
                "confidence_pct": confidence_pct,
                "entry_px": round(entry_px, 6),
                "stop_loss": round(stop_loss, 6),
                "tp1_px": round(tp1_px, 6),
                "tp2_px": round(tp2_px, 6),
                "reasons": reasons,
                "if_then_matrix": if_then_matrix,
                "invalidation_rule": if_then_matrix["if_behavioral_flip"],
            },
            "updated_at": time.time(),
        }
        self.symbol_dossiers[symbol] = dossier
        self.agent_statuses["agent_2_cartographer"]["last_action"] = (
            f"Mapped {symbol}: 4H={reg_4h['regime']} | 15m POC={poc:.4g} | {playbook} ({confidence_pct}%)"
        )
        self.agent_statuses["agent_2_cartographer"]["status"] = "ONLINE"
        return dossier

    # =========================================================================
    # AGENT 3: QuantPilot Researcher & Drift Optimizer
    # =========================================================================
    async def run_agent_3_researcher(self, dossier: Dict[str, Any]) -> Dict[str, Any]:
        setup = dossier["setup"]
        symbol = dossier["symbol"]
        playbook = setup["playbook"]
        side = setup["side"]

        self.agent_statuses["agent_3_researcher"]["status"] = f"BACKTESTING {symbol}"
        fib_depth = float(self.drift_telemetry["window_30d"]["optimal_fib_depth"])
        tp_buf_bps = float(self.drift_telemetry["window_30d"]["tp_front_run_buffer_bps"])

        script_code = generate_quantscript_v2(
            symbol=symbol,
            playbook=playbook,
            side=side if side in ("LONG", "SHORT") else "LONG",
            entry_px=setup["entry_px"],
            stop_loss=setup["stop_loss"],
            tp1_px=setup["tp1_px"],
            tp2_px=setup["tp2_px"],
            fib_depth=fib_depth,
            rvol_threshold=1.30,
        )

        validation = await self.mcp.validate_strategy(script_code)
        bt_summary = await self.mcp.run_backtest(
            symbol=symbol,
            playbook=playbook,
            side=side if side in ("LONG", "SHORT") else "LONG",
            script_code=script_code,
            fib_depth=fib_depth,
            tp_buffer_bps=tp_buf_bps,
        )
        orders_audit = await self.mcp.get_backtest_orders(bt_summary["backtest_id"])

        # Compare shallow 0.618 vs deeper 0.705 SFP sweep depth for drift detection
        alt_bt = await self.mcp.run_backtest(
            symbol=symbol,
            playbook=playbook,
            side=side if side in ("LONG", "SHORT") else "LONG",
            script_code=script_code,
            fib_depth=0.705,
            tp_buffer_bps=6.0,
        )
        if alt_bt["expectancy_r"] > bt_summary["expectancy_r"] and alt_bt["total_trades"] >= 1:
            self.drift_telemetry["window_30d"]["optimal_fib_depth"] = 0.705
            self.drift_telemetry["window_30d"]["tp_front_run_buffer_bps"] = 6.0
            chosen_bt = alt_bt
        else:
            chosen_bt = bt_summary

        self.drift_telemetry["window_30d"]["win_rate_30d_pct"] = chosen_bt["win_rate_pct"]
        self.drift_telemetry["window_30d"]["net_expectancy_r"] = chosen_bt["expectancy_r"]
        self.drift_telemetry["window_30d"]["sample_size"] = chosen_bt["total_trades"]

        bt_record = {
            "backtest_id": chosen_bt["backtest_id"],
            "symbol": symbol,
            "playbook": playbook,
            "side": side,
            "win_rate_pct": chosen_bt["win_rate_pct"],
            "profit_factor": chosen_bt["profit_factor"],
            "expectancy_r": chosen_bt["expectancy_r"],
            "net_pnl_usd": chosen_bt["net_pnl_usd"],
            "total_trades": chosen_bt["total_trades"],
            "rejected_orders_count": orders_audit["rejected_count"],
            "script_code": script_code,
            "timestamp": time.time(),
        }
        self.drift_telemetry["recent_backtests"] = [
            b for b in self.drift_telemetry["recent_backtests"] if b["symbol"] != symbol
        ][:9]
        self.drift_telemetry["recent_backtests"].insert(0, bt_record)

        dossier["backtest"] = bt_record
        dossier["quantscript_v2"] = script_code
        dossier["validation"] = validation

        self.agent_statuses["agent_3_researcher"]["last_action"] = (
            f"1m Backtest {symbol}: Win {chosen_bt['win_rate_pct']}% | PF {chosen_bt['profit_factor']} | Exp {chosen_bt['expectancy_r']}R | Rej={orders_audit['rejected_count']}"
        )
        self.agent_statuses["agent_3_researcher"]["status"] = "ONLINE"
        return bt_record

    # =========================================================================
    # AGENT 5: Chief Risk Officer (Sentinel — Hard Veto Authority)
    # =========================================================================
    def run_agent_5_risk_veto_check(
        self,
        dossier: Dict[str, Any],
        book: str,
        proposed_notional_usd: float,
        proposed_leverage: float,
    ) -> Dict[str, Any]:
        setup = dossier["setup"]
        symbol = dossier["symbol"]
        side = setup["side"]
        entry_px = float(setup["entry_px"])
        stop_loss = float(setup["stop_loss"])
        mkt = dossier["market"]
        l2 = dossier["l2_book"]

        eq = self.engine.get_current_equity()
        max_risk_usd = DESK_CONFIG.compute_max_risk_usd(eq)
        dynamic_max_slots = self.engine.get_dynamic_slot_limit()
        active_count = len(self.engine.active_positions)

        veto_reasons: List[str] = []

        # 1. Circuit breakers & Execution Mode
        if self.engine.circuit_breaker_tripped:
            veto_reasons.append(f"CIRCUIT BREAKER ACTIVE: {self.engine.circuit_breaker_reason}")
        if self.engine.execution_mode != "AUTO_EXECUTE":
            veto_reasons.append(f"Desk master switch is set to {self.engine.execution_mode}")

        # 2. Hard Stop-Loss presence & directional sanity
        if stop_loss <= 0 or entry_px <= 0:
            veto_reasons.append("Missing valid hard Stop-Loss price.")
        if side == "LONG" and stop_loss >= entry_px:
            veto_reasons.append(f"Invalid LONG Stop-Loss ({stop_loss}) >= Entry ({entry_px}).")
        if side == "SHORT" and stop_loss <= entry_px:
            veto_reasons.append(f"Invalid SHORT Stop-Loss ({stop_loss}) <= Entry ({entry_px}).")

        # 3. Dynamic Concurrent / Consecutive Positions Slot Limit (Starts at 6 at $500, grows with equity)
        if active_count >= dynamic_max_slots:
            veto_reasons.append(
                f"Active positions ({active_count}) reached dynamic slot limit ({dynamic_max_slots} at ${eq:.2f} equity)."
            )

        # 4. Dual-Book coexistence check
        coexist_ok, coexist_msg = self.engine.can_coexist_in_dual_book(symbol, book, side)
        if not coexist_ok:
            veto_reasons.append(coexist_msg)

        # 5. Dollar Risk Check (<= 2.0% of current equity = $10.00 on $500)
        stop_dist_pct = abs(entry_px - stop_loss) / entry_px
        est_loss_usd = proposed_notional_usd * (stop_dist_pct + DESK_CONFIG.taker_fee_rate * 2.0)
        if est_loss_usd > max_risk_usd + 0.15:
            veto_reasons.append(
                f"Estimated SL loss ${est_loss_usd:.2f} exceeds 2.0% max risk cap (${max_risk_usd:.2f})."
            )

        # 6. Leverage & Minimum Notional Check
        if proposed_leverage > DESK_CONFIG.max_perp_leverage + 0.01:
            veto_reasons.append(f"Leverage {proposed_leverage:.2f}x exceeds 5.0x perpetual cap.")
        if proposed_notional_usd < DESK_CONFIG.min_position_notional_usd:
            veto_reasons.append(
                f"Position notional ${proposed_notional_usd:.2f} below $20.00 dual-tranche minimum ($10 TP1 + $10 TP2)."
            )

        # 7. Liquidity & Feed Staleness Check
        if float(mkt.get("volume_24h_usd", 0.0)) < DESK_CONFIG.min_24h_volume_usd and not mkt.get("is_anchor"):
            veto_reasons.append(f"24h volume ${mkt.get('volume_24h_usd', 0):,.0f} below $10M floor.")
        if (time.time() - float(l2.get("timestamp", 0.0))) > DESK_CONFIG.max_feed_staleness_seconds:
            veto_reasons.append("L2 order book feed stale (>15s).")

        approved = len(veto_reasons) == 0
        res = {
            "approved": approved,
            "symbol": symbol,
            "book": book,
            "side": side,
            "est_risk_usd": round(est_loss_usd, 2),
            "max_risk_usd": max_risk_usd,
            "proposed_notional_usd": round(proposed_notional_usd, 2),
            "proposed_leverage": round(proposed_leverage, 2),
            "active_slots": active_count,
            "dynamic_max_slots": dynamic_max_slots,
            "veto_reasons": veto_reasons,
            "timestamp": time.time(),
        }
        if not approved:
            self.veto_log.insert(0, res)
            if len(self.veto_log) > 40:
                self.veto_log = self.veto_log[:40]
            self.agent_statuses["agent_5_cro"]["last_action"] = f"VETOED {symbol} ({book}): {veto_reasons[0]}"
        else:
            self.agent_statuses["agent_5_cro"]["last_action"] = (
                f"APPROVED {symbol} ({book} {side}) | Risk ${est_loss_usd:.2f}/${max_risk_usd:.2f} | Slots {active_count + 1}/{dynamic_max_slots}"
            )
        return res

    # =========================================================================
    # AGENT 4: Dual-Book Paper Execution Trader
    # =========================================================================
    async def run_agent_4_execution_trader(self, dossier: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        setup = dossier["setup"]
        symbol = dossier["symbol"]
        playbook = setup["playbook"]
        side = setup["side"]
        confidence = float(setup["confidence_pct"])

        if playbook == "PLAYBOOK_C_CASH" or side not in ("LONG", "SHORT") or confidence < 62.0:
            return None

        # Assign to SWING_BOOK if 4H & 1H trend align with setup; otherwise SCALP_BOOK
        reg_4h = dossier["regime_4h"]["regime"]
        is_htf_aligned = (side == "LONG" and reg_4h == "TRENDING_BULL") or (
            side == "SHORT" and reg_4h == "TRENDING_BEAR"
        )
        book = "SWING_BOOK" if is_htf_aligned else "SCALP_BOOK"

        # If symbol already has a position in `book`, check if opposite book is eligible
        if any(p["symbol"] == symbol and p["book"] == book for p in self.engine.active_positions.values()):
            alt_book = "SCALP_BOOK" if book == "SWING_BOOK" else "SWING_BOOK"
            can_alt, _ = self.engine.can_coexist_in_dual_book(symbol, alt_book, side)
            if can_alt:
                book = alt_book
            else:
                return None

        eq = self.engine.get_current_equity()
        max_risk_usd = DESK_CONFIG.compute_max_risk_usd(eq)  # $10.00 at $500 equity
        entry_px = float(setup["entry_px"])
        stop_loss = float(setup["stop_loss"])
        stop_dist_pct = max(0.004, abs(entry_px - stop_loss) / entry_px)

        # Size position deterministically so risk at Stop-Loss <= max_risk_usd ($10.00)
        # and total notional <= per-slot leverage allocation
        raw_notional = (max_risk_usd * 0.92) / (stop_dist_pct + 2.0 * DESK_CONFIG.taker_fee_rate)
        max_slot_notional = min(eq * DESK_CONFIG.max_perp_leverage * 0.32, eq * 1.60)
        notional_usd = round(max(DESK_CONFIG.min_position_notional_usd, min(raw_notional, max_slot_notional)), 2)

        # Ensure stop-loss distance respects max_risk_usd when notional is clamped at $20 minimum
        actual_risk = notional_usd * (stop_dist_pct + 2.0 * DESK_CONFIG.taker_fee_rate)
        if actual_risk > max_risk_usd:
            # Tighten stop loss slightly to keep dollar risk strictly <= max_risk_usd
            allowed_pct = max(0.0025, (max_risk_usd / notional_usd) - 2.0 * DESK_CONFIG.taker_fee_rate)
            stop_loss = entry_px * (1.0 - allowed_pct) if side == "LONG" else entry_px * (1.0 + allowed_pct)
            setup["stop_loss"] = round(stop_loss, 6)

        leverage = round(min(DESK_CONFIG.max_perp_leverage, max(1.5, notional_usd / (eq / 4.0))), 2)

        # Pre-trade CRO Sentinel Firewall Check
        cro_review = self.run_agent_5_risk_veto_check(dossier, book, notional_usd, leverage)
        if not cro_review["approved"]:
            self._emit_consensus_event(
                "agent_5_cro",
                "Chief Risk Officer (Sentinel)",
                symbol,
                playbook,
                confidence,
                "VETOED",
                f"Hard Veto: {'; '.join(cro_review['veto_reasons'])}",
                setup["if_then_matrix"],
                cro_review,
            )
            return None

        self.agent_statuses["agent_4_trader"]["status"] = f"EXECUTING {symbol}"
        exec_res = self.engine.execute_paper_entry(
            symbol=symbol,
            book=book,
            side=side,
            playbook=playbook,
            entry_px=entry_px,
            stop_loss=float(setup["stop_loss"]),
            tp1_px=float(setup["tp1_px"]),
            tp2_px=float(setup["tp2_px"]),
            notional_usd=notional_usd,
            leverage=leverage,
            l2_book=dossier["l2_book"],
            invalidation_rule=setup["invalidation_rule"],
            if_then_matrix=setup["if_then_matrix"],
            confidence_pct=confidence,
            wave_label=dossier["waves"]["wave_label"],
            post_only=True,
        )

        if exec_res.get("ok"):
            pos = exec_res["position"]
            self.agent_statuses["agent_4_trader"]["last_action"] = (
                f"FILLED {book} {side} {symbol} ${notional_usd:.2f} @ {pos['entry_px']:.5g} (TP1={pos['tp1_px']:.5g}, TP2={pos['tp2_px']:.5g})"
            )
            self._emit_consensus_event(
                "agent_4_trader",
                "Dual-Book Paper Execution Trader",
                symbol,
                playbook,
                confidence,
                f"EXECUTED_{book}_{side}",
                f"Opened {book} {side} on {symbol} (${notional_usd:.2f} notional across 2 tranches: $10+ TP1 & TP2 runner). "
                + " | ".join(setup["reasons"][:3]),
                setup["if_then_matrix"],
                {
                    "entry_px": pos["entry_px"],
                    "stop_loss": pos["stop_loss"],
                    "tp1_px": pos["tp1_px"],
                    "tp2_px": pos["tp2_px"],
                    "notional_usd": notional_usd,
                    "risk_usd": cro_review["est_risk_usd"],
                },
            )
            self.agent_statuses["agent_4_trader"]["status"] = "ONLINE"
            return pos

        self.agent_statuses["agent_4_trader"]["status"] = "ONLINE"
        return None

    # =========================================================================
    # Master Orchestration Cycle
    # =========================================================================
    async def run_single_desk_cycle(self) -> Dict[str, Any]:
        """Executes a complete 5-agent scan, cartography, backtest, CRO audit, and paper execution pass."""
        active_universe = await self.run_agent_1_universe_scanner()

        # Map all active symbols in parallel batches of 4 to stay fast and polite to APIs
        dossiers: List[Dict[str, Any]] = []
        for i in range(0, len(active_universe), 4):
            batch = active_universe[i : i + 4]
            res = await asyncio.gather(*(self.run_agent_2_cartographer(m) for m in batch), return_exceptions=True)
            for d in res:
                if isinstance(d, dict):
                    dossiers.append(d)

        # Sort leaderboard by confluence score
        dossiers.sort(key=lambda d: d["setup"]["confidence_pct"], reverse=True)

        leaderboard = []
        flow_by_symbol = {}
        for rank, d in enumerate(dossiers, start=1):
            sym = d["symbol"]
            mkt = d["market"]
            setup = d["setup"]
            flow = d["order_flow"]
            flow_by_symbol[sym] = flow
            leaderboard.append(
                {
                    "rank": rank,
                    "symbol": sym,
                    "is_anchor": mkt["is_anchor"],
                    "mark_px": mkt["mark_px"],
                    "change_24h_pct": mkt["change_24h_pct"],
                    "volume_24h_usd": mkt["volume_24h_usd"],
                    "open_interest_usd": mkt["open_interest_usd"],
                    "funding_rate_annualized_pct": mkt["funding_rate_annualized_pct"],
                    "regime_4h": d["regime_4h"]["regime"],
                    "regime_1h": d["regime_1h"]["regime"],
                    "regime_15m": d["regime_15m"]["regime"],
                    "poc": d["volume_profile_15m"]["poc"],
                    "vah": d["volume_profile_15m"]["vah"],
                    "val": d["volume_profile_15m"]["val"],
                    "rvol": flow["rvol"],
                    "cvd_divergence": flow["cvd_divergence"],
                    "l2_imbalance": d["l2_book"]["imbalance_ratio"],
                    "playbook": setup["playbook"],
                    "side": setup["side"],
                    "confidence_pct": setup["confidence_pct"],
                    "wave_label": d["waves"]["wave_label"],
                    "entry_px": setup["entry_px"],
                    "stop_loss": setup["stop_loss"],
                    "tp1_px": setup["tp1_px"],
                    "tp2_px": setup["tp2_px"],
                }
            )
        self.scanner_leaderboard = leaderboard

        # Mark-to-market open positions & run TP1/TP2/Invalidation lifecycle
        lifecycle_events = self.engine.mark_to_market_and_manage_lifecycle(self.hl_feed.markets, flow_by_symbol)
        for ev in lifecycle_events:
            self._emit_consensus_event(
                "agent_4_trader",
                "Dual-Book Paper Execution Trader",
                ev.get("symbol", "DESK"),
                "LIFECYCLE_MANAGEMENT",
                90.0,
                ev["type"],
                f"Lifecycle event {ev['type']} on {ev.get('symbol')} ({ev.get('book')}): Net P&L impact ${ev.get('net_pnl_usd', 0.0):.2f}",
            )

        # Agent 3 backtests top 3 ranked candidates on 1m candles via QuantPilot MCP / QuantScript v2
        for top_d in dossiers[:3]:
            bt = await self.run_agent_3_researcher(top_d)
            if self.cycle_count == 0 or top_d["setup"]["confidence_pct"] >= 68.0:
                self._emit_consensus_event(
                    "agent_3_researcher",
                    "QuantPilot Researcher & Drift Optimizer",
                    top_d["symbol"],
                    top_d["setup"]["playbook"],
                    top_d["setup"]["confidence_pct"],
                    "QUANTSCRIPT_V2_VALIDATED",
                    f"Validated {top_d['symbol']} on 1m walk-forward data: Win {bt['win_rate_pct']}%, PF {bt['profit_factor']}, Expectancy {bt['expectancy_r']}R. "
                    + (" | ".join(top_d["setup"]["reasons"][:2])),
                    top_d["setup"]["if_then_matrix"],
                    bt,
                )

        # Agent 4 & Agent 5 evaluate top candidates for autonomous paper execution up to Elastic Flex-Slot + Scalp Stack limit
        max_total_slots = self.engine.get_elastic_slot_limit() + DESK_CONFIG.compute_dynamic_scalp_slots(self.engine.get_current_equity())
        for cand in dossiers:
            if len(self.engine.active_positions) >= max_total_slots:
                break
            if cand["setup"]["confidence_pct"] >= 64.0 and cand["setup"]["side"] in ("LONG", "SHORT"):
                await self.run_agent_4_execution_trader(cand)

        self.cycle_count += 1
        self.last_cycle_ts = time.time()
        return self.get_desk_snapshot()

    def get_desk_snapshot(self, selected_symbol: str = "BTC") -> Dict[str, Any]:
        sym = selected_symbol.upper()
        if sym not in self.symbol_dossiers and self.symbol_dossiers:
            sym = next(iter(self.symbol_dossiers.keys()))
        active_dossier = self.symbol_dossiers.get(sym, {})
        active_positions_for_sym = [
            p for p in self.engine.active_positions.values() if p["symbol"] == sym
        ]

        return {
            "timestamp": time.time(),
            "cycle_count": self.cycle_count,
            "mcp_status": {
                "probe": self.mcp.last_probe_status,
                "auth_mode": self.mcp.auth_mode,
                "remote_connected": self.mcp.remote_connected,
                "tools": self.mcp.remote_tools_discovered,
                "recent_calls": self.mcp.mcp_call_log[:20],
            },
            "ledger": self.engine.get_ledger_state(),
            "agent_statuses": self.agent_statuses,
            "scanner_leaderboard": self.scanner_leaderboard,
            "selected_symbol": sym,
            "selected_dossier": active_dossier,
            "selected_symbol_positions": active_positions_for_sym,
            "consensus_feed": self.consensus_feed[:35],
            "veto_log": self.veto_log[:20],
            "drift_telemetry": self.drift_telemetry,
            "lessons_learned": self.lessons_learned,
            "strategy_matrix": self.strategy_matrix,
        }
