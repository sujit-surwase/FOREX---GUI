import pandas as pd
import MetaTrader5 as mt5
import threading
import time
from mt5_config import *

# === Strategy Parameters ===
SYMBOL = "USDJPY"
VOLUME = 0.2
MAGIC = 77881
LOOKBACK = 18
ATR_PERIOD = 14
SL_MULT = 1.1
TP_MULT = 2.0
INTERVAL = 30

# Global flag to control running state of strategy
bot_running = False

def calculate_indicators(df):
    df.rename(columns={'close': 'Close', 'high': 'High', 'low': 'Low'}, inplace=True, errors='ignore')
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift()).abs(),
        (df['Low'] - df['Close'].shift()).abs()
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(ATR_PERIOD).mean()
    df['High_Break'] = df['High'].rolling(LOOKBACK).max()
    df['Low_Break'] = df['Low'].rolling(LOOKBACK).min()
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    if len(df) < LOOKBACK + ATR_PERIOD:
        return 'WAIT', None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    median_atr = df['ATR'][-ATR_PERIOD:].median()
    if prev['Close'] <= prev['High_Break'] and last['Close'] > last['High_Break'] and last['ATR'] > median_atr:
        stop_loss = last['Close'] - SL_MULT * last['ATR']
        return 'BUY', stop_loss
    if prev['Close'] >= prev['Low_Break'] and last['Close'] < last['Low_Break'] and last['ATR'] > median_atr:
        stop_loss = last['Close'] + SL_MULT * last['ATR']
        return 'SELL', stop_loss
    return 'WAIT', None

def place_order(symbol, signal, stop_loss, log_callback=None):
    tick = get_tick_info(symbol)
    if not tick or not stop_loss:
        log(f"❌ Missing tick info or stop loss for {symbol}", log_callback)
        return False
    price = tick.ask if signal == "BUY" else tick.bid
    take_profit = price + TP_MULT * abs(price - stop_loss) if signal == "BUY" else price - TP_MULT * abs(price - stop_loss)
    order_type = mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": VOLUME,
        "type": order_type,
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": 20,
        "magic": MAGIC,
        "comment": "ATR Momentum Breakout",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    log(f"⚡ SENDING {signal} @ {price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f}", log_callback)
    result = send_order(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"✅ {signal} ORDER PLACED! Ticket: {result.order}", log_callback)
        return True
    else:
        log(f"❌ {signal} ORDER FAILED: {result.comment if result else 'No result'}", log_callback)
        return False

def _strategy_loop(symbol, log_callback=None):
    global bot_running
    while bot_running:
        try:
            if not get_positions(symbol):
                df = fetch_data(symbol, count=LOOKBACK + ATR_PERIOD + 10)
                if not df.empty:
                    df = calculate_indicators(df)
                    signal, sl = get_signal(df)
                    if signal != "WAIT":
                        place_order(symbol, signal, sl, log_callback)
            else:
                log(f"📊 Position already open on {symbol}. Monitoring...", log_callback)
            time.sleep(INTERVAL)
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}", log_callback)
            time.sleep(INTERVAL)

def run_strategy(symbol, log_callback=None, profit_callback=None):
    global bot_running
    if bot_running:
        log("⚠️ Strategy is already running.", log_callback)
        return True
    if not connect(log_callback):
        return False
    bot_running = True
    log(f"🚀 Fast ATR Breakout Strategy Started on {symbol}", log_callback)
    threading.Thread(target=_strategy_loop, args=(symbol, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    global bot_running
    bot_running = False
    log("🛑 Strategy stopping...", log_callback)
    shutdown()
    log("✅ Strategy stopped.", log_callback)
