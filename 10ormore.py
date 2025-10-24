# my_strategy.py
# Strategy: Sells after 10+ consecutive green candles are followed by 2 red candles.

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import threading
import time

# ==========================================================================================
# == STRATEGY PARAMETERS (Required for Backtester & Live Trading)
# ==========================================================================================
VOLUME = 0.5
RR_RATIO = 3.0 # Reward:Risk ratio for backtesting
SL_USD = 200   # Stop Loss in USD (used for live trading)
TP_USD = 600   # Take Profit in USD (used for live trading)
MAGIC = 99999  # Magic number for live trades

# ==========================================================================================
# == BACKTESTER-SPECIFIC FUNCTIONS
# ==========================================================================================
def calculate_indicators(df):
    """
    Required by the backtester. Prepares data for analysis.
    """
    column_map = {'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}
    df.rename(columns=column_map, inplace=True)
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """
    Required by the backtester. Checks for the trading signal.
    """
    if len(df) < 12:
        return "WAIT", None

    current_candle = df.iloc[-1]
    previous_candle = df.iloc[-2]

    if not (current_candle['Close'] < current_candle['Open'] and previous_candle['Close'] < previous_candle['Open']):
        return "WAIT", None

    green_count = 0
    for i in range(len(df) - 3, -1, -1):
        if df.iloc[i]['Close'] > df.iloc[i]['Open']:
            green_count += 1
        else:
            break
    
    if green_count >= 10:
        signal = "SELL"
        stop_loss = previous_candle['High']
        return signal, stop_loss

    return "WAIT", None

# ==========================================================================================
# == LIVE TRADING FUNCTIONS
# ==========================================================================================
bot_running = False
strategy_thread = None

def place_sell_order(symbol, log_callback):
    """Places a SELL order for the specified symbol."""
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        log_callback(f"❌ Could not get info for {symbol}")
        return

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        log_callback(f"❌ Could not get tick for {symbol}")
        return
    
    entry_price = tick.bid
    digits = symbol_info.digits
    
    sl = round(entry_price + (SL_USD / (symbol_info.trade_tick_value * VOLUME)), digits)
    tp = round(entry_price - (TP_USD / (symbol_info.trade_tick_value * VOLUME)), digits)

    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
        "type": mt5.ORDER_TYPE_SELL, "price": entry_price, "sl": sl, "tp": tp,
        "deviation": 20, "magic": MAGIC, "comment": "SELL-10green-2red",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    log_callback(f"📤 Sending SELL order for {symbol} @ {entry_price:.5f}")
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log_callback(f"✅ SELL order placed! Ticket: {result.order}")
    else:
        retcode = result.retcode if result else 'N/A'
        comment = result.comment if result else 'No result'
        log_callback(f"❌ SELL order failed. Code: {retcode}, Comment: {comment}")


# MODIFIED: Updated the function signature to match the new dashboard.
def run_strategy(symbol, timeframe, log_callback):
    """
    Required by the GUI. Starts the live trading loop.
    """
    global bot_running, strategy_thread
    bot_running = True
    
    def strategy_loop():
        log_callback(f"✅ Strategy thread started for {symbol} on timeframe ID {timeframe}.")
        last_signal_time = None

        while bot_running:
            try:
                # The main dashboard now handles profit updates automatically.
                # This loop only needs to check for signals and place trades.

                # --- Signal Checking ---
                # MODIFIED: Uses the 'timeframe' passed from the GUI.
                rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 50)
                if rates is None or len(rates) < 12:
                    time.sleep(5) # Wait if not enough data
                    continue
                
                df = pd.DataFrame(rates)
                df = calculate_indicators(df)
                signal, _ = get_signal(df)

                # --- Trade Execution ---
                positions = mt5.positions_get(symbol=symbol)
                position_count = len(positions) if positions else 0
                
                if signal == "SELL" and position_count == 0:
                    current_time = time.time()
                    if last_signal_time and (current_time - last_signal_time) < 300: # 5 minute cooldown
                        log_callback("Signal found, but in cooldown period to prevent re-entry.")
                    else:
                        log_callback(f"🎯 Live SELL signal detected for {symbol}!")
                        place_sell_order(symbol, log_callback)
                        last_signal_time = current_time

            except Exception as e:
                log_callback(f"❌ Error in live loop: {e}")
            
            # Wait for 15 seconds before the next check
            time.sleep(15)

        log_callback(f"🔌 Strategy thread for {symbol} shut down.")

    strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
    strategy_thread.start()
    return True # Indicate successful start

# MODIFIED: Updated the function to accept the log_callback.
def stop_strategy(log_callback):
    """
    Required by the GUI. Stops the live trading loop.
    """
    global bot_running
    if bot_running:
        bot_running = False
        log_callback("✅ Stop signal received by strategy.")