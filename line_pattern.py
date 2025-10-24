import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import json
from typing import Dict, List, Tuple, Optional
import logging
import threading
import time
import MetaTrader5 as mt5
from mt5_config import * # Assumes you have your mt5_config.py file

# ==========================================================================================
# == REQUIRED PARAMETERS FOR THE BACKTESTER
# ==========================================================================================
SYMBOL = "EURUSD"       # Change to the symbol you want to backtest
VOLUME = 0.1
RR_RATIO = 2.0          # Default Reward:Risk ratio

# ==========================================================================================
# == REQUIRED FUNCTIONS FOR THE BACKTESTER
# ==========================================================================================

def calculate_indicators(df):
    """
    This function is required by the backtester.
    For this strategy, no traditional indicators are needed. We just prepare the data.
    """
    df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True, errors='ignore')
    for col in ['Open', 'High', 'Low', 'Close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def get_signal(df):
    """
    This function is required by the backtester.
    It uses the logic from the LinePatternTrader class to find signals.
    """
    if len(df) < 20: return "WAIT", None

    temp_trader = LinePatternTrader()
    patterns = temp_trader.detect_trend_lines(df)
    signals = temp_trader.generate_trading_signals(patterns)
    
    if signals:
        best_signal = max(signals, key=lambda s: s['confidence'])
        return best_signal['type'], best_signal['stop_loss']
            
    return "WAIT", None

# ==========================================================================================
# == LIVE TRADING FUNCTIONS (ADDED TO FIX THE ERROR)
# ==========================================================================================
bot_running = False
INTERVAL = 60 # Check every 60 seconds
MAGIC = 554433

def place_order(symbol, signal_info, log_callback=None):
    """Places a trade for the live strategy."""
    tick = get_tick_info(symbol)
    if not tick:
        log(f"❌ Could not get tick info for {symbol}", log_callback)
        return False

    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
        "type": mt5.ORDER_TYPE_BUY if signal_info['type'] == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if signal_info['type'] == "BUY" else tick.bid,
        "sl": signal_info['stop_loss'], "tp": signal_info['take_profit'],
        "deviation": 20, "magic": MAGIC, "comment": "Line Pattern",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    }

    log(f"⚡ SENDING {signal_info['type']} @ {request['price']:.5f}", log_callback)
    result = send_order(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"✅ {signal_info['type']} ORDER PLACED! Ticket: {result.order}", log_callback)
        return True
    else:
        log(f"❌ {signal_info['type']} FAILED: {result.comment if result else 'No result'}", log_callback)
        return False

def _strategy_loop(symbol, log_callback=None):
    """The main worker loop for the live strategy."""
    trader = LinePatternTrader()
    while bot_running:
        try:
            if not get_positions(symbol):
                df = fetch_data(symbol, count=100)
                if not df.empty:
                    patterns = trader.detect_trend_lines(df)
                    signals = trader.generate_trading_signals(patterns)
                    if signals:
                        best_signal = max(signals, key=lambda s: s['confidence'])
                        if best_signal['confidence'] > 0.7: # Confidence threshold for live trading
                            place_order(symbol, best_signal, log_callback)
            else:
                log(f"📊 Position already open on {symbol}. Monitoring...", log_callback)
            
            time.sleep(INTERVAL)
        except Exception as e:
            log(f"❌ Error in live strategy loop: {e}", log_callback)
            time.sleep(INTERVAL)

def run_strategy(symbol, log_callback=None, profit_callback=None):
    """Starts the live trading bot. This is called by the GUI."""
    global bot_running
    if bot_running:
        log("⚠️ Strategy is already running.", log_callback)
        return True

    if not connect(log_callback): return False

    bot_running = True
    log(f"🚀 Line Pattern Strategy Started on {symbol}", log_callback)
    
    threading.Thread(target=_strategy_loop, args=(symbol, log_callback), daemon=True).start()
    return True

def stop_strategy(log_callback=None):
    """Stops the live trading bot. This is called by the GUI."""
    global bot_running
    bot_running = False
    log("🛑 Strategy stopping...", log_callback)
    shutdown()
    log("✅ Strategy stopped.", log_callback)

# ==========================================================================================
# == ORIGINAL CLASS-BASED CODE
# ==========================================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LinePatternTrader:
    def __init__(self, initial_balance: float = 10000, risk_percentage: float = 2.0, min_pattern_length: int = 10):
        self.balance, self.initial_balance = initial_balance, initial_balance
        self.risk_percentage, self.min_pattern_length = risk_percentage, min_pattern_length
        self.positions, self.trade_history, self.current_patterns = [], [], {}
        
    def detect_trend_lines(self, prices: pd.DataFrame, lookback: int = 20) -> Dict:
        if len(prices) < self.min_pattern_length: return {}
        highs, lows, closes = prices['High'].values, prices['Low'].values, prices['Close'].values
        pivot_highs, pivot_lows = self._find_pivot_points(highs, lookback, 'high'), self._find_pivot_points(lows, lookback, 'low')
        resistance_line, support_line = self._calculate_trend_line(pivot_highs, 'resistance'), self._calculate_trend_line(pivot_lows, 'support')
        channel_pattern = self._detect_channel(resistance_line, support_line, closes)
        return {'resistance_line': resistance_line, 'support_line': support_line, 'channel_pattern': channel_pattern,
                'current_price': closes[-1], 'pivot_highs': pivot_highs, 'pivot_lows': pivot_lows}
    
    def _find_pivot_points(self, data: np.array, window: int, point_type: str) -> List[Tuple]:
        pivots = []
        for i in range(window, len(data) - window):
            is_pivot = False
            if point_type == 'high':
                if all(data[i] >= data[i-j] for j in range(1, window+1)) and all(data[i] >= data[i+j] for j in range(1, window+1)):
                    is_pivot = True
            else:
                if all(data[i] <= data[i-j] for j in range(1, window+1)) and all(data[i] <= data[i+j] for j in range(1, window+1)):
                    is_pivot = True
            if is_pivot: pivots.append((i, data[i]))
        return pivots[-10:]

    def _calculate_trend_line(self, pivots: List[Tuple], line_type: str) -> Optional[Dict]:
        if len(pivots) < 2: return None
        (x1, y1), (x2, y2) = pivots[-2], pivots[-1]
        slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0
        intercept = y1 - slope * x1
        return {'slope': slope, 'intercept': intercept, 'points': [(x1, y1), (x2, y2)], 'type': line_type, 'strength': len(pivots)}
    
    def _detect_channel(self, resistance: Dict, support: Dict, closes: np.array) -> Dict:
        if not resistance or not support: return {'valid': False}
        is_parallel = abs(resistance['slope'] - support['slope']) < 0.001
        current_idx = len(closes) - 1
        res_val, sup_val = (resistance['slope'] * current_idx + resistance['intercept']), (support['slope'] * current_idx + support['intercept'])
        width = res_val - sup_val
        position = (closes[-1] - sup_val) / width if width > 0 else 0.5
        return {'valid': is_parallel and width > 0, 'width': width, 'resistance_value': res_val, 'support_value': sup_val, 'position_in_channel': position, 'is_parallel': is_parallel}
    
    def generate_trading_signals(self, pattern_data: Dict) -> List[Dict]:
        signals = []
        if not pattern_data: return signals
        current_price, channel = pattern_data['current_price'], pattern_data.get('channel_pattern', {})
        if channel.get('valid', False):
            pos, res_val, sup_val = channel['position_in_channel'], channel['resistance_value'], channel['support_value']
            if pos <= 0.2:
                signals.append({'type': 'BUY', 'reason': 'Price near channel support', 'entry_price': current_price, 'stop_loss': sup_val * 0.998, 'take_profit': res_val * 0.995, 'confidence': 0.8 if pos <= 0.1 else 0.6})
            elif pos >= 0.8:
                signals.append({'type': 'SELL', 'reason': 'Price near channel resistance', 'entry_price': current_price, 'stop_loss': res_val * 1.002, 'take_profit': sup_val * 1.005, 'confidence': 0.8 if pos >= 0.9 else 0.6})
        return signals