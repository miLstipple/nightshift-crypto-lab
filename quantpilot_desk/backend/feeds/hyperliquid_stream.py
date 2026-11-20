"""Live Hyperliquid market data feed & L2 orderbook stream."""
from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List, Optional
import httpx

from backend.config import DESK_CONFIG


class HyperliquidDataFeed:
    """
    Streams and caches real-time market metadata, 24h volume, Open Interest, Funding Rates,
    L2 Order Books, and multi-timeframe OHLCV candles from Hyperliquid (`https://api.hyperliquid.xyz/info`).
    """

    def __init__(self) -> None:
        self.info_url = DESK_CONFIG.hyperliquid_info_url
        self._client: Optional[httpx.AsyncClient] = None
        self.markets: Dict[str, Dict[str, Any]] = {}
        self.l2_books: Dict[str, Dict[str, Any]] = {}
        self.candles_cache: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self.last_meta_sync: float = 0.0
        self.last_l2_sync: Dict[str, float] = {}
        self.last_candle_sync: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=12.0, headers={"Content-Type": "application/json"})
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch_universe_and_contexts(self) -> Dict[str, Dict[str, Any]]:
        """
        Queries Hyperliquid `metaAndAssetCtxs` to get real-time prices, 24h notional volume,
        Open Interest, funding rates, and leverage caps for all perpetuals, plus spot context.
        """
        client = await self.get_client()
        resp = await client.post(self.info_url, json={"type": "metaAndAssetCtxs"})
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list) or len(data) < 2:
            return self.markets

        universe = data[0].get("universe", [])
        asset_ctxs = data[1]
        now = time.time()

        updated: Dict[str, Dict[str, Any]] = {}
        for idx, meta in enumerate(universe):
            if meta.get("isDelisted"):
                continue
            if idx >= len(asset_ctxs):
                break
            ctx = asset_ctxs[idx]
            symbol = meta.get("name", "")
            mark_px = float(ctx.get("markPx") or 0.0)
            mid_px = float(ctx.get("midPx") or mark_px)
            oracle_px = float(ctx.get("oraclePx") or mark_px)
            prev_day_px = float(ctx.get("prevDayPx") or mark_px)
            day_ntl_vlm = float(ctx.get("dayNtlVlm") or 0.0)
            open_interest_coins = float(ctx.get("openInterest") or 0.0)
            open_interest_usd = open_interest_coins * mark_px
            funding_rate = float(ctx.get("funding") or 0.0)

            if mark_px <= 0:
                continue

            change_24h_pct = ((mark_px - prev_day_px) / prev_day_px * 100.0) if prev_day_px > 0 else 0.0
            impact_pxs = ctx.get("impactPxs") or [mid_px, mid_px]
            bid_impact = float(impact_pxs[0]) if len(impact_pxs) > 0 and impact_pxs[0] else mid_px
            ask_impact = float(impact_pxs[1]) if len(impact_pxs) > 1 and impact_pxs[1] else mid_px
            spread_bps = ((ask_impact - bid_impact) / mid_px * 10000.0) if mid_px > 0 else 1.5

            updated[symbol] = {
                "symbol": symbol,
                "market_type": "PERP",
                "sz_decimals": int(meta.get("szDecimals", 2)),
                "max_exchange_leverage": float(meta.get("maxLeverage", 5)),
                "desk_max_leverage": min(float(meta.get("maxLeverage", 5)), DESK_CONFIG.max_perp_leverage),
                "mark_px": mark_px,
                "mid_px": mid_px,
                "oracle_px": oracle_px,
                "prev_day_px": prev_day_px,
                "change_24h_pct": round(change_24h_pct, 2),
                "volume_24h_usd": round(day_ntl_vlm, 2),
                "open_interest_usd": round(open_interest_usd, 2),
                "open_interest_coins": round(open_interest_coins, 4),
                "funding_rate_hourly": funding_rate,
                "funding_rate_annualized_pct": round(funding_rate * 24 * 365 * 100.0, 2),
                "spread_bps": round(max(0.5, spread_bps), 2),
                "is_anchor": symbol in DESK_CONFIG.anchor_symbols,
                "meets_liquidity_filter": day_ntl_vlm >= DESK_CONFIG.min_24h_volume_usd,
                "timestamp": now,
            }

        async with self._lock:
            self.markets = updated
            self.last_meta_sync = now
        return updated

    async def fetch_l2_book(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches real-time L2 Order Book for `symbol` from Hyperliquid (`l2Book`),
        computes bid/ask liquidity walls, top-of-book spread, and order book imbalance.
        """
        now = time.time()
        if symbol in self.l2_books and (now - self.last_l2_sync.get(symbol, 0.0)) < 2.5:
            return self.l2_books[symbol]

        client = await self.get_client()
        resp = await client.post(self.info_url, json={"type": "l2Book", "coin": symbol})
        resp.raise_for_status()
        raw = resp.json()
        levels = raw.get("levels", [[], []])
        raw_bids = levels[0] if len(levels) > 0 else []
        raw_asks = levels[1] if len(levels) > 1 else []

        bids = []
        asks = []
        total_bid_usd = 0.0
        total_ask_usd = 0.0
        max_bid_wall = {"px": 0.0, "usd": 0.0, "sz": 0.0}
        max_ask_wall = {"px": 0.0, "usd": 0.0, "sz": 0.0}

        for b in raw_bids[:20]:
            px = float(b["px"])
            sz = float(b["sz"])
            usd = px * sz
            total_bid_usd += usd
            bids.append({"px": px, "sz": sz, "usd": round(usd, 2), "n": int(b.get("n", 1))})
            if usd > max_bid_wall["usd"]:
                max_bid_wall = {"px": px, "sz": sz, "usd": round(usd, 2)}

        for a in raw_asks[:20]:
            px = float(a["px"])
            sz = float(a["sz"])
            usd = px * sz
            total_ask_usd += usd
            asks.append({"px": px, "sz": sz, "usd": round(usd, 2), "n": int(a.get("n", 1))})
            if usd > max_ask_wall["usd"]:
                max_ask_wall = {"px": px, "sz": sz, "usd": round(usd, 2)}

        best_bid = bids[0]["px"] if bids else 0.0
        best_ask = asks[0]["px"] if asks else 0.0
        mid_px = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else best_bid or best_ask
        spread_bps = ((best_ask - best_bid) / mid_px * 10000.0) if mid_px > 0 else 0.0

        # Top-5 depth for immediate execution slippage estimation
        top5_bid_usd = sum(x["usd"] for x in bids[:5])
        top5_ask_usd = sum(x["usd"] for x in asks[:5])
        total_depth = total_bid_usd + total_ask_usd
        imbalance_ratio = ((total_bid_usd - total_ask_usd) / total_depth) if total_depth > 0 else 0.0

        book = {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_px": mid_px,
            "spread_bps": round(spread_bps, 3),
            "bids": bids[:12],
            "asks": asks[:12],
            "total_bid_usd_20": round(total_bid_usd, 2),
            "total_ask_usd_20": round(total_ask_usd, 2),
            "top5_bid_usd": round(top5_bid_usd, 2),
            "top5_ask_usd": round(top5_ask_usd, 2),
            "imbalance_ratio": round(imbalance_ratio, 4),  # -1.0 (heavy ask) to +1.0 (heavy bid)
            "max_bid_wall": max_bid_wall,
            "max_ask_wall": max_ask_wall,
            "timestamp": now,
        }
        self.l2_books[symbol] = book
        self.last_l2_sync[symbol] = now
        return book

    async def fetch_candles(self, symbol: str, interval: str = "15m", lookback_bars: int = 160) -> List[Dict[str, Any]]:
        """
        Fetches real historical OHLCV candles from Hyperliquid `candleSnapshot`.
        Supported intervals: '1m', '15m', '1h', '4h', '1d'.
        """
        cache_key = f"{symbol}:{interval}"
        now_s = time.time()
        ttl = 8.0 if interval in ("1m", "15m") else 30.0
        if (
            symbol in self.candles_cache
            and interval in self.candles_cache[symbol]
            and (now_s - self.last_candle_sync.get(cache_key, 0.0)) < ttl
        ):
            return self.candles_cache[symbol][interval]

        interval_ms_map = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }
        bar_ms = interval_ms_map.get(interval, 900_000)
        end_ms = int(now_s * 1000)
        start_ms = end_ms - (lookback_bars * bar_ms)

        client = await self.get_client()
        resp = await client.post(
            self.info_url,
            json={
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            },
        )
        resp.raise_for_status()
        raw_candles = resp.json()

        parsed: List[Dict[str, Any]] = []
        for c in raw_candles:
            o = float(c["o"])
            h = float(c["h"])
            l = float(c["l"])
            cl = float(c["c"])
            v = float(c["v"])
            t_sec = int(c["t"]) // 1000
            parsed.append(
                {
                    "time": t_sec,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": cl,
                    "volume": v,
                    "volume_usd": round(v * cl, 2),
                    "trades": int(c.get("n", 0)),
                }
            )

        if symbol not in self.candles_cache:
            self.candles_cache[symbol] = {}
        self.candles_cache[symbol][interval] = parsed
        self.last_candle_sync[cache_key] = now_s
        return parsed

    def select_active_universe(self, top_n_rotators: int = 6) -> List[Dict[str, Any]]:
        """
        Returns the active tradeable universe:
        - Anchors: BTC, SOL, ETH, XRP
        - Dynamic Rotators: Top liquid altcoins meeting >$10M 24h volume filter sorted by
          composite activity (24h volume + absolute 24h momentum).
        """
        anchors = []
        rotators = []
        for sym, m in self.markets.items():
            if sym in DESK_CONFIG.anchor_symbols:
                anchors.append(m)
            elif m["meets_liquidity_filter"]:
                rotators.append(m)

        # Sort anchors in canonical order
        anchor_order = {s: i for i, s in enumerate(DESK_CONFIG.anchor_symbols)}
        anchors.sort(key=lambda x: anchor_order.get(x["symbol"], 99))

        # Rank rotators by liquidity * momentum score
        rotators.sort(
            key=lambda x: (
                (x["volume_24h_usd"] / 1e7) * (1.0 + abs(x["change_24h_pct"]) * 0.25)
            ),
            reverse=True,
        )
        return anchors + rotators[:top_n_rotators]
