import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import threading
import time
from mt5_config import *

# ==========================================================================================
# == REQUIRED PARAMETERS FOR THE BACKTESTER
# ==========================================================================================
SYMBOL = "BTCUSDm"
VOLUME = 0.5
RR_RATIO = 3.0

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """Cleans and prepares data for the backtester."""
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True, errors='ignore')
    for col in ['Open', 'High', 'Low', 'Close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """Combines logic for Hammer (BUY) and Hanging Man (SELL) patterns."""
    if len(df) < 13: return 'WAIT', None
    last_candle, previous_candle = df.iloc[-1], df.iloc[-2]

    # Check for BUY Signal (Hammer Pattern): 2 green after 10+ red
    if last_candle['Close'] > last_candle['Open'] and previous_candle['Close'] > previous_candle['Open']:
        red_count = 0
        for i in range(len(df) - 3, -1, -1):
            if df.iloc[i]['Close'] < df.iloc[i]['Open']: red_count += 1
            else: break
        if red_count >= 10: return 'BUY', last_candle['Low']

    # Check for SELL Signal (Hanging Man Pattern): 2 red after 10+ green
    if last_candle['Close'] < last_candle['Open'] and previous_candle['Close'] < previous_candle['Open']:
        green_count = 0
        for i in range(len(df) - 3, -1, -1):
            if df.iloc[i]['Close'] > df.iloc[i]['Open']: green_count += 1
            else: break
        if green_count >= 10: return 'SELL', last_candle['High']
            
    return 'WAIT', None

# ==========================================================================================
# == LIVE TRADING FUNCTIONS
# ==========================================================================================

INTERVAL = 60
MAGIC = 99999
SL_USD = 200
TP_USD = 600
TIMEFRAME = mt5.TIMEFRAME_M1
bot_running = False
last_buy_signal, last_sell_signal = None, None

def log(message, log_callback=None):
    if log_callback: log_callback(message)
    else: print(message)

def place_order(symbol, signal, log_callback=None):
    """Places a BUY or SELL order for the live strategy."""
    try:
        tick, info = get_tick_info(symbol), get_symbol_info(symbol)
        if not tick or not info:
            log(f"❌ Market data missing for {symbol}.", log_callback)
            return

        point, digits = info.point, info.digits
        if signal == "BUY":
            price = tick.ask
            sl, tp = round(price - (SL_USD * point), digits), round(price + (TP_USD * point), digits)
            order_type, comment = mt5.ORDER_TYPE_BUY, "BUY-Hammer-1Min"
        else: # SELL
            price = tick.bid
            sl, tp = round(price + (SL_USD * point), digits), round(price - (TP_USD * point), digits)
            order_type, comment = mt5.ORDER_TYPE_SELL, "SELL-HangingMan-1Min"

        request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME, "type": order_type,
                   "price": price, "sl": sl, "tp": tp, "deviation": 2000, "magic": MAGIC, "comment": comment,
                   "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC}
        
        log(f"📤 Sending {signal} order for {symbol} @ {price}", log_callback)
        result = send_order(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ {signal} order placed! Ticket: {result.order}", log_callback)
        else:
            log(f"❌ {signal} order failed. RetCode: {result.retcode if result else 'None'}", log_callback)
    except Exception as e:
        log(f"❌ Error placing {signal} order: {e}", log_callback)

# --- MODIFIED: Added 'symbol' as the first parameter ---
def run_strategy(symbol, log_callback=None, profit_callback=None):
    """Main strategy function that the GUI expects."""
    global bot_running, last_buy_signal, last_sell_signal
    if not connect(log_callback): return False
    
    bot_running = True
    last_buy_signal, last_sell_signal = None, None
    log(f"🚀 DUAL PATTERN Strategy Started on {symbol}", log_callback)
    
    if profit_callback:
        threading.Thread(target=update_profit_thread, args=(symbol, profit_callback,), daemon=True).start()
    
    def strategy_loop():
        global last_buy_signal, last_sell_signal
        while bot_running:
            try:
                # MODIFIED: Use the symbol passed from the GUI
                df = fetch_data(symbol, TIMEFRAME, 100)
                if df is None or df.empty:
                    time.sleep(INTERVAL)
                    continue
                
                # We use the backtester's get_signal function for the core logic
                signal, _ = get_signal(calculate_indicators(df.copy()))
                
                if signal == "BUY" and signal != last_buy_signal:
                    place_order(symbol, "BUY", log_callback)
                    last_buy_signal = signal
                elif signal != "BUY":
                    last_buy_signal = None
                
                if signal == "SELL" and signal != last_sell_signal:
                    place_order(symbol, "SELL", log_callback)
                    last_sell_signal = signal
                elif signal != "SELL":
                    last_sell_signal = None
                
                time.sleep(INTERVAL)
            except Exception as e:
                log(f"❌ Error in strategy loop: {e}", log_callback)
                time.sleep(INTERVAL)
    
    threading.Thread(target=strategy_loop, daemon=True).start()
    return True

def update_profit_thread(symbol, profit_callback):
    """Update profit display in separate thread."""
    while bot_running:
        try:
            positions = get_positions(symbol)
            total_profit = sum(pos.profit for pos in positions)
            profit_callback(total_profit, len(positions))
            time.sleep(5)
        except Exception as e:
            log(f"Error updating profit: {e}")
            time.sleep(10)

def close_all_positions(symbol, log_callback=None):
    """Close all open positions for the specified symbol."""
    positions = get_positions(symbol)
    if not positions:
        log(f"📊 No positions to close for {symbol}", log_callback)
        return
    
    for pos in positions:
        price = get_tick_info(symbol).ask if pos.type == mt5.ORDER_TYPE_SELL else get_tick_info(symbol).bid
        close_request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": pos.volume,
                         "type": mt5.ORDER_TYPE_BUY if pos.type == mt5.ORDER_TYPE_SELL else mt5.ORDER_TYPE_SELL,
                         "position": pos.ticket, "price": price, "deviation": 2000, "magic": MAGIC,
                         "comment": "Manual close", "type_time": mt5.ORDER_TIME_GTC,
                         "type_filling": mt5.ORDER_FILLING_IOC}
        result = send_order(close_request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ Closed position: {pos.ticket}", log_callback)
        else:
            log(f"❌ Failed to close position {pos.ticket}", log_callback)

def stop_strategy(log_callback=None):
    """Stop the strategy."""
    global bot_running
    bot_running = False
    shutdown()
    log("🛑 Strategy Stopped.", log_callback)