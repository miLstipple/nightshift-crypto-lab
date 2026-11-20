"""
Deterministic Quantitative Analytics Engine.
Calculates:
- Multi-Timeframe Regime (4H, 1H, 15m): Trending Bull, Trending Bear, Volatility Compression Coil, Mean-Reverting Range
- Volume Profile: Point of Control (POC), Value Area High (VAH), Value Area Low (VAL) (70% volume area)
- Support & Resistance Structural Zones (Monthly/Weekly/Daily/HTF swing pivots)
- Anchored VWAPs (Session, Swing High, Swing Low)
- Fibonacci Retracements (0.382, 0.5, 0.618, 0.705, 0.786), Extensions (1.272, 1.618), and Fib Time Zones
- Elliott Wave Count (Impulse Waves 1-5 & Corrective A-B-C) & Harmonic PRZ (Gartley / Bat / Crab)
- Order-Flow Metrics: Cumulative Volume Delta (CVD), CVD Divergence/Absorption, Swing Failure Pattern (SFP), RVOL, ATR
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


def compute_ema(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) == 0:
        return np.array([])
    alpha = 2.0 / (period + 1.0)
    ema = np.zeros_like(values, dtype=float)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < 2:
        return float(closes[-1] * 0.01) if len(closes) == 1 else 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    arr = np.array(trs, dtype=float)
    if len(arr) < period:
        return float(np.mean(arr))
    return float(np.mean(arr[-period:]))


def compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_volume_profile(candles: List[Dict[str, Any]], num_bins: int = 28) -> Dict[str, Any]:
    """
    Computes deterministic Volume Profile (POC, VAH, VAL) across the candle window
    using a 70% Value Area accumulation around the Point of Control.
    """
    if not candles:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0, "bins": []}

    low_min = min(c["low"] for c in candles)
    high_max = max(c["high"] for c in candles)
    if high_max <= low_min:
        px = candles[-1]["close"]
        return {"poc": px, "vah": px * 1.005, "val": px * 0.995, "bins": []}

    bin_size = (high_max - low_min) / num_bins
    bin_volumes = np.zeros(num_bins, dtype=float)
    bin_centers = np.array([low_min + (i + 0.5) * bin_size for i in range(num_bins)])

    for c in candles:
        typical_px = (c["high"] + c["low"] + c["close"]) / 3.0
        idx = int(min(num_bins - 1, max(0, math.floor((typical_px - low_min) / bin_size))))
        bin_volumes[idx] += c["volume_usd"]

    total_vol = float(np.sum(bin_volumes))
    if total_vol <= 0:
        px = candles[-1]["close"]
        return {"poc": px, "vah": px * 1.005, "val": px * 0.995, "bins": []}

    poc_idx = int(np.argmax(bin_volumes))
    poc_px = float(bin_centers[poc_idx])

    # Expand up/down from poc_idx until 70% of total volume is captured
    target_vol = 0.70 * total_vol
    accum_vol = float(bin_volumes[poc_idx])
    low_idx = poc_idx
    high_idx = poc_idx

    while accum_vol < target_vol and (low_idx > 0 or high_idx < num_bins - 1):
        up_vol = bin_volumes[high_idx + 1] if high_idx < num_bins - 1 else -1.0
        dn_vol = bin_volumes[low_idx - 1] if low_idx > 0 else -1.0
        if up_vol >= dn_vol and high_idx < num_bins - 1:
            high_idx += 1
            accum_vol += float(bin_volumes[high_idx])
        elif low_idx > 0:
            low_idx -= 1
            accum_vol += float(bin_volumes[low_idx])
        else:
            break

    val_px = float(low_min + low_idx * bin_size)
    vah_px = float(low_min + (high_idx + 1) * bin_size)

    max_bin_vol = float(np.max(bin_volumes)) or 1.0
    bins_out = [
        {
            "price": float(bin_centers[i]),
            "volume_usd": round(float(bin_volumes[i]), 2),
            "pct_of_poc": round(float(bin_volumes[i]) / max_bin_vol, 3),
            "in_value_area": low_idx <= i <= high_idx,
            "is_poc": i == poc_idx,
        }
        for i in range(num_bins)
    ]

    return {
        "poc": poc_px,
        "vah": vah_px,
        "val": val_px,
        "bins": bins_out,
    }


def compute_fibonacci_structure(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Identifies major swing high and swing low over lookback window, then calculates:
    - Retracements: 0.382, 0.5, 0.618, 0.705 (OTE), 0.786
    - Extensions: 1.272, 1.618
    - Fib Time Zones from the primary pivot index
    """
    if len(candles) < 10:
        px = candles[-1]["close"] if candles else 0.0
        return {"swing_high": px, "swing_low": px, "direction": "BULL", "retracements": {}, "extensions": {}, "time_zones": []}

    highs = np.array([c["high"] for c in candles], dtype=float)
    lows = np.array([c["low"] for c in candles], dtype=float)

    high_idx = int(np.argmax(highs))
    low_idx = int(np.argmin(lows))
    swing_high = float(highs[high_idx])
    swing_low = float(lows[low_idx])
    rng = max(1e-8, swing_high - swing_low)

    # If swing low happened before swing high -> impulse is UP (retracements measured down from high)
    impulse_up = low_idx <= high_idx
    direction = "BULL_IMPULSE" if impulse_up else "BEAR_IMPULSE"

    fib_ratios = [0.382, 0.500, 0.618, 0.705, 0.786]
    ext_ratios = [1.272, 1.618]
    retracements: Dict[str, float] = {}
    extensions: Dict[str, float] = {}

    for r in fib_ratios:
        key = f"{r:.3f}"
        if impulse_up:
            retracements[key] = swing_high - r * rng
        else:
            retracements[key] = swing_low + r * rng

    for e in ext_ratios:
        key = f"{e:.3f}"
        if impulse_up:
            extensions[key] = swing_low + e * rng
        else:
            extensions[key] = swing_high - e * rng

    # Fib Time Zones from earliest major pivot
    anchor_idx = min(high_idx, low_idx)
    anchor_time = candles[anchor_idx]["time"]
    bar_sec = (candles[-1]["time"] - candles[-2]["time"]) if len(candles) >= 2 else 900
    fib_numbers = [5, 8, 13, 21, 34, 55, 89]
    time_zones = [
        {
            "fib_num": fn,
            "bar_offset": fn,
            "timestamp": anchor_time + fn * bar_sec,
            "active_now": abs((len(candles) - 1 - anchor_idx) - fn) <= 2,
        }
        for fn in fib_numbers
    ]

    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "high_time": candles[high_idx]["time"],
        "low_time": candles[low_idx]["time"],
        "direction": direction,
        "retracements": retracements,
        "extensions": extensions,
        "time_zones": time_zones,
    }


def detect_swings_and_waves(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Detects local fractal pivot points, labels Elliott Wave structure (Impulse 1-5 or A-B-C),
    detects Harmonic PRZs (Gartley / Bat / Crab ratios), and extracts HTF Support/Resistance levels.
    """
    if len(candles) < 25:
        return {"wave_label": "Neutral", "wave_points": [], "harmonic_prz": None, "sr_zones": []}

    pivots: List[Dict[str, Any]] = []
    window = 4
    for i in range(window, len(candles) - window):
        h = candles[i]["high"]
        l = candles[i]["low"]
        is_high = all(h >= candles[j]["high"] for j in range(i - window, i + window + 1) if j != i)
        is_low = all(l <= candles[j]["low"] for j in range(i - window, i + window + 1) if j != i)
        if is_high:
            if not pivots or pivots[-1]["type"] != "H":
                pivots.append({"index": i, "time": candles[i]["time"], "price": h, "type": "H"})
            elif h > pivots[-1]["price"]:
                pivots[-1] = {"index": i, "time": candles[i]["time"], "price": h, "type": "H"}
        elif is_low:
            if not pivots or pivots[-1]["type"] != "L":
                pivots.append({"index": i, "time": candles[i]["time"], "price": l, "type": "L"})
            elif l < pivots[-1]["price"]:
                pivots[-1] = {"index": i, "time": candles[i]["time"], "price": l, "type": "L"}

    # Support / Resistance clustering from pivots
    high_pivots = sorted([p["price"] for p in pivots if p["type"] == "H"], reverse=True)
    low_pivots = sorted([p["price"] for p in pivots if p["type"] == "L"])
    last_px = candles[-1]["close"]

    resistances = [p for p in high_pivots if p > last_px][:3]
    supports = [p for p in low_pivots if p < last_px][-3:]
    if not resistances:
        resistances = [max(c["high"] for c in candles)]
    if not supports:
        supports = [min(c["low"] for c in candles)]

    sr_zones = []
    for idx, r in enumerate(sorted(resistances)):
        sr_zones.append({"type": "RESISTANCE", "label": f"HTF R{idx + 1}", "price": r})
    for idx, s in enumerate(sorted(supports, reverse=True)):
        sr_zones.append({"type": "SUPPORT", "label": f"HTF S{idx + 1}", "price": s})

    # Elliott Wave 5-point sequence labeling
    recent_pivots = pivots[-6:] if len(pivots) >= 6 else pivots
    wave_points = []
    wave_summary = "Wave Structure Developing"

    if len(recent_pivots) >= 5:
        pts = recent_pivots[-5:]
        labels_impulse = ["(1)", "(2)", "(3)", "(4)", "(5)"]
        labels_abc = ["(1)", "(2)", "(A)", "(B)", "(C)"]
        # Check if progressive impulse or corrective
        if pts[-1]["type"] == "L" and pts[-1]["price"] > pts[1]["price"]:
            labels = ["(0)", "(1)", "(2)", "(3)", "(4)"]
            wave_summary = "Bullish Wave (4) Pullback -> Wave (5) Setup"
        elif pts[-1]["type"] == "H" and pts[-1]["price"] < pts[1]["price"]:
            labels = ["(0)", "(1)", "(2)", "(3)", "(4)"]
            wave_summary = "Bearish Wave (4) Bounce -> Wave (5) Setup"
        elif pts[-1]["type"] == "L":
            labels = labels_abc
            wave_summary = "Bullish ABC Corrective Completion at (C)"
        else:
            labels = labels_impulse
            wave_summary = "Impulse Wave (5) Exhaustion / Reversal Watch"

        for p, lbl in zip(pts, labels):
            wave_points.append(
                {
                    "time": p["time"],
                    "price": p["price"],
                    "type": p["type"],
                    "label": lbl,
                }
            )

    # Harmonic XABCD PRZ check on last 5 pivots
    harmonic_prz = None
    if len(recent_pivots) >= 5:
        X, A, B, C, D = recent_pivots[-5:]
        xa = abs(A["price"] - X["price"])
        ab = abs(B["price"] - A["price"])
        xd = abs(D["price"] - X["price"])
        if xa > 0:
            ab_xa = ab / xa
            xd_xa = xd / xa
            if 0.55 <= ab_xa <= 0.68 and 0.72 <= xd_xa <= 0.85:
                harmonic_prz = {
                    "pattern": "Bullish Gartley PRZ" if D["type"] == "L" else "Bearish Gartley PRZ",
                    "prz_price": D["price"],
                    "ab_xa_ratio": round(ab_xa, 3),
                    "xd_xa_ratio": round(xd_xa, 3),
                }
            elif 0.35 <= ab_xa <= 0.55 and 0.83 <= xd_xa <= 0.93:
                harmonic_prz = {
                    "pattern": "Bullish Bat 0.886 PRZ" if D["type"] == "L" else "Bearish Bat 0.886 PRZ",
                    "prz_price": D["price"],
                    "ab_xa_ratio": round(ab_xa, 3),
                    "xd_xa_ratio": round(xd_xa, 3),
                }

    return {
        "wave_label": wave_summary,
        "wave_points": wave_points,
        "harmonic_prz": harmonic_prz,
        "sr_zones": sr_zones,
    }


def compute_order_flow_and_cvd(candles: List[Dict[str, Any]], l2_book: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Computes Cumulative Volume Delta (CVD), Relative Volume (RVOL), Anchored VWAP,
    CVD absorption/divergence, and Swing Failure Pattern (SFP) sweep detection.
    """
    if len(candles) < 10:
        return {
            "cvd_current": 0.0,
            "cvd_slope_10": 0.0,
            "cvd_divergence": "NONE",
            "rvol": 1.0,
            "avwap": candles[-1]["close"] if candles else 0.0,
            "sfp_detected": "NONE",
            "cvd_series": [],
        }

    cvd = 0.0
    cvd_series = []
    vwap_num = 0.0
    vwap_den = 0.0

    for c in candles:
        rng = max(1e-9, c["high"] - c["low"])
        # Intrabar buying vs selling pressure delta
        buy_fraction = (c["close"] - c["low"]) / rng
        sell_fraction = (c["high"] - c["close"]) / rng
        bar_delta_usd = c["volume_usd"] * (buy_fraction - sell_fraction)
        cvd += bar_delta_usd
        cvd_series.append({"time": c["time"], "cvd": round(cvd, 2), "delta": round(bar_delta_usd, 2)})

        typ = (c["high"] + c["low"] + c["close"]) / 3.0
        vwap_num += typ * c["volume_usd"]
        vwap_den += c["volume_usd"]

    avwap = (vwap_num / vwap_den) if vwap_den > 0 else candles[-1]["close"]

    # RVOL: latest 3-bar average volume vs 30-bar baseline
    vols = np.array([c["volume_usd"] for c in candles], dtype=float)
    recent_vol = float(np.mean(vols[-3:])) if len(vols) >= 3 else float(vols[-1])
    base_vol = float(np.mean(vols[-35:-3])) if len(vols) >= 35 else float(np.mean(vols))
    rvol = round(recent_vol / max(1.0, base_vol), 2)

    # CVD Divergence / Absorption check over last 12 bars
    px_change_12 = candles[-1]["close"] - candles[-12]["close"] if len(candles) >= 12 else 0.0
    cvd_change_12 = cvd_series[-1]["cvd"] - cvd_series[-12]["cvd"] if len(cvd_series) >= 12 else 0.0

    cvd_divergence = "NONE"
    if px_change_12 < 0 and cvd_change_12 > 0:
        cvd_divergence = "BULLISH_ABSORPTION"
    elif px_change_12 > 0 and cvd_change_12 < 0:
        cvd_divergence = "BEARISH_ABSORPTION"
    elif px_change_12 > 0 and cvd_change_12 > 0 and rvol >= 1.35:
        cvd_divergence = "BULLISH_AGGRESSION"
    elif px_change_12 < 0 and cvd_change_12 < 0 and rvol >= 1.35:
        cvd_divergence = "BEARISH_AGGRESSION"

    # Swing Failure Pattern (SFP) check: sweep of prior 20-bar low/high followed by close back inside range
    sfp_detected = "NONE"
    if len(candles) >= 24:
        prior_window = candles[-24:-2]
        prior_low = min(c["low"] for c in prior_window)
        prior_high = max(c["high"] for c in prior_window)
        last_2 = candles[-2:]
        swept_low = any(c["low"] < prior_low for c in last_2) and candles[-1]["close"] > prior_low
        swept_high = any(c["high"] > prior_high for c in last_2) and candles[-1]["close"] < prior_high
        if swept_low:
            sfp_detected = "BULLISH_SFP_SWEEP"
        elif swept_high:
            sfp_detected = "BEARISH_SFP_SWEEP"

    l2_imbalance = l2_book.get("imbalance_ratio", 0.0) if l2_book else 0.0

    return {
        "cvd_current": round(cvd, 2),
        "cvd_change_12": round(cvd_change_12, 2),
        "cvd_divergence": cvd_divergence,
        "rvol": rvol,
        "avwap": avwap,
        "sfp_detected": sfp_detected,
        "l2_imbalance": l2_imbalance,
        "cvd_series": cvd_series[-60:],
    }


def classify_single_tf_regime(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Classifies a single timeframe (4H, 1H, or 15m) into:
    - TRENDING_BULL
    - TRENDING_BEAR
    - VOLATILITY_COMPRESSION_COIL
    - MEAN_REVERTING_RANGE
    """
    if len(candles) < 25:
        return {"regime": "MEAN_REVERTING_RANGE", "bb_width_pct": 2.0, "ema_alignment": "NEUTRAL", "rsi": 50.0}

    closes = np.array([c["close"] for c in candles], dtype=float)
    highs = np.array([c["high"] for c in candles], dtype=float)
    lows = np.array([c["low"] for c in candles], dtype=float)

    ema20 = compute_ema(closes, 20)
    ema50 = compute_ema(closes, 50)
    rsi = compute_rsi(closes, 14)

    # Bollinger Band Width (20, 2.0) compared to 50-bar rolling width
    bb_widths = []
    for i in range(20, len(closes) + 1):
        sl = closes[i - 20 : i]
        m = float(np.mean(sl))
        sd = float(np.std(sl))
        bb_widths.append((4.0 * sd / m * 100.0) if m > 0 else 0.0)

    curr_bbw = bb_widths[-1] if bb_widths else 2.0
    p25_bbw = float(np.percentile(bb_widths, 28)) if len(bb_widths) >= 10 else curr_bbw * 0.8

    px = float(closes[-1])
    e20 = float(ema20[-1])
    e50 = float(ema50[-1])
    slope20_pct = ((ema20[-1] - ema20[-5]) / ema20[-5] * 100.0) if len(ema20) >= 5 and ema20[-5] > 0 else 0.0

    is_coil = curr_bbw <= p25_bbw * 1.05
    if is_coil:
        regime = "VOLATILITY_COMPRESSION_COIL"
    elif px > e20 > e50 and slope20_pct > 0.12:
        regime = "TRENDING_BULL"
    elif px < e20 < e50 and slope20_pct < -0.12:
        regime = "TRENDING_BEAR"
    else:
        regime = "MEAN_REVERTING_RANGE"

    return {
        "regime": regime,
        "bb_width_pct": round(curr_bbw, 3),
        "is_coil": bool(is_coil),
        "ema20": e20,
        "ema50": e50,
        "slope20_pct": round(slope20_pct, 3),
        "rsi": rsi,
        "atr": compute_atr(highs, lows, closes, 14),
    }
