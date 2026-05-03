# Runs a historical simulation based on the provided strategy file.

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime
import os

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
    trade_number = 1

    for i in range(1, len(df)):
        current_candle = df.iloc[i]
        
        if open_position:
            pnl = 0
            close_reason = None
            if open_position['type'] == 'BUY':
                if current_candle['low'] <= open_position['sl']:
                    pnl = (open_position['sl'] - open_position['entry_price'])
                    close_reason = "Stop Loss"
                elif current_candle['high'] >= open_position['tp']:
                    pnl = (open_position['tp'] - open_position['entry_price'])
                    close_reason = "Take Profit"
            elif open_position['type'] == 'SELL':
                if current_candle['high'] >= open_position['sl']:
                    pnl = (open_position['entry_price'] - open_position['sl'])
                    close_reason = "Stop Loss"
                elif current_candle['low'] <= open_position['tp']:
                    pnl = (open_position['entry_price'] - open_position['tp'])
                    close_reason = "Take Profit"

            if close_reason:
                symbol_info = mt5.symbol_info(symbol)
                contract_size = symbol_info.trade_contract_size if symbol_info else 1
                final_pnl = pnl * open_position['volume'] * contract_size
                balance += final_pnl
                
                # Calculate balance after trade
                balance_after = balance
                
                open_position['trade_number'] = trade_number
                open_position['pnl'] = final_pnl
                open_position['pnl_pips'] = pnl
                open_position['exit_price'] = open_position['sl'] if close_reason == "Stop Loss" else open_position['tp']
                open_position['exit_time'] = current_candle['time']
                open_position['closed_by'] = close_reason
                open_position['balance_after'] = balance_after
                open_position['return_pct'] = (final_pnl / initial_balance) * 100
                
                trades.append(open_position)
                trade_number += 1
                open_position = None
                
                drawdown = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                max_drawdown = max(max_drawdown, drawdown)
                peak_balance = max(balance, peak_balance)
        
        if not open_position:
            historical_slice = df.iloc[:i+1]
            signal, stop_loss = strategy_module.get_signal(historical_slice)
            
            if signal != "WAIT":
                entry_price = current_candle['close'] 
                if stop_loss is None or not isinstance(stop_loss, (int, float)):
                    continue
                sl_distance = abs(entry_price - stop_loss)
                if sl_distance == 0: continue
                rr_ratio = getattr(strategy_module, 'RR_RATIO', 2.0)
                tp_price = entry_price + (sl_distance * rr_ratio) if signal == "BUY" else entry_price - (sl_distance * rr_ratio)
                volume = getattr(strategy_module, 'VOLUME', 0.1)
                open_position = {
                    'type': signal, 
                    'entry_price': entry_price, 
                    'entry_time': current_candle['time'], 
                    'sl': stop_loss, 
                    'tp': tp_price, 
                    'volume': volume,
                    'sl_distance_pips': sl_distance,
                    'rr_ratio': rr_ratio
                }
    
    log_callback("✅ Simulation complete. Calculating results...")
    mt5.shutdown()

    if not trades:
        return {'status': 'ok', 'message': 'No trades were executed.', 'results': []}

    # Create 'results' folder if it doesn't exist
    log_callback("📁 Creating results folder...")
    results_folder = "results"
    
    try:
        # Create results folder in current working directory
        if not os.path.exists(results_folder):
            os.makedirs(results_folder)
            log_callback(f"✅ Created new folder: {results_folder}")
        else:
            log_callback(f"✅ Using existing folder: {results_folder}")
    except Exception as e:
        log_callback(f"⚠️ Could not create results folder: {e}")
        results_folder = "."  # Fallback to current directory

    # Export trades to Excel in results folder
    log_callback("📊 Exporting trade details to Excel...")
    excel_filename = None
    
    try:
        # Create trades DataFrame
        trades_df = pd.DataFrame(trades)
        
        # Reorder and format columns for better readability
        column_order = [
            'trade_number', 'type', 'entry_time', 'entry_price', 
            'exit_time', 'exit_price', 'sl', 'tp', 'volume',
            'sl_distance_pips', 'rr_ratio', 'pnl_pips', 'pnl', 
            'return_pct', 'balance_after', 'closed_by'
        ]
        
        trades_df = trades_df[column_order]
        
        # Rename columns for better readability in Excel
        trades_df.columns = [
            'Trade #', 'Type', 'Entry Time', 'Entry Price', 
            'Exit Time', 'Exit Price', 'Stop Loss', 'Take Profit', 'Volume',
            'SL Distance (Pips)', 'RR Ratio', 'P/L (Pips)', 'P/L ($)', 
            'Return %', 'Balance After', 'Closed By'
        ]
        
        # Create timestamped filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        excel_filename = f'backtest_{symbol}_{timeframe_str}_{timestamp}.xlsx'
        
        # Full path in results folder
        full_path = os.path.join(results_folder, excel_filename)
        full_path = os.path.abspath(full_path)
        
        log_callback(f"📂 Saving to: {full_path}")
        
        # Method 1: Try with xlsxwriter (recommended for new files)
        try:
            with pd.ExcelWriter(full_path, engine='xlsxwriter') as writer:
                trades_df.to_excel(writer, sheet_name='Trade Details', index=False)
                
                # Get workbook and worksheet objects
                workbook = writer.book
                worksheet = writer.sheets['Trade Details']
                
                # Add formatting
                money_format = workbook.add_format({'num_format': '$#,##0.00'})
                percent_format = workbook.add_format({'num_format': '0.00%'})
                date_format = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss'})
                header_format = workbook.add_format({
                    'bold': True, 
                    'bg_color': '#4472C4',
                    'font_color': 'white',
                    'border': 1
                })
                
                # Format header row
                for col_num, value in enumerate(trades_df.columns.values):
                    worksheet.write(0, col_num, value, header_format)
                
                # Apply column widths
                worksheet.set_column('A:A', 10)  # Trade #
                worksheet.set_column('B:B', 8)   # Type
                worksheet.set_column('C:C', 20)  # Entry Time
                worksheet.set_column('D:D', 12)  # Entry Price
                worksheet.set_column('E:E', 20)  # Exit Time
                worksheet.set_column('F:F', 12)  # Exit Price
                worksheet.set_column('G:G', 12)  # Stop Loss
                worksheet.set_column('H:H', 12)  # Take Profit
                worksheet.set_column('I:I', 10)  # Volume
                worksheet.set_column('J:J', 18)  # SL Distance
                worksheet.set_column('K:K', 10)  # RR Ratio
                worksheet.set_column('L:L', 12)  # P/L Pips
                worksheet.set_column('M:M', 14)  # P/L $
                worksheet.set_column('N:N', 12)  # Return %
                worksheet.set_column('O:O', 15)  # Balance After
                worksheet.set_column('P:P', 15)  # Closed By
             
            log_callback(f"✅ Excel file saved successfully!")
            log_callback(f"📍 Location: {full_path}")
            
        except Exception as e1:
            # Method 2: Fallback to openpyxl
            log_callback(f"⚠️ xlsxwriter failed, trying openpyxl... ({str(e1)})")
            try:
                with pd.ExcelWriter(full_path, engine='openpyxl') as writer:
                    trades_df.to_excel(writer, sheet_name='Trade Details', index=False)
                log_callback(f"✅ Excel file saved with openpyxl!")
                log_callback(f"📍 Location: {full_path}")
            except Exception as e2:
                # Method 3: Simple fallback without formatting
                log_callback(f"⚠️ openpyxl failed, trying basic export... ({str(e2)})")
                trades_df.to_excel(full_path, sheet_name='Trade Details', index=False)
                log_callback(f"✅ Excel file saved (basic format)!")
                log_callback(f"📍 Location: {full_path}")
        
        # Verify file was created
        if os.path.exists(full_path):
            file_size = os.path.getsize(full_path)
            log_callback(f"✅ File verified: {file_size} bytes")
        else:
            log_callback(f"⚠️ Warning: File may not have been created")
            
    except Exception as e:
        log_callback(f"❌ Failed to export to Excel: {str(e)}")
        log_callback(f"Error type: {type(e).__name__}")
        import traceback
        log_callback(f"Traceback: {traceback.format_exc()}")
        full_path = None

    # Calculate metrics
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
    
    if full_path:
        results_data.append({
            "Metric": "Excel File", 
            "Value": excel_filename, 
            "Insight": f"Saved in: {os.path.dirname(full_path)}"
        })

    return {
        'status': 'ok', 
        'start_date': start_date.strftime('%Y-%m-%d'), 
        'end_date': end_date.strftime('%Y-%m-%d'),
        'timeframe': timeframe_str, 
        'results': results_data,
        'excel_file': full_path
    }
