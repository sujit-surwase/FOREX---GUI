"""
Bear_Final.py — SELL-only candlestick + indicator strategy

Designed to integrate with the trading dashboard GUI and backtester.
- Live Trading: run_strategy(), stop_strategy(), close_all_positions()
- Backtesting: calculate_indicators(), get_signal()
"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.50
MAGIC = 99999
SL_USD = 35     # Risk per trade (approx) in USD
TP_USD = 70   # Reward per trade in USD
RR_RATIO = 2.0    # Reward:Risk ratio for the backtester
LOOKBACK_BARS = 200
INTERVAL = 2    # seconds between trading checks
MAX_POSITIONS = 3 # NEW: Set the maximum number of concurrent trades
# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER (Unchanged)
# ==========================================================================================

def calculate_indicators(df):
    """
    This function is required by the backtester.
    It calculates all necessary indicators.
    """
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'}, inplace=True, errors='ignore')
    
    df['SMA_9'] = df['Close'].rolling(window=9).mean()
    df['Donchian_HH_9'] = df['High'].rolling(window=9).max()
    
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
    loss = -delta.where(delta < 0, 0).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """
    This function is required by the backtester.
    It determines the signal and the price for the stop loss.
    """
    if len(df) < 2: return "WAIT", None

    last_candle = df.iloc[-1]
    sell_signals = 0

    if last_candle.get('High', 0) >= last_candle.get('Donchian_HH_9', float('inf')): sell_signals += 1
    if last_candle.get('Volume', 0) <= df['Volume'].tail(15).mean() * 1.5: sell_signals += 1
    if last_candle.get('Close', 0) < last_candle.get('Open', 0): sell_signals += 1
    if last_candle.get('RSI', 0) >= 35: sell_signals += 1
    if last_candle.get('Close', 0) < last_candle.get('SMA_9', float('inf')): sell_signals += 1

    if sell_signals >= 3:
        return "SELL", last_candle['High']
    
    return "WAIT", None

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR LIVE TRADING
# ==========================================================================================
bot_running = False
last_signal = None
_stop_event = threading.Event()

# --- Helper functions (log, connect, shutdown, etc.) are unchanged ---
def log(message, log_callback=None):
    if log_callback: log_callback(message)
    else: print(message)

def connect(log_callback=None):
    if mt5.initialize(): log("✅ Connected to MT5 terminal.", log_callback); return True
    log(f"❌ MT5 initialize failed: {mt5.last_error()}", log_callback); return False

def shutdown(): mt5.shutdown()
def get_symbol_info(symbol): return mt5.symbol_info(symbol)
def get_tick_info(symbol): return mt5.symbol_info_tick(symbol)
def get_positions(symbol): return mt5.positions_get(symbol=symbol) or []

def fetch_data(symbol, timeframe, bars=LOOKBACK_BARS):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None: return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def get_sell_signal_live(df, log_callback=None):
    signal, _ = get_signal(calculate_indicators(df.copy()))
    if signal == "SELL": log("🎯 SELL SIGNAL CONFIRMED (Live)", log_callback)
    return signal

def usd_to_price_move(usd, volume, info):
    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size
    if not (tick_value > 0 and tick_size > 0): return usd * info.point
    usd_per_tick = tick_value * volume
    if usd_per_tick == 0: return usd * info.point
    return (usd / usd_per_tick) * tick_size

def place_sell_order(symbol, log_callback=None):
    # This function is unchanged
    try:
        info = get_symbol_info(symbol)
        tick = get_tick_info(symbol)
        if not tick or not info:
            log("❌ Symbol/tick info unavailable.", log_callback); return False

        price = tick.bid
        sl_dist = usd_to_price_move(SL_USD, VOLUME, info)
        tp_dist = usd_to_price_move(TP_USD, VOLUME, info)
        sl_price = round(price + sl_dist, info.digits)
        tp_price = round(price - tp_dist, info.digits)
        
        request = { "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
                    "type": mt5.ORDER_TYPE_SELL, "price": price, "sl": sl_price, "tp": tp_price,
                    "deviation": 50, "magic": MAGIC, "comment": "BearLiveSELL",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC, }

        log(f"📤 SELL {symbol} {VOLUME} @ {price} | SL:{sl_price} TP:{tp_price}", log_callback)
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"✅ SELL placed! Ticket: {result.order}", log_callback); return True
        log(f"❌ SELL failed RetCode {result.retcode if result else 'None'}", log_callback); return False
    except Exception as e:
        log(f"❌ Error placing SELL: {e}", log_callback); return False

def close_all_positions(symbol, log_callback=None):
    # This function is unchanged
    try:
        positions = get_positions(symbol)
        if not positions: log("📊 No open positions to close.", log_callback); return
        tick = get_tick_info(symbol)
        for pos in positions:
            price = tick.ask if pos.type == mt5.ORDER_TYPE_SELL else tick.bid
            req = { "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
                    "type": mt5.ORDER_TYPE_BUY if pos.type == mt5.ORDER_TYPE_SELL else mt5.ORDER_TYPE_SELL,
                    "position": pos.ticket, "price": price, "deviation": 50, "magic": MAGIC,
                    "comment": "BearCloseAll", "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC, }
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"✅ Closed position {pos.ticket}", log_callback)
            else:
                log(f"❌ Failed to close {pos.ticket}", log_callback)
    except Exception as e:
        log(f"❌ Error closing positions: {e}", log_callback)

def _strategy_loop(symbol, timeframe, log_callback=None):
    global last_signal
    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, bars=LOOKBACK_BARS, timeframe=timeframe)
            if df.empty:
                log("⚠️ No price data.", log_callback)
                time.sleep(5)
                continue
            
            signal = get_sell_signal_live(df, log_callback)

            # --- MODIFIED LOGIC ---
            if signal == "SELL" and signal != last_signal:
                positions = get_positions(symbol)
                position_count = len(positions)
                
                # Check if the number of open positions is less than the max limit
                if position_count < MAX_POSITIONS:
                    if place_sell_order(symbol, log_callback):
                        # Lock the signal after a successful trade to prevent spamming
                        last_signal = signal
                else:
                    log(f"⚠️ Max positions ({MAX_POSITIONS}) reached, skipping new signal.", log_callback)
            
            elif signal == "WAIT":
                # Reset the signal lock when the signal disappears
                last_signal = None
            
            time.sleep(INTERVAL)
        except Exception as e:
            log(f"❌ Error in main loop: {e}", log_callback)
            time.sleep(5)

# --- Main functions called by the GUI (Unchanged) ---
def run_strategy(symbol, timeframe, log_callback=None, profit_callback=None):
    global bot_running
    if bot_running:
        log("⚠️ Strategy already running.", log_callback)
        return True
    
    bot_running = True
    _stop_event.clear()
    log(f"🚀 Bear_Final Strategy Started on {symbol}", log_callback)
    
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback,), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    global bot_running
    if not bot_running: return
    bot_running = False
    _stop_event.set()
    time.sleep(0.5)
    log("🛑 Strategy stopped.", log_callback)