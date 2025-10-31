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


# --- Order Management ---

def place_buy_order(stop_loss_price):
    global SYMBOL
    tick = get_tick_info(SYMBOL)
    info = get_symbol_info(SYMBOL)
    
    if not tick or not info:
        log("Market or symbol data missing for placing order.")
        return None
    
    price = tick.ask
    digits = info.digits
    price = round(price, digits)
    sl = round(stop_loss_price, digits)

    if sl >= price:
        log(f"Invalid SL: SL {sl} >= Price {price}. Aborting order.")
        return None

    # Calculate Volume based on 2% risk of a $5000 capital (example)
    # This should ideally get capital from account_info
    account = get_account_info()
    capital = account.balance if account else 5000.0
    
    risk_amount = capital * 0.02
    price_diff = abs(price - sl)
    
    if price_diff > 0:
        calculated_volume = risk_amount / (price_diff * info.trade_contract_size)
        calculated_volume = min(calculated_volume, VOLUME) # Cap at max volume
        calculated_volume = max(calculated_volume, info.volume_min)
        # Round to volume step
        calculated_volume = round(calculated_volume / info.volume_step) * info.volume_step
        volume = calculated_volume
    else:
        volume = info.volume_min
    
    if volume <= 0:
        log(f"Calculated volume is {volume}. Using min volume {info.volume_min}")
        volume = info.volume_min

    tp_distance = price_diff * 2 # 2:1 Risk/Reward
    tp = price + tp_distance
    tp = round(tp, digits)
    
    fill_mode = info.filling_mode
    if fill_mode not in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK]:
        # Default to a common filling mode if not supported
        fill_mode = mt5.ORDER_FILLING_IOC 
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20, # 20 points deviation
        "magic": MAGIC,
        "comment": "BUY-Fibonacci",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": fill_mode,
    }
    
    log(f"Sending BUY order: {volume} lots @ {price} | SL: {sl}, TP: {tp}")
    
    result = send_order(request)
    if result is None:
        log(f"order_send() returned None. Error: {get_last_error()}")
        return None
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"BUY order failed. RetCode: {result.retcode} | Comment: {result.comment} ({get_last_error()})")
        return None
    
    log(f"BUY order placed successfully! Ticket: {result.order}")
    return result.order


def trail_buy_stop_logic(ticket):
    global bot_running, SYMBOL
    
    info = get_symbol_info(SYMBOL)
    if not info:
        log(f"Could not get symbol info for trailing SL on ticket #{ticket}")
        return
        
    digits = info.digits
    
    log(f"Started dynamic trailing stop for BUY position #{ticket}")
    
    initial_entry_price = None
    initial_tp = None
    trailing_active = False
    
    while bot_running:
        positions = get_positions(SYMBOL)
        pos = next((p for p in positions if p.ticket == ticket and p.magic == MAGIC), None)
        if not pos:
            log(f"Position #{ticket} closed or not found. Stopping trailer.")
            break
        
        current_price_tick = get_tick_info(SYMBOL)
        if not current_price_tick:
            time.sleep(5)
            continue
            
        current_price = current_price_tick.bid # Use BID price to check against SL
        
        if initial_entry_price is None:
            initial_entry_price = pos.price_open
            initial_tp = pos.tp
            log(f"Trailer #{ticket} Initialized: Entry={initial_entry_price:.5f}, TP={initial_tp:.5f}, SL={pos.sl:.5f}")
        
        if initial_tp > 0 and initial_entry_price > 0:
            distance_to_tp = initial_tp - initial_entry_price
            current_profit_distance = current_price - initial_entry_price
            profit_percentage = current_profit_distance / distance_to_tp if distance_to_tp > 0 else 0
            
            if profit_percentage >= TRAILING_ACTIVATION and not trailing_active:
                trailing_active = True
                log(f"Trailing #{ticket} ACTIVATED! Price reached {profit_percentage*100:.1f}% of target.")
            
            if trailing_active:
                # Calculate new SL based on 7% trail from current price
                trailing_sl = current_price * (1 - TRAILING_PERCENT)
                trailing_sl = round(trailing_sl, digits)
                
                # Only move SL up, never down
                if trailing_sl > pos.sl:
                    sltp_request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": SYMBOL,
                        "position": ticket,
                        "sl": trailing_sl,
                        "tp": pos.tp, # Keep original TP
                        "magic": MAGIC
                    }
                    
                    result = send_order(sltp_request)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        log(f"SL #{ticket} TRAILED: {pos.sl:.5f} -> {trailing_sl:.5f} | Price: {current_price:.5f} | P/L: ${pos.profit:.2f}")
                    elif result:
                        log(f"Failed to trail SL #{ticket}. RetCode: {result.retcode} | Comment: {result.comment}")
                    else:
                        log(f"Failed to trail SL #{ticket}. send_order returned None.")
            
            # Log waiting message only if not active
            elif not trailing_active and profit_percentage >= 0:
                 log(f"Trailer #{ticket} waiting for {TRAILING_ACTIVATION*100}% target. Current: {profit_percentage*100:.1f}% | P/L: ${pos.profit:.2f}")

        # Check every 10 seconds
        time.sleep(10)


# --- Main Strategy Loop ---

def strategy_loop():
    """The main trading loop, runs in a separate thread."""
    global bot_running, last_signal, SYMBOL, TIMEFRAME
    
    active_trails = {} # To keep track of trailing threads
    
    log("Strategy loop started...")
    
    # Calculate sleep time to align with candle close
    # e.g., for M15, run shortly after 00, 15, 30, 45
    # This is a simple version: just check every minute
    LOOP_INTERVAL_SECONDS = 60 

    while bot_running:
        try:
            # Check for new candle
            # This is a simple time-based check.
            # A more robust method would be to check df.index[-1]
            
            df = fetch_data(SYMBOL, TIMEFRAME, 1000)
            if df.empty:
                log("No data received, sleeping.")
                time.sleep(LOOP_INTERVAL_SECONDS)
                continue
            
            signal, stop_loss = get_signal(df)
            
            # Check for existing positions to avoid opening multiple
            positions = get_positions(SYMBOL)
            my_positions = [p for p in positions if p.magic == MAGIC]

            if signal == "BUY" and signal != last_signal and not my_positions:
                log("--- BUY SIGNAL RECEIVED ---")
                ticket = place_buy_order(stop_loss)
                if ticket:
                    log(f"--- NEW POSITION OPENED: #{ticket} ---")
                    # Start a new trailing SL thread for this ticket
                    trail_thread = threading.Thread(
                        target=trail_buy_stop_logic,
                        args=(ticket,),
                        daemon=True
                    )
                    trail_thread.start()
                    active_trails[ticket] = trail_thread
                    log(f"Started trailing thread for position #{ticket}")
                
                last_signal = signal # Set last_signal only after successful order
            
            elif signal == "WAIT":
                if last_signal == "BUY":
                    log("Signal reset to WAIT. Ready for next BUY.")
                    last_signal = None
            
            # Clean up finished trailing threads
            for ticket in list(active_trails.keys()):
                pos_exists = any(p.ticket == ticket for p in my_positions)
                if not pos_exists:
                    log(f"Position #{ticket} no longer open. Removing from trailer list.")
                    active_trails.pop(ticket, None)
            
            time.sleep(LOOP_INTERVAL_SECONDS)
            
        except Exception as e:
            log(f"FATAL ERROR in strategy loop: {e}\n{traceback.format_exc()}")
            time.sleep(LOOP_INTERVAL_SECONDS)


# --- FUNCTIONS REQUIRED BY main.py ---

def run_strategy(symbol, timeframe, log_callback):
    """
    Called by main.py to start the strategy.
    
    :param symbol: str (e.g., "EURUSD")
    :param timeframe: int (MT5 TIMEFRAME constant)
    :param log_callback: function (to send logs to GUI)
    :return: bool (True if started, False if failed)
    """
    global bot_running, strategy_thread, last_signal
    global SYMBOL, TIMEFRAME, LOG_CALLBACK
    
    if bot_running:
        log("Strategy is already running.")
        return False
        
    log("--- Starting Fibonacci Retracement Strategy ---")
    
    # Set global variables
    bot_running = True
    last_signal = None
    SYMBOL = symbol
    TIMEFRAME = timeframe
    LOG_CALLBACK = log_callback
    
    # Log strategy parameters
    log("Strategy Rules:")
    log(" 1. Find 5-day swing high/low (must not be in last 15 candles)")
    log(" 2. Calculate Fibonacci retracement levels")
    log(" 3. Wait for price to hit 61.8% (uptrend) or 38.2% (downtrend reversal) [MANDATORY]")
    log(" 4. Confirm with bullish candle moving upward")
    log(" 5. RSI < 50")
    log(" 6. SMA20 > SMA50 (high volatility)")
    # Cosmetic log fix for floating point
    log(f" 7. Trailing stop: {TRAILING_PERCENT*100:.1f}% when {TRAILING_ACTIVATION*100:.1f}% of target reached")
    log(f" 8. Symbol: {SYMBOL}, Timeframe: {TIMEFRAME}, Max Volume: {VOLUME}")
    
    # Start the main strategy loop in a new thread
    strategy_thread = threading.Thread(target=strategy_loop, daemon=True)
    strategy_thread.start()
    
    log("--- Strategy Started Successfully ---")
    return True


def stop_strategy(log_callback):
    """
    Called by main.py to stop the strategy.
    
    :param log_callback: function (to send logs to GUI)
    """
    global bot_running, strategy_thread
    
    if not bot_running:
        log_callback("Strategy is not running.")
        return

    log_callback("--- Stopping Strategy ---")
    bot_running = False # Signal the loop to stop
    
    if strategy_thread and strategy_thread.is_alive():
        log_callback("Waiting for strategy thread to terminate...")
        strategy_thread.join(timeout=10) # Wait up to 10s
        if strategy_thread.is_alive():
            log_callback("Warning: Strategy thread did not terminate gracefully.")
        else:
            log_callback("Strategy thread terminated.") # Added for clarity
    
    log_callback("--- Strategy Stopped ---")


# --- Main block for standalone testing ---
if __name__ == "__main__":
    """
    This block allows you to run the strategy script directly
    for testing purposes, without the main.py GUI.
    It will handle its own MT5 connection.
    """
    
    # --- Dummy log_callback for testing ---
    def standalone_log(message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    # --- Standalone Connection ---
    try:
        # Try to import credentials from mt5_config.py if it exists
        # This is ONLY for standalone testing
        try:
            from mt5_config import LOGIN, PASSWORD, SERVER, TERMINAL_PATH
        except ImportError:
            standalone_log("mt5_config.py not found. Please create it for standalone testing.")
            LOGIN, PASSWORD, SERVER, TERMINAL_PATH = 0, "", "", "" # Set dummy values
            
        if not LOGIN:
            standalone_log("Please fill in mt5_config.py with your credentials.")
        else:
            if not mt5.initialize(path=TERMINAL_PATH, login=LOGIN, password=PASSWORD, server=SERVER):
                standalone_log(f"Standalone initialize() failed, error code = {mt5.last_error()}")
                mt5.shutdown()
            else:
                standalone_log("Standalone MT5 Connection Initialized.")
                standalone_log("Press Ctrl+C to stop the strategy.")
                
                # --- Start the strategy ---
                # Use test parameters
                TEST_SYMBOL = "EURUSD"
                TEST_TIMEFRAME = mt5.TIMEFRAME_M15
                
                if run_strategy(TEST_SYMBOL, TEST_TIMEFRAME, standalone_log):
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        standalone_log("\nCtrl+C detected. Stopping strategy...")
                        stop_strategy(standalone_log)
                        mt5.shutdown()
                        standalone_log("Standalone MT5 Connection Shutdown.")
                else:
                    standalone_log("Failed to start strategy.")
                    mt5.shutdown()

    except Exception as e:
        standalone_log(f"An error occurred: {e}")
        mt5.shutdown()




