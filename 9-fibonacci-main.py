"""
Fibonacci_Retracement_v1.py — A trend-continuation strategy using RSI, SMA, and Fibonacci retracements.

Integrates seamlessly with the GUI dashboard and backtester:
- Detects swing highs/lows over 5-day windows.
- Uses Fibonacci 38.2% & 61.8% retracements for confirmation.
- Confirms trade using RSI < 50 (for BUYs) or > 50 (for SELLs) and SMA crossover.
- Includes dynamic trailing stop logic for both BUY and SELL trades.
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
    """Generates a BUY or SELL signal based on Fibonacci retracement logic."""
    if len(df) < 100:
        return "WAIT", None
    
    # FIX: The 'Rolling' object does not have .idxmax() or .idxmin()
    # Instead, we slice the last 80 candles and find the max/min and their indices from that slice.
    df_slice = df.iloc[-80:] # Get the last 80 candles
    hh = df_slice['high'].max()
    ll = df_slice['low'].min()
    hh_idx = df_slice['high'].idxmax()
    ll_idx = df_slice['low'].idxmin()
    
    diff = hh - ll
    if diff == 0: # Avoid division by zero if price hasn't moved
        return "WAIT", None
    
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if ll_idx < hh_idx: # Uptrend: Low came before High
        direction = "uptrend"
        fib_382 = ll + (diff * 0.382)
        fib_618 = ll + (diff * 0.618)
        
        # BUY signal logic
        if prev['low'] <= fib_618 <= prev['high'] and curr['close'] > curr['open']:
            if curr['RSI'] < 50 and curr['SMA20'] > curr['SMA50']:
                stop_loss = fib_382
                return "BUY", stop_loss
    
    elif hh_idx < ll_idx: # Downtrend: High came before Low
        direction = "downtrend"
        # Fib levels for a downtrend are measured from the top down
        fib_382 = hh - (diff * 0.382) # This is the 38.2% level (target for SL)
        fib_618 = hh - (diff * 0.618) # This is the 61.8% level (entry trigger)
        
        # SELL signal logic (inverse of BUY)
        # 1. Previous candle touches 61.8% level (fib_618)
        # 2. Current candle is BEARISH
        # 3. RSI > 50 (overbought)
        # 4. SMA20 < SMA50 (downtrend confirmed)
        if prev['low'] <= fib_618 <= prev['high'] and curr['close'] < curr['open']:
            if curr['RSI'] > 50 and curr['SMA20'] < curr['SMA50']:
                stop_loss = fib_382 # SL is at the 38.2% level
                return "SELL", stop_loss

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
    # Set index for compatibility with older logic, though live loop doesn't strictly need it
    df.set_index('time', inplace=True, drop=False) 
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

        # FIX: Correct TP calculation for SELL orders
        if order_type == mt5.ORDER_TYPE_BUY:
            tp_price = price + (risk * RR_RATIO)
        else: # SELL
            tp_price = price - (risk * RR_RATIO)
        
        # Determine filling mode
        fill_mode = info.filling_mode
        if fill_mode not in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK]:
            log(f"Warning: Symbol's default filling mode ({fill_mode}) not standard. Defaulting to IOC.", log_callback)
            fill_mode = mt5.ORDER_FILLING_IOC
        
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
            "type_filling": fill_mode,
        }

        log(f"🚀 Sending {signal_type} order: {symbol} @ {price:.5f} | SL: {stop_loss_price:.5f} | TP: {tp_price:.5f}", log_callback)
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ {signal_type} Order filled! Ticket: {result.order}", log_callback)
            
            # FIX: Start the correct trailing stop logic based on signal type
            if signal_type == "BUY":
                threading.Thread(target=trail_buy_stop_logic, args=(symbol, result.order, log_callback), daemon=True).start()
            elif signal_type == "SELL":
                threading.Thread(target=trail_sell_stop_logic, args=(symbol, result.order, log_callback), daemon=True).start()
                
            return True
        elif result:
            log(f"❌ {signal_type} Order failed: {result.comment} (RetCode: {result.retcode})", log_callback)
            return False
        else:
            log(f"❌ {signal_type} Order failed: mt5.order_send() returned None. LastError: {mt5.last_error()}", log_callback)
            return False
            
    except Exception as e:
        log(f"❌ Order placement error: {e}", log_callback)
        return False


def trail_buy_stop_logic(symbol, ticket, log_callback=None):
    """Moves stop-loss dynamically as price reaches target for BUY trades."""
    info = mt5.symbol_info(symbol)
    if not info:
        log(f"❌ Cannot start BUY trailer for #{ticket}: Failed to get symbol_info.", log_callback)
        return
        
    digits = info.digits
    log(f"📈 Trailing stop monitoring activated for BUY ticket #{ticket}", log_callback)

    while bot_running and not _stop_event.is_set():
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                log(f"✅ Position #{ticket} closed. Stopping trailer.", log_callback)
                break 

            pos = positions[0]
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                time.sleep(5)
                continue
                
            current_price = tick.bid # Use BID price to check against SL/TP for a BUY order
            entry_price = pos.price_open
            tp_price = pos.tp
            sl_price = pos.sl

            if tp_price <= entry_price:
                log(f"Trailer #{ticket}: Invalid TP ({tp_price}) <= Entry ({entry_price}). Waiting.", log_callback)
                time.sleep(5)
                continue

            profit_progress = (current_price - entry_price) / (tp_price - entry_price) if (tp_price - entry_price) != 0 else 0
            
            if profit_progress >= TRAILING_ACTIVATION:
                new_sl = current_price * (1 - TRAILING_PERCENT)
                if new_sl > sl_price: # Only move SL *up*
                    sltp_req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": symbol,
                        "position": ticket,
                        "sl": round(new_sl, digits),
                        "tp": pos.tp,
                    }
                    result = mt5.order_send(sltp_req)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        log(f"🔁 SL for BUY #{ticket} updated: {sl_price:.5f} → {new_sl:.5f}", log_callback)
                    elif result:
                        log(f"❌ SL update for BUY #{ticket} failed: {result.comment}", log_callback)
            
            time.sleep(10) 
            
        except Exception as e:
            log(f"❌ Trailing error for BUY #{ticket}: {e}", log_callback)
            time.sleep(10)

# --- NEW FUNCTION: trail_sell_stop_logic ---
def trail_sell_stop_logic(symbol, ticket, log_callback=None):
    """Moves stop-loss dynamically as price reaches target for SELL trades."""
    info = mt5.symbol_info(symbol)
    if not info:
        log(f"❌ Cannot start SELL trailer for #{ticket}: Failed to get symbol_info.", log_callback)
        return
        
    digits = info.digits
    log(f"📉 Trailing stop monitoring activated for SELL ticket #{ticket}", log_callback)

    while bot_running and not _stop_event.is_set():
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                log(f"✅ Position #{ticket} closed. Stopping trailer.", log_callback)
                break 

            pos = positions[0]
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                time.sleep(5)
                continue
                
            current_price = tick.ask # Use ASK price to check against SL/TP for a SELL order
            entry_price = pos.price_open
            tp_price = pos.tp
            sl_price = pos.sl

            if tp_price >= entry_price:
                log(f"Trailer #{ticket}: Invalid TP ({tp_price}) >= Entry ({entry_price}). Waiting.", log_callback)
                time.sleep(5)
                continue

            profit_progress = (entry_price - current_price) / (entry_price - tp_price) if (entry_price - tp_price) != 0 else 0
            
            if profit_progress >= TRAILING_ACTIVATION:
                new_sl = current_price * (1 + TRAILING_PERCENT) # Add percentage for sell
                if new_sl < sl_price: # Only move SL *down*
                    sltp_req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": symbol,
                        "position": ticket,
                        "sl": round(new_sl, digits),
                        "tp": pos.tp,
                    }
                    result = mt5.order_send(sltp_req)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        log(f"🔁 SL for SELL #{ticket} updated: {sl_price:.5f} → {new_sl:.5f}", log_callback)
                    elif result:
                        log(f"❌ SL update for SELL #{ticket} failed: {result.comment}", log_callback)
            
            time.sleep(10) 
            
        except Exception as e:
            log(f"❌ Trailing error for SELL #{ticket}: {e}", log_callback)
            time.sleep(10)


def _strategy_loop(symbol, timeframe, log_callback=None):
    global bot_running
    last_signal_time = pd.Timestamp(0)
    
    while bot_running and not _stop_event.is_set():
        try:
            df_raw = fetch_data(symbol, timeframe)
            if df_raw.empty:
                log("No data fetched, retrying...", log_callback)
                time.sleep(5)
                continue
            
            current_candle_time = df_raw.index[-2] # Time of last closed candle
            if current_candle_time <= last_signal_time:
                time.sleep(5) 
                continue

            df_for_signal = df_raw.iloc[:-1].copy()
            if df_for_signal.empty:
                time.sleep(5)
                continue
                
            df_ind = calculate_indicators(df_for_signal)
            signal, sl = get_signal(df_ind)
            
            # Check for existing positions BEFORE processing signal
            positions = mt5.positions_get(symbol=symbol, magic=MAGIC)
            if positions is None:
                log("Could not check positions, skipping trade.", log_callback)
                time.sleep(5)
                continue
            
            if not positions: # Only trade if no positions are open
                if signal == "BUY":
                    log(f"📊 BUY Signal detected on {symbol}", log_callback)
                    place_order(symbol, "BUY", sl, log_callback)
                    last_signal_time = current_candle_time
                    time.sleep(60) # Wait after signal
                
                # --- ADDED: SELL signal handling ---
                elif signal == "SELL":
                    log(f"📊 SELL Signal detected on {symbol}", log_callback)
                    place_order(symbol, "SELL", sl, log_callback)
                    last_signal_time = current_candle_time
                    time.sleep(60) # Wait after signal
                
                else: # signal == "WAIT"
                    log(f"⏳ Waiting for setup... (Last check: {df_ind.index[-1]})", log_callback)
            
            else: # Positions are open
                log("Position already open. Waiting for it to close.", log_callback)

            
            time.sleep(10) # Main loop sleep
            
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}", log_callback)
            time.sleep(10)


def run_strategy(symbol, timeframe, log_callback=None):
    global bot_running, _stop_event
    if bot_running:
        log("⚠️ Strategy already running.", log_callback)
        return True

    bot_running = True
    _stop_event.clear()
    log(f"🚀 Fibonacci Retracement Strategy Started on {symbol} | TF: {timeframe}", log_callback)
    
    # Pass the real log_callback to the thread
    # Use the 'log' function as the default if None is provided
    effective_log_callback = log_callback if log_callback else log
    
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, effective_log_callback), daemon=True).start()
    return True


def stop_strategy(log_callback=None):
    global bot_running, _stop_event
    
    # Use the 'log' function as a fallback
    effective_log_callback = log_callback if log_callback else log
    
    if not bot_running: 
        effective_log_callback("Strategy is not running.")
        return
        
    bot_running = False
    _stop_event.set()
    
    effective_log_callback("🛑 Strategy stopping...")
    time.sleep(1) # Give the loop a moment to exit
    effective_log_callback("✅ Strategy stopped.")


