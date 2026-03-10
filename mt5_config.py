import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
#owner - Sujit Surwase

# === CONFIG ===
LOGIN = 433297549   # ✅ updated to your login
PASSWORD = "Sujit@123"
SERVER = "Exness-MT5Trial7"  # ✅ updated to your server
TERMINAL_PATH = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"


# === LOGGING ===
def log(message, log_callback=None):
    """Log message with timestamp"""
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    formatted_message = f"{timestamp} {message}"
    
    if log_callback:
        log_callback(formatted_message)
    else:
        print(formatted_message)

# === MT5 CONNECTION & WRAPPERS ===
def connect(log_callback=None):
    """Connect to MetaTrader 5"""
    if not mt5.initialize(path=TERMINAL_PATH):
        log(f"❌ Init error: {mt5.last_error()}", log_callback)
        return False

    if not mt5.login(LOGIN, PASSWORD, SERVER):
        log(f"❌ Login failed: {mt5.last_error()}", log_callback)
        return False

    log(f"✅ Connected to MT5 account {LOGIN} on {SERVER}", log_callback)

    # === Fetch account info ===
    account_info = mt5.account_info()
    if account_info:
        log(f"📊 Account Balance: {account_info.balance} USD", log_callback)
        log(f"📊 Equity: {account_info.equity} USD", log_callback)
        log(f"📊 Free Margin: {account_info.margin_free} USD", log_callback)
        log(f"📊 Leverage: 1:{account_info.leverage}", log_callback)
    else:
        log("⚠️ Could not retrieve account info", log_callback)

    return True

def shutdown():
    """Shutdown MT5 connection"""
    mt5.shutdown()

def fetch_data(symbol, timeframe=mt5.TIMEFRAME_M1, count=100):
    """Fetch candle data from MT5 for a specific symbol."""
    try:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Standardize column names
        df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low', 
            'close': 'Close', 'tick_volume': 'Volume'
        }, inplace=True, errors='ignore')
        
        return df
        
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return pd.DataFrame()

def get_symbol_info(symbol):
    """Get symbol information"""
    return mt5.symbol_info(symbol)

def get_tick_info(symbol):
    """Get current tick information"""
    return mt5.symbol_info_tick(symbol)

def get_positions(symbol=None):
    """Get current positions for a specific symbol or all symbols if None."""
    if symbol:
        return mt5.positions_get(symbol=symbol)
    return mt5.positions_get()

def send_order(request):
    """Send order to MT5"""
    return mt5.order_send(request)

def get_last_error():
    """Get last MT5 error"""
    return mt5.last_error()
