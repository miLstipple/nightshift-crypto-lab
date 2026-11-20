"""
Live-Data Dual-Book Paper Matching Engine & Trade Lifecycle Manager.
- Initialized at $500.00 virtual USDC balance.
- Maintains separate logical ledgers for:
  1. SWING_BOOK (HTF multi-session swing positions)
  2. SCALP_BOOK (Intraday tactical order-flow scalp positions)
  Allowing a protected swing trade (TP1 banked, SL locked at breakeven+fees) to coexist
  with an intraday scalp on the same or correlated asset.
- Enforces Hyperliquid $10.00 minimum order notional per tranche (TP1 + TP2 = $20.00 min position).
- Accounts for realistic Maker (1.5 bps Post-Only) & Taker (4.5 bps + L2 impact) fees.
- Persists all fills, closed positions, equity history, and rejected setups to SQLite.
"""
from __future__ import annotations
import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.config import DESK_CONFIG


class DualBookPaperMatchingEngine:
    def __init__(self) -> None:
        os.makedirs(os.path.dirname(DESK_CONFIG.sqlite_db_path), exist_ok=True)
        self.db_path = DESK_CONFIG.sqlite_db_path
        self._init_db()

        self.cash_balance_usd: float = DESK_CONFIG.initial_equity_usd
        self.realized_pnl_usd: float = 0.0
        self.total_fees_paid_usd: float = 0.0
        self.day_high_equity_usd: float = DESK_CONFIG.initial_equity_usd
        self.peak_equity_usd: float = DESK_CONFIG.initial_equity_usd

        # Active positions keyed by position_id
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.closed_positions: List[Dict[str, Any]] = []
        self.order_audit_log: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = [
            {
                "time": int(time.time()),
                "equity": DESK_CONFIG.initial_equity_usd,
                "cash": DESK_CONFIG.initial_equity_usd,
                "unrealized_pnl": 0.0,
                "drawdown_pct": 0.0,
            }
        ]

        # Master execution mode: "AUTO_EXECUTE" | "PAUSE" | "KILL_SWITCH"
        self.execution_mode: str = "AUTO_EXECUTE"
        self.circuit_breaker_tripped: bool = False
        self.circuit_breaker_reason: str = ""

        # Restore persisted positions and audit log from SQLite if present
        self._load_from_db()

    def _load_from_db(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT status, payload_json, realized_pnl_usd, fees_usd FROM paper_trades ORDER BY opened_at DESC").fetchall()
                for status, payload_json, realized_pnl, fees in rows:
                    if not payload_json:
                        continue
                    pos = json.loads(payload_json)
                    self.realized_pnl_usd += float(realized_pnl or 0.0)
                    self.total_fees_paid_usd += float(fees or 0.0)
                    if status == "OPEN":
                        self.active_positions[pos["position_id"]] = pos
                    else:
                        self.closed_positions.append(pos)

                audit_rows = conn.execute(
                    "SELECT order_id, timestamp, symbol, book, side, order_type, status, notional_usd, price, fee_usd, reason "
                    "FROM order_audit ORDER BY timestamp DESC LIMIT 100"
                ).fetchall()
                for r in audit_rows:
                    self.order_audit_log.append(
                        {
                            "order_id": r[0],
                            "timestamp": r[1],
                            "symbol": r[2],
                            "book": r[3],
                            "side": r[4],
                            "order_type": r[5],
                            "status": r[6],
                            "notional_usd": r[7],
                            "price": r[8],
                            "fee_usd": r[9],
                            "reason": r[10],
                        }
                    )
        except Exception:
            pass

    def reset_paper_ledger(self) -> Dict[str, Any]:
        """Resets the paper account to a clean $500.00 balance and clears SQLite tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM paper_trades")
                conn.execute("DELETE FROM order_audit")
                conn.commit()
        except Exception:
            pass

        self.cash_balance_usd = DESK_CONFIG.initial_equity_usd
        self.realized_pnl_usd = 0.0
        self.total_fees_paid_usd = 0.0
        self.day_high_equity_usd = DESK_CONFIG.initial_equity_usd
        self.peak_equity_usd = DESK_CONFIG.initial_equity_usd
        self.active_positions.clear()
        self.closed_positions.clear()
        self.order_audit_log.clear()
        self.execution_mode = "AUTO_EXECUTE"
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason = ""
        self.equity_curve = [
            {
                "time": int(time.time()),
                "equity": DESK_CONFIG.initial_equity_usd,
                "cash": DESK_CONFIG.initial_equity_usd,
                "unrealized_pnl": 0.0,
                "drawdown_pct": 0.0,
            }
        ]
        return self.get_ledger_state()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    position_id TEXT PRIMARY KEY,
                    book TEXT,
                    symbol TEXT,
                    side TEXT,
                    playbook TEXT,
                    status TEXT,
                    entry_px REAL,
                    current_px REAL,
                    stop_loss REAL,
                    tp1_px REAL,
                    tp2_px REAL,
                    notional_usd REAL,
                    realized_pnl_usd REAL,
                    fees_usd REAL,
                    mae_pct REAL,
                    mfe_pct REAL,
                    opened_at REAL,
                    closed_at REAL,
                    payload_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_audit (
                    order_id TEXT PRIMARY KEY,
                    timestamp REAL,
                    symbol TEXT,
                    book TEXT,
                    side TEXT,
                    order_type TEXT,
                    status TEXT,
                    notional_usd REAL,
                    price REAL,
                    fee_usd REAL,
                    reason TEXT
                )
                """
            )
            conn.commit()

    def _record_order_audit(
        self,
        symbol: str,
        book: str,
        side: str,
        order_type: str,
        status: str,
        notional_usd: float,
        price: float,
        fee_usd: float,
        reason: str,
    ) -> Dict[str, Any]:
        entry = {
            "order_id": f"ord_{int(time.time() * 1000)}_{uuid.uuid4().hex[:5]}",
            "timestamp": time.time(),
            "symbol": symbol,
            "book": book,
            "side": side,
            "order_type": order_type,
            "status": status,
            "notional_usd": round(notional_usd, 2),
            "price": round(price, 6),
            "fee_usd": round(fee_usd, 4),
            "reason": reason,
        }
        self.order_audit_log.insert(0, entry)
        if len(self.order_audit_log) > 100:
            self.order_audit_log = self.order_audit_log[:100]

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO order_audit
                    (order_id, timestamp, symbol, book, side, order_type, status, notional_usd, price, fee_usd, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["order_id"],
                        entry["timestamp"],
                        entry["symbol"],
                        entry["book"],
                        entry["side"],
                        entry["order_type"],
                        entry["status"],
                        entry["notional_usd"],
                        entry["price"],
                        entry["fee_usd"],
                        entry["reason"],
                    ),
                )
                conn.commit()
        except Exception:
            pass
        return entry

    def get_unrealized_pnl(self) -> float:
        return sum(float(p.get("unrealized_pnl_usd", 0.0)) for p in self.active_positions.values())

    def get_current_equity(self) -> float:
        return round(DESK_CONFIG.initial_equity_usd + self.realized_pnl_usd + self.get_unrealized_pnl(), 2)

    def get_total_open_notional(self) -> float:
        return round(sum(float(p.get("remaining_notional_usd", 0.0)) for p in self.active_positions.values()), 2)

    def get_used_margin(self) -> float:
        return round(
            sum(
                float(p.get("remaining_notional_usd", 0.0)) / max(1.0, float(p.get("leverage", 3.0)))
                for p in self.active_positions.values()
            ),
            2,
        )

    def get_free_margin(self) -> float:
        return round(max(0.0, self.get_current_equity() - self.get_used_margin()), 2)

    def get_risk_free_or_closing_count(self) -> int:
        """
        Counts active trades that are either:
          1. Already Risk-Free (TP1 banked & SL locked at BE/Ratchet), OR
          2. SCALP_BOOK sniper positions, OR
          3. >75% of the way toward TP1/TP2 (imminent close).
        """
        count = 0
        for p in self.active_positions.values():
            if p.get("is_risk_free", False) or p.get("book") == "SCALP_BOOK":
                count += 1
                continue
            entry_px = float(p.get("entry_px", 0.0))
            cur_px = float(p.get("current_px", entry_px))
            tp1_px = float(p.get("tp1_px", entry_px))
            if abs(tp1_px - entry_px) > 1e-9:
                progress = abs(cur_px - entry_px) / abs(tp1_px - entry_px)
                if progress >= 0.75:
                    count += 1
        return count

    def get_dynamic_slot_limit(self) -> int:
        """
        Base dynamic concurrent trade slot limit:
        Starts at 6 at $500.00 equity and grows +1 per +$75.00 of account growth.
        """
        return DESK_CONFIG.compute_dynamic_max_positions(self.get_current_equity())

    def get_elastic_slot_limit(self, required_margin_usd: float = 80.0) -> int:
        """
        Elastic Flex-Slot Overdrive limit:
        Allows flexing +1 to +2 burst slots (e.g., Slot #8 / #9) when all base slots are full,
        free margin is available, and >=1 open positions are risk-free or imminent-to-close.
        """
        return DESK_CONFIG.compute_elastic_slot_limit(
            current_equity=self.get_current_equity(),
            risk_free_or_closing_count=self.get_risk_free_or_closing_count(),
            free_margin_usd=self.get_free_margin(),
            required_margin_usd=required_margin_usd,
        )

    def recycle_scalp_slot_for_trigger(self, trigger_symbol: str) -> Optional[Dict[str, Any]]:
        """
        If all base slots are full and an armed plan triggers, checks if any open SCALP_BOOK
        position is in profit or stagnant so it can be harvested 100% immediately to free the slot.
        """
        for pos_id, pos in list(self.active_positions.items()):
            if pos.get("book") == "SCALP_BOOK" and float(pos.get("unrealized_pnl_usd", 0.0)) > 0.15:
                return self._close_position(
                    pos_id,
                    float(pos["current_px"]),
                    f"SCALP_SLOT_RECYCLED_FOR_{trigger_symbol}_TRIGGER",
                    is_maker=True,
                )
        return None

    def can_coexist_in_dual_book(self, symbol: str, book: str, side: str) -> tuple[bool, str]:
        """
        Checks Dual-Book coexistence rules:
        - Never allow duplicate active positions in the SAME book for the SAME symbol.
        - A SCALP_BOOK position CAN coexist with a SWING_BOOK position on the same symbol
          ONLY if the SWING_BOOK position is already in a `risk-free state` (TP1 banked & SL locked at breakeven+fees).
        """
        for pos in self.active_positions.values():
            if pos["symbol"] != symbol:
                continue
            if pos["book"] == book:
                return False, f"Duplicate position in {book} for {symbol} already open."
            # Opposite book exists on same symbol: verify swing trade is risk-free
            if not pos.get("is_risk_free", False):
                return (
                    False,
                    f"Cannot open {book} on {symbol} while existing {pos['book']} position is not yet risk-free (TP1 unbanked).",
                )
        return True, "Dual-Book coexistence verified."

    def execute_paper_entry(
        self,
        symbol: str,
        book: str,  # "SWING_BOOK" | "SCALP_BOOK"
        side: str,  # "LONG" | "SHORT"
        playbook: str,
        entry_px: float,
        stop_loss: float,
        tp1_px: float,
        tp2_px: float,
        notional_usd: float,
        leverage: float,
        l2_book: Optional[Dict[str, Any]],
        invalidation_rule: str,
        if_then_matrix: Dict[str, str],
        confidence_pct: float,
        wave_label: str,
        post_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes a 2-tranche (50% TP1 + 50% TP2 runner) paper entry against live L2 book.
        Enforces $10.00 minimum notional per tranche and records realistic maker/taker fees.
        """
        if self.execution_mode != "AUTO_EXECUTE":
            self._record_order_audit(
                symbol, book, side, "LIMIT_POST_ONLY", "REJECTED", notional_usd, entry_px, 0.0,
                f"Desk execution mode is {self.execution_mode}"
            )
            return {"ok": False, "reason": f"Execution mode is {self.execution_mode}"}

        coexist_ok, coexist_msg = self.can_coexist_in_dual_book(symbol, book, side)
        if not coexist_ok:
            self._record_order_audit(symbol, book, side, "LIMIT_POST_ONLY", "REJECTED", notional_usd, entry_px, 0.0, coexist_msg)
            return {"ok": False, "reason": coexist_msg}

        tranche1_notional = round(notional_usd * 0.50, 2)
        tranche2_notional = round(notional_usd - tranche1_notional, 2)

        if tranche1_notional < DESK_CONFIG.min_order_notional_usd or tranche2_notional < DESK_CONFIG.min_order_notional_usd:
            reason = (
                f"Tranche notional (${tranche1_notional:.2f} / ${tranche2_notional:.2f}) violates "
                f"Hyperliquid ${DESK_CONFIG.min_order_notional_usd:.2f} minimum order notional."
            )
            self._record_order_audit(symbol, book, side, "LIMIT_POST_ONLY", "REJECTED", notional_usd, entry_px, 0.0, reason)
            return {"ok": False, "reason": reason}

        # Realistic L2 fill price & fee calculation
        spread_bps = l2_book.get("spread_bps", 1.5) if l2_book else 1.5
        if post_only:
            fill_px = entry_px
            entry_fee = notional_usd * DESK_CONFIG.maker_fee_rate
            order_type = "POST_ONLY_LIMIT"
        else:
            slip_mult = (spread_bps * 0.5 / 10000.0) * (1.0 if side == "LONG" else -1.0)
            fill_px = entry_px * (1.0 + slip_mult)
            entry_fee = notional_usd * DESK_CONFIG.taker_fee_rate
            order_type = "MARKET_IOC"

        self.total_fees_paid_usd += entry_fee
        self.realized_pnl_usd -= entry_fee

        position_id = f"pos_{symbol.lower()}_{book[:5].lower()}_{int(time.time() * 1000) % 1000000}"
        qty_coins = notional_usd / fill_px if fill_px > 0 else 0.0
        now = time.time()

        pos = {
            "position_id": position_id,
            "symbol": symbol,
            "book": book,
            "side": side,
            "playbook": playbook,
            "order_type": order_type,
            "entry_px": round(fill_px, 6),
            "current_px": round(fill_px, 6),
            "initial_stop_loss": round(stop_loss, 6),
            "stop_loss": round(stop_loss, 6),
            "tp1_px": round(tp1_px, 6),
            "tp2_px": round(tp2_px, 6),
            "initial_notional_usd": round(notional_usd, 2),
            "remaining_notional_usd": round(notional_usd, 2),
            "tranche1_notional_usd": tranche1_notional,
            "tranche2_notional_usd": tranche2_notional,
            "qty_coins": round(qty_coins, 5),
            "leverage": round(leverage, 2),
            "margin_used_usd": round(notional_usd / max(1.0, leverage), 2),
            "tp1_filled": False,
            "is_risk_free": False,
            "realized_pnl_usd": round(-entry_fee, 3),
            "unrealized_pnl_usd": 0.0,
            "unrealized_pnl_pct": 0.0,
            "fees_paid_usd": round(entry_fee, 4),
            "mae_pct": 0.0,
            "mfe_pct": 0.0,
            "confidence_pct": round(confidence_pct, 1),
            "wave_label": wave_label,
            "invalidation_rule": invalidation_rule,
            "if_then_matrix": if_then_matrix,
            "opened_at": now,
            "updated_at": now,
            "status": "OPEN",
        }
        self.active_positions[position_id] = pos
        self._persist_position(pos)

        self._record_order_audit(
            symbol,
            book,
            side,
            order_type,
            "FILLED",
            notional_usd,
            fill_px,
            entry_fee,
            f"Opened 2-tranche ({book}) position: TP1 ${tranche1_notional:.2f} @ {tp1_px:.4f} | TP2 ${tranche2_notional:.2f} @ {tp2_px:.4f}",
        )
        return {"ok": True, "position": pos}

    def mark_to_market_and_manage_lifecycle(
        self,
        live_markets: Dict[str, Dict[str, Any]],
        flow_by_symbol: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Updates every open position against real-time Hyperliquid mark prices:
        1. Computes live Unrealized P&L, MAE (Max Adverse Excursion), and MFE (Max Favorable Excursion).
        2. Checks TP1 fill -> banks 50% tranche, moves Stop-Loss to Breakeven + fees (`is_risk_free = True`).
        3. Checks TP2 fill -> closes remaining 50% runner tranche.
        4. Checks Hard Stop-Loss or Behavioral Invalidation -> exits position immediately.
        5. Updates $500 equity curve and checks 5% daily / 12% peak drawdown circuit breakers.
        """
        events: List[Dict[str, Any]] = []
        now = time.time()

        for pos_id, pos in list(self.active_positions.items()):
            sym = pos["symbol"]
            mkt = live_markets.get(sym)
            if not mkt:
                continue

            px = float(mkt["mark_px"])
            entry_px = float(pos["entry_px"])
            side = pos["side"]
            dir_mult = 1.0 if side == "LONG" else -1.0

            move_pct = dir_mult * ((px - entry_px) / entry_px) * 100.0
            if move_pct < 0:
                pos["mae_pct"] = round(max(float(pos["mae_pct"]), abs(move_pct)), 3)
            else:
                pos["mfe_pct"] = round(max(float(pos["mfe_pct"]), move_pct), 3)

            rem_notional = float(pos["remaining_notional_usd"])
            unrealized = rem_notional * dir_mult * ((px - entry_px) / entry_px)
            pos["current_px"] = round(px, 6)
            pos["unrealized_pnl_usd"] = round(unrealized, 3)
            pos["unrealized_pnl_pct"] = round(move_pct * float(pos["leverage"]), 2)
            pos["updated_at"] = now

            # 1. Check Hard Stop-Loss
            stop_hit = (side == "LONG" and px <= float(pos["stop_loss"])) or (
                side == "SHORT" and px >= float(pos["stop_loss"])
            )
            if stop_hit:
                ev = self._close_position(pos_id, float(pos["stop_loss"]), "STOP_LOSS_TRIGGERED", is_maker=False)
                if ev:
                    events.append(ev)
                continue

            # 2. Dynamic Target & Turnaround Intelligence at TP1
            if not pos["tp1_filled"]:
                tp1_hit = (side == "LONG" and px >= float(pos["tp1_px"])) or (
                    side == "SHORT" and px <= float(pos["tp1_px"])
                )
                if tp1_hit:
                    # SCALP_BOOK RULE: 100% Point-to-Point Single-Target Exit (Never leave a lingering scalp runner blocking a slot!)
                    if pos.get("book") == "SCALP_BOOK" and DESK_CONFIG.scalp_single_target_full_exit:
                        ev = self._close_position(
                            pos_id,
                            float(pos["tp1_px"]),
                            "SCALP_100PCT_POINT_TO_POINT_TARGET_EXIT",
                            is_maker=True,
                        )
                        if ev:
                            ev["dynamic_action"] = "SCALP_SLOT_FREED_AT_TARGET"
                            events.append(ev)
                        continue

                    flow = (flow_by_symbol or {}).get(sym, {})
                    rvol = float(flow.get("rvol", 1.1))
                    cvd_div = str(flow.get("cvd_divergence", "NONE"))
                    sfp_detected = bool(flow.get("sfp_detected", False))

                    # Detect if TP1 is a structural turnaround / exhaustion wall
                    opposing_absorption = (
                        (side == "SHORT" and (cvd_div == "BULLISH_AGGRESSION" or sfp_detected or rvol < 0.88))
                        or (side == "LONG" and (cvd_div == "BEARISH_AGGRESSION" or sfp_detected or rvol < 0.88))
                    )

                    # MODE A: Turnaround Target -> Harvest 100% at TP1 & Signal Counter-Scalp / Re-Entry
                    if opposing_absorption:
                        ev = self._close_position(
                            pos_id,
                            float(pos["tp1_px"]),
                            "TP1_TURNAROUND_100PCT_HARVEST_AND_FLIP",
                            is_maker=True,
                        )
                        if ev:
                            ev["dynamic_action"] = "TURNAROUND_HARVEST_100PCT_AND_FLIP_SCALP"
                            events.append(ev)
                        continue

                    # Otherwise bank 50% Tranche 1 at TP1
                    t1_notional = float(pos["tranche1_notional_usd"])
                    t1_px = float(pos["tp1_px"])
                    t1_fee = t1_notional * DESK_CONFIG.maker_fee_rate
                    t1_gross = t1_notional * dir_mult * ((t1_px - entry_px) / entry_px)
                    t1_net = t1_gross - t1_fee

                    self.realized_pnl_usd += t1_net
                    self.total_fees_paid_usd += t1_fee
                    pos["tp1_filled"] = True
                    pos["is_risk_free"] = True
                    pos["remaining_notional_usd"] = float(pos["tranche2_notional_usd"])
                    pos["realized_pnl_usd"] = round(float(pos["realized_pnl_usd"]) + t1_net, 3)
                    pos["fees_paid_usd"] = round(float(pos["fees_paid_usd"]) + t1_fee, 4)

                    # MODE B vs MODE C: Dynamic Stop Ratchet vs High-Velocity Impulse Runner
                    if rvol < 1.45:
                        # MODE B: Moderate momentum -> Lock 65% of TP1 profit distance so a turnaround doesn't round-trip to entry
                        new_sl = entry_px + 0.65 * (t1_px - entry_px)
                        sl_mode_label = "65% Profit-Ratchet SL"
                    else:
                        # MODE C: High-velocity RVOL >= 1.45 breakout -> Lock Breakeven + 25% cushion to give TP2 runner room
                        new_sl = entry_px + 0.25 * (t1_px - entry_px)
                        sl_mode_label = "Impulse Runner +25% Cushion SL"

                    pos["stop_loss"] = round(new_sl, 6)
                    self._persist_position(pos)

                    self._record_order_audit(
                        sym,
                        pos["book"],
                        "SELL" if side == "LONG" else "BUY",
                        "TP1_LIMIT_MAKER",
                        "FILLED",
                        t1_notional,
                        t1_px,
                        t1_fee,
                        f"TP1 banked (+${t1_net:.2f}). Dynamic {sl_mode_label} locked at {new_sl:.5f} (RVOL {rvol:.2f}x).",
                    )
                    events.append(
                        {
                            "type": "TP1_BANKED_DYNAMIC_LOCK",
                            "symbol": sym,
                            "book": pos["book"],
                            "net_pnl_usd": round(t1_net, 2),
                            "new_stop_loss": round(new_sl, 6),
                            "sl_mode": sl_mode_label,
                        }
                    )
            else:
                # Active TP2 Runner: Dynamically ratchet SL to 65% of MFE if momentum stalls mid-flight
                flow = (flow_by_symbol or {}).get(sym, {})
                rvol = float(flow.get("rvol", 1.1))
                cvd_div = str(flow.get("cvd_divergence", "NONE"))
                opposing_flip = (side == "SHORT" and cvd_div == "BULLISH_AGGRESSION") or (
                    side == "LONG" and cvd_div == "BEARISH_AGGRESSION"
                )
                if opposing_flip and move_pct > 0.40:
                    ev = self._close_position(
                        pos_id,
                        px,
                        "RUNNER_TURNAROUND_HARVEST_CVD_FLIP",
                        is_maker=True,
                    )
                    if ev:
                        events.append(ev)
                    continue

            # 3. Check TP2 Runner Fill
            tp2_hit = (side == "LONG" and px >= float(pos["tp2_px"])) or (
                side == "SHORT" and px <= float(pos["tp2_px"])
            )
            if tp2_hit:
                ev = self._close_position(pos_id, float(pos["tp2_px"]), "TP2_RUNNER_TARGET_HIT", is_maker=True)
                if ev:
                    events.append(ev)
                continue

            # 4. Check Behavioral Invalidation (15m opposing order-flow / CVD absorption flip)
            if flow_by_symbol and sym in flow_by_symbol:
                flow = flow_by_symbol[sym]
                cvd_div = flow.get("cvd_divergence", "NONE")
                rvol = float(flow.get("rvol", 1.0))
                if side == "LONG" and cvd_div == "BEARISH_AGGRESSION" and rvol >= 1.85 and move_pct < -0.35:
                    ev = self._close_position(pos_id, px, "BEHAVIORAL_INVALIDATION_CVD_FLIP", is_maker=False)
                    if ev:
                        events.append(ev)
                    continue
                elif side == "SHORT" and cvd_div == "BULLISH_AGGRESSION" and rvol >= 1.85 and move_pct < -0.35:
                    ev = self._close_position(pos_id, px, "BEHAVIORAL_INVALIDATION_CVD_FLIP", is_maker=False)
                    if ev:
                        events.append(ev)
                    continue

        # Update equity high-water marks (anchored to realized equity so transient unrealized spikes don't false-trip daily DD)
        eq = self.get_current_equity()
        realized_eq = round(DESK_CONFIG.initial_equity_usd + self.realized_pnl_usd, 2)
        self.day_high_equity_usd = max(self.day_high_equity_usd, realized_eq)
        self.peak_equity_usd = max(self.peak_equity_usd, realized_eq)

        daily_floor = DESK_CONFIG.compute_daily_drawdown_floor(self.day_high_equity_usd)
        peak_floor = DESK_CONFIG.compute_peak_drawdown_floor(self.peak_equity_usd)

        if eq <= peak_floor and not self.circuit_breaker_tripped:
            self.circuit_breaker_tripped = True
            self.execution_mode = "KILL_SWITCH"
            self.circuit_breaker_reason = f"12% Peak Drawdown Circuit Breaker Tripped (Equity ${eq:.2f} <= ${peak_floor:.2f})"
            self.close_all_positions(live_markets, reason="PEAK_DRAWDOWN_KILL_SWITCH")
        elif eq <= daily_floor and not self.circuit_breaker_tripped:
            self.circuit_breaker_tripped = True
            self.execution_mode = "PAUSE"
            self.circuit_breaker_reason = f"5% Daily Drawdown Circuit Breaker Tripped (Equity ${eq:.2f} <= ${daily_floor:.2f})"

        dd_pct = ((self.peak_equity_usd - eq) / self.peak_equity_usd * 100.0) if self.peak_equity_usd > 0 else 0.0
        if not self.equity_curve or (int(now) - self.equity_curve[-1]["time"]) >= 5:
            self.equity_curve.append(
                {
                    "time": int(now),
                    "equity": eq,
                    "cash": round(DESK_CONFIG.initial_equity_usd + self.realized_pnl_usd - self.get_used_margin(), 2),
                    "unrealized_pnl": round(self.get_unrealized_pnl(), 2),
                    "drawdown_pct": round(dd_pct, 2),
                }
            )
            if len(self.equity_curve) > 360:
                self.equity_curve = self.equity_curve[-360:]

        return events

    def _close_position(self, position_id: str, exit_px: float, reason: str, is_maker: bool = False) -> Optional[Dict[str, Any]]:
        pos = self.active_positions.pop(position_id, None)
        if not pos:
            return None

        entry_px = float(pos["entry_px"])
        rem_notional = float(pos["remaining_notional_usd"])
        side = pos["side"]
        dir_mult = 1.0 if side == "LONG" else -1.0

        fee_rate = DESK_CONFIG.maker_fee_rate if is_maker else DESK_CONFIG.taker_fee_rate
        exit_fee = rem_notional * fee_rate
        gross_pnl = rem_notional * dir_mult * ((exit_px - entry_px) / entry_px)
        net_leg_pnl = gross_pnl - exit_fee

        self.realized_pnl_usd += net_leg_pnl
        self.total_fees_paid_usd += exit_fee

        pos["current_px"] = round(exit_px, 6)
        pos["remaining_notional_usd"] = 0.0
        pos["unrealized_pnl_usd"] = 0.0
        pos["realized_pnl_usd"] = round(float(pos["realized_pnl_usd"]) + net_leg_pnl, 3)
        pos["fees_paid_usd"] = round(float(pos["fees_paid_usd"]) + exit_fee, 4)
        pos["closed_at"] = time.time()
        pos["close_reason"] = reason
        pos["status"] = "CLOSED"

        self.closed_positions.insert(0, pos)
        if len(self.closed_positions) > 100:
            self.closed_positions = self.closed_positions[:100]
        self._persist_position(pos)

        self._record_order_audit(
            pos["symbol"],
            pos["book"],
            "SELL" if side == "LONG" else "BUY",
            "LIMIT_MAKER" if is_maker else "STOP_MARKET_TAKER",
            "FILLED",
            rem_notional,
            exit_px,
            exit_fee,
            f"Closed {pos['book']} ({reason}) | Total Position Net P&L: ${pos['realized_pnl_usd']:.2f}",
        )
        return {
            "type": "POSITION_CLOSED",
            "position_id": position_id,
            "symbol": pos["symbol"],
            "book": pos["book"],
            "reason": reason,
            "net_pnl_usd": pos["realized_pnl_usd"],
        }

    def close_all_positions(self, live_markets: Dict[str, Dict[str, Any]], reason: str = "MANUAL_KILL_SWITCH") -> List[Dict[str, Any]]:
        closed = []
        for pos_id, pos in list(self.active_positions.items()):
            sym = pos["symbol"]
            px = float(live_markets.get(sym, {}).get("mark_px", pos["current_px"]))
            ev = self._close_position(pos_id, px, reason, is_maker=False)
            if ev:
                closed.append(ev)
        return closed

    def _persist_position(self, pos: Dict[str, Any]) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO paper_trades
                    (position_id, book, symbol, side, playbook, status, entry_px, current_px, stop_loss, tp1_px, tp2_px,
                     notional_usd, realized_pnl_usd, fees_usd, mae_pct, mfe_pct, opened_at, closed_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pos["position_id"],
                        pos["book"],
                        pos["symbol"],
                        pos["side"],
                        pos["playbook"],
                        pos["status"],
                        pos["entry_px"],
                        pos["current_px"],
                        pos["stop_loss"],
                        pos["tp1_px"],
                        pos["tp2_px"],
                        pos["initial_notional_usd"],
                        pos["realized_pnl_usd"],
                        pos["fees_paid_usd"],
                        pos["mae_pct"],
                        pos["mfe_pct"],
                        pos["opened_at"],
                        pos.get("closed_at") or 0.0,
                        json.dumps(pos),
                    ),
                )
                conn.commit()
        except Exception:
            pass

    def get_ledger_state(self) -> Dict[str, Any]:
        eq = self.get_current_equity()
        used_margin = self.get_used_margin()
        free_collateral = round(max(0.0, eq - used_margin), 2)
        dynamic_max_slots = self.get_dynamic_slot_limit()
        swing_positions = [p for p in self.active_positions.values() if p["book"] == "SWING_BOOK"]
        scalp_positions = [p for p in self.active_positions.values() if p["book"] == "SCALP_BOOK"]

        daily_floor = DESK_CONFIG.compute_daily_drawdown_floor(self.day_high_equity_usd)
        peak_floor = DESK_CONFIG.compute_peak_drawdown_floor(self.peak_equity_usd)
        daily_dd_usd = round(max(0.0, self.day_high_equity_usd - eq), 2)
        peak_dd_usd = round(max(0.0, self.peak_equity_usd - eq), 2)

        return {
            "initial_equity_usd": DESK_CONFIG.initial_equity_usd,
            "current_equity_usd": eq,
            "free_collateral_usd": free_collateral,
            "used_margin_usd": used_margin,
            "open_notional_usd": self.get_total_open_notional(),
            "effective_leverage": round(self.get_total_open_notional() / max(1.0, eq), 2),
            "realized_pnl_usd": round(self.realized_pnl_usd, 2),
            "unrealized_pnl_usd": round(self.get_unrealized_pnl(), 2),
            "total_pnl_usd": round(eq - DESK_CONFIG.initial_equity_usd, 2),
            "total_pnl_pct": round((eq - DESK_CONFIG.initial_equity_usd) / DESK_CONFIG.initial_equity_usd * 100.0, 2),
            "total_fees_paid_usd": round(self.total_fees_paid_usd, 3),
            "active_positions_count": len(self.active_positions),
            "swing_active_count": len(swing_positions),
            "scalp_active_count": len(scalp_positions),
            "base_max_positions": DESK_CONFIG.base_max_concurrent_positions,
            "dynamic_max_positions": dynamic_max_slots,
            "elastic_flex_max_positions": self.get_elastic_slot_limit(),
            "scalp_stack_max_positions": DESK_CONFIG.compute_dynamic_scalp_slots(eq),
            "next_slot_equity_target_usd": round(
                DESK_CONFIG.initial_equity_usd
                + (dynamic_max_slots - DESK_CONFIG.base_max_concurrent_positions + 1) * DESK_CONFIG.equity_step_per_extra_slot,
                2,
            ),
            "max_risk_per_trade_usd": DESK_CONFIG.compute_max_risk_usd(eq, "SWING_BOOK"),
            "scalp_max_risk_per_trade_usd": DESK_CONFIG.compute_max_risk_usd(eq, "SCALP_BOOK"),
            "daily_drawdown_usd": daily_dd_usd,
            "daily_drawdown_limit_usd": round(self.day_high_equity_usd * DESK_CONFIG.daily_drawdown_limit_pct, 2),
            "daily_drawdown_floor_usd": daily_floor,
            "peak_drawdown_usd": peak_dd_usd,
            "peak_drawdown_limit_usd": round(self.peak_equity_usd * DESK_CONFIG.peak_drawdown_limit_pct, 2),
            "peak_drawdown_floor_usd": peak_floor,
            "execution_mode": self.execution_mode,
            "circuit_breaker_tripped": self.circuit_breaker_tripped,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "swing_book": swing_positions,
            "scalp_book": scalp_positions,
            "closed_positions": self.closed_positions[:25],
            "order_audit_log": self.order_audit_log[:35],
            "equity_curve": self.equity_curve,
        }
