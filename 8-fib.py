# ======================================================================
#                 FINAL UPDATED FIBONACCI STRATEGY (FIXED)
# ======================================================================

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import threading
import time
import traceback
from datetime import datetime

# --- Config ---
VOLUME = 1.0
MAGIC = 99999
TRAILING_PERCENT = 0.07
TRAILING_ACTIVATION = 0.70

# --- State ---
bot_running = False
last_signal = None
strategy_thread = None

SYMBOL = ""
TIMEFRAME = None
LOG_CALLBACK = None


# ======================================================================
#                             LOGGING
# ======================================================================

def log(message, force_print=False):
    if LOG_CALLBACK:
        try:
            LOG_CALLBACK(message)
        except:
            print(message)
    elif force_print:
        print(message)


# ======================================================================
#                             MT5 HELPERS
# ======================================================================

def fetch_data(symbol, timeframe, count=1000):
    try:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            log(f"NO DATA for {symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        return df

    except Exception as e:
        log(str(e))
        return pd.DataFrame()


def get_tick_info(symbol):
    return mt5.symbol_info_tick(symbol)


def get_symbol_info(symbol):
    return mt5.symbol_info(symbol)


def get_account_info():
    return mt5.account_info()


def get_positions(symbol=None):
    try:
        return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    except:
        return []


def send_order(request):
    try:
        return mt5.order_send(request)
    except:
        log("ORDER SEND ERROR")
        log(traceback.format_exc())
        return None


# ======================================================================
#                             INDICATORS
# ======================================================================

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def compute_sma(series, period):
    return series.rolling(period).mean()


def calculate_indicators(df):
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"])
    df["sma_20"] = compute_sma(df["close"], 20)
    df["sma_50"] = compute_sma(df["close"], 50)
    return df


# ======================================================================
#                   SWINGS + FIB LEVELS + FIB LOGIC
# ======================================================================

def find_swing_points(df, lookback_days=5):
    candles = lookback_days * 24 * 4
    if len(df) < candles:
        return None, None, None, None

    data = df.iloc[-candles:]
    hh = data["high"].max()
    ll = data["low"].min()
    hh_idx = data["high"].idxmax()
    ll_idx = data["low"].idxmin()

    return hh, ll, hh_idx, ll_idx


def check_recent_swing_points(df, hh_idx, ll_idx):
    recent = df.index[-15:]
    return hh_idx in recent or ll_idx in recent


def calculate_fibonacci_levels(high, low, direction):
    diff = high - low

    if direction == "uptrend":
        return {
            "0.0": low,
            "38.2": low + diff * 0.382,
            "50.0": low + diff * 0.5,
            "61.8": low + diff * 0.618,
            "100.0": high
        }

    else:  # downtrend reversal (HH → LL)
        return {
            "100.0": high,
            "61.8": high - diff * 0.382,
            "50.0": high - diff * 0.5,
            "38.2": high - diff * 0.618,
            "0.0": low
        }


# ======================================================================
#              FIXED FIBONACCI BUY CONDITION (BUG REMOVED)
# ======================================================================

def check_fibonacci_condition(df, fib, direction):
    if len(df) < 2:
        return False, None

    cur = df.iloc[-1]
    prev = df.iloc[-2]

    current_green = cur["close"] > cur["open"]
    moving_up = cur["close"] > prev["close"]

    # ---------------------------------------------------------
    # UP-TREND LOGIC (normal retracement)
    # ---------------------------------------------------------
    if direction == "uptrend":
        fib_618 = fib["61.8"]
        fib_382 = fib["38.2"]

        touched = prev["low"] <= fib_618 <= prev["high"]

        if touched and current_green and moving_up:
            log(f"Fib BUY: Uptrend → Candle touched 61.8")
            return True, fib_382  # SL BELOW price

    # ---------------------------------------------------------
    # DOWN-TREND LOGIC (reversal) — FIXED SL
    # ---------------------------------------------------------
    else:
        fib_382 = fib["38.2"]  # ALWAYS BELOW PRICE

        touched = prev["low"] <= fib_382 <= prev["high"]

        if touched and current_green and moving_up:
            log(f"Fib BUY: Downtrend reversal → Touched 38.2")
            return True, fib_382

    return False, None


# ======================================================================
#                           SIGNAL ENGINE
# ======================================================================

def get_signal(df):

    if len(df) < 120:
        return "WAIT", None

    df2 = df.iloc[:-1].copy()
    df2 = calculate_indicators(df2)

    rsi = df2["rsi"].iloc[-1]
    sma20 = df2["sma_20"].iloc[-1]
    sma50 = df2["sma_50"].iloc[-1]

    hh, ll, hh_idx, ll_idx = find_swing_points(df2)
    if hh is None:
        return "WAIT", None

    if check_recent_swing_points(df2, hh_idx, ll_idx):
        return "WAIT", None

    direction = "uptrend" if ll_idx < hh_idx else "downtrend"
    fib = calculate_fibonacci_levels(hh, ll, direction)

    fib_ok, stop_loss = check_fibonacci_condition(df2, fib, direction)
    if not fib_ok:
        return "WAIT", None

    # FILTERS
    filters = 0
    if rsi < 50: filters += 1
    if sma20 > sma50: filters += 1
    filters += 1  # swing valid

    if filters >= 2:
        return "BUY", stop_loss

    return "WAIT", None


# ======================================================================
#                       ORDER PLACEMENT
# ======================================================================

def place_buy_order(sl_price):
    global SYMBOL

    tick = get_tick_info(SYMBOL)
    info = get_symbol_info(SYMBOL)

    if not tick or not info:
        return None

    price = round(tick.ask, info.digits)
    sl = round(sl_price, info.digits)

    if sl >= price:
        log(f"SL INVALID (SL {sl} >= Price {price})")
        return None

    acc = get_account_info()
    capital = acc.balance if acc else 5000

    risk = capital * 0.02
    diff = price - sl

    volume = risk / (diff * info.trade_contract_size)
    volume = max(info.volume_min, min(volume, VOLUME))

    tp = round(price + diff * 2, info.digits)

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "sl": sl,
        "tp": tp,
        "magic": MAGIC,
        "comment": "Fib-Strategy"
    }

    result = send_order(req)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"BUY SUCCESS! Ticket #{result.order}")
        return result.order

    log("BUY FAILED")
    return None


# ======================================================================
#                      TRAILING STOP ENGINE
# ======================================================================

def trail_buy_stop_logic(ticket):
    global bot_running, SYMBOL

    info = get_symbol_info(SYMBOL)
    digits = info.digits

    while bot_running:
        pos = next((p for p in get_positions(SYMBOL) if p.ticket == ticket), None)
        if not pos:
            break

        tick = get_tick_info(SYMBOL)
        price = tick.bid

        entry = pos.price_open
        tp = pos.tp

        progress = (price - entry) / (tp - entry)

        if progress >= TRAILING_ACTIVATION:
            new_sl = round(price * (1 - TRAILING_PERCENT), digits)
            if new_sl > pos.sl:
                req = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": SYMBOL,
                    "position": ticket,
                    "sl": new_sl,
                    "tp": pos.tp
                }
                send_order(req)

        time.sleep(5)


# ======================================================================
#                 MAIN STRATEGY LOOP (FIXED last_signal)
# ======================================================================

def strategy_loop():
    global bot_running, last_signal, SYMBOL, TIMEFRAME

    while bot_running:
        try:
            df = fetch_data(SYMBOL, TIMEFRAME, 1000)
            if df.empty:
                time.sleep(60)
                continue

            signal, sl = get_signal(df)

            positions = get_positions(SYMBOL)
            my_positions = [p for p in positions if p.magic == MAGIC]

            # ==================================================
            #          FIXED BUY LOGIC (NO BLOCKING)
            # ==================================================
            if signal == "BUY" and not my_positions:

                log("--- BUY SIGNAL RECEIVED ---")

                ticket = place_buy_order(sl)

                if ticket:
                    last_signal = "BUY"

                    t = threading.Thread(
                        target=trail_buy_stop_logic,
                        args=(ticket,),
                        daemon=True
                    )
                    t.start()

                else:
                    # order failed → do NOT block future BUYs
                    last_signal = None

            elif signal == "WAIT":
                last_signal = None  # always ready for next BUY

            time.sleep(60)

        except Exception as e:
            log(str(e))
            log(traceback.format_exc())
            time.sleep(60)


# ======================================================================
#                         EXTERNAL API
# ======================================================================

def run_strategy(symbol, timeframe, log_callback):
    global bot_running, SYMBOL, TIMEFRAME, LOG_CALLBACK, last_signal

    if bot_running:
        return False

    bot_running = True
    last_signal = None
    SYMBOL = symbol
    TIMEFRAME = timeframe
    LOG_CALLBACK = log_callback

    t = threading.Thread(target=strategy_loop, daemon=True)
    t.start()

    log("Fibonacci Strategy Started")
    return True


def stop_strategy(log_callback):
    global bot_running
    bot_running = False
    log_callback("Strategy Stopped")
