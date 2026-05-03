# main.py
import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox
import threading
import os
import sys
import traceback
from datetime import datetime, timedelta
import importlib.util
import MetaTrader5 as mt5

# Gracefully handle the case where mt5_config.py might not exist
try:
    import mt5_config
except ImportError:
    # Create a dummy config object if the file is missing
    class DummyConfig:
        LOGIN = ""
        PASSWORD = ""
        SERVER = ""
        TERMINAL_PATH = ""
    mt5_config = DummyConfig()

def safe_print(text):
    """Safely print text to avoid UnicodeEncodeError in terminals."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', errors='ignore').decode())

class TradingGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("🚀 Trading Dashboard")
        self.root.geometry("1400x900")
        self.root.configure(bg='#0a0a0a')

        self.colors = {
            'bg_primary': '#0a0a0a', 'bg_secondary': '#1a1a1a', 'bg_accent': '#2a2a2a',
            'text_primary': '#ffffff', 'text_secondary': '#b0b0b0', 'accent_green': '#00ff88',
            'accent_red': '#ff3366', 'accent_blue': '#3366ff', 'accent_yellow': '#ffdd00',
            'accent_orange': '#ff6600', 'gradient_start': '#1a1a2e', 'gradient_end': '#16213e'
        }
        
        # --- Configure Styles ---
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=self.colors['bg_primary'], borderwidth=0)
        style.configure("TNotebook.Tab", background=self.colors['bg_accent'], foreground=self.colors['text_secondary'], padding=[10, 5], font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", self.colors['accent_blue'])], foreground=[("selected", self.colors['text_primary'])])
        style.configure("TFrame", background=self.colors['bg_secondary'])
        style.configure("Treeview", background=self.colors['bg_primary'], foreground=self.colors['text_primary'], fieldbackground=self.colors['bg_primary'], borderwidth=0, rowheight=25)
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"), background=self.colors['bg_accent'], foreground=self.colors['text_primary'], relief="flat")
        style.map('Treeview.Heading', background=[('active', self.colors['accent_blue'])])
        
        # --- Class Variables ---
        self.timeframe_map = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1}
        self.strategy_running = False
        self.strategy_module = None
        self.mt5_connected = False
        self.available_symbols = []

        self.refresh_strategies()
        self.strategy_var = tk.StringVar(value=self.strategies[0] if self.strategies else "")
        self.symbol_var = tk.StringVar()
        self.timeframe_var = tk.StringVar(value="M15")
        self.login_var = tk.StringVar(value=getattr(mt5_config, 'LOGIN', ''))
        self.password_var = tk.StringVar(value=getattr(mt5_config, 'PASSWORD', ''))
        self.server_var = tk.StringVar(value=getattr(mt5_config, 'SERVER', ''))
        self.path_var = tk.StringVar(value=getattr(mt5_config, 'TERMINAL_PATH', ''))

        self.create_header()
        self.create_main_content()
        self.create_footer()

        self.root.after(500, self.connect_to_mt5)

    def create_header(self):
        header_frame = tk.Frame(self.root, bg=self.colors['gradient_start'], height=60)
        header_frame.pack(fill=tk.X, side=tk.TOP)
        header_frame.pack_propagate(False)
        tk.Label(header_frame, text="⚡ ULTRA-FAST TRADING DASHBOARD ⚡", font=("Segoe UI", 20, "bold"), fg=self.colors['accent_yellow'], bg=self.colors['gradient_start']).pack(expand=True, pady=10)

    def refresh_strategies(self):
        try:
            current_script = os.path.basename(sys.argv[0])
            self.strategies = [f for f in os.listdir('.') if f.endswith('.py') and f != current_script and not f.startswith(('backtester', 'mt5_config'))]
        except Exception as e:
            self.strategies = []
            safe_print(f"Could not refresh strategies: {e}")

    def create_main_content(self):
        main_frame = tk.Frame(self.root, bg=self.colors['bg_primary'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        controls_container = tk.Frame(main_frame, bg=self.colors['bg_primary'], width=300)
        controls_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        controls_container.pack_propagate(False)

        # --- Left Panel Widgets ---
        self._create_left_panel(controls_container)

        # --- Right Panel with Tabs ---
        notebook = ttk.Notebook(main_frame, style="TNotebook")
        notebook.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        positions_tab = ttk.Frame(notebook, style="TFrame")
        connection_tab = ttk.Frame(notebook, style="TFrame")
        terminal_tab = ttk.Frame(notebook, style="TFrame")

        notebook.add(positions_tab, text="  Live Positions  ")
        notebook.add(connection_tab, text="  MT5 Connection  ")
        notebook.add(terminal_tab, text="  Terminal  ")

        self._create_positions_tab(positions_tab)
        self._create_connection_tab(connection_tab)
        self._create_terminal_tab(terminal_tab)

        # --- NEW LINE ADDED HERE ---
        # Select the 'Terminal' tab by default (it's the 3rd tab, index 2)
        notebook.select(terminal_tab)

        self.log_message("🚀 Trading Dashboard Initialized. Connecting to MT5...")
    def _create_left_panel(self, parent):
        profit_frame = tk.Frame(parent, bg=self.colors['bg_secondary'], relief='raised', bd=2)
        profit_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(profit_frame, text="💰 LIVE P/L", font=("Segoe UI", 12, "bold"), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).pack(pady=(8, 2))
        self.total_profit_label = tk.Label(profit_frame, text="$0.00", font=("Segoe UI", 22, "bold"), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary'])
        self.total_profit_label.pack(pady=5, padx=15)
        self.position_count_label = tk.Label(profit_frame, text="Positions: 0", font=("Segoe UI", 10), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary'])
        self.position_count_label.pack(pady=(2, 8))

        balance_frame = tk.Frame(parent, bg=self.colors['bg_secondary'], relief='raised', bd=2)
        balance_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(balance_frame, text="🏦 ACCOUNT BALANCE", font=("Segoe UI", 12, "bold"), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).pack(pady=(8, 2))
        self.account_balance_label = tk.Label(balance_frame, text="$--", font=("Segoe UI", 22, "bold"), fg=self.colors['accent_yellow'], bg=self.colors['bg_secondary'])
        self.account_balance_label.pack(pady=(5, 12), padx=15)

        strategy_frame = tk.Frame(parent, bg=self.colors['bg_secondary'], relief='raised', bd=2)
        strategy_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(strategy_frame, text="📋 SELECT STRATEGY", font=("Segoe UI", 14, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack(pady=8)
        dropdown_container = tk.Frame(strategy_frame, bg=self.colors['bg_secondary'])
        dropdown_container.pack(pady=8, padx=10)
        self.main_strategy_dropdown = ttk.Combobox(dropdown_container, textvariable=self.strategy_var, values=self.strategies, width=30, font=("Segoe UI", 10), state="readonly")
        self.main_strategy_dropdown.pack(pady=3)

        control_frame = tk.Frame(parent, bg=self.colors['bg_secondary'], relief='raised', bd=2)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(control_frame, text="⚙️ LIVE CONTROLS", font=("Segoe UI", 14, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack(pady=8)
        params_frame = tk.Frame(control_frame, bg=self.colors['bg_secondary'])
        params_frame.pack(pady=5, padx=10, fill=tk.X)
        tk.Label(params_frame, text="Symbol:", font=("Segoe UI", 10), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).pack(side=tk.LEFT, padx=(0, 5))
        self.symbol_dropdown = ttk.Combobox(params_frame, textvariable=self.symbol_var, font=("Segoe UI", 10), state="disabled", width=10)
        self.symbol_dropdown.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 10))
        tk.Label(params_frame, text="Timeframe:", font=("Segoe UI", 10), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).pack(side=tk.LEFT, padx=(0, 5))
        self.timeframe_dropdown = ttk.Combobox(params_frame, textvariable=self.timeframe_var, values=list(self.timeframe_map.keys()), font=("Segoe UI", 10), state="readonly", width=5)
        self.timeframe_dropdown.pack(side=tk.LEFT)
        button_container = tk.Frame(control_frame, bg=self.colors['bg_secondary'])
        button_container.pack(pady=10, padx=10, fill=tk.X)
        self.start_button = tk.Button(button_container, text="▶️ START LIVE", bg=self.colors['accent_green'], fg='black', font=("Segoe UI", 12, "bold"), relief='flat', pady=10, command=self.start_strategy, state="disabled")
        self.start_button.pack(pady=5, fill=tk.X)
        self.stop_button = tk.Button(button_container, text="⏹️ STOP LIVE", bg=self.colors['accent_red'], fg='white', font=("Segoe UI", 12, "bold"), relief='flat', pady=10, state="disabled", command=self.stop_strategy)
        self.stop_button.pack(pady=5, fill=tk.X)
        self.backtest_button = tk.Button(button_container, text="🔬 BACKTEST", bg=self.colors['accent_blue'], fg='white', font=("Segoe UI", 12, "bold"), relief='flat', pady=10, command=self.open_backtest_window)
        self.backtest_button.pack(pady=5, fill=tk.X)
        self.close_positions_button = tk.Button(button_container, text="🚪 CLOSE ALL POSITIONS", bg=self.colors['accent_orange'], fg='black', font=("Segoe UI", 10, "bold"), relief='flat', pady=8, command=self.close_all_positions, state="disabled")
        self.close_positions_button.pack(pady=(10, 5), fill=tk.X)

        status_frame = tk.Frame(parent, bg=self.colors['bg_secondary'], relief='raised', bd=2)
        status_frame.pack(fill=tk.X)
        tk.Label(status_frame, text="📊 STATUS", font=("Segoe UI", 14, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack(pady=8)
        self.connection_label = tk.Label(status_frame, text="🔴 Not Connected", font=("Segoe UI", 10, "bold"), fg=self.colors['accent_red'], bg=self.colors['bg_secondary'])
        self.connection_label.pack(pady=2)
        self.status_label = tk.Label(status_frame, text="🔴 STRATEGY STOPPED", font=("Segoe UI", 12, "bold"), fg=self.colors['accent_red'], bg=self.colors['bg_secondary'])
        self.status_label.pack(pady=(0, 8))

    def _create_connection_tab(self, parent_frame):
        container = tk.Frame(parent_frame, bg=self.colors['bg_secondary'], padx=30, pady=20)
        container.pack(expand=True, fill='both')
        tk.Label(container, text="MT5 Connection Details", font=("Segoe UI", 16, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack(pady=(0, 20))
        form_frame = tk.Frame(container, bg=self.colors['bg_secondary'])
        form_frame.pack(pady=10)
        labels = ["Login ID:", "Password:", "Server:", "Terminal Path:"]
        variables = [self.login_var, self.password_var, self.server_var, self.path_var]
        for i, text in enumerate(labels):
            tk.Label(form_frame, text=text, font=("Segoe UI", 10), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).grid(row=i, column=0, sticky='w', pady=6, padx=5)
            entry = tk.Entry(form_frame, textvariable=variables[i], width=50, font=("Segoe UI", 10), bg=self.colors['bg_primary'], fg=self.colors['text_primary'], insertbackground=self.colors['text_primary'])
            if "Password" in text: entry.config(show="*")
            entry.grid(row=i, column=1, sticky='ew', pady=6, padx=5)
        self.connect_button_tab = tk.Button(container, text="🔗 Connect to MT5", command=self.connect_to_mt5, bg=self.colors['accent_blue'], fg='white', relief='flat', font=("Segoe UI", 12, "bold"), pady=10)
        self.connect_button_tab.pack(pady=20, fill=tk.X, padx=10)
        self.connection_status_tab_label = tk.Label(container, text="", font=("Segoe UI", 10), fg=self.colors['accent_yellow'], bg=self.colors['bg_secondary'])
        self.connection_status_tab_label.pack(pady=10)

    def _create_positions_tab(self, parent_frame):
        container = tk.Frame(parent_frame, bg=self.colors['bg_secondary'])
        container.pack(expand=True, fill='both', padx=10, pady=10)
        columns = ('ticket', 'symbol', 'type', 'volume', 'open_price', 'sl', 'tp', 'pnl')
        self.positions_tree = ttk.Treeview(container, columns=columns, show='headings', style="Treeview")
        headings = {'ticket': 'Ticket', 'symbol': 'Symbol', 'type': 'Type', 'volume': 'Volume', 'open_price': 'Open Price', 'sl': 'S/L', 'tp': 'T/P', 'pnl': 'P/L ($)'}
        widths = {'ticket': 80, 'symbol': 100, 'type': 60, 'volume': 60, 'open_price': 100, 'sl': 100, 'tp': 100, 'pnl': 80}
        for col, text in headings.items(): self.positions_tree.heading(col, text=text)
        for col, width in widths.items(): self.positions_tree.column(col, width=width, anchor='e')
        self.positions_tree.column('symbol', anchor='w')
        self.positions_tree.column('type', anchor='center')
        self.positions_tree.tag_configure('profit', foreground=self.colors['accent_green'])
        self.positions_tree.tag_configure('loss', foreground=self.colors['accent_red'])
        vsb = ttk.Scrollbar(container, orient="vertical", command=self.positions_tree.yview)
        hsb = ttk.Scrollbar(container, orient="horizontal", command=self.positions_tree.xview)
        self.positions_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        self.positions_tree.pack(side='left', fill='both', expand=True)

    def _create_terminal_tab(self, parent_frame):
        terminal_header = tk.Frame(parent_frame, bg=self.colors['bg_secondary'])
        terminal_header.pack(fill=tk.X, pady=5, padx=10)
        tk.Label(terminal_header, text="💻 TERMINAL OUTPUT", font=("Segoe UI", 16, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack(side=tk.LEFT, padx=5)
        log_container = tk.Frame(parent_frame, bg=self.colors['bg_secondary'])
        log_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_box = scrolledtext.ScrolledText(log_container, font=("Consolas", 12), bg=self.colors['bg_primary'], fg=self.colors['accent_green'], insertbackground=self.colors['accent_green'], selectbackground=self.colors['bg_accent'], border=2, relief='sunken', wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, expand=True)
    
    def create_footer(self):
        footer_frame = tk.Frame(self.root, bg=self.colors['gradient_end'], height=30)
        footer_frame.pack(fill=tk.X, side=tk.BOTTOM)
        footer_frame.pack_propagate(False)
        tk.Label(footer_frame, text=f"⚡ Trading System Active | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", font=("Segoe UI", 9), fg=self.colors['text_secondary'], bg=self.colors['gradient_end']).pack(expand=True, pady=5)

    def log_message(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_message = f"[{timestamp}] {message}"
        self.root.after(0, self._update_log, formatted_message)

    def _update_log(self, message):
        self.log_box.insert(tk.END, f"{message}\n")
        self.log_box.see(tk.END)

    def load_strategy_module(self, strategy_file):
        try:
            module_name = strategy_file.replace('.py', '')
            spec = importlib.util.spec_from_file_location(module_name, strategy_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as e:
            self.log_message(f"❌ Error loading strategy module: {e}")
            return None

    def _update_balance_display(self):
        if self.mt5_connected:
            try:
                account_info = mt5.account_info()
                if account_info:
                    self.account_balance_label.config(text=f"${account_info.balance:,.2f}")
            except Exception as e:
                self.log_message(f"Could not update balance: {e}")
        self.root.after(5000, self._update_balance_display)

    def _update_live_positions(self):
        total_profit, position_count = 0.0, 0
        if self.mt5_connected:
            try:
                positions = mt5.positions_get() or []
                position_count = len(positions)
                
                # Efficient update: store current tickets and update/add/remove
                current_tickets_in_tree = {self.positions_tree.item(item, "values")[0] for item in self.positions_tree.get_children()}
                position_tickets = {str(pos.ticket) for pos in positions}
                
                # Remove closed positions
                for ticket_str in current_tickets_in_tree - position_tickets:
                    for item in self.positions_tree.get_children():
                        if self.positions_tree.item(item, "values")[0] == ticket_str:
                            self.positions_tree.delete(item)
                            break

                for pos in positions:
                    total_profit += pos.profit
                    pos_type = "BUY" if pos.type == 0 else "SELL"
                    tag = 'profit' if pos.profit >= 0 else 'loss'
                    values = (str(pos.ticket), pos.symbol, pos_type, pos.volume, f"{pos.price_open:.5f}", f"{pos.sl:.5f}", f"{pos.tp:.5f}", f"{pos.profit:,.2f}")
                    
                    # Add new or update existing positions
                    found = False
                    for item in self.positions_tree.get_children():
                        if self.positions_tree.item(item, "values")[0] == str(pos.ticket):
                            self.positions_tree.item(item, values=values, tags=(tag,))
                            found = True
                            break
                    if not found:
                        self.positions_tree.insert("", "end", values=values, tags=(tag,))

            except Exception as e:
                self.log_message(f"Error updating live positions: {e}")
        
        color = self.colors['accent_green' if total_profit >= 0 else 'accent_red']
        self.total_profit_label.config(text=f"${total_profit:,.2f}", fg=color if position_count > 0 else self.colors['text_secondary'])
        self.position_count_label.config(text=f"Positions: {position_count}")
        self.root.after(1000, self._update_live_positions)

    def connect_to_mt5(self):
        self.log_message("Attempting to connect to MetaTrader 5...")
        self.connect_button_tab.config(state="disabled", text="Connecting...")
        self.connection_status_tab_label.config(text="Attempting to connect...")
        
        try:
            login, password, server, path = int(self.login_var.get()), self.password_var.get(), self.server_var.get(), self.path_var.get()
        except ValueError:
            messagebox.showerror("Input Error", "Login ID must be a number.")
            self.connect_button_tab.config(state="normal", text="🔗 Connect to MT5")
            return

        def connection_thread():
            if not mt5.initialize(path=path, login=login, password=password, server=server):
                self.root.after(0, self.update_connection_status, False, f"Initialization failed. Error: {mt5.last_error()}")
                return
            account_info = mt5.account_info()
            if not account_info:
                self.root.after(0, self.update_connection_status, False, "Login failed. Check credentials.")
                mt5.shutdown()
                return
            self.root.after(0, self.update_connection_status, True, f"Connected: Account #{account_info.login}")
            self.root.after(0, self.populate_symbols)
        threading.Thread(target=connection_thread, daemon=True).start()

    def update_connection_status(self, success, message):
        self.log_message(message)
        if success:
            self.mt5_connected = True
            self.connection_label.config(text=f"✅ Connected", fg=self.colors['accent_green'])
            self.connection_status_tab_label.config(text=message, fg=self.colors['accent_green'])
            self.connect_button_tab.config(text="✅ Connected", state="disabled")
            self.start_button.config(state="normal")
            self.close_positions_button.config(state="normal")
            self._update_balance_display()
            self._update_live_positions()
        else:
            self.mt5_connected = False
            self.connection_label.config(text=f"🔴 Not Connected", fg=self.colors['accent_red'])
            self.connection_status_tab_label.config(text=f"Connection Failed: {message}", fg=self.colors['accent_red'])
            self.connect_button_tab.config(state="normal", text="🔗 Reconnect to MT5")
            messagebox.showerror("Connection Failed", message)

    def populate_symbols(self):
        symbols = mt5.symbols_get()
        if symbols:
            self.available_symbols = sorted([s.name for s in symbols])
            self.symbol_dropdown.config(values=self.available_symbols, state="normal")
            if "EURUSD" in self.available_symbols: self.symbol_var.set("EURUSD")
            elif self.available_symbols: self.symbol_var.set(self.available_symbols[0])
            self.log_message(f"Found {len(self.available_symbols)} symbols.")
        else:
            self.available_symbols = []
            self.log_message("Could not retrieve symbols from MT5.")
    
    def disconnect_from_mt5(self):
        if self.mt5_connected:
            mt5.shutdown()
            self.mt5_connected = False
            self.log_message("Disconnected from MetaTrader 5.")

    def open_backtest_window(self):
        if not self.mt5_connected: messagebox.showerror("Not Connected", "Please connect to MT5 first."); return
        strategy_file = self.strategy_var.get()
        if not strategy_file: messagebox.showerror("Error", "Please select a strategy!"); return
        
        self.backtest_window = tk.Toplevel(self.root)
        self.backtest_window.title(f"🔬 Backtest Settings for {strategy_file}")
        self.backtest_window.geometry("400x400")
        self.backtest_window.configure(bg=self.colors['bg_secondary'])
        self.backtest_window.transient(self.root)
        
        tk.Label(self.backtest_window, text="Backtest Parameters", font=("Segoe UI", 16, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack(pady=15)
        param_frame = tk.Frame(self.backtest_window, bg=self.colors['bg_secondary'])
        param_frame.pack(pady=10, padx=20, fill=tk.X)
        
        labels = ["Symbol:", "Initial Balance ($):", "Timeframe:", "Start Date (YYYY-MM-DD):", "End Date (YYYY-MM-DD):"]
        self.backtest_symbol_var = tk.StringVar()
        self.balance_var = tk.StringVar(value="10000")
        self.timeframe_var_backtest = tk.StringVar(value="H1")
        self.start_date_var = tk.StringVar(value=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
        self.end_date_var = tk.StringVar(value=datetime.now().strftime('%Y-%m-%d'))
        variables = [self.backtest_symbol_var, self.balance_var, self.timeframe_var_backtest, self.start_date_var, self.end_date_var]
        
        for i, text in enumerate(labels):
            tk.Label(param_frame, text=text, font=("Segoe UI", 10), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).grid(row=i, column=0, sticky='w', pady=5)
            if i == 0: # Symbol
                widget = ttk.Combobox(param_frame, textvariable=variables[i], values=self.available_symbols, width=18, font=("Segoe UI", 10))
                if "EURUSD" in self.available_symbols: variables[i].set("EURUSD")
            elif i == 2: # Timeframe
                widget = ttk.Combobox(param_frame, textvariable=variables[i], values=list(self.timeframe_map.keys()), width=18, font=("Segoe UI", 10), state="readonly")
            else: # Other entries
                widget = tk.Entry(param_frame, textvariable=variables[i], width=20, font=("Segoe UI", 10))
            widget.grid(row=i, column=1, sticky='e', pady=5)
            
        run_btn = tk.Button(self.backtest_window, text="🚀 RUN BACKTEST", command=self.start_backtest, font=("Segoe UI", 12, "bold"), bg=self.colors['accent_green'], fg='black', relief='flat', pady=10)
        run_btn.pack(pady=20, padx=20, fill=tk.X)
        self.backtest_status_label = tk.Label(self.backtest_window, text="Ready to start...", font=("Segoe UI", 10), fg=self.colors['accent_yellow'], bg=self.colors['bg_secondary'])
        self.backtest_status_label.pack(pady=10)

    def start_backtest(self):
        strategy_file = self.strategy_var.get()
        try:
            symbol, initial_balance = self.backtest_symbol_var.get(), float(self.balance_var.get())
            start_date, end_date = datetime.strptime(self.start_date_var.get(), '%Y-%m-%d'), datetime.strptime(self.end_date_var.get(), '%Y-%m-%d')
            timeframe_str, timeframe = self.timeframe_var_backtest.get(), self.timeframe_map[self.timeframe_var_backtest.get()]
            if not symbol or end_date <= start_date:
                messagebox.showerror("Input Error", "Invalid symbol or date range.")
                return
        except Exception as e:
            messagebox.showerror("Input Error", f"Invalid input format: {e}")
            return
        
        self.log_message(f"🔬 Starting backtest for '{strategy_file}' on {symbol}...")
        self.backtest_status_label.config(text="⚙️ Running... Please wait.")
        try:
            backtester_spec = importlib.util.spec_from_file_location("backtester", "backtester.py")
            backtester_module = importlib.util.module_from_spec(backtester_spec)
            backtester_spec.loader.exec_module(backtester_module)
        except FileNotFoundError:
             messagebox.showerror("Error", "backtester.py file not found!")
             self.backtest_status_label.config(text="Error: backtester.py not found.")
             return
        
        def run_backtest_thread():
            strategy_module = self.load_strategy_module(strategy_file)
            if not strategy_module:
                self.root.after(0, self.backtest_status_label.config, {"text": "Error: Failed to load strategy."})
                return
            results = backtester_module.run_backtest(strategy_module, symbol, start_date, end_date, initial_balance, timeframe, timeframe_str, self.log_message)
            self.root.after(0, self.display_backtest_results, results)
        threading.Thread(target=run_backtest_thread, daemon=True).start()

    def display_backtest_results(self, results):
        if hasattr(self, 'backtest_window') and self.backtest_window.winfo_exists():
            self.backtest_window.destroy()
        if not results or results.get('status') == 'error':
            error_msg = results.get('message', 'An unknown error occurred.')
            self.log_message(f"❌ Backtest failed: {error_msg}")
            messagebox.showerror("Backtest Failed", error_msg)
            return
        
        self.log_message("✅ Backtest complete! Displaying results.")
        results_list = results.get('results', [])
        if not results_list:
            messagebox.showinfo("Backtest Info", results.get('message', "No trades were executed."))
            return

        results_window = tk.Toplevel(self.root)
        results_window.title("📊 Backtest Results")
        results_window.geometry("600x500")
        results_window.configure(bg=self.colors['bg_secondary'])
        
        summary_frame = tk.Frame(results_window, bg=self.colors['bg_secondary'], pady=10)
        summary_frame.pack(fill=tk.X)
        tk.Label(summary_frame, text="Backtest Performance Summary", font=("Segoe UI", 16, "bold"), fg=self.colors['text_primary'], bg=self.colors['bg_secondary']).pack()
        tk.Label(summary_frame, text=f"Period: {results.get('start_date', 'N/A')} to {results.get('end_date', 'N/A')} | Timeframe: {results.get('timeframe', 'N/A')}", font=("Segoe UI", 10), fg=self.colors['text_secondary'], bg=self.colors['bg_secondary']).pack()
        
        tree_frame = tk.Frame(results_window)
        tree_frame.pack(expand=True, fill='both', padx=20, pady=10)
        tree = ttk.Treeview(tree_frame, columns=('Metric', 'Value', 'Insight'), show='headings')
        tree.heading('Metric', text='Metric'); tree.heading('Value', text='Value'); tree.heading('Insight', text='Insight')
        tree.column('Metric', anchor='w', width=120); tree.column('Value', anchor='center', width=100); tree.column('Insight', anchor='w', width=350)
        for item in results_list:
            tree.insert('', 'end', values=(item['Metric'], item['Value'], item['Insight']))
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        button_frame = tk.Frame(results_window, bg=self.colors['bg_secondary'], pady=10)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)
        new_backtest_btn = tk.Button(button_frame, text="🔄 Run New Backtest", font=("Segoe UI", 12, "bold"), bg=self.colors['accent_blue'], fg='white', relief='flat', pady=5, command=lambda: self.run_new_backtest_from_results(results_window))
        new_backtest_btn.pack(padx=20)

    def run_new_backtest_from_results(self, results_window):
        results_window.destroy()
        self.open_backtest_window()

    def start_strategy(self):
        if self.strategy_running: return
        if not self.mt5_connected: messagebox.showerror("Error", "Not connected to MetaTrader 5."); return
        
        strategy_file, symbol, timeframe_str = self.strategy_var.get(), self.symbol_var.get(), self.timeframe_var.get()
        if not all([strategy_file, symbol, timeframe_str]):
            messagebox.showerror("Error", "Please select a strategy, symbol, and timeframe!"); return
            
        timeframe = self.timeframe_map[timeframe_str]
        self.strategy_module = self.load_strategy_module(strategy_file)
        if not (self.strategy_module and hasattr(self.strategy_module, 'run_strategy') and hasattr(self.strategy_module, 'stop_strategy')):
            messagebox.showerror("Error", "Invalid strategy file. Missing required functions."); return

        self.strategy_running = True
        self.start_button.config(state="disabled"); self.stop_button.config(state="normal"); self.status_label.config(text="🟢 STRATEGY RUNNING", fg=self.colors['accent_green'])
        self.log_message(f"🚀 Starting strategy '{strategy_file}' on {symbol} with timeframe {timeframe_str}")
        
        def run_strategy_thread():
            try:
                success = self.strategy_module.run_strategy(symbol, timeframe, log_callback=self.log_message)
                if not success:
                    self.log_message("❌ Strategy failed to start")
                    self.root.after(0, self.stop_strategy, False)
            except Exception as e:
                self.log_message(f"❌ Error running strategy: {e}\n{traceback.format_exc()}")
                self.root.after(0, self.stop_strategy, False)
        threading.Thread(target=run_strategy_thread, daemon=True).start()

    def stop_strategy(self, from_gui=True):
        if not self.strategy_running and from_gui: return
        self.log_message("🛑 Stopping strategy...")
        try:
            if self.strategy_module and hasattr(self.strategy_module, 'stop_strategy'):
                self.strategy_module.stop_strategy(self.log_message)
        except Exception as e:
            self.log_message(f"❌ Error sending stop signal: {e}")
        self.strategy_running = False
        self.start_button.config(state="normal"); self.stop_button.config(state="disabled"); self.status_label.config(text="🔴 STRATEGY STOPPED", fg=self.colors['accent_red'])

    def close_all_positions(self):
        if not self.mt5_connected: messagebox.showwarning("Warning", "Not connected to MT5."); return
        symbol = self.symbol_var.get()
        self.log_message(f"🚪 Closing all positions for {symbol if symbol else 'all symbols'}...")
        threading.Thread(target=self._execute_close_all, args=(symbol or None,), daemon=True).start()

    def _execute_close_all(self, symbol=None):
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            self.log_message(f"No open positions found for {symbol if symbol else 'any symbol'}.")
            return
        closed_count = 0
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                self.log_message(f"Could not get tick for {pos.symbol} to close position #{pos.ticket}")
                continue
            price = tick.ask if pos.type == 1 else tick.bid # pos.type 1 is SELL
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "position": pos.ticket, "symbol": pos.symbol, "volume": pos.volume, 
                "type": mt5.ORDER_TYPE_BUY if pos.type == 1 else mt5.ORDER_TYPE_SELL, "price": price, "deviation": 20, 
                "magic": pos.magic, "comment": "Closed by Dashboard", "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1
                self.log_message(f"Closed position #{pos.ticket} on {pos.symbol}.")
            else:
                self.log_message(f"Failed to close position #{pos.ticket}. Error: {result.comment}")
        self.log_message(f"✅ Finished closing positions. Total closed: {closed_count}.")

    def run(self):
        def on_closing():
            if self.strategy_running:
                if messagebox.askyesno("Confirm Exit", "A strategy is running. Are you sure you want to stop it and exit?"):
                    self.stop_strategy(from_gui=False)
                    self.disconnect_from_mt5()
                    self.root.destroy()
            else:
                self.disconnect_from_mt5()
                self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        self.root.mainloop()

def main():
    try:
        safe_print("🚀 Starting Trading Strategy GUI...")
        app = TradingGUI()
        app.run()
    except Exception as e:
        safe_print(f"❌ A fatal error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()