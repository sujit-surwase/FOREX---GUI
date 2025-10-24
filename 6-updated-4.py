"""
EMA_Breakout_v2.py — 200 EMA breakout strategy with 3-stage dynamic trailing.

✅ When profit > $30 → move SL to lock $15 profit.
✅ When profit > $50 → extend TP by +$60 and move SL +$15 beyond previous SL.
✅ When profit > $80 → extend TP by +$50 and move SL +$30 beyond previous SL.
"""

import MetaTrader5 as mt5
import pandas as pd
import threading
import time

# ================= USER SETTINGS =================
VOLUME = 0.4
MAGIC = 445566
MAX_CONCURRENT_TRADES = 3

STOP_LOSS_USD = 35.0
TAKE_PROFIT_USD = 70.0

# --- Dynamic Trailing & Extension ---
TRAIL_TRIGGER_USD = 30.0      # When profit > $30
LOCK_PROFIT_USD = 15.0        # Lock $15 profit
EXTEND_TRIGGER_USD = 50.0     # When profit > $50
EXTEND_TP_USD = 60.0          # Add +$60 to TP
EXTEND_SL_USD = 20.0          # Move SL +$15 beyond current SL

# --- Stage 3 ---
SUPER_EXTEND_TRIGGER_USD = 80.0  # When profit > $80
SUPER_EXTEND_TP_USD = 50.0       # Add +$50 to TP
SUPER_EXTEND_SL_USD = 30.0       # Move SL +$30 beyond previous SL

# --- Indicator Settings ---
EMA_PERIOD = 200
ATR_PERIOD = 14
ATR_THRESHOLD_PERCENT = 0.05

# ==========================================================================================
# == BACKTESTER FUNCTIONS
# ==========================================================================================
def calculate_indicators(df):
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True, errors='ignore')
    df['EMA_200'] = df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = high_low.combine(high_close, max).combine(low_close, max)
    df['ATR'] = tr.rolling(window=ATR_PERIOD).mean()
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    if len(df) < 2:
        return "WAIT", None
    setup, action = df.iloc[-2], df.iloc[-1]
    min_atr_value = setup['Close'] * (ATR_THRESHOLD_PERCENT / 100.0)
    if setup['ATR'] <= min_atr_value:
        return "WAIT", None
    if setup['Low'] <= setup['EMA_200'] and action['High'] > setup['High']:
        return "BUY", setup['Low']
    if setup['High'] >= setup['EMA_200'] and action['Low'] < setup['Low']:
        return "SELL", setup['High']
    return "WAIT", None

# ==========================================================================================
# == LIVE TRADING FUNCTIONS
# ==========================================================================================
bot_running = False
_stop_event = threading.Event()

def log(msg, log_callback=None):
    if log_callback:
        log_callback(msg)
    else:
        print(msg)

def fetch_data(symbol, timeframe, bars=250):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def usd_to_points(usd, volume, info):
    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size
    if tick_value > 0 and volume > 0:
        ticks_needed = usd / (tick_value * volume)
        return ticks_needed * tick_size
    return 0

def place_order(symbol, signal_type, log_callback=None):
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not tick or not info:
        log("❌ Tick/info unavailable.", log_callback)
        return None

    order_type = mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if signal_type == "BUY" else tick.bid

    sl_points = usd_to_points(STOP_LOSS_USD, VOLUME, info)
    tp_points = usd_to_points(TAKE_PROFIT_USD, VOLUME, info)

    if sl_points == 0 or tp_points == 0:
        log("❌ Could not calculate SL/TP points from USD.", log_callback)
        return None

    stop_loss = price - sl_points if signal_type == "BUY" else price + sl_points
    take_profit = price + tp_points if signal_type == "BUY" else price - tp_points

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": VOLUME,
        "type": order_type,
        "price": price,
        "sl": round(stop_loss, info.digits),
        "tp": round(take_profit, info.digits),
        "deviation": 20,
        "magic": MAGIC,
        "comment": "EMA_Breakout_v2",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"✅ {signal_type} placed @ {price:.5f} | SL={stop_loss:.5f}, TP={take_profit:.5f}", log_callback)
        return result.order
    log(f"❌ Order failed: {result.comment if result else 'Unknown'}", log_callback)
    return None

def _monitor_open_position(ticket, log_callback=None):
    """Monitors and dynamically updates SL/TP based on live profit."""
    log(f"⚙️ Monitoring trade #{ticket}...")
    sl_moved = False
    tp_extended = False
    super_extended = False  # new stage 3 flag

    while bot_running and not _stop_event.is_set():
        try:
            pos = mt5.positions_get(ticket=ticket)
            if not pos:
                log(f"✅ Trade #{ticket} closed.")
                break
            pos = pos[0]
            info = mt5.symbol_info(pos.symbol)
            if not info:
                time.sleep(1)
                continue

            profit = pos.profit

            # === Stage 1: Lock profit ===
            if not sl_moved and profit >= TRAIL_TRIGGER_USD:
                lock_pts = usd_to_points(LOCK_PROFIT_USD, pos.volume, info)
                new_sl = pos.price_open + lock_pts if pos.type == mt5.ORDER_TYPE_BUY else pos.price_open - lock_pts
                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": round(new_sl, info.digits), "tp": pos.tp}
                mt5.order_send(req)
                sl_moved = True
                log(f"🔁 SL moved to lock ${LOCK_PROFIT_USD} profit on #{ticket}", log_callback)

            # === Stage 2: Extend TP & SL ===
            if not tp_extended and profit >= EXTEND_TRIGGER_USD:
                add_tp_pts = usd_to_points(EXTEND_TP_USD, pos.volume, info)
                add_sl_pts = usd_to_points(EXTEND_SL_USD, pos.volume, info)

                new_tp = pos.tp + add_tp_pts if pos.type == mt5.ORDER_TYPE_BUY else pos.tp - add_tp_pts
                new_sl = pos.sl + add_sl_pts if pos.type == mt5.ORDER_TYPE_BUY else pos.sl - add_sl_pts

                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": round(new_sl, info.digits), "tp": round(new_tp, info.digits)}
                mt5.order_send(req)
                tp_extended = True
                log(f"🚀 Profit > $50 — TP +${EXTEND_TP_USD}, SL +${EXTEND_SL_USD} beyond previous.", log_callback)

            # === Stage 3: Super Extend TP & SL ===
            if not super_extended and profit >= SUPER_EXTEND_TRIGGER_USD:
                add_tp_pts = usd_to_points(SUPER_EXTEND_TP_USD, pos.volume, info)
                add_sl_pts = usd_to_points(SUPER_EXTEND_SL_USD, pos.volume, info)

                new_tp = pos.tp + add_tp_pts if pos.type == mt5.ORDER_TYPE_BUY else pos.tp - add_tp_pts
                new_sl = pos.sl + add_sl_pts if pos.type == mt5.ORDER_TYPE_BUY else pos.sl - add_sl_pts

                req = {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": round(new_sl, info.digits), "tp": round(new_tp, info.digits)}
                mt5.order_send(req)
                super_extended = True
                log(f"💎 Profit > $80 — TP +${SUPER_EXTEND_TP_USD}, SL +${SUPER_EXTEND_SL_USD} beyond previous.", log_callback)

            time.sleep(2)

        except Exception as e:
            log(f"❌ Error monitoring #{ticket}: {e}")
            time.sleep(5)

def _strategy_loop(symbol, timeframe, log_callback=None):
    setup_candle = None
    pending_signal = None

    while bot_running and not _stop_event.is_set():
        try:
            df = fetch_data(symbol, timeframe, 250)
            if df.empty:
                time.sleep(1)
                continue

            df = calculate_indicators(df)
            last = df.iloc[-1]

            if setup_candle is None or last['time'] != setup_candle['time']:
                pending_signal = None
                min_atr = last['Close'] * (ATR_THRESHOLD_PERCENT / 100.0)
                if last['ATR'] > min_atr:
                    if last['Low'] <= last['EMA_200']:
                        setup_candle = last.copy()
                        pending_signal = "BUY"
                        log(f"📈 BUY setup waiting for breakout > {setup_candle['High']:.5f}", log_callback)
                    elif last['High'] >= last['EMA_200']:
                        setup_candle = last.copy()
                        pending_signal = "SELL"
                        log(f"📉 SELL setup waiting for breakdown < {setup_candle['Low']:.5f}", log_callback)

            if pending_signal:
                tick = mt5.symbol_info_tick(symbol)
                if not tick:
                    time.sleep(1)
                    continue

                open_positions = mt5.positions_get(symbol=symbol) or []
                if len(open_positions) >= MAX_CONCURRENT_TRADES:
                    log(f"⚠️ Max trades reached for {symbol}.", log_callback)
                    time.sleep(5)
                    continue

                if pending_signal == "BUY" and tick.ask > setup_candle['High']:
                    ticket = place_order(symbol, "BUY", log_callback)
                    if ticket:
                        threading.Thread(target=_monitor_open_position, args=(ticket, log_callback), daemon=True).start()
                    setup_candle = None
                    time.sleep(5)

                elif pending_signal == "SELL" and tick.bid < setup_candle['Low']:
                    ticket = place_order(symbol, "SELL", log_callback)
                    if ticket:
                        threading.Thread(target=_monitor_open_position, args=(ticket, log_callback), daemon=True).start()
                    setup_candle = None
                    time.sleep(5)

            time.sleep(1)

        except Exception as e:
            log(f"❌ Error in strategy loop: {e}")
            time.sleep(5)

def run_strategy(symbol, timeframe, log_callback=None, profit_callback=None):
    global bot_running
    if bot_running:
        log("⚠️ Strategy already running.", log_callback)
        return True
    bot_running = True
    _stop_event.clear()
    log(f"🚀 EMA Breakout Strategy v2 Started on {symbol}", log_callback)
    threading.Thread(target=_strategy_loop, args=(symbol, timeframe, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    global bot_running
    if not bot_running:
        return
    bot_running = False
    _stop_event.set()
    log("🛑 Strategy stopping...", log_callback)
    time.sleep(1.5)
    log("✅ Strategy stopped.", log_callback)
