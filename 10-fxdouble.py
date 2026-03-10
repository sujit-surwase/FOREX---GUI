# =============================================================
# XAUUSD Strategy Module (Backtesting + Live Trading Compatible)
# EMA 9/21 Crossover | 40 pip SL | 80 pip TP | Lot Doubling
# Works with main.py + backtester.py (GUI System)
# =============================================================

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

# --------------------------
# STRATEGY CONFIG
# --------------------------
SYMBOL = "XAUUSD"
MAGIC = 555555

BASE_LOT = 0.01
MAX_LOT = 1.0

SL_PIPS = 40
TP_PIPS = 80


# =============================================================
# 1. GET MT5 RATES
# =============================================================
def get_rates(symbol, timeframe=mt5.TIMEFRAME_M5, n=200):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    if rates is None:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


# =============================================================
# 2. INDICATOR CALCULATION (Required for Backtester)
# =============================================================
def calculate_indicators(df):
    df = df.copy()  # IMPORTANT: Stops SettingWithCopyWarning

    df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
    return df


# =============================================================
# 3. SIGNAL GENERATION (Required for Backtester)
# =============================================================
def generate_signal(df):
    if len(df) < 30:
        return None

    fast_prev = df["ema_fast"].iloc[-2]
    slow_prev = df["ema_slow"].iloc[-2]
    fast_last = df["ema_fast"].iloc[-1]
    slow_last = df["ema_slow"].iloc[-1]

    # BUY signal
    if fast_prev < slow_prev and fast_last > slow_last:
        return "BUY"

    # SELL signal
    if fast_prev > slow_prev and fast_last < slow_last:
        return "SELL"

    return None


# =============================================================
# 4. BACKTESTER WRAPPER (Required)
# =============================================================
def get_signal(df):
    """
    Backtester expects:
        return signal, stop_loss
    This strategy does not use custom stop_loss levels → return None
    """
    df = calculate_indicators(df)
    sig = generate_signal(df)
    return sig, None


# =============================================================
# 5. LOT DOUBLING LOGIC
# =============================================================
def get_next_lot(symbol, magic, base_lot, max_lot):
    now = datetime.now()
    frm = now - timedelta(days=30)

    deals = mt5.history_deals_get(frm, now)
    if deals is None or len(deals) == 0:
        return base_lot

    filtered = [
        d for d in deals
        if d.symbol == symbol
        and d.magic == magic
        and d.entry == mt5.DEAL_ENTRY_OUT
    ]

    if not filtered:
        return base_lot

    last_deal = sorted(filtered, key=lambda d: d.time)[-1]

    if last_deal.profit > 0:
        next_lot = min(last_deal.volume * 2.0, max_lot)
        return next_lot

    return base_lot


# =============================================================
# 6. SL + TP in POINTS (Works for Exness XAUUSD)
# =============================================================
def get_sl_tp_points():
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        return None, None

    # Determine pip-to-point conversion
    if info.digits in (3, 5):
        pip_to_point = 10
    else:
        pip_to_point = 1

    sl_points = SL_PIPS * pip_to_point
    tp_points = TP_PIPS * pip_to_point

    return sl_points, tp_points


# =============================================================
# 7. LIVE TRADING FUNCTION USED BY main.py
# =============================================================
def run_strategy():
    """
    main.py calls this during LIVE trading.
    """

    df = get_rates(SYMBOL, n=200)
    if df is None or len(df) < 30:
        return None

    df = calculate_indicators(df)
    signal = generate_signal(df)

    if signal is None:
        return None

    lot = get_next_lot(SYMBOL, MAGIC, BASE_LOT, MAX_LOT)
    sl_points, tp_points = get_sl_tp_points()

    return {
        "symbol": SYMBOL,
        "signal": signal,
        "lot": lot,
        "sl_points": sl_points,
        "tp_points": tp_points,
        "magic": MAGIC
    }
