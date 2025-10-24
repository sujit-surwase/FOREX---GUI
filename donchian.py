import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import threading
import time
from mt5_config import *

# ==========================================================================================
# == REQUIRED PARAMETERS FOR THE BACKTESTER
# ==========================================================================================
SYMBOL = "EURUSD"
VOLUME = 0.1
RR_RATIO = 2.0      # Take Profit will be 2x the Stop Loss distance

# --- Strategy-Specific Parameters ---
EMA_PERIOD = 200        # Period for the long-term trend-filtering EMA
DONCHIAN_PERIOD = 20    # Period for the Donchian Channel breakout

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """
    This function is required by the backtester.
    It calculates the EMA and Donchian Channels.
    """
    try:
        # Ensure column names are standardized
        df.rename(columns={'close': 'Close', 'low': 'Low', 'high': 'High'}, inplace=True, errors='ignore')
        
        # 1. Calculate the long-term EMA for trend direction
        df['EMA_200'] = df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean()
        
        # 2. Calculate Donchian Channels
        # The upper band is the highest high over the last N periods
        df['Donchian_Upper'] = df['High'].rolling(window=DONCHIAN_PERIOD).max()
        # The lower band is the lowest low over the last N periods
        df['Donchian_Lower'] = df['Low'].rolling(window=DONCHIAN_PERIOD).min()
        
        # Drop rows with initial NaN values from rolling calculations
        return df.dropna()
        
    except Exception as e:
        print(f"Indicator calculation error: {e}")
        return None

def get_signal(df):
    """
    This function is required by the backtester.
    It generates a signal based on an EMA trend filter and a Donchian breakout.
    """
    if df is None or len(df) < 2:
        return "WAIT", None
    
    try:
        last_candle = df.iloc[-1]
        
        # --- BUY SIGNAL LOGIC ---
        # 1. Price must be above the 200 EMA (uptrend).
        # 2. Price must close above the upper Donchian band (breakout).
        is_uptrend = last_candle['Close'] > last_candle['EMA_200']
        is_buy_breakout = last_candle['Close'] > last_candle['Donchian_Upper']

        if is_uptrend and is_buy_breakout:
            signal = "BUY"
            # For a breakout, a logical stop loss is the other side of the channel
            stop_loss = last_candle['Donchian_Lower']
            return signal, stop_loss
        
        # --- SELL SIGNAL LOGIC ---
        # 1. Price must be below the 200 EMA (downtrend).
        # 2. Price must close below the lower Donchian band (breakout).
        is_downtrend = last_candle['Close'] < last_candle['EMA_200']
        is_sell_breakout = last_candle['Close'] < last_candle['Donchian_Lower']

        if is_downtrend and is_sell_breakout:
            signal = "SELL"
            # For a breakout, a logical stop loss is the other side of the channel
            stop_loss = last_candle['Donchian_Upper']
            return signal, stop_loss
            
        return "WAIT", None
        
    except Exception:
        return "WAIT", None

# ==========================================================================================
# == LIVE TRADING FUNCTIONS (Can be adapted for this new strategy)
# ==========================================================================================

INTERVAL = 60
MAGIC = 99999
TIMEFRAME = mt5.TIMEFRAME_M1

bot_running = False

def run_strategy(symbol, log_callback=None, profit_callback=None):
    """Live trading runner for the EMA+Donchian strategy."""
    global bot_running
    if not connect(log_callback): return False

    bot_running = True
    log(f"🚀 EMA+Donchian Strategy Started on {symbol}", log_callback)

    def strategy_loop():
        while bot_running:
            try:
                if len(get_positions(symbol)) == 0:
                    df = fetch_data(symbol, count=EMA_PERIOD + DONCHIAN_PERIOD)
                    if not df.empty:
                        df_with_indicators = calculate_indicators(df)
                        signal, sl = get_signal(df_with_indicators)
                        if signal != "WAIT":
                            # This part needs a function to calculate TP and place the order
                            log(f"LIVE SIGNAL DETECTED: {signal} on {symbol}", log_callback)
                            # place_order(symbol, signal, sl, RR_RATIO, log_callback)
                time.sleep(INTERVAL)
            except Exception as e:
                log(f"❌ Error in live loop: {e}", log_callback)
                time.sleep(INTERVAL)
    
    threading.Thread(target=strategy_loop, daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    global bot_running
    bot_running = False
    log("🛑 Strategy stopping...", log_callback)
    shutdown()