"""
Fibonacci_Retracement_v1.py — A trend-continuation strategy using RSI, SMA, and Fibonacci retracements.

Integrates seamlessly with the GUI dashboard and backtester:
- Detects swing highs/lows over 5-day windows.
- Uses Fibonacci 38.2% & 61.8% retracements for confirmation.
- Confirms trade using RSI < 50 and SMA20 > SMA50.
- Includes dynamic trailing stop logic.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.5
MAGIC = 99999
CAPITAL = 5000
RR_RATIO = 2.0
ATR_PERIOD = 14
TRAILING_PERCENT = 0.07
TRAILING_ACTIVATION = 0.70

bot_running = False
_stop_event = threading.Event()

# ==========================================================================================
# == BACKTESTER FUNCTIONS
# ==========================================================================================

def calculate_indicators(df):
    """Prepares data with SMA, RSI, and basic structure."""
    
    # FIX: Removed the .rename() line to keep columns lowercase
    
    # RSI
    # FIX: 'Close' -> 'close'
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # SMAs
    # FIX: 'Close' -> 'close'
    df['SMA20'] = df['close'].rolling(window=20).mean()
    df['SMA50'] = df['close'].rolling(window=50).mean()
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def get_signal(df):
    """Generates a BUY signal based on Fibonacci retracement logic."""
    if len(df) < 100:
        return "WAIT", None
    
    # FIX: 'High', 'Low' -> 'high', 'low'
    hh = df['high'].rolling(window=80).max().iloc[-1]
    ll = df['low'].rolling(window=80).min().iloc[-1]
    direction = "uptrend" if ll < hh else "downtrend"
    diff = hh - ll

    # Fibonacci retracement levels
    fib_382 = ll + (diff * 0.382)
    fib_618 = ll + (diff * 0.618)
    
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if direction == "uptrend":
        # FIX: 'Low', 'High', 'Close', 'Open' -> 'low', 'high', 'close', 'open'
        if prev['low'] <= fib_618 <= prev['high'] and curr['close'] > curr['open']:
            if curr['RSI'] < 50 and curr['SMA20'] > curr['SMA50']:
                stop_loss = fib_382
                return "BUY", stop_loss

    return "WAIT", None

# ==========================================================================================
# == LIVE TRADING FUNCTIONS
# ==========================================================================================

def log(message, log_callback=None):
    if log_callback: log_callback(message)
    else: print(message)

def fetch_data(symbol, timeframe, bars=500):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df


def place_order(symbol, signal_type, stop_loss_price, log_callback=None):
    try:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if not tick or not info:
            log("❌ Symbol info/tick unavailable.", log_callback)
            return False

        order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if signal_type == "BUY" else tick.bid
        risk = abs(price - stop_loss_price)
        if risk == 0:
            log("❌ Invalid stop loss distance.", log_callback)
            return False

        tp_price = price + (risk * RR_RATIO)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": VOLUME,
            "type": order_type,
            "price": price,
            "sl": round(stop_loss_price, info.digits),
            "tp": round(tp_price, info.digits),
            "deviation": 20,
            "magic": MAGIC,
            "comment": "FibonacciRetracementV1",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        log(f"🚀 Sending BUY order: {symbol} @ {price:.5f} | SL: {stop_loss_price:.5f} | TP: {tp_price:.5f}", log_callback)
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ Order filled! Ticket: {result.order}", log_callback)
            threading.Thread(target=trail_buy_stop_logic, args=(symbol, result.order, log_callback), daemon=True).start()
            return True
        else:
            log(f"❌ Order failed: {result.comment}", log_callback)
            return False
    except Exception as e:
        log(f"❌ Order placement error: {e}", log_callback)
        return False


def trail_buy_stop_logic(symbol, ticket, log_callback=None):
    """Moves stop-loss dynamically as price reaches target."""
    info = mt5.symbol_info(symbol)
    digits = info.digits
    log(f"📈 Trailing stop activated for ticket #{ticket}", log_callback)

    while bot_running and not _stop_event.is_set():
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                break

            pos = positions[0]
            current_price = mt5.symbol_info_tick(symbol).ask
            entry_price = pos.price_open
            tp_price = pos.tp
            sl_price = pos.sl

            if tp_price <= entry_price:
                time.sleep(5)
                continue

            profit_progress = (current_price - entry_price) / (tp_price - entry_price)
            if profit_progress >= TRAILING_ACTIVATION:
                new_sl = current_price * (1 - TRAILING_PERCENT)
                if new_sl > sl_price:
                    sltp_req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": symbol,
                        "position": ticket,
                        "sl": round(new_sl, digits),
                        "tp": pos.tp,
                    }
                    result = mt5.order_send(sltp_req)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        log(f"🔁 SL updated: {sl_price:.5f} → {new_sl:.5f}", log_callback)
            time.sleep(10)
        except Exception as e:
            log(f"❌ Trailing error: {e}", log_callback)
            time.sleep(5)


def _strategy_loop(symbol, timeframe, log_callback=None):
    global bot_running
    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, timeframe)
            if df.empty:
                time.sleep(2)
                continue

            df_ind = calculate_indicators(df)
            signal, sl = get_signal(df_ind)
            if signal == "BUY":
                log(f"📊 BUY Signal detected on {symbol}", log_callback)
                place_order(symbol, "BUY", sl, log_callback)
                time.sleep(60)
            else:
                log(f"⏳ Waiting for setup...", log_callback)
            time.sleep(5)
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}", log_callback)
            time.sleep(5)


def run_strategy(symbol, timeframe, log_callback=None):
    global bot_running
    if bot_running:
        log("⚠️ Strategy already running.", log_callback)
        return True

    bot_running = True
    _stop_event.clear()
    log(f"🚀 Fibonacci Retracement Strategy Started on {symbol} | TF: {timeframe}", log_callback)
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback), daemon=True).start()
    return True


def stop_strategy(log_callback=None):
    global bot_running
    if not bot_running: return
    bot_running = False
    _stop_event.set()
    log("🛑 Strategy stopping...", log_callback)
    time.sleep(1)
    log("✅ Strategy stopped.", log_callback)
