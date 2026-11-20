"""
Deterministic Configuration & Risk Limits for the QuantPilot Autonomous Paper Trading Desk.
All risk limits, scaling formulas, fee schedules, and exchange parameters are strictly enforced here.
"""
from __future__ import annotations
import math
import os
from dataclasses import dataclass, field
from typing import List

# Sanitize IPv6 bracket literal in NO_PROXY so httpx URLPattern parser succeeds while keeping HTTP_PROXY/HTTPS_PROXY intact
for _proxy_var in ("NO_PROXY", "no_proxy"):
    if _proxy_var in os.environ and "[::1]" in os.environ[_proxy_var]:
        os.environ[_proxy_var] = os.environ[_proxy_var].replace("[::1]", "127.0.0.1")



@dataclass
class DeskConfig:
    # Account & Risk Limits (Dual-Stack Architecture: SWING_STACK + SCALP_STACK + Flex Overdrive)
    initial_equity_usd: float = 500.00
    max_risk_per_trade_pct: float = 0.020          # 2.0% ($10.00 at $500) max risk for SWING_BOOK trades
    scalp_max_risk_per_trade_pct: float = 0.025    # 2.5% ($12.50 at $500) tactical risk for tight-stop SCALP_BOOK micro-trades
    max_perp_leverage: float = 5.0                 # 5x cap on perpetuals
    max_spot_leverage: float = 1.0                 # 1x cap on spot

    # Stack 1: SWING_BOOK Slot Stack + Elastic Flex-Slot Overdrive
    base_max_concurrent_positions: int = 6         # Swing Stack starts at 6 at $500.00, scales +1 per +$75.00 growth
    equity_step_per_extra_slot: float = 75.00      # +1 Swing slot per +$75.00 of account growth
    max_flex_burst_slots: int = 2                  # +1 to +2 elastic Flex Slots when open swings are risk-free/closing & margin exists
    max_concurrent_positions_cap: int = 16         # Hard institutional ceiling on Swing Stack

    # Stack 2: Dedicated SCALP_BOOK Micro-Sniper Slot Stack (Fast In-and-Out 1m/5m Trading)
    base_max_scalp_slots: int = 3                  # 3 dedicated Scalp Slots running in parallel with Swing Stack
    equity_step_per_scalp_slot: float = 150.00     # +1 Scalp slot per +$150.00 of account growth
    max_scalp_slots_cap: int = 6                   # Hard ceiling on concurrent micro-scalps
    scalp_single_target_full_exit: bool = True     # Scalps exit 100% at TP1 (never leave a lingering runner blocking a slot)
    scalp_max_hold_minutes: int = 18               # Stagnant scalp velocity time-stop (recycles slot if target not hit in 18m)

    # Exchange & Order Sizing Constraints (Hyperliquid Realistic Schedule)
    min_order_notional_usd: float = 10.00          # Hyperliquid $10 minimum order notional per leg
    min_position_notional_usd: float = 20.00       # $10 TP1 tranche + $10 TP2 runner tranche
    maker_fee_rate: float = 0.00015                # 1.5 bps (0.0150%) Post-Only Maker fee
    taker_fee_rate: float = 0.00045                # 4.5 bps (0.0450%) Market/Stop Taker fee

    # Circuit Breakers (Chief Risk Officer Sentinel)
    daily_drawdown_limit_pct: float = 0.050        # 5.0% ($25.00 on $500) daily drawdown auto-halt
    peak_drawdown_limit_pct: float = 0.120         # 12.0% ($60.00 on $500) peak drawdown kill-switch
    max_correlated_exposure_pct: float = 0.65      # Max 65% of max notional in same directional beta cluster
    max_feed_staleness_seconds: float = 15.0       # Reject trade if L2/candle feed is older than 15s

    # Universe Scope & Liquidity Filter
    anchor_symbols: List[str] = field(default_factory=lambda: ["BTC", "SOL", "ETH", "XRP"])
    candidate_rotators: List[str] = field(
        default_factory=lambda: [
            "HYPE", "SUI", "DOGE", "AVAX", "LINK", "AAVE", "ENA", "NEAR", "ARB", "OP", "PEPE", "WIF", "TIA", "INJ"
        ]
    )
    min_24h_volume_usd: float = 10_000_000.0       # >$10M minimum 24h volume filter

    # Endpoints & Ports
    quantpilot_mcp_url: str = os.environ.get("QUANTPILOT_MCP_URL", "https://mcp.quantpilot.com/mcp")
    quantpilot_mcp_token: str = os.environ.get("QUANTPILOT_MCP_TOKEN", "")
    hyperliquid_info_url: str = "https://api.hyperliquid.xyz/info"
    hyperliquid_ws_url: str = "wss://api.hyperliquid.xyz/ws"
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8000
    sqlite_db_path: str = "/home/antigravity/quantpilot_desk/data/desk_ledger.sqlite"

    def compute_dynamic_max_positions(self, current_equity: float) -> int:
        """
        Dynamically scales the base SWING_BOOK concurrent positions limit as the account grows.
        Starts at 6 for $500.00 equity and adds +1 slot for every +$75.00 in equity growth.
        """
        growth = max(0.0, current_equity - self.initial_equity_usd)
        extra_slots = int(math.floor(growth / self.equity_step_per_extra_slot))
        return min(self.max_concurrent_positions_cap, self.base_max_concurrent_positions + extra_slots)

    def compute_dynamic_scalp_slots(self, current_equity: float) -> int:
        """
        Dedicated SCALP_BOOK Micro-Sniper Slot Stack:
        Starts at 3 dedicated fast-turnover slots at $500.00 equity and scales +1 per +$150.00 growth.
        """
        growth = max(0.0, current_equity - self.initial_equity_usd)
        extra = int(math.floor(growth / self.equity_step_per_scalp_slot))
        return min(self.max_scalp_slots_cap, self.base_max_scalp_slots + extra)

    def compute_elastic_slot_limit(
        self,
        current_equity: float,
        risk_free_or_closing_count: int = 0,
        free_margin_usd: float = 0.0,
        required_margin_usd: float = 80.0,
    ) -> int:
        """
        Elastic Flex-Slot Overdrive for SWING_BOOK:
        If all base Swing slots (e.g., 7/7) are full and an armed plan trigger is ready to execute,
        allows flexing up to +2 extra slots (e.g., Slot #8 / #9) when:
          1. Available Free Margin >= 1.25x required margin for the new trade, AND
          2. At least 1 open trade is already Risk-Free (TP1 banked + BE/Ratchet SL locked)
             or imminent-to-close (>75% progress to target).
        """
        base_slots = self.compute_dynamic_max_positions(current_equity)
        if free_margin_usd >= required_margin_usd * 1.25 and risk_free_or_closing_count > 0:
            flex_bonus = min(self.max_flex_burst_slots, risk_free_or_closing_count)
            return min(self.max_concurrent_positions_cap, base_slots + flex_bonus)
        return base_slots

    def compute_max_risk_usd(self, current_equity: float, book: str = "SWING_BOOK") -> float:
        """Deterministic max dollar risk per trade: 2.0% for SWING_BOOK, 2.5% for tight-stop SCALP_BOOK."""
        pct = self.scalp_max_risk_per_trade_pct if book == "SCALP_BOOK" else self.max_risk_per_trade_pct
        return round(current_equity * pct, 2)

    def compute_daily_drawdown_floor(self, day_high_equity: float) -> float:
        """5.0% daily drawdown floor from rolling 24h high-water mark."""
        return round(day_high_equity * (1.0 - self.daily_drawdown_limit_pct), 2)

    def compute_peak_drawdown_floor(self, peak_equity: float) -> float:
        """12.0% peak drawdown floor from all-time paper high-water mark."""
        return round(peak_equity * (1.0 - self.peak_drawdown_limit_pct), 2)


DESK_CONFIG = DeskConfig()
