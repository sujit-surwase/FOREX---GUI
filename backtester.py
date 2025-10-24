# backtester.py
# Runs a historical simulation based on the provided strategy file.

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

def run_backtest(strategy_module, symbol, start_date, end_date, initial_balance, timeframe, timeframe_str, log_callback):
    """
    Main function to run the backtest. This is the function called by the GUI.
    """
    if not mt5.initialize():
        return {'status': 'error', 'message': 'MT5 Initialize failed.'}
    
    log_callback(f"⬇️ Fetching historical data for {symbol} on timeframe {timeframe_str}...")
    try:
        rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
        if rates is None or len(rates) == 0:
            mt5.shutdown()
            return {'status': 'error', 'message': 'No historical data found for the selected period.'}
        data = pd.DataFrame(rates)
        data['time'] = pd.to_datetime(data['time'], unit='s')
        log_callback(f"✅ Fetched {len(data)} candles.")
    except Exception as e:
        mt5.shutdown()
        return {'status': 'error', 'message': f"Failed to fetch data: {e}"}

    log_callback("📈 Calculating indicators...")
    df = strategy_module.calculate_indicators(data.copy())
    if df is None or df.empty:
        mt5.shutdown()
        return {'status': 'error', 'message': 'Failed to calculate indicators.'}

    log_callback("🔄 Starting trade simulation...")
    balance = initial_balance
    peak_balance = initial_balance
    max_drawdown = 0
    trades = []
    open_position = None

    for i in range(1, len(df)):
        current_candle = df.iloc[i]
        
        if open_position:
            pnl = 0
            close_reason = None
            if open_position['type'] == 'BUY':
                if current_candle['Low'] <= open_position['sl']:
                    pnl = (open_position['sl'] - open_position['entry_price'])
                    close_reason = "Stop Loss"
                elif current_candle['High'] >= open_position['tp']:
                    pnl = (open_position['tp'] - open_position['entry_price'])
                    close_reason = "Take Profit"
            elif open_position['type'] == 'SELL':
                if current_candle['High'] >= open_position['sl']:
                    pnl = (open_position['entry_price'] - open_position['sl'])
                    close_reason = "Stop Loss"
                elif current_candle['Low'] <= open_position['tp']:
                    pnl = (open_position['entry_price'] - open_position['tp'])
                    close_reason = "Take Profit"

            if close_reason:
                symbol_info = mt5.symbol_info(symbol)
                contract_size = symbol_info.trade_contract_size if symbol_info else 1
                final_pnl = pnl * open_position['volume'] * contract_size
                balance += final_pnl
                open_position['pnl'] = final_pnl
                open_position['exit_price'] = open_position['sl'] if close_reason == "Stop Loss" else open_position['tp']
                open_position['exit_time'] = current_candle['time']
                open_position['closed_by'] = close_reason
                trades.append(open_position)
                open_position = None
                drawdown = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                max_drawdown = max(max_drawdown, drawdown)
                peak_balance = max(balance, peak_balance)
        
        if not open_position:
            historical_slice = df.iloc[:i+1]
            signal, stop_loss = strategy_module.get_signal(historical_slice)
            
            if signal != "WAIT":
                entry_price = current_candle['Close'] 
                if stop_loss is None or not isinstance(stop_loss, (int, float)):
                    continue
                sl_distance = abs(entry_price - stop_loss)
                if sl_distance == 0: continue
                rr_ratio = getattr(strategy_module, 'RR_RATIO', 2.0)
                tp_price = entry_price + (sl_distance * rr_ratio) if signal == "BUY" else entry_price - (sl_distance * rr_ratio)
                volume = getattr(strategy_module, 'VOLUME', 0.1)
                open_position = {
                    'type': signal, 'entry_price': entry_price, 'entry_time': current_candle['time'], 
                    'sl': stop_loss, 'tp': tp_price, 'volume': volume,
                }
    
    log_callback("✅ Simulation complete. Calculating results...")
    mt5.shutdown()

    if not trades:
        return {'status': 'ok', 'message': 'No trades were executed.', 'results': []}

    pnl_list = [t['pnl'] for t in trades]
    wins = [pnl for pnl in pnl_list if pnl > 0]
    losses = [pnl for pnl in pnl_list if pnl < 0]

    total_pnl = sum(pnl_list)
    total_trades = len(trades)
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
    total_return = (total_pnl / initial_balance) * 100
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_trade_pnl = total_pnl / total_trades if total_trades > 0 else 0

    results_data = [
        {"Metric": "Total Return", "Value": f"{total_return:.2f}%", "Insight": "Total percentage gain/loss on initial capital."},
        {"Metric": "Net Profit", "Value": f"${total_pnl:,.2f}", "Insight": "The sum of profits and losses from all trades."},
        {"Metric": "Max Drawdown", "Value": f"{max_drawdown * 100:.2f}%", "Insight": "The largest peak-to-trough drop in portfolio value."},
        {"Metric": "Win Rate", "Value": f"{win_rate:.2f}%", "Insight": "The percentage of trades that were profitable."},
        {"Metric": "Profit Factor", "Value": f"{profit_factor:.2f}", "Insight": "Gross profits / gross losses. >1 is profitable."},
        {"Metric": "Total Trades", "Value": str(total_trades), "Insight": "The total number of trades executed."},
        {"Metric": "Avg P/L", "Value": f"${avg_trade_pnl:,.2f}", "Insight": "The average profit or loss per trade."}
    ]

    return {
        'status': 'ok', 'start_date': start_date.strftime('%Y-%m-%d'), 'end_date': end_date.strftime('%Y-%m-%d'),
        'timeframe': timeframe_str, 'results': results_data
    }