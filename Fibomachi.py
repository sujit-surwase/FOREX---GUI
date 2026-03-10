# This strategy is designed to be loaded by main.py
# It does NOT manage its own MT5 connection.
# It expects main.py to have already initialized MT5.

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import threading
import time
import traceback
from datetime import datetime

# --- Strategy Configuration ---
VOLUME = 0.5  # Default/Max volume
MAGIC = 99999
TRAILING_PERCENT = 0.07   # Trail by 7%
TRAILING_ACTIVATION = 0.70 # Activate trailing at 70% of profit target

# --- Global State Variables ---
# These are controlled by run_strategy and stop_strategy
bot_running = False
last_signal = None
strategy_thread = None

# These are set when the strategy is started
SYMBOL = ""
TIMEFRAME = None # This will be the MT5 TIMEFRAME constant
LOG_CALLBACK = None

# --- Logging Helper ---
def log(message, force_print=False):
    """Logs a message using the callback from main.py"""
    if LOG_CALLBACK:
        try:
            LOG_CALLBACK(message)
        except Exception:
            # Fallback if GUI is frozen
            print(message)
    elif force_print:
        # Used by __main__ or if callback fails
        print(message)

# --- MT5 Helper Functions ---
# These functions use the MT5 connection already established by main.py

def fetch_data(symbol, timeframe, count=1000):
    """Fetches historical OHLC data."""
    try:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            log(f"No data fetched for {symbol} {timeframe}")
            return pd.DataFrame()
        
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        return df
    except Exception as e:
        log(f"Error fetching data: {e}\n{traceback.format_exc()}")
        return pd.DataFrame()

def get_tick_info(symbol):
    """Gets the latest tick information."""
    try:
        return mt5.symbol_info_tick(symbol)
    except Exception as e:
        log(f"Error get_tick_info: {e}")
        return None

def get_symbol_info(symbol):
    """Gets static symbol information."""
    try:
        return mt5.symbol_info(symbol)
    except Exception as e:
        log(f"Error get_symbol_info: {e}")
        return None

def get_account_info():
    """Gets account information."""
    try:
        return mt5.account_info()
    except Exception as e:
        log(f"Error get_account_info: {e}")
        return None

def get_positions(symbol=None):
    """Gets open positions for a symbol or all symbols."""
    try:
        if symbol:
            return mt5.positions_get(symbol=symbol)
        return mt5.positions_get()
    except Exception as e:
        log(f"Error get_positions: {e}")
        return []

def send_order(request):
    """Sends a trade request."""
    try:
        return mt5.order_send(request)
    except Exception as e:
        log(f"Error send_order: {e}\n{traceback.format_exc()}")
        return None

def get_last_error():
    """Gets the last MT5 error."""
    try:
        return mt5.last_error()
    except Exception as e:
        return str(e)

# --- Core Strategy Logic (from your file) ---

def calculate_indicators(df):
    """
    Calculates all indicators needed for the strategy.
    This function is required by backtester.py
    """
    # Ensure dataframe is a copy to avoid SettingWithCopyWarning
    df_out = df.copy()
    df_out.loc[:, 'rsi'] = compute_rsi(df_out['close'], 14)
    df_out.loc[:, 'sma_20'] = compute_sma(df_out['close'], 20)
    df_out.loc[:, 'sma_50'] = compute_sma(df_out['close'], 50)
    return df_out

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_sma(series, period=20):
    return series.rolling(window=period).mean()

def find_swing_points(df, lookback_days=5):
    # Assuming 15-min timeframe, 4 candles/hr * 24 hrs
    lookback_candles = lookback_days * 24 * 4
    
    if len(df) < lookback_candles:
        return None, None, None, None
    
    lookback_data = df.iloc[-lookback_candles:].copy()
    
    highest_high = lookback_data['high'].max()
    lowest_low = lookback_data['low'].min()
    
    highest_high_idx = lookback_data['high'].idxmax()
    lowest_low_idx = lookback_data['low'].idxmin()
    
    return highest_high, lowest_low, highest_high_idx, lowest_low_idx

def check_recent_swing_points(df, hh_idx, ll_idx):
    if len(df) < 15:
        return False
    
    recent_15_indices = df.index[-15:]
    
    if hh_idx in recent_15_indices or ll_idx in recent_15_indices:
        return True
    return False

def calculate_fibonacci_levels(high, low, direction):
    diff = high - low
    
    if direction == "uptrend":
        levels = {
            '0.0': low,
            '38.2': low + (diff * 0.382),
            '50.0': low + (diff * 0.5),
            '61.8': low + (diff * 0.618),
            '100.0': high
        }
    else: # downtrend
        levels = {
            '100.0': high,
            '61.8': high - (diff * 0.382), # 38.2% retracement from high
            '50.0': high - (diff * 0.5),   # 50.0% retracement from high
            '38.2': high - (diff * 0.618), # 61.8% retracement from high
            '0.0': low
        }
    
    return levels

def check_fibonacci_condition(df, fib_levels, direction):
    if len(df) < 2:
        return False, None
    
    current = df.iloc[-1]
    previous = df.iloc[-2]
    
    current_close = current['close']
    current_open = current['open']
    prev_close = previous['close']
    
    if direction == "uptrend":
        fib_618 = fib_levels['61.8']
        fib_382 = fib_levels['38.2']
        
        prev_touched_618 = (previous['low'] <= fib_618 <= previous['high'])
        current_is_green = current_close > current_open
        current_moving_up = current_close > prev_close
        
        if prev_touched_618 and current_is_green and current_moving_up:
            log(f"Uptrend Fib Signal: Prev candle touched 61.8 ({fib_618:.5f}), current candle bullish")
            return True, fib_382 # SL at 38.2
    
    else: # downtrend
        fib_382 = fib_levels['38.2'] # This is the 61.8% retracement
        fib_618 = fib_levels['61.8'] # This is the 38.2% retracement
        
        prev_touched_382 = (previous['low'] <= fib_382 <= previous['high'])
        current_is_green = current_close > current_open
        current_moving_up = current_close > prev_close
        
        if prev_touched_382 and current_is_green and current_moving_up:
            log(f"Downtrend Reversal Fib Signal: Prev candle touched 38.2 ({fib_382:.5f}), current candle bullish")
            return True, fib_618 # SL at 61.8
    
    return False, None

def get_signal(df):
    """
    This is the main signal function.
    It's called by the live strategy loop and can also be called by a backtester.
    It expects a dataframe WITH the latest unclosed candle.
    """
    if len(df) < 100:
        return "WAIT", None
    
    # Create copy EXCLUDING the last (unclosed) candle for signal generation
    df_copy = df.iloc[:-1].copy()
    
    if len(df_copy) < 100: # Ensure we still have enough data
        return "WAIT", None

    # MODIFICATION: Call the new calculate_indicators function
    # This replaces the inline .loc calls for rsi, sma_20, and sma_50
    df_copy = calculate_indicators(df_copy)
    
    # Get values from the last *closed* candle
    current_rsi = df_copy['rsi'].iloc[-1]
    current_sma_20 = df_copy['sma_20'].iloc[-1]
    current_sma_50 = df_copy['sma_50'].iloc[-1]
    current_price = df_copy['close'].iloc[-1]
    
    log(f"Checking signals: RSI: {current_rsi:.2f}, SMA20: {current_sma_20:.5f}, SMA50: {current_sma_50:.5f}, Price: {current_price:.5f}")
    
    buy_signals = 0
    stop_loss = None
    
    hh, ll, hh_idx, ll_idx = find_swing_points(df_copy, lookback_days=5)
    
    if hh is None or ll is None:
        log("Not enough data for swing points")
        return "WAIT", None
    
    log(f"CONDITION 1: Swing Points - HH: {hh:.5f}, LL: {ll:.5f}")
    
    if check_recent_swing_points(df_copy, hh_idx, ll_idx):
        log("Swing points are too recent (in last 15 candles). Waiting.")
        return "WAIT", None
    
    log("Swing points are valid (not in last 15 candles)")
    buy_signals += 1
    
    if ll_idx < hh_idx:
        direction = "uptrend"
        fib_levels = calculate_fibonacci_levels(hh, ll, direction)
        log("CONDITION 2: Uptrend detected (LL to HH)")
    else:
        direction = "downtrend"
        fib_levels = calculate_fibonacci_levels(hh, ll, direction)
        log("CONDITION 2: Downtrend reversal setup (HH to LL)")
    
    # --- NEW LOGIC: 1 Mandatory Signal + 2 of 3 Filters ---
    
    # 1. Check Mandatory Signal (Fibonacci)
    fib_condition, fib_sl = check_fibonacci_condition(df_copy, fib_levels, direction)
    
    if fib_condition:
        log(f"CONDITION 3 (Mandatory): Fibonacci signal confirmed. Proposed SL: {fib_sl:.5f}")
        stop_loss = fib_sl
    else:
        log("CONDITION 3 (Mandatory): No Fibonacci signal. Waiting.")
        return "WAIT", None # Exit early if mandatory signal fails
        
    # 2. Check Filter Signals (Must pass 2 of 3)
    filter_signals = 0
    
    if buy_signals == 1: # This check relies on the "Swing points are valid" check above
        log(f"FILTER 1 (Swings): Valid swing points. (1/3)")
        filter_signals += 1
    else:
        log(f"FILTER 1 (Swings): Swing points invalid. (0/3)")
        
    if current_rsi < 50:
        log(f"FILTER 2 (RSI): {current_rsi:.2f} < 50 - Valid. (2/3)")
        filter_signals += 1
    else:
        log(f"FILTER 2 (RSI): {current_rsi:.2f} >= 50 - Invalid. (1/3)")
    
    if current_sma_20 > current_sma_50:
        log(f"FILTER 3 (SMA): SMA20 > SMA50 - High volatility confirmed. (3/3)")
        filter_signals += 1
    else:
        log(f"FILTER 3 (SMA): SMA20 <= SMA50 - Low volatility. (2/3)")

    log(f"Total Filters Passed: {filter_signals}/3")

    if filter_signals >= 2:
        log(f"STRONG BUY SIGNAL: Mandatory Fib signal + {filter_signals}/3 filters met! SL: {stop_loss:.5f}")
        return "BUY", stop_loss
    else:
        log(f"WEAK SIGNAL: Mandatory Fib signal passed, but only {filter_signals}/3 filters met. Waiting.")
        return "WAIT", None





# --- Order Management (Fixed from line 182) ---

def place_buy_order(stop_loss_price, fib_levels, direction):
    """
    Places a BUY order with proper SL/TP based on Fibonacci levels.
    
    Args:
        stop_loss_price: The calculated stop loss price
        fib_levels: Dictionary of Fibonacci levels
        direction: "uptrend" or "downtrend"
    
    Returns:
        tuple: (ticket, entry_price, initial_tp, fib_levels, direction) or (None, None, None, None, None)
    """
    global SYMBOL
    tick = get_tick_info(SYMBOL)
    info = get_symbol_info(SYMBOL)
    
    if not tick or not info:
        log("❌ Market or symbol data missing for placing order.")
        return None, None, None, None, None
    
    price = tick.ask
    digits = info.digits
    price = round(price, digits)
    sl = round(stop_loss_price, digits)

    # CRITICAL FIX: For BUY orders, SL must be BELOW entry price
    if sl >= price:
        log(f"⚠️ SL issue detected: SL {sl} >= Price {price}")
        # Calculate proper SL based on Fibonacci levels
        if direction == "uptrend":
            # SL should be at 38.2% level
            sl = round(fib_levels['38.2'], digits)
        else:  # downtrend reversal
            # SL should be at 61.8% level (below entry)
            sl = round(fib_levels['61.8'], digits)
        
        # Final safety check
        if sl >= price:
            # Use 2% below entry as emergency SL
            sl = round(price * 0.98, digits)
            log(f"⚠️ Emergency SL set at 2% below entry: {sl}")
        
        log(f"✅ Corrected SL: {sl}")

    if sl >= price:
        log(f"❌ FATAL: Cannot set valid SL. SL {sl} >= Price {price}. Aborting order.")
        return None, None, None, None, None

    # Calculate Volume based on 2% risk
    account = get_account_info()
    capital = account.balance if account else 5000.0
    
    risk_amount = capital * 0.02
    price_diff = abs(price - sl)
    
    if price_diff > 0:
        calculated_volume = risk_amount / (price_diff * info.trade_contract_size)
        calculated_volume = min(calculated_volume, VOLUME)
        calculated_volume = max(calculated_volume, info.volume_min)
        calculated_volume = round(calculated_volume / info.volume_step) * info.volume_step
        volume = calculated_volume
    else:
        volume = info.volume_min
    
    if volume <= 0:
        log(f"⚠️ Calculated volume is {volume}. Using min volume {info.volume_min}")
        volume = info.volume_min

    # DYNAMIC TP: Set initial target to next Fibonacci level
    tp = get_next_fib_target(price, fib_levels, direction)
    tp = round(tp, digits)
    
    # Ensure TP is above entry for BUY
    if tp <= price:
        tp_distance = price_diff * 2  # Fallback to 2:1 R/R
        tp = price + tp_distance
        tp = round(tp, digits)
        log(f"⚠️ Using fallback TP (2:1 R/R): {tp}")
    
    log(f"📊 Risk/Reward | Entry: {price} | SL: {sl} | TP: {tp} | R/R: {(tp-price)/(price-sl):.2f}:1")
    
    fill_mode = info.filling_mode
    if fill_mode not in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK]:
        fill_mode = mt5.ORDER_FILLING_IOC 
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": MAGIC,
        "comment": "BUY-Fib",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_mode,
    }
    
    log(f"📤 Sending BUY order: {volume} lots @ {price} | SL: {sl} | TP: {tp}")
    
    result = send_order(request)
    if result is None:
        log(f"❌ order_send() returned None. Error: {get_last_error()}")
        return None, None, None, None, None
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"❌ BUY order failed. RetCode: {result.retcode} | Comment: {result.comment}")
        return None, None, None, None, None
    
    log(f"✅ BUY order placed! Ticket: #{result.order}")
    return result.order, price, tp, fib_levels, direction


def get_next_fib_target(current_price, fib_levels, direction):
    """
    Determines the next Fibonacci target based on current price.
    
    Progression: 0.0 → 0.236 → 0.382 → 0.5 → 0.618 → 0.786 → 1.0
    """
    # Add missing Fibonacci levels
    fib_0 = fib_levels['0.0']
    fib_100 = fib_levels['100.0']
    diff = abs(fib_100 - fib_0)
    
    # Calculate all Fibonacci levels
    if direction == "uptrend":
        all_fibs = {
            '0.0': fib_0,
            '23.6': fib_0 + (diff * 0.236),
            '38.2': fib_0 + (diff * 0.382),
            '50.0': fib_0 + (diff * 0.5),
            '61.8': fib_0 + (diff * 0.618),
            '78.6': fib_0 + (diff * 0.786),
            '100.0': fib_100
        }
    else:  # downtrend - targets going UP from current price
        all_fibs = {
            '0.0': fib_0,
            '23.6': fib_100 - (diff * 0.764),  # 23.6% up from low
            '38.2': fib_100 - (diff * 0.618),  # 38.2% up from low
            '50.0': fib_100 - (diff * 0.5),    # 50% up from low
            '61.8': fib_100 - (diff * 0.382),  # 61.8% up from low
            '78.6': fib_100 - (diff * 0.214),  # 78.6% up from low
            '100.0': fib_100
        }
    
    # Find next level above current price
    sorted_levels = sorted(all_fibs.items(), key=lambda x: x[1])
    
    for level_name, level_price in sorted_levels:
        if level_price > current_price:
            log(f"🎯 Next Fib target: {level_name} @ {level_price:.5f}")
            return level_price
    
    # If price is above all levels, use 100.0 level
    return all_fibs['100.0']


def trail_buy_stop_logic(ticket, entry_price, initial_tp, fib_levels, direction):
    """
    Dynamic trailing stop with Fibonacci-based target progression.
    
    Logic:
    1. Monitor price progress toward current TP
    2. When price hits EXACTLY 70% of target, activate 7% trailing stop
    3. When price reaches EXACTLY 99% of TP, move to next Fibonacci level
    4. Continue until position is closed
    """
    global bot_running, SYMBOL
    
    info = get_symbol_info(SYMBOL)
    if not info:
        log(f"❌ Could not get symbol info for trailing SL on ticket #{ticket}")
        return
        
    digits = info.digits
    
    log(f"🚀 Started dynamic Fibonacci trailing for position #{ticket}")
    log(f"📍 Initial TP: {initial_tp:.5f} | Entry: {entry_price:.5f}")
    
    current_tp = initial_tp
    trailing_active = False
    target_moved = False  # Flag to prevent multiple TP updates
    last_update_time = time.time()
    last_progress = 0
    
    while bot_running:
        positions = get_positions(SYMBOL)
        pos = next((p for p in positions if p.ticket == ticket and p.magic == MAGIC), None)
        if not pos:
            log(f"⚠️ Position #{ticket} closed. Stopping trailer.")
            break
        
        current_price_tick = get_tick_info(SYMBOL)
        if not current_price_tick:
            time.sleep(5)
            continue
            
        current_price = current_price_tick.bid  # Use BID for SL checks
        current_sl = pos.sl
        current_tp_pos = pos.tp
        
        # Calculate progress toward TP
        distance_to_tp = abs(current_tp_pos - entry_price)
        current_profit_distance = current_price - entry_price
        
        if distance_to_tp > 0:
            progress_percent = (current_profit_distance / distance_to_tp) * 100
        else:
            progress_percent = 0
        
        # Calculate distance from current price to TP in percentage
        remaining_distance = abs(current_tp_pos - current_price)
        distance_to_tp_from_current = (remaining_distance / current_tp_pos) * 100 if current_tp_pos > 0 else 100
        
        # Log status every 60 seconds or when progress changes significantly
        if time.time() - last_update_time > 60 or abs(progress_percent - last_progress) > 5:
            log(f"📊 #{ticket} | Price: {current_price:.5f} | Progress: {progress_percent:.1f}% | To TP: {distance_to_tp_from_current:.2f}% | P/L: ${pos.profit:.2f} | Trailing: {'✅' if trailing_active else '❌'}")
            last_update_time = time.time()
            last_progress = progress_percent
        
        # CRITICAL: Check if price is within 1% of TP (99% reached)
        if distance_to_tp_from_current <= 1.0 and not target_moved:
            log(f"🎯 TARGET NEARLY REACHED! Price is {distance_to_tp_from_current:.2f}% from TP ({current_tp_pos:.5f})")
            log(f"🔄 Moving to next Fibonacci level...")
            
            next_tp = get_next_fib_target(current_price, fib_levels, direction)
            next_tp = round(next_tp, digits)
            
            # Ensure next TP is above current price
            if next_tp > current_price * 1.001:  # At least 0.1% above current price
                # Update TP to next Fibonacci level
                sltp_request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": SYMBOL,
                    "position": ticket,
                    "sl": current_sl,
                    "tp": next_tp,
                    "magic": MAGIC
                }
                
                result = send_order(sltp_request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"✅ TP UPDATED: {current_tp_pos:.5f} → {next_tp:.5f}")
                    current_tp = next_tp
                    target_moved = True
                    trailing_active = False  # Reset trailing for new target
                    
                    # Wait a moment before checking new target
                    time.sleep(2)
                    target_moved = False  # Allow next TP update
                else:
                    log(f"⚠️ Failed to update TP. RetCode: {result.retcode if result else 'None'}")
            else:
                log(f"🏁 Final Fibonacci level reached or next level too close. Maintaining TP: {current_tp_pos:.5f}")
        
        # CRITICAL: Activate trailing stop ONLY at exactly 70% progress
        if progress_percent >= 70.0 and not trailing_active:
            trailing_active = True
            log(f"🔥🔥🔥 TRAILING STOP ACTIVATED! 🔥🔥🔥")
            log(f"📈 Progress: {progress_percent:.1f}% (≥70% threshold reached)")
            log(f"🛡️ 7% Trailing SL now active - SL will follow price")
        
        # Execute trailing stop logic ONLY if activated
        if trailing_active:
            # Calculate trailing SL (7% below current price)
            trailing_sl = current_price * (1 - TRAILING_PERCENT)
            trailing_sl = round(trailing_sl, digits)
            
            # IMPORTANT: Only move SL up, never down
            if trailing_sl > current_sl:
                sltp_request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": SYMBOL,
                    "position": ticket,
                    "sl": trailing_sl,
                    "tp": current_tp_pos,
                    "magic": MAGIC
                }
                
                result = send_order(sltp_request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    sl_increase = trailing_sl - current_sl
                    log(f"⬆️ SL TRAILED UP: {current_sl:.5f} → {trailing_sl:.5f} (+{sl_increase:.5f}) | Price: {current_price:.5f}")
                elif result:
                    log(f"⚠️ Trail failed. RetCode: {result.retcode}")
            else:
                # SL not moved - this is normal, just means price hasn't moved enough
                pass
        else:
            # Log waiting status only occasionally
            if progress_percent > 0 and time.time() - last_update_time > 120:
                log(f"⏳ Waiting for 70% threshold. Current: {progress_percent:.1f}%")
        
        time.sleep(10)  # Check every 10 seconds


# --- Main Strategy Loop (Updated) ---

def strategy_loop():
    """The main trading loop with improved error handling."""
    global bot_running, last_signal, SYMBOL, TIMEFRAME
    
    active_trails = {}
    last_candle_time = None
    
    log("✅ Strategy loop started successfully")
    
    LOOP_INTERVAL_SECONDS = 30  # Check every 30 seconds

    while bot_running:
        try:
            df = fetch_data(SYMBOL, TIMEFRAME, 1000)
            if df.empty:
                log("⚠️ No data received, retrying...")
                time.sleep(LOOP_INTERVAL_SECONDS)
                continue
            
            # Check if new candle has closed
            current_candle_time = df.index[-2]  # Last closed candle
            
            if last_candle_time is None:
                last_candle_time = current_candle_time
            
            if current_candle_time != last_candle_time:
                log(f"🕒 New candle closed at {current_candle_time}. Checking for signals...")
                last_candle_time = current_candle_time
                
                signal, stop_loss = get_signal(df)
                
                # Check for existing positions
                positions = get_positions(SYMBOL)
                my_positions = [p for p in positions if p.magic == MAGIC]

                if signal == "BUY" and signal != last_signal and not my_positions:
                    log("━━━ 🎯 BUY SIGNAL RECEIVED ━━━")
                    
                    # Get Fibonacci levels for dynamic TP management
                    df_copy = df.iloc[:-1].copy()
                    df_copy = calculate_indicators(df_copy)
                    
                    hh, ll, hh_idx, ll_idx = find_swing_points(df_copy, lookback_days=5)
                    
                    if ll_idx < hh_idx:
                        direction = "uptrend"
                    else:
                        direction = "downtrend"
                    
                    fib_levels = calculate_fibonacci_levels(hh, ll, direction)
                    
                    ticket, entry, tp, fibs, dir = place_buy_order(stop_loss, fib_levels, direction)
                    
                    if ticket:
                        log(f"✅ Position #{ticket} opened successfully!")
                        
                        # Start trailing thread
                        trail_thread = threading.Thread(
                            target=trail_buy_stop_logic,
                            args=(ticket, entry, tp, fibs, dir),
                            daemon=True
                        )
                        trail_thread.start()
                        active_trails[ticket] = trail_thread
                        log(f"🔄 Started trailing thread for #{ticket}")
                    
                    last_signal = signal
                
                elif signal == "WAIT":
                    if last_signal == "BUY":
                        log("🔄 Signal reset to WAIT. Ready for next signal.")
                        last_signal = None
                
                # Clean up finished threads
                for ticket in list(active_trails.keys()):
                    pos_exists = any(p.ticket == ticket for p in my_positions)
                    if not pos_exists:
                        active_trails.pop(ticket, None)
            
            time.sleep(LOOP_INTERVAL_SECONDS)
            
        except Exception as e:
            log(f"❌ ERROR in strategy loop: {e}\n{traceback.format_exc()}")
            time.sleep(LOOP_INTERVAL_SECONDS)


# --- FUNCTIONS REQUIRED BY main.py (Updated) ---

def run_strategy(symbol, timeframe, log_callback):
    """Called by main.py to start the strategy."""
    global bot_running, strategy_thread, last_signal
    global SYMBOL, TIMEFRAME, LOG_CALLBACK
    
    if bot_running:
        log("⚠️ Strategy is already running.")
        return False
        
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("🚀 Starting Fibonacci Retracement Strategy")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    bot_running = True
    last_signal = None
    SYMBOL = symbol
    TIMEFRAME = timeframe
    LOG_CALLBACK = log_callback
    
    log("📋 Strategy Rules:")
    log("  1️⃣ Find 5-day swing high/low (must not be in last 15 candles)")
    log("  2️⃣ Calculate Fibonacci retracement levels")
    log("  3️⃣ [MANDATORY] Wait for Fib 61.8% (uptrend) or 38.2% (downtrend)")
    log("  4️⃣ Confirm with bullish candle")
    log("  5️⃣ [FILTER] RSI < 50")
    log("  6️⃣ [FILTER] SMA20 > SMA50")
    log(f"  7️⃣ Dynamic TP: Progress through Fib levels (0.236→0.382→0.5→0.618→0.786→1.0)")
    log(f"  8️⃣ Trailing SL: {TRAILING_PERCENT*100:.0f}% activates at {TRAILING_ACTIVATION*100:.0f}% of target")
    log(f"  9️⃣ Symbol: {SYMBOL} | Timeframe: {TIMEFRAME} | Max Volume: {VOLUME}")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
    strategy_thread.start()
    
    log("✅ Strategy started successfully!")
    return True


def stop_strategy(log_callback):
    """Called by main.py to stop the strategy."""
    global bot_running, strategy_thread
    
    if not bot_running:
        log_callback("⚠️ Strategy is not running.")
        return

    log_callback("🛑 Stopping strategy...")
    bot_running = False
    
    if strategy_thread and strategy_thread.is_alive():
        log_callback("⏳ Waiting for strategy thread to terminate...")
        strategy_thread.join(timeout=10)
        if strategy_thread.is_alive():
            log_callback("⚠️ Thread did not terminate gracefully.")
        else:
            log_callback("✅ Strategy thread terminated.")
    
    log_callback("━━━ ✅ Strategy Stopped ━━━")


# --- Standalone Testing Block ---
if __name__ == "__main__":
    def standalone_log(message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    try:
        try:
            from mt5_config import LOGIN, PASSWORD, SERVER, TERMINAL_PATH
        except ImportError:
            standalone_log("❌ mt5_config.py not found.")
            LOGIN, PASSWORD, SERVER, TERMINAL_PATH = 0, "", "", ""
            
        if not LOGIN:
            standalone_log("⚠️ Please fill in mt5_config.py with your credentials.")
        else:
            if not mt5.initialize(path=TERMINAL_PATH, login=LOGIN, password=PASSWORD, server=SERVER):
                standalone_log(f"❌ MT5 init failed: {mt5.last_error()}")
                mt5.shutdown()
            else:
                standalone_log("✅ MT5 Connection Initialized")
                standalone_log("Press Ctrl+C to stop")
                
                TEST_SYMBOL = "EURUSD"
                TEST_TIMEFRAME = mt5.TIMEFRAME_M15
                
                if run_strategy(TEST_SYMBOL, TEST_TIMEFRAME, standalone_log):
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        standalone_log("\n🛑 Ctrl+C detected. Stopping...")
                        stop_strategy(standalone_log)
                        mt5.shutdown()
                        standalone_log("✅ MT5 Shutdown Complete")
                else:
                    standalone_log("❌ Failed to start strategy")
                    mt5.shutdown()

    except Exception as e:
        standalone_log(f"❌ Error: {e}")
        mt5.shutdown()
