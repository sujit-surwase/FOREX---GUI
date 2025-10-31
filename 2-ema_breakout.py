"""
EMA_Breakout_v1.py — A breakout strategy based on the 200 EMA with advanced trade management.

Designed to integrate with the trading dashboard GUI and backtester.
- Waits for a candle to touch the 200 EMA.
- Enters a trade when the price breaks the high/low of that "setup candle".
- Manages open trades with a conditional Trailing Stop Loss and a Moving Target.
- Uses a strict 1:2 initial risk-to-reward ratio.
"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.10          # Lot size for trades
MAGIC = 445566
RR_RATIO = 2.0         # Initial Reward:Risk ratio

# --- MODIFIED: Advanced Position Management Settings ---
TRAILING_STOP_TRIGGER_PERCENT = 50.0  # Start trailing SL after 50% of TP is reached
TRAILING_STOP_DISTANCE_PERCENT = 25.0 # Trail the SL at this percentage away from the current price
TARGET_MOVE_THRESHOLD_PERCENT = 70.0  # When 70% of the target is reached...
TARGET_MOVE_FACTOR = 1.0              # ...move the target by 1x the original risk distance

# --- Indicator Settings ---
EMA_PERIOD = 200
ATR_PERIOD = 14
ATR_THRESHOLD_PERCENT = 0.05

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER (Fixed)
# ==========================================================================================

def calculate_indicators(df):
    """Calculates all necessary indicators for the backtester."""
    
    # FIX: Removed the .rename() line to keep columns lowercase
    
    # FIX: 'Close' -> 'close'
    df['EMA_200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    
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
    """Determines the signal for the backtester."""
    if len(df) < 2: return "WAIT", None
    setup_candle, action_candle = df.iloc[-2], df.iloc[-1]
    
    # FIX: 'Close' -> 'close'
    min_atr_value = setup_candle.get('close', 0) * (ATR_THRESHOLD_PERCENT / 100.0)
    if setup_candle.get('ATR', 0) <= min_atr_value: return "WAIT", None
    
    # FIX: 'Low', 'High' -> 'low', 'high'
    if setup_candle.get('low', 0) <= setup_candle.get('EMA_200', float('inf')) and action_candle.get('high', 0) > setup_candle.get('high', 0):
        return "BUY", setup_candle['low']
    
    # FIX: 'High', 'Low' -> 'high', 'low'
    if setup_candle.get('high', 0) >= setup_candle.get('EMA_200', 0) and action_candle.get('low', 0) < setup_candle.get('low', 0):
        return "SELL", setup_candle['high']
    
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
    df = pd.DataFrame(rates); df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def place_order(symbol, signal_type, stop_loss_price, log_callback=None):
    info = mt5.symbol_info(symbol); tick = mt5.symbol_info_tick(symbol)
    if not tick or not info: log("❌ Tick/info unavailable.", log_callback); return None
    
    order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if signal_type == "BUY" else tick.bid
    risk_dist = abs(price - stop_loss_price)
    if risk_dist == 0: log("❌ Risk distance is zero.", log_callback); return None
    tp_price = price + (risk_dist * RR_RATIO) if signal_type == "BUY" else price - (risk_dist * RR_RATIO)

    request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME, "type": order_type, 
               "price": price, "sl": round(stop_loss_price, info.digits), "tp": round(tp_price, info.digits),
               "deviation": 20, "magic": MAGIC, "comment": "EMABreakoutV1_Adv",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC}
    
    log(f"🔥 {signal_type} {symbol} @ {price:.5f} | SL:{stop_loss_price:.5f} TP:{tp_price:.5f}", log_callback)
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"✅ {signal_type} filled! Ticket: {result.order}"); return result.order
    log(f"❌ {signal_type} failed: {result.comment if result else 'No result'}", log_callback); return None

def _monitor_open_position(ticket, log_callback=None):
    """MODIFIED: Manages a single open trade with conditional trailing stop and moving target."""
    log(f"⚙️ Started monitoring position #{ticket}...")
    target_has_moved = False
    trailing_stop_activated = False # State variable for the new logic
    
    while bot_running and not _stop_event.is_set():
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                log(f"✅ Position #{ticket} has been closed.")
                break # Exit the monitor loop
            
            position = position[0]
            tick = mt5.symbol_info_tick(position.symbol)
            if not tick:
                time.sleep(1)
                continue
                
            info = mt5.symbol_info(position.symbol)
            current_price = tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask
            
            # --- CALCULATE PROGRESS TO TARGET ---
            total_target_dist = abs(position.tp - position.price_open)
            dist_covered = abs(current_price - position.price_open)
            progress_percent = (dist_covered / total_target_dist) * 100 if total_target_dist > 0 else 0

            # --- CONDITIONAL TRAILING STOP LOSS LOGIC ---
            # 1. Check if trailing stop should be activated
            if not trailing_stop_activated and progress_percent >= TRAILING_STOP_TRIGGER_PERCENT:
                trailing_stop_activated = True
                log(f"🎉 Trailing stop activated for #{ticket} at {progress_percent:.0f}% of target.")

            # 2. If activated, trail the price
            if trailing_stop_activated:
                if position.type == mt5.ORDER_TYPE_BUY: # BUY Trade
                    new_sl = current_price * (1 - (TRAILING_STOP_DISTANCE_PERCENT / 100.0))
                    # Only move the stop loss up
                    if new_sl > position.sl:
                        log(f"📈 Trailing SL for BUY #{ticket} up to {new_sl:.5f}")
                        request = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": round(new_sl, info.digits), "tp": position.tp}
                        mt5.order_send(request)
                else: # SELL Trade
                    new_sl = current_price * (1 + (TRAILING_STOP_DISTANCE_PERCENT / 100.0))
                    # Only move the stop loss down
                    if new_sl < position.sl:
                        log(f"📉 Trailing SL for SELL #{ticket} down to {new_sl:.5f}")
                        request = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": round(new_sl, info.digits), "tp": position.tp}
                        mt5.order_send(request)

            # --- MOVING TARGET LOGIC (Remains the same) ---
            if not target_has_moved and progress_percent >= TARGET_MOVE_THRESHOLD_PERCENT:
                original_risk = abs(position.price_open - position.sl)
                if position.type == mt5.ORDER_TYPE_BUY:
                    new_tp = position.tp + (original_risk * TARGET_MOVE_FACTOR)
                    log(f"🎯 Moving target for BUY #{ticket} up to {new_tp:.5f}")
                else: # SELL
                    new_tp = position.tp - (original_risk * TARGET_MOVE_FACTOR)
                    log(f"🎯 Moving target for SELL #{ticket} down to {new_tp:.5f}")
                
                request = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": position.sl, "tp": round(new_tp, info.digits)}
                mt5.order_send(request)
                target_has_moved = True # Ensure target only moves once
            
            time.sleep(2) # Check every 2 seconds
        except Exception as e:
            log(f"❌ Error monitoring position #{ticket}: {e}")
            time.sleep(10)

def _strategy_loop(symbol, timeframe, log_callback=None):
    """Main loop for finding trade entries."""
    setup_candle = None; pending_signal = None
    
    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, timeframe, 250)
            if df.empty: time.sleep(1); continue

            df_indicators = calculate_indicators(df.copy())
            last_candle = df_indicators.iloc[-1]
            
            if setup_candle is None or last_candle['time'] != setup_candle['time']:
                pending_signal = None
                
                # FIX: 'Close' -> 'close'
                min_atr_value = last_candle.get('close', 0) * (ATR_THRESHOLD_PERCENT / 100.0)
                current_atr = last_candle.get('ATR', 0)
                
                if current_atr > min_atr_value:
                    # FIX: 'Low', 'High' -> 'low', 'high'
                    if last_candle['low'] <= last_candle['EMA_200']:
                        setup_candle = last_candle.copy(); pending_signal = "BUY"
                        log(f"📈 BUY Setup on {symbol}: Waiting for breakout above {setup_candle['high']:.5f}", log_callback)
                    elif last_candle['high'] >= last_candle['EMA_200']:
                        setup_candle = last_candle.copy(); pending_signal = "SELL"
                        log(f"📉 SELL Setup on {symbol}: Waiting for breakdown below {setup_candle['low']:.5f}", log_callback)

            if pending_signal:
                tick = mt5.symbol_info_tick(symbol)
                if not tick or mt5.positions_get(symbol=symbol):
                    time.sleep(1); continue

                # FIX: 'High', 'Low' -> 'high', 'low'
                if pending_signal == "BUY" and tick.ask > setup_candle['high']:
                    log(f"🔥 BUY TRIGGER on {symbol}!", log_callback)
                    stop_loss = setup_candle['low'] - ((setup_candle['high'] - setup_candle['low']) * 0.1)
                    ticket = place_order(symbol, "BUY", stop_loss, log_callback)
                    if ticket:
                        threading.Thread(target=_monitor_open_position, args=(ticket, log_callback), daemon=True).start()
                    pending_signal = None; setup_candle = None
                    time.sleep(60)
                
                # FIX: 'High', 'Low' -> 'high', 'low'
                elif pending_signal == "SELL" and tick.bid < setup_candle['low']:
                    log(f"🔥 SELL TRIGGER on {symbol}!", log_callback)
                    stop_loss = setup_candle['high'] + ((setup_candle['high'] - setup_candle['low']) * 0.1)
                    ticket = place_order(symbol, "SELL", stop_loss, log_callback)
                    if ticket:
                        threading.Thread(target=_monitor_open_position, args=(ticket, log_callback), daemon=True).start()
                    pending_signal = None; setup_candle = None
                    time.sleep(60)
            time.sleep(1)
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}", log_callback)
            time.sleep(10)

def run_strategy(symbol, timeframe, log_callback=None):
    """Starts the trading bot. Required by the GUI."""
    global bot_running
    if bot_running: log("⚠️ Strategy is already running.", log_callback); return True
    bot_running = True; _stop_event.clear()
    log(f"🚀 EMA Breakout Strategy (Advanced) Started on {symbol}", log_callback)
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    """Stops the trading bot. Required by the GUI."""
    global bot_running
    if not bot_running: return
    bot_running = False; _stop_event.set()
    log("🛑 Strategy stopping...", log_callback); time.sleep(1.5); log("✅ Strategy stopped.", log_callback)

