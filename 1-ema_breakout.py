"""
EMA_Breakout_v1.py — A breakout strategy based on the 200 EMA with an ATR volatility filter.

Designed to integrate with the trading dashboard GUI and backtester.
- Waits for a candle to touch the 200 EMA.
- Enters a trade when the price breaks the high/low of that "setup candle".
- Only trades if ATR is greater than 0.5.
- Uses a strict 1:3 risk-to-reward ratio.
"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.10          # Lot size for trades
MAGIC = 445566
RR_RATIO = 3.0         # Reward:Risk ratio (Take Profit is 2x Stop Loss)
ATR_THRESHOLD = 50     # The minimum ATR value required to consider a trade

# --- Indicator Settings ---
EMA_PERIOD = 200
ATR_PERIOD = 14        # Standard period for Average True Range

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """Calculates all necessary indicators for the backtester."""
    
    # FIX: REMOVED the df.rename() line that was causing the case mismatch.
    
    # EMA for the reversal zone
    # FIX: 'Close' -> 'close'
    df['EMA_200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    
    # --- ADDED: ATR Calculation for Volatility Filter ---
    # FIX: 'High', 'Low', 'Close' -> 'high', 'low', 'close'
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = high_low.combine(high_close, max).combine(low_close, max)
    df['ATR'] = tr.rolling(window=ATR_PERIOD).mean()
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """
    Determines the signal for the backtester, including the new ATR check.
    """
    if len(df) < 2: return "WAIT", None

    setup_candle = df.iloc[-2]
    action_candle = df.iloc[-1]

    # --- ADDED: ATR Volatility Filter for Backtester ---
    if setup_candle.get('ATR', 0) <= ATR_THRESHOLD:
        return "WAIT", None # Market volatility is too low, ignore signals

    # --- BUY SIGNAL CHECK ---
    # FIX: 'Low', 'High' -> 'low', 'high'
    if setup_candle.get('low', 0) <= setup_candle.get('EMA_200', float('inf')):
        if action_candle.get('high', 0) > setup_candle.get('high', 0):
            stop_loss_price = setup_candle['low']
            return "BUY", stop_loss_price

    # --- SELL SIGNAL CHECK ---
    # FIX: 'High', 'Low' -> 'high', 'low'
    if setup_candle.get('high', 0) >= setup_candle.get('EMA_200', 0):
        if action_candle.get('low', 0) < setup_candle.get('low', 0):
            stop_loss_price = setup_candle['high']
            return "SELL", stop_loss_price
    
    return "WAIT", None

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR LIVE TRADING
# ==========================================================================================
bot_running = False
_stop_event = threading.Event()

def log(message, log_callback=None):
    if log_callback: log_callback(message)
    else: print(message)

def fetch_data(symbol, timeframe, bars=250):
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
            log("❌ Tick/info unavailable.", log_callback)
            return False

        order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if signal_type == "BUY" else tick.bid
        
        risk_dist = abs(price - stop_loss_price)
        if risk_dist == 0:
             log("❌ Risk distance is zero. Cannot place trade.", log_callback)
             return False
        
        tp_price = price + (risk_dist * RR_RATIO) if signal_type == "BUY" else price - (risk_dist * RR_RATIO)

        request = { "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
                    "type": order_type, "price": price, "sl": round(stop_loss_price, info.digits), "tp": round(tp_price, info.digits),
                    "deviation": 20, "magic": MAGIC, "comment": "EMABreakoutV1_ATR",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC }

        log(f"🔥 {signal_type} {symbol} {VOLUME} @ {price:.5f} | SL:{stop_loss_price:.5f} TP:{tp_price:.5f}", log_callback)
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ {signal_type} filled! Ticket: {result.order}", log_callback)
            return True
        log(f"❌ {signal_type} failed: {result.comment if result else 'No result'}", log_callback)
        return False
    except Exception as e:
        log(f"❌ Order placement error: {e}", log_callback)
        return False

def _strategy_loop(symbol, timeframe, log_callback=None):
    """The main trading loop that checks for setups and triggers."""
    setup_candle = None
    pending_signal = None
    
    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, timeframe, 250)
            if df.empty:
                time.sleep(1)
                continue

            df_indicators = calculate_indicators(df.copy())
            last_candle = df_indicators.iloc[-1]
            
            if setup_candle is None or last_candle['time'] != setup_candle['time']:
                pending_signal = None
                
                # --- ADDED: ATR Volatility Filter for Live Trading ---
                if last_candle.get('ATR', 0) <= ATR_THRESHOLD:
                    # This log message can be noisy, so it's commented out. 
                    # You can uncomment it for debugging if you want to see when trades are being filtered.
                    # log(f"⏳ Waiting... ATR ({last_candle['ATR']:.4f}) is below {ATR_THRESHOLD} threshold.", log_callback)
                    pass # Don't look for a setup if volatility is too low
                else:
                    # If ATR is high enough, check for a new setup
                    # FIX: 'Low', 'High' -> 'low', 'high'
                    if last_candle['low'] <= last_candle['EMA_200']:
                        setup_candle = last_candle.copy()
                        pending_signal = "BUY"
                        log(f"📈 BUY Setup on {symbol}: Candle at {setup_candle['time']} touched EMA (ATR: {setup_candle['ATR']:.4f}). Waiting for breakout above {setup_candle['high']:.5f}", log_callback)
                    
                    elif last_candle['high'] >= last_candle['EMA_200']:
                        setup_candle = last_candle.copy()
                        pending_signal = "SELL"
                        log(f"📉 SELL Setup on {symbol}: Candle at {setup_candle['time']} touched EMA (ATR: {setup_candle['ATR']:.4f}). Waiting for breakdown below {setup_candle['low']:.5f}", log_callback)

            if pending_signal:
                tick = mt5.symbol_info_tick(symbol)
                if not tick:
                    time.sleep(1)
                    continue

                if mt5.positions_get(symbol=symbol):
                    pending_signal = None; setup_candle = None
                    continue

                # FIX: 'High', 'Low' -> 'high', 'low'
                if pending_signal == "BUY" and tick.ask > setup_candle['high']:
                    log(f"🔥 BUY TRIGGER on {symbol}! Price {tick.ask:.5f} broke above {setup_candle['high']:.5f}", log_callback)
                    stop_loss = setup_candle['low'] - ((setup_candle['high'] - setup_candle['low']) * 0.1)
                    place_order(symbol, "BUY", stop_loss, log_callback)
                    pending_signal = None; setup_candle = None
                    time.sleep(60)
                
                elif pending_signal == "SELL" and tick.bid < setup_candle['low']:
                    log(f"🔥 SELL TRIGGER on {symbol}! Price {tick.bid:.5f} broke below {setup_candle['low']:.5f}", log_callback)
                    stop_loss = setup_candle['high'] + ((setup_candle['high'] - setup_candle['low']) * 0.1)
                    place_order(symbol, "SELL", stop_loss, log_callback)
                    pending_signal = None; setup_candle = None
                    time.sleep(60)

            time.sleep(1)
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
    log(f"🚀 EMA Breakout Strategy Started on {symbol} | Timeframe: {timeframe}", log_callback)
    
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    """Stops the trading bot. Required by the GUI."""
    global bot_running
    if not bot_running: return
    
    bot_running = False
    _stop_event.set()
    log("🛑 Strategy stopping...", log_callback)
    time.sleep(1.5)
    log("✅ Strategy stopped.", log_callback)
    #sujits code
    #with the help of arnav
