"""
High_Win_Rate_Scalper.py — Mean-reversion scalping strategy.

Designed to integrate with the trading dashboard GUI and backtester.
- Trades on RSI overbought/oversold conditions when price hits Bollinger Bands.
- Aims for frequent, small wins.
"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.10          # Lot size
MAGIC = 13579
RR_RATIO = 1.2         # Reward:Risk ratio for the backtester (e.g., 1.2 means TP is 1.2x SL)
MAX_CONCURRENT_TRADES = 3 # The maximum number of trades to open for a single symbol at a time

# --- Indicator Settings ---
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
BB_PERIOD = 20
BB_STD_DEV = 2.0

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """Calculates all necessary indicators for the backtester."""
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'}, inplace=True, errors='ignore')
    
    # Bollinger Bands
    df['BB_SMA'] = df['Close'].rolling(window=BB_PERIOD).mean()
    df['BB_STD'] = df['Close'].rolling(window=BB_PERIOD).std()
    df['BB_Upper'] = df['BB_SMA'] + (df['BB_STD'] * BB_STD_DEV)
    df['BB_Lower'] = df['BB_SMA'] - (df['BB_STD'] * BB_STD_DEV)
    
    # RSI
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/RSI_PERIOD, adjust=False).mean()
    loss = -delta.where(delta < 0, 0).ewm(alpha=1/RSI_PERIOD, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """Determines the signal and stop loss price for the backtester."""
    if len(df) < 2: return "WAIT", None

    last = df.iloc[-1]
    
    # BUY Signal: Price hits lower BB and RSI is oversold
    if last.get('Low', 0) <= last.get('BB_Lower', float('inf')) and last.get('RSI', 50) < RSI_OVERSOLD:
        return "BUY", last['Low']

    # SELL Signal: Price hits upper BB and RSI is overbought
    if last.get('High', 0) >= last.get('BB_Upper', 0) and last.get('RSI', 50) > RSI_OVERBOUGHT:
        return "SELL", last['High']
    
    return "WAIT", None

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR LIVE TRADING
# ==========================================================================================
bot_running = False
_stop_event = threading.Event()

def log(message, log_callback=None):
    if log_callback: log_callback(message)
    else: print(message)

def fetch_data(symbol, timeframe, bars=100):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None: return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def place_order(symbol, signal_type, stop_loss_price, log_callback=None):
    try:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if not tick or not info:
            log("❌ Symbol/tick info unavailable.", log_callback)
            return False

        order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if signal_type == "BUY" else tick.bid
        
        # Calculate SL and TP
        if signal_type == "BUY":
            sl = stop_loss_price - (info.point * 5) # Place SL slightly below the low
            dist = price - sl
            tp = price + (dist * RR_RATIO)
        else: # SELL
            sl = stop_loss_price + (info.point * 5) # Place SL slightly above the high
            dist = sl - price
            tp = price - (dist * RR_RATIO)

        request = { "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
                    "type": order_type, "price": price, "sl": round(sl, info.digits), "tp": round(tp, info.digits),
                    "deviation": 20, "magic": MAGIC, "comment": "HighWinRateScalp",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC }

        log(f"📤 {signal_type} {symbol} {VOLUME} @ {price} | SL:{sl:.5f} TP:{tp:.5f}", log_callback)
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ {signal_type} placed! Ticket: {result.order}", log_callback)
            return True
        log(f"❌ {signal_type} failed RetCode {result.retcode if result else 'None'}", log_callback)
        return False
    except Exception as e:
        log(f"❌ Error placing order: {e}", log_callback)
        return False

def _strategy_loop(symbol, timeframe, log_callback=None):
    """The main trading loop that runs in a separate thread."""
    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, timeframe)
            if df.empty:
                log("⚠️ No price data, waiting...", log_callback)
                time.sleep(5)
                continue
            
            df_indicators = calculate_indicators(df.copy())
            signal, stop_price = get_signal(df_indicators)

            if signal != "WAIT":
                # Check how many positions are already open for this symbol
                positions = mt5.positions_get(symbol=symbol) or []
                
                # --- MODIFIED: Check if the number of open positions is less than the max allowed ---
                if len(positions) < MAX_CONCURRENT_TRADES:
                    place_order(symbol, signal, stop_price, log_callback)
                    # Wait for a while after placing a trade to avoid rapid re-entry
                    time.sleep(60) 
                else:
                    log(f"⚠️ Max trades ({MAX_CONCURRENT_TRADES}) reached for {symbol}. Skipping new signal.", log_callback)
            
            # Check every 5 seconds for a signal
            time.sleep(5)
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}", log_callback)
            time.sleep(10)

def run_strategy(symbol, timeframe, log_callback=None, profit_callback=None):
    """Starts the trading bot. Required by the GUI."""
    global bot_running
    if bot_running:
        log("⚠️ Strategy is already running.", log_callback)
        return True
    
    bot_running = True
    _stop_event.clear()
    log(f"🚀 High Win Rate Scalper Started on {symbol} | Timeframe: {timeframe}", log_callback)
    
    # The main GUI now handles profit updates, so the profit_callback is not needed here.
    
    # Start the main strategy loop in a separate thread
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    """Stops the trading bot. Required by the GUI."""
    global bot_running
    if not bot_running: return
    
    bot_running = False
    _stop_event.set()
    log("🛑 Strategy stopping...", log_callback)
    time.sleep(1) # Give the loop a moment to exit
    # The GUI's disconnect function handles mt5.shutdown()
    log("✅ Strategy stopped.", log_callback)

