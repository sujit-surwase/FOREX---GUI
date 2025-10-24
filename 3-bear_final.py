"""
EMA_Breakout_v1.py — A breakout strategy based on the 200 EMA with advanced, multi-stage trade management.

Designed to integrate with the trading dashboard GUI and backtester.
- Enters on a breakout of a candle that touches the 200 EMA.
- Manages trades with a precise, two-stage profit securing and target extension system.
- Uses a fixed dollar-based risk and reward.
"""
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.4          # MODIFIED: Lot size for trades set to 0.25
MAGIC = 445566

# --- Fixed Dollar Risk Management ---
STOP_LOSS_USD = 35.0     # The amount in USD to risk on each trade.
TAKE_PROFIT_USD = 70.0     # The amount in USD to target for profit (maintaining 1:2 RR).
# The RR_RATIO setting is no longer needed as it's defined by the USD values above.

# --- Advanced Position Management Settings ---
PROFIT_THRESHOLD_1 = 35.0  # Profit in USD to trigger the first stop loss move
LOCK_IN_PROFIT_1 = 15.0  # Amount of profit in USD to lock in with the new stop loss

PROFIT_THRESHOLD_2 = 45.0  # Profit in USD to trigger the target move
TARGET_EXTENSION_PERCENT = 50.0 # Percentage to extend the target by

# --- Indicator Settings ---
EMA_PERIOD = 200
ATR_PERIOD = 14
ATR_THRESHOLD_PERCENT = 0.05

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER (Unchanged for compatibility)
# == NOTE: Backtester will still use candle-based SL. Live trading uses fixed USD risk.
# ==========================================================================================
def calculate_indicators(df):
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True, errors='ignore')
    df['EMA_200'] = df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    high_low = df['High'] - df['Low']; high_close = (df['High'] - df['Close'].shift()).abs(); low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = high_low.combine(high_close, max).combine(low_close, max)
    df['ATR'] = tr.rolling(window=ATR_PERIOD).mean()
    df.dropna(inplace=True); df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    if len(df) < 2: return "WAIT", None
    setup_candle, action_candle = df.iloc[-2], df.iloc[-1]
    min_atr_value = setup_candle.get('Close', 0) * (ATR_THRESHOLD_PERCENT / 100.0)
    if setup_candle.get('ATR', 0) <= min_atr_value: return "WAIT", None
    if setup_candle.get('Low', 0) <= setup_candle.get('EMA_200', float('inf')) and action_candle.get('High', 0) > setup_candle.get('High', 0):
        return "BUY", setup_candle['Low']
    if setup_candle.get('High', 0) >= setup_candle.get('EMA_200', 0) and action_candle.get('Low', 0) < setup_candle.get('Low', 0):
        return "SELL", setup_candle['High']
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

def usd_to_points(usd_amount, volume, info):
    """Converts a USD amount to price points for SL/TP calculation."""
    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size
    if tick_value > 0 and volume > 0:
        # Calculate how many ticks are needed to equal the USD amount
        ticks_needed = usd_amount / (tick_value * volume)
        # Convert ticks to price points
        return ticks_needed * tick_size
    return 0

# --- MODIFIED: place_order now calculates SL/TP based on fixed USD amounts ---
def place_order(symbol, signal_type, log_callback=None):
    info = mt5.symbol_info(symbol); tick = mt5.symbol_info_tick(symbol)
    if not tick or not info: log("❌ Tick/info unavailable.", log_callback); return None
    
    order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if signal_type == "BUY" else tick.bid

    # Calculate SL and TP distance in price points from the fixed USD amounts
    sl_points = usd_to_points(STOP_LOSS_USD, VOLUME, info)
    tp_points = usd_to_points(TAKE_PROFIT_USD, VOLUME, info)

    if sl_points == 0 or tp_points == 0:
        log("❌ Could not calculate SL/TP points from USD. Check symbol info.", log_callback)
        return None

    if signal_type == "BUY":
        stop_loss_price = price - sl_points
        tp_price = price + tp_points
    else: # SELL
        stop_loss_price = price + sl_points
        tp_price = price - tp_points

    request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME, "type": order_type, 
               "price": price, "sl": round(stop_loss_price, info.digits), "tp": round(tp_price, info.digits),
               "deviation": 20, "magic": MAGIC, "comment": "EMABreakoutV1_FixedRisk",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC}
    
    log(f"🔥 {signal_type} {symbol} @ {price:.5f} | SL:{stop_loss_price:.5f} (Risk ~${STOP_LOSS_USD}) TP:{tp_price:.5f} (Target ~${TAKE_PROFIT_USD})", log_callback)
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"✅ {signal_type} filled! Ticket: {result.order}"); return result.order
    log(f"❌ {signal_type} failed: {result.comment if result else 'No result'}", log_callback); return None

def _monitor_open_position(ticket, log_callback=None):
    """Manages a trade with a two-stage profit-securing and target-extension logic."""
    log(f"⚙️ Started monitoring position #{ticket}...")
    sl_moved_to_profit = False
    target_has_moved = False
    
    while bot_running and not _stop_event.is_set():
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position: log(f"✅ Position #{ticket} closed."); break
            position = position[0]; info = mt5.symbol_info(position.symbol)
            if not info: time.sleep(1); continue
            
            if not sl_moved_to_profit and position.profit > PROFIT_THRESHOLD_1:
                profit_points = usd_to_points(LOCK_IN_PROFIT_1, position.volume, info)
                new_sl = position.price_open + profit_points if position.type == mt5.ORDER_TYPE_BUY else position.price_open - profit_points
                log(f"🎉 Stage 1 Triggered for #{ticket}! Profit > ${PROFIT_THRESHOLD_1}.")
                log(f"📈 Moving SL to lock in ~${LOCK_IN_PROFIT_1} profit at price {new_sl:.5f}")
                request = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": round(new_sl, info.digits), "tp": position.tp}
                mt5.order_send(request)
                sl_moved_to_profit = True

            if not target_has_moved and position.profit > PROFIT_THRESHOLD_2:
                original_risk_dist = abs(position.price_open - position.sl)
                extension_amount = original_risk_dist * (TARGET_EXTENSION_PERCENT / 100.0)
                new_tp = position.tp + extension_amount if position.type == mt5.ORDER_TYPE_BUY else position.tp - extension_amount
                log(f"🎉 Stage 2 Triggered for #{ticket}! Profit > ${PROFIT_THRESHOLD_2}.")
                log(f"🎯 Moving TP further to {new_tp:.5f}")
                request = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": position.sl, "tp": round(new_tp, info.digits)}
                mt5.order_send(request)
                target_has_moved = True
            
            time.sleep(2)
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
                min_atr_value = last_candle.get('Close', 0) * (ATR_THRESHOLD_PERCENT / 100.0)
                if last_candle.get('ATR', 0) > min_atr_value:
                    if last_candle['Low'] <= last_candle['EMA_200']:
                        setup_candle = last_candle.copy(); pending_signal = "BUY"
                        log(f"📈 BUY Setup on {symbol}: Waiting for breakout > {setup_candle['High']:.5f}", log_callback)
                    elif last_candle['High'] >= last_candle['EMA_200']:
                        setup_candle = last_candle.copy(); pending_signal = "SELL"
                        log(f"📉 SELL Setup on {symbol}: Waiting for breakdown < {setup_candle['Low']:.5f}", log_callback)

            if pending_signal:
                tick = mt5.symbol_info_tick(symbol)
                if not tick or mt5.positions_get(symbol=symbol): time.sleep(1); continue

                if pending_signal == "BUY" and tick.ask > setup_candle['High']:
                    log(f"🔥 BUY TRIGGER on {symbol}!", log_callback)
                    # The place_order function now handles SL/TP calculation
                    ticket = place_order(symbol, "BUY", log_callback)
                    if ticket:
                        threading.Thread(target=_monitor_open_position, args=(ticket, log_callback), daemon=True).start()
                    pending_signal = None; setup_candle = None
                    time.sleep(60)
                
                elif pending_signal == "SELL" and tick.bid < setup_candle['Low']:
                    log(f"🔥 SELL TRIGGER on {symbol}!", log_callback)
                    # The place_order function now handles SL/TP calculation
                    ticket = place_order(symbol, "SELL", log_callback)
                    if ticket:
                        threading.Thread(target=_monitor_open_position, args=(ticket, log_callback), daemon=True).start()
                    pending_signal = None; setup_candle = None
                    time.sleep(60)
            time.sleep(1)
        except Exception as e:
            log(f"❌ Error in strategy loop: {e}")
            time.sleep(10)

def run_strategy(symbol, timeframe, log_callback=None, profit_callback=None):
    """Starts the trading bot. Required by the GUI."""
    global bot_running
    if bot_running: log("⚠️ Strategy is already running.", log_callback); return True
    bot_running = True; _stop_event.clear()
    log(f"🚀 EMA Breakout Strategy (Fixed Risk) Started on {symbol}", log_callback)
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    """Stops the trading bot. Required by the GUI."""
    global bot_running
    if not bot_running: return
    bot_running = False; _stop_event.set()
    log("🛑 Strategy stopping...", log_callback); time.sleep(1.5); log("✅ Strategy stopped.", log_callback)

