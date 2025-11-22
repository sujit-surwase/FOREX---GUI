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
import sys
import io

# Fix Windows console encoding for emoji support
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# --- Strategy Configuration ---
VOLUME = 0.5  # Default/Max volume
MAGIC = 99999
TRAILING_PERCENT = 0.07   # Trail by 7%
TRAILING_ACTIVATION = 0.50 # Activate trailing at 50% of profit target ($500)

# NEW: Fixed profit targets in dollars
FIXED_SL_DOLLARS = 500.0   # Stop loss at $500
FIXED_TP_DOLLARS = 1000.0  # Initial target at $1000
TP_EXTENSION_THRESHOLD = 0.80  # At 80% of target ($800), extend TP
TP_EXTENSION_AMOUNT = 500.0    # Extend TP by $500 more

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
    # Remove problematic Unicode characters for Windows compatibility
    safe_message = message.encode('ascii', 'replace').decode('ascii')
    
    if LOG_CALLBACK:
        try:
            LOG_CALLBACK(safe_message)
        except Exception:
            # Fallback if GUI is frozen
            print(safe_message)
    elif force_print:
        # Used by __main__ or if callback fails
        print(safe_message)

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

def find_swing_points(df, lookback_days=2):
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
    
    hh, ll, hh_idx, ll_idx = find_swing_points(df_copy, lookback_days=2)
    
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


# --- Order Management (UPDATED WITH FIXED SL/TP) ---

def place_buy_order(stop_loss_price, fib_levels, direction):
    """
    Places a BUY order with FIXED $500 SL and $1000 TP.
    
    Args:
        stop_loss_price: The calculated Fibonacci stop loss price (for reference)
        fib_levels: Dictionary of Fibonacci levels (for reference)
        direction: "uptrend" or "downtrend" (for reference)
    
    Returns:
        tuple: (ticket, entry_price, initial_tp_dollars, fib_levels, direction) or (None, None, None, None, None)
    """
    global SYMBOL
    tick = get_tick_info(SYMBOL)
    info = get_symbol_info(SYMBOL)
    
    if not tick or not info:
        log("[X] Market or symbol data missing for placing order.")
        return None, None, None, None, None
    
    price = tick.ask
    digits = info.digits
    price = round(price, digits)
    
    # Calculate SL and TP based on FIXED DOLLAR AMOUNTS
    # $500 risk, $1000 profit target
    point_value = info.trade_contract_size  # For forex, typically 100,000 for 1 lot
    
    # Calculate how many pips/points equal $500 and $1000
    # For EURUSD: 1 lot = $10/pip, so $500 = 50 pips
    volume = VOLUME
    dollar_per_point = volume * info.trade_contract_size * info.point
    
    if dollar_per_point <= 0:
        log("[X] Cannot calculate dollar per point. Aborting.")
        return None, None, None, None, None
    
    # Calculate price distance for $500 SL and $1000 TP
    sl_distance = (FIXED_SL_DOLLARS / dollar_per_point) * info.point
    tp_distance = (FIXED_TP_DOLLARS / dollar_per_point) * info.point
    
    sl = round(price - sl_distance, digits)
    tp = round(price + tp_distance, digits)
    
    # Safety checks
    if sl >= price:
        log(f"[X] FATAL: Cannot set valid SL. SL {sl} >= Price {price}. Aborting order.")
        return None, None, None, None, None
    
    if tp <= price:
        log(f"[X] FATAL: Cannot set valid TP. TP {tp} <= Price {price}. Aborting order.")
        return None, None, None, None, None
    
    log(f"[FIXED-SLTP] Entry: {price} | SL: {sl} (-${FIXED_SL_DOLLARS}) | TP: {tp} (+${FIXED_TP_DOLLARS})")
    log(f"[STATS] Distance | SL: {sl_distance:.5f} ({sl_distance/info.point:.1f} points) | TP: {tp_distance:.5f} ({tp_distance/info.point:.1f} points)")
    log(f"[STATS] Risk/Reward Ratio: 1:{FIXED_TP_DOLLARS/FIXED_SL_DOLLARS:.1f}")
    
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
        "deviation": 50,
        "magic": MAGIC,
        "comment": "BUY-Fixed-SLTP",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_mode,
    }
    
    log(f"[>] Sending BUY order: {volume} lots @ {price} | SL: {sl} | TP: {tp}")
    
    result = send_order(request)
    if result is None:
        log(f"[X] order_send() returned None. Error: {get_last_error()}")
        return None, None, None, None, None
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"[X] BUY order failed. RetCode: {result.retcode} | Comment: {result.comment}")
        return None, None, None, None, None
    
    log(f"[OK] BUY order placed! Ticket: #{result.order}")
    return result.order, price, FIXED_TP_DOLLARS, fib_levels, direction


def trail_buy_stop_logic(ticket, entry_price, initial_tp_dollars, fib_levels, direction):
    """
    Dynamic trailing stop with fixed dollar targets.
    
    Logic:
    1. Monitor profit in dollars
    2. At 50% profit ($500), activate 7% trailing stop
    3. At 80% profit ($800), extend TP by $500 (to $1500 total) while keeping trailing active
    4. Continue trailing at 7% below current price
    """
    global bot_running, SYMBOL
    
    info = get_symbol_info(SYMBOL)
    if not info:
        log(f"[X] Could not get symbol info for trailing SL on ticket #{ticket}")
        return
        
    digits = info.digits
    
    log(f"[TRAIL] Started fixed-dollar trailing for position #{ticket}")
    log(f"[TRAIL] Entry: {entry_price:.5f} | Initial Target: ${initial_tp_dollars}")
    log(f"[TRAIL] Trailing activates at 50% (${FIXED_TP_DOLLARS * 0.5:.0f})")
    log(f"[TRAIL] TP extends at 80% (${FIXED_TP_DOLLARS * 0.8:.0f}) by ${TP_EXTENSION_AMOUNT}")
    
    current_target_dollars = initial_tp_dollars
    trailing_active = False
    tp_extended = False
    last_update_time = time.time()
    last_profit = 0
    
    while bot_running:
        positions = get_positions(SYMBOL)
        pos = next((p for p in positions if p.ticket == ticket and p.magic == MAGIC), None)
        if not pos:
            log(f"[TRAIL] Position #{ticket} closed. Stopping trailer.")
            break
        
        current_price_tick = get_tick_info(SYMBOL)
        if not current_price_tick:
            time.sleep(5)
            continue
            
        current_price = current_price_tick.bid
        current_sl = pos.sl
        current_profit = pos.profit  # Profit in dollars
        
        # Calculate progress percentage
        progress_percent = (current_profit / current_target_dollars) * 100 if current_target_dollars > 0 else 0
        
        # Log status periodically
        if time.time() - last_update_time > 60 or abs(current_profit - last_profit) > 50:
            trail_status = "[ACTIVE]" if trailing_active else "[WAITING FOR 50%]"
            tp_status = "[EXTENDED]" if tp_extended else f"[${current_target_dollars:.0f}]"
            log(f"[TRAIL] #{ticket} | Price: {current_price:.5f} | P/L: ${current_profit:.2f} ({progress_percent:.1f}%) | {trail_status} {tp_status}")
            last_update_time = time.time()
            last_profit = current_profit
        
        # ACTIVATION: At 50% profit ($500), activate trailing
        if current_profit >= (FIXED_TP_DOLLARS * TRAILING_ACTIVATION) and not trailing_active:
            trailing_active = True
            log("="*60)
            log(f"[TRAIL-ON] *** TRAILING STOP ACTIVATED ***")
            log(f"[TRAIL-ON] Profit: ${current_profit:.2f} (>= ${FIXED_TP_DOLLARS * TRAILING_ACTIVATION:.0f})")
            log(f"[TRAIL-ON] 7% Trailing SL now active")
            log("="*60)
        
        # EXTENSION: At 80% profit ($800), extend TP by $500
        if current_profit >= (FIXED_TP_DOLLARS * TP_EXTENSION_THRESHOLD) and not tp_extended:
            log("="*60)
            log(f"[TP-EXTEND] *** TARGET EXTENSION TRIGGERED ***")
            log(f"[TP-EXTEND] Profit: ${current_profit:.2f} (>= ${FIXED_TP_DOLLARS * TP_EXTENSION_THRESHOLD:.0f})")
            log(f"[TP-EXTEND] Extending TP by ${TP_EXTENSION_AMOUNT}")
            log("="*60)
            
            # Calculate new TP price
            new_target_dollars = current_target_dollars + TP_EXTENSION_AMOUNT
            
            # Calculate price distance for new target
            volume = pos.volume
            dollar_per_point = volume * info.trade_contract_size * info.point
            
            if dollar_per_point > 0:
                new_tp_distance = (new_target_dollars / dollar_per_point) * info.point
                new_tp = round(entry_price + new_tp_distance, digits)
                
                # Ensure new TP is above current price
                if new_tp > current_price * 1.001:
                    sltp_request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": SYMBOL,
                        "position": ticket,
                        "sl": current_sl,
                        "tp": new_tp,
                        "magic": MAGIC
                    }
                    
                    result = send_order(sltp_request)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        log(f"[TP-UPDATE] TP extended: ${current_target_dollars:.0f} -> ${new_target_dollars:.0f}")
                        log(f"[TP-UPDATE] New TP price: {new_tp:.5f}")
                        current_target_dollars = new_target_dollars
                        tp_extended = True
                    else:
                        log(f"[TP-FAIL] Failed to extend TP")
                else:
                    log(f"[TP-SKIP] New TP too close to current price")
        
        # Execute trailing ONLY if activated
        if trailing_active:
            # Calculate trailing SL: 7% below current price
            trailing_sl = current_price * (1 - TRAILING_PERCENT)
            trailing_sl = round(trailing_sl, digits)
            
            # Only move SL UP, never down
            if trailing_sl > current_sl:
                sltp_request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": SYMBOL,
                    "position": ticket,
                    "sl": trailing_sl,
                    "tp": pos.tp,  # Keep current TP
                    "magic": MAGIC
                }
                
                result = send_order(sltp_request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    sl_increase = trailing_sl - current_sl
                    log(f"[SL-UP] SL trailed: {current_sl:.5f} -> {trailing_sl:.5f} (+{sl_increase:.5f}) | Price: {current_price:.5f}")
        
        time.sleep(10)


# --- Main Strategy Loop (Updated) ---

def strategy_loop():
    """The main trading loop with improved error handling."""
    global bot_running, last_signal, SYMBOL, TIMEFRAME
    
    active_trails = {}
    last_candle_time = None
    
    log("[LOOP] Strategy loop started successfully")
    
    LOOP_INTERVAL_SECONDS = 30  # Check every 30 seconds

    while bot_running:
        try:
            df = fetch_data(SYMBOL, TIMEFRAME, 1000)
            if df.empty:
                log("[LOOP] No data received, retrying...")
                time.sleep(LOOP_INTERVAL_SECONDS)
                continue
            
            # Check if new candle has closed
            current_candle_time = df.index[-2]  # Last closed candle
            
            if last_candle_time is None:
                last_candle_time = current_candle_time
            
            if current_candle_time != last_candle_time:
                log(f"[CANDLE] New candle closed at {current_candle_time}. Checking for signals...")
                last_candle_time = current_candle_time
                
                signal, stop_loss = get_signal(df)
                
                # Check for existing positions
                positions = get_positions(SYMBOL)
                my_positions = [p for p in positions if p.magic == MAGIC]

                if signal == "BUY" and signal != last_signal and not my_positions:
                    log("="*50)
                    log(" BUY SIGNAL RECEIVED ")
                    log("="*50)
                    
                    # Get Fibonacci levels for reference (not used for SL/TP calculation)
                    df_copy = df.iloc[:-1].copy()
                    df_copy = calculate_indicators(df_copy)
                    
                    hh, ll, hh_idx, ll_idx = find_swing_points(df_copy, lookback_days=2)
                    
                    if ll_idx < hh_idx:
                        direction = "uptrend"
                    else:
                        direction = "downtrend"
                    
                    fib_levels = calculate_fibonacci_levels(hh, ll, direction)
                    
                    ticket, entry, tp_dollars, fibs, dir = place_buy_order(stop_loss, fib_levels, direction)
                    
                    if ticket:
                        log(f"[ORDER] Position #{ticket} opened successfully!")
                        
                        # Start trailing thread
                        trail_thread = threading.Thread(
                            target=trail_buy_stop_logic,
                            args=(ticket, entry, tp_dollars, fibs, dir),
                            daemon=True
                        )
                        trail_thread.start()
                        active_trails[ticket] = trail_thread
                        log(f"[TRAIL] Started trailing thread for #{ticket}")
                    
                    last_signal = signal
                
                elif signal == "WAIT":
                    if last_signal == "BUY":
                        log("[SIGNAL] Signal reset to WAIT. Ready for next signal.")
                        last_signal = None
                
                # Clean up finished threads
                for ticket in list(active_trails.keys()):
                    pos_exists = any(p.ticket == ticket for p in my_positions)
                    if not pos_exists:
                        active_trails.pop(ticket, None)
            
            time.sleep(LOOP_INTERVAL_SECONDS)
            
        except Exception as e:
            log(f"[ERROR] ERROR in strategy loop: {e}\n{traceback.format_exc()}")
            time.sleep(LOOP_INTERVAL_SECONDS)


# --- FUNCTIONS REQUIRED BY main.py (Updated) ---

def run_strategy(symbol, timeframe, log_callback):
    """Called by main.py to start the strategy."""
    global bot_running, strategy_thread, last_signal
    global SYMBOL, TIMEFRAME, LOG_CALLBACK
    
    if bot_running:
        log("[INFO] Strategy is already running.")
        return False
        
    log("="*60)
    log("[START] Starting Fibonacci Retracement Strategy")
    log("="*60)
    
    bot_running = True
    last_signal = None
    SYMBOL = symbol
    TIMEFRAME = timeframe
    LOG_CALLBACK = log_callback
    
    log("[RULES] Strategy Rules:")
    log("  1. Find 2-day swing high/low (must not be in last 15 candles)")
    log("  2. Calculate Fibonacci retracement levels")
    log("  3. [MANDATORY] Wait for Fib 61.8% (uptrend) or 38.2% (downtrend)")
    log("  4. Confirm with bullish candle")
    log("  5. [FILTER] RSI < 50")
    log("  6. [FILTER] SMA20 > SMA50")
    log(f"  7. FIXED SL: ${FIXED_SL_DOLLARS} | FIXED TP: ${FIXED_TP_DOLLARS}")
    log(f"  8. Trailing SL: {TRAILING_PERCENT*100:.0f}% activates at {TRAILING_ACTIVATION*100:.0f}% profit (${FIXED_TP_DOLLARS*TRAILING_ACTIVATION:.0f})")
    log(f"  9. TP Extension: At {TP_EXTENSION_THRESHOLD*100:.0f}% profit (${FIXED_TP_DOLLARS*TP_EXTENSION_THRESHOLD:.0f}), extend TP by ${TP_EXTENSION_AMOUNT}")
    log(f" 10. Symbol: {SYMBOL} | Timeframe: {TIMEFRAME} | Volume: {VOLUME}")
    log("="*60)
    
    strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
    strategy_thread.start()
    
    log("[SUCCESS] Strategy started successfully!")
    return True


def stop_strategy(log_callback):
    """Called by main.py to stop the strategy."""
    global bot_running, strategy_thread
    
    if not bot_running:
        log_callback("[INFO] Strategy is not running.")
        return

    log_callback("[STOP] Stopping strategy...")
    bot_running = False
    
    if strategy_thread and strategy_thread.is_alive():
        log_callback("[STOP] Waiting for strategy thread to terminate...")
        strategy_thread.join(timeout=10)
        if strategy_thread.is_alive():
            log_callback("[WARN] Thread did not terminate gracefully.")
        else:
            log_callback("[SUCCESS] Strategy thread terminated.")
    
    log_callback("="*60)
    log_callback("[STOPPED] Strategy Stopped")
    log_callback("="*60)


# --- Standalone Testing Block ---
if __name__ == "__main__":
    def standalone_log(message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"[{timestamp}] {message}")

    try:
        try:
            from mt5_config import LOGIN, PASSWORD, SERVER, TERMINAL_PATH
        except ImportError:
            standalone_log("[WARN] mt5_config.py not found.")
            LOGIN, PASSWORD, SERVER, TERMINAL_PATH = 0, "", "", ""
            
        if not LOGIN:
            standalone_log("[ERROR] Please fill in mt5_config.py with your credentials.")
        else:
            if not mt5.initialize(path=TERMINAL_PATH, login=LOGIN, password=PASSWORD, server=SERVER):
                standalone_log(f"[ERROR] MT5 init failed: {mt5.last_error()}")
                mt5.shutdown()
            else:
                standalone_log("[SUCCESS] MT5 Connection Initialized")
                standalone_log("[INFO] Press Ctrl+C to stop")
                
                TEST_SYMBOL = "EURUSD"
                TEST_TIMEFRAME = mt5.TIMEFRAME_M15
                
                if run_strategy(TEST_SYMBOL, TEST_TIMEFRAME, standalone_log):
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        standalone_log("\n[INTERRUPT] Ctrl+C detected. Stopping...")
                        stop_strategy(standalone_log)
                        mt5.shutdown()
                        standalone_log("[SUCCESS] MT5 Shutdown Complete")
                else:
                    standalone_log("[ERROR] Failed to start strategy")
                    mt5.shutdown()

    except Exception as e:
        standalone_log(f"[ERROR] Error: {e}")
        standalone_log(f"[TRACE] {traceback.format_exc()}")
        mt5.shutdown()
