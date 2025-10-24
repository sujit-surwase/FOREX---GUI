import pandas as pd
import numpy as np
from datetime import datetime
import time
import threading
import mt5_config
import MetaTrader5 as mt5

# ==========================================================================================
# == REQUIRED PARAMETERS FOR THE BACKTESTER
# ==========================================================================================
SYMBOL = "EURUSD"
VOLUME = 0.01
RR_RATIO = 2.0

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """Calculates Bollinger Bands for the backtester."""
    period = 20
    std_dev = 2
    df.rename(columns={'close': 'Close', 'high': 'High', 'low': 'Low'}, inplace=True, errors='ignore')
    df['Middle_Band'] = df['Close'].rolling(window=period).mean()
    df['Std_Dev'] = df['Close'].rolling(window=period).std()
    df['Upper_Band'] = df['Middle_Band'] + (df['Std_Dev'] * std_dev)
    df['Lower_Band'] = df['Middle_Band'] - (df['Std_Dev'] * std_dev)
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """Generates a signal for the backtester."""
    if len(df) < 2: return 'WAIT', None
    last_candle, prev_candle = df.iloc[-1], df.iloc[-2]

    if last_candle['Close'] <= last_candle['Lower_Band'] and prev_candle['Close'] > last_candle['Close']:
        return 'BUY', last_candle['Low']
    elif last_candle['Close'] >= last_candle['Upper_Band'] and prev_candle['Close'] < last_candle['Close']:
        return 'SELL', last_candle['High']
    return 'WAIT', None

# ==========================================================================================
# == ORIGINAL CODE FOR LIVE TRADING (NOW MODIFIED TO ACCEPT SYMBOL)
# ==========================================================================================

STRATEGY_INTERVAL = 5
bot_running = False

def safe_log(message, log_callback=None):
    """Safe logging function."""
    try:
        if log_callback: log_callback(message)
        else: print(message)
    except Exception as e:
        print(f"Logging failed: {e}")

class BollingerBandStrategy:
    def __init__(self, symbol, period=20, std_dev=2, volume=0.01, sl_pips=50, tp_pips=100):
        self.symbol = symbol # MODIFIED: Store the symbol
        self.period = period
        self.std_dev = std_dev
        self.volume = volume
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips
        self.last_signal = None
        self.position_open = False

    def calculate_bollinger_bands(self, df):
        if len(df) < self.period: return None, None, None, None
        close_prices = df['close'].values
        sma = np.mean(close_prices[-self.period:])
        std = np.std(close_prices[-self.period:])
        upper_band = sma + (self.std_dev * std)
        lower_band = sma - (self.std_dev * std)
        return upper_band, sma, lower_band, close_prices[-1]

    def generate_signal(self, df):
        upper_band, middle_band, lower_band, current_price = self.calculate_bollinger_bands(df)
        if None in [upper_band, middle_band, lower_band]: return 'HOLD'
        
        prev_price = df['close'].iloc[-2] if len(df) >= 2 else current_price
        signal = 'HOLD'
        
        if current_price <= lower_band and prev_price > current_price:
            if self.last_signal != 'BUY': signal = 'BUY'
        elif current_price >= upper_band and prev_price < current_price:
            if self.last_signal != 'SELL': signal = 'SELL'
        elif self.position_open and ((self.last_signal == 'BUY' and current_price >= middle_band) or \
                                     (self.last_signal == 'SELL' and current_price <= middle_band)):
            signal = 'CLOSE'
        return signal

    def execute_trade(self, signal, log_callback=None):
        if signal == 'HOLD': return True
        
        tick_info = mt5_config.get_tick_info(self.symbol)
        symbol_info = mt5_config.get_symbol_info(self.symbol)
        if not tick_info or not symbol_info:
            safe_log(f"Failed to get info for {self.symbol}", log_callback)
            return False
        
        point = symbol_info.point
        order_type = None

        if signal == 'BUY':
            price, sl, tp = tick_info.ask, price - self.sl_pips * point, price + self.tp_pips * point
            order_type = mt5.ORDER_TYPE_BUY
        elif signal == 'SELL':
            price, sl, tp = tick_info.bid, price + self.sl_pips * point, price - self.tp_pips * point
            order_type = mt5.ORDER_TYPE_SELL
        elif signal == 'CLOSE':
            return self.close_positions(log_callback)
        else:
            return False

        request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": self.volume,
                   "type": order_type, "price": price, "sl": sl, "tp": tp, "deviation": 20,
                   "magic": 99999, "comment": f"Bollinger {signal}", "type_time": mt5.ORDER_TIME_GTC,
                   "type_filling": mt5.ORDER_FILLING_IOC}
        
        result = mt5_config.send_order(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            safe_log(f"Order failed: {result.comment}", log_callback)
            return False
        else:
            safe_log(f"Order executed: {signal} at {price:.5f}", log_callback)
            self.last_signal, self.position_open = signal, True
            return True

    def close_positions(self, log_callback=None):
        positions = mt5_config.get_positions(self.symbol)
        if not positions:
            self.position_open = False
            return True
        
        for position in positions:
            tick_info = mt5_config.get_tick_info(self.symbol)
            if not tick_info: continue
            
            price = tick_info.bid if position.type == mt5.POSITION_TYPE_BUY else tick_info.ask
            order_type = mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                
            request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": position.volume,
                       "type": order_type, "position": position.ticket, "price": price, "deviation": 20,
                       "magic": 99999, "comment": "Bollinger Close", "type_time": mt5.ORDER_TIME_GTC,
                       "type_filling": mt5.ORDER_FILLING_IOC}
            
            result = mt5_config.send_order(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                safe_log(f"Position closed: {position.ticket}", log_callback)
                self.position_open, self.last_signal = False, None
            else:
                safe_log(f"Close failed: {result.comment}", log_callback)
                return False
        return True

# --- MODIFIED: Added 'symbol' as the first parameter ---
def run_strategy(symbol, log_callback=None, profit_callback=None):
    global bot_running
    if not mt5_config.connect(log_callback): return False
    
    bot_running = True
    # MODIFIED: Pass the symbol from the GUI to the strategy class
    strategy = BollingerBandStrategy(symbol=symbol)
    safe_log(f"[START] Bollinger Band Strategy Started on {symbol}", log_callback)
    
    if profit_callback:
        threading.Thread(target=update_profit_thread, args=(symbol, profit_callback,), daemon=True).start()

    def strategy_loop():
        while bot_running:
            try:
                # MODIFIED: Use the correct symbol for fetching data
                df = mt5_config.fetch_data(symbol)
                if not df.empty:
                    signal = strategy.generate_signal(df)
                    if signal in ['BUY', 'SELL', 'CLOSE']:
                        strategy.execute_trade(signal, log_callback)
                time.sleep(STRATEGY_INTERVAL)
            except Exception as e:
                safe_log(f"[ERROR] Error in strategy loop: {e}", log_callback)
                time.sleep(5)

    threading.Thread(target=strategy_loop, daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    global bot_running
    bot_running = False
    if mt5.terminal_info() is not None:
        mt5.shutdown()
    safe_log("Strategy stopped.", log_callback)

def close_all_positions(symbol, log_callback=None):
    # MODIFIED: Needs symbol to know which positions to close
    strategy = BollingerBandStrategy(symbol=symbol)
    strategy.close_positions(log_callback)

def update_profit_thread(symbol, profit_callback):
    while bot_running:
        try:
            positions = mt5_config.get_positions(symbol)
            total_profit = sum(pos.profit for pos in positions)
            if profit_callback:
                profit_callback(total_profit, len(positions))
            time.sleep(2)
        except Exception as e:
            print(f"Error updating profit: {e}")
            time.sleep(5)