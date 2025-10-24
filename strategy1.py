import pandas as pd
import MetaTrader5 as mt5
import threading
import time
from mt5_config import *  # Your utility functions/config here

# PARAMETERS
SYMBOL = "EURUSD"
VOLUME = 0.1
MAGIC = 54321
RR_RATIO = 2.0  # Take Profit = 2 x Stop Loss

WINDOW = 20    # Bollinger Band window
NUM_STD = 2    # Standard deviations for bands
RSI_PERIOD = 14
RSI_LOWER = 30
RSI_UPPER = 70
INTERVAL = 60  # seconds between trading checks

bot_running = False

def calculate_indicators(df):
    df.rename(columns={'close':'Close', 'high':'High', 'low':'Low'}, inplace=True, errors='ignore')
    df['MA20'] = df['Close'].rolling(WINDOW).mean()
    df['BB_UPPER'] = df['MA20'] + NUM_STD * df['Close'].rolling(WINDOW).std()
    df['BB_LOWER'] = df['MA20'] - NUM_STD * df['Close'].rolling(WINDOW).std()
    df['RSI'] = df['Close'].rolling(RSI_PERIOD).apply(
        lambda x: 100 - (100/(1+((x.diff().clip(lower=0).sum())/(abs(x.diff()).sum() - x.diff().clip(lower=0).sum()))))
    )
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    if len(df) < WINDOW:
        return 'WAIT', None
    last = df.iloc[-1]

    # BUY signal: price < lower BB and RSI < 30
    if last['Close'] < last['BB_LOWER'] and last['RSI'] < RSI_LOWER:
        stop_loss = df.iloc[-WINDOW:]['Low'].min()
        return 'BUY', stop_loss

    # SELL signal: price > upper BB and RSI > 70
    if last['Close'] > last['BB_UPPER'] and last['RSI'] > RSI_UPPER:
        stop_loss = df.iloc[-WINDOW:]['High'].max()
        return 'SELL', stop_loss
    return 'WAIT', None

def place_order(symbol, signal, stop_loss, log_callback=None):
    tick = get_tick_info(symbol)
    if not tick:
        log(f"❌ Could not get tick info for {symbol}", log_callback)
        return False
    order_type = mt5.ORDER_TYPE_BUY if signal == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if signal == "BUY" else tick.bid
    risk = abs(price - stop_loss)
    take_profit = price + (risk * RR_RATIO) if signal == "BUY" else price - (risk * RR_RATIO)
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
        "type": order_type, "price": price, "sl": stop_loss, "tp": take_profit,
        "deviation": 20, "magic": MAGIC, "comment": "BB+RSI MR", "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    log(f"⚡ SENDING {signal} @ {price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f}", log_callback)
    result = send_order(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"✅ {signal} ORDER PLACED! Ticket: {result.order}", log_callback)
        return True
    else:
        log(f"❌ {signal} FAILED: {result.comment if result else 'No result'}", log_callback)
        return False

def _strategy_loop(symbol, log_callback=None):
    while bot_running:
        try:
            if not get_positions(symbol):
                df = fetch_data(symbol, count=max(WINDOW, RSI_PERIOD) + 10)
                if not df.empty:
                    df = calculate_indicators(df)
                    signal, sl = get_signal(df)
                    if signal != "WAIT":
                        place_order(symbol, signal, sl, log_callback)
            else:
                log(f"📊 Position already open on {symbol}. Monitoring...", log_callback)
            time.sleep(INTERVAL)
        except Exception as e:
            log(f"❌ Error in live strategy loop: {e}", log_callback)
            time.sleep(INTERVAL)

def run_strategy(symbol, log_callback=None, profit_callback=None):
    global bot_running
    if bot_running:
        log("⚠️ Strategy is already running.", log_callback)
        return True
    if not connect(log_callback): return False
    bot_running = True
    log(f"🚀 BB+RSI Mean Reversion Started on {symbol}", log_callback)
    threading.Thread(target=_strategy_loop, args=(symbol, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    global bot_running
    bot_running = False
    log("🛑 Strategy stopping...", log_callback)
    shutdown()
    log("✅ Strategy stopped.", log_callback)
