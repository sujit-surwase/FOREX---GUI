"""
Momentum_Scalper_v3.py — A high-profitability, fast-execution strategy.

Designed to integrate with the trading dashboard GUI and backtester.
- Captures strong momentum bursts in a confirmed trend.
- Uses a strict 1:2 risk-to-reward ratio.
- Optimized for fast execution with IOC orders.+

"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.10          # Lot size for trades
MAGIC = 24680
RR_RATIO = 2.0         # Reward:Risk ratio (Take Profit is 2x Stop Loss)

# --- Indicator Settings ---
EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 50
RSI_PERIOD = 14
RSI_MOMENTUM_LEVEL_BUY = 55  # RSI must cross above this for a buy
RSI_MOMENTUM_LEVEL_SELL = 45 # RSI must cross below this for a sell
ATR_PERIOD = 14

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """Calculates all necessary indicators for the backtester."""
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'}, inplace=True, errors='ignore')
    
    # EMAs
    df['EMA_Fast'] = df['Close'].ewm(span=EMA_FAST_PERIOD, adjust=False).mean()
    df['EMA_Slow'] = df['Close'].ewm(span=EMA_SLOW_PERIOD, adjust=False).mean()

    # ATR for volatility filter
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = high_low.combine(high_close, max).combine(low_close, max)
    df['ATR'] = tr.rolling(window=ATR_PERIOD).mean()
    
    # RSI for momentum trigger
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
    prev = df.iloc[-2]

    # --- SHARED CONDITIONS ---
    # Volatility Check: ATR must be at least 0.05% of the price
    min_atr = last.get('Close', 0) * 0.0005 
    is_volatile_enough = last.get('ATR', 0) > min_atr
    
    # Momentum Candle Check: Body must be at least 40% of the total candle range (Relaxed from 60%)
    candle_range = last.get('High', 0) - last.get('Low', 0)
    body_size = abs(last.get('Close', 0) - last.get('Open', 0))
    is_momentum_candle = body_size >= (candle_range * 0.4) if candle_range > 0 else False

    # --- BUY SIGNAL ---
    is_uptrend = last.get('Close', 0) > last.get('EMA_Fast', 0) > last.get('EMA_Slow', 0)
    rsi_buy_trigger = prev.get('RSI', 50) < RSI_MOMENTUM_LEVEL_BUY and last.get('RSI', 50) >= RSI_MOMENTUM_LEVEL_BUY
    
    if is_uptrend and is_volatile_enough and rsi_buy_trigger and is_momentum_candle:
        return "BUY", last['Low']

    # --- SELL SIGNAL ---
    is_downtrend = last.get('Close', 0) < last.get('EMA_Fast', 0) < last.get('EMA_Slow', 0)
    rsi_sell_trigger = prev.get('RSI', 50) > RSI_MOMENTUM_LEVEL_SELL and last.get('RSI', 50) <= RSI_MOMENTUM_LEVEL_SELL

    if is_downtrend and is_volatile_enough and rsi_sell_trigger and is_momentum_candle:
        return "SELL", last['High']
    
    return "WAIT", None

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR LIVE TRADING (Fast Execution)
# ==========================================================================================
bot_running = False
_stop_event = threading.Event()

def log(message, log_callback=None):
    if log_callback: log_callback(message)
    else: print(message)

def fetch_data(symbol, timeframe, bars=200):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None: return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def place_order_fast(symbol, signal_type, stop_loss_price, log_callback=None):
    """Optimized for fast order placement with IOC."""
    try:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if not tick or not info:
            log("❌ Tick/info unavailable.", log_callback)
            return False

        order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if signal_type == "BUY" else tick.bid
        
        # Calculate SL and TP with strict 1:2 ratio
        if signal_type == "BUY":
            sl = stop_loss_price - (info.point * 5)
            risk_dist = price - sl
            tp = price + (risk_dist * RR_RATIO)
        else: # SELL
            sl = stop_loss_price + (info.point * 5)
            risk_dist = sl - price
            tp = price - (risk_dist * RR_RATIO)

        request = { "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
                    "type": order_type, "price": price, "sl": round(sl, info.digits), "tp": round(tp, info.digits),
                    "deviation": 20, "magic": MAGIC, "comment": "MomentumIgnition",
                    "type_time": mt5.ORDER_TIME_GTC, 
                    "type_filling": mt5.ORDER_FILLING_IOC, # Immediate Or Cancel for fast execution
                  }

        log(f"🔥 FAST EXECUTION: {signal_type} {symbol} @ {price}", log_callback)
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
    """The main trading loop, checks every 1 second."""
    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, timeframe)
            if df.empty:
                time.sleep(1)
                continue
            
            df_indicators = calculate_indicators(df.copy())
            signal, stop_price = get_signal(df_indicators)

            if signal != "WAIT":
                # Only trade if no other positions are open for this symbol
                positions = mt5.positions_get(symbol=symbol)
                if not positions:
                    place_order_fast(symbol, signal, stop_price, log_callback)
                    # --- MODIFIED: Reduced pause for faster re-entry ---
                    time.sleep(10) 
                else:
                    # Optional: Could add logic to add to a winning position here
                    pass
            
            # Fast check interval
            time.sleep(1)
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}", log_callback)
            time.sleep(5)

def run_strategy(symbol, timeframe, log_callback=None, profit_callback=None):
    """Starts the trading bot. Required by the GUI."""
    global bot_running
    if bot_running:
        log("⚠️ Strategy is already running.", log_callback)
        return True
    
    bot_running = True
    _stop_event.clear()
    log(f"🚀 Momentum Ignition Scalper Started on {symbol}", log_callback)
    
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


