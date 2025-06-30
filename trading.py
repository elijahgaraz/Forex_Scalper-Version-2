import threading
import random
import time
import os # Added for path operations
from typing import List

# Try to import QuickFIX and verify necessary classes, otherwise fallback to stubs
try:
    import quickfix as fix
    # Check for necessary FIX classes
    if not hasattr(fix, 'SessionSettings') or not hasattr(fix, 'SocketInitiator') or not hasattr(fix, 'Application'):
        raise ImportError("QuickFIX installation missing required classes")
    USE_QUICKFIX = True
except ImportError:
    USE_QUICKFIX = False
    print("QuickFIX not available or incomplete: running in stub mode. Install python-quickfix correctly for live FIX connectivity.")

class Trader:
    def __init__(self, settings, history_size: int = 100):
        self.settings = settings
        self.is_connected = False
        self._last_error = ""
        self.price_history: List[float] = []
        self.history_size = history_size
        if USE_QUICKFIX:
            self.application = None
            self.store_factory = None
            self.log_factory = None
            self.settings_file = None
            self.initiator = None
            self._logon_event = threading.Event()
            self._logout_event = threading.Event()

    def connect(self) -> bool:
        if not USE_QUICKFIX:
            self.is_connected = True
            return True
        try:
            # Ensure fix_config_path from settings is used.
            # The settings object is self.settings.quote_fix for quote connection related settings
            fix_config_file_path = self.settings.quote_fix.config_path
            if not os.path.exists(fix_config_file_path):
                self._last_error = f"FIX configuration file not found: {fix_config_file_path}"
                print(self._last_error)
                return False

            self.settings_file = fix.SessionSettings(fix_config_file_path)

            # Override password if provided in settings (from env var)
            if self.settings.quote_fix.password:
                # Iterate over all session IDs in the settings file
                sids = self.settings_file.getSessions()
                for sid in sids:
                    session_dict = self.settings_file.get(sid)
                    session_dict.setString("Password", self.settings.quote_fix.password)
                    # Potentially remove if it was an empty string from file?
                    # Or QuickFIX handles empty Password field appropriately if not set.
                print("Overridden FIX Password from settings/environment variable.")

            # Check for DataDictionary, FileStorePath, FileLogPath
            # These paths are typically relative to where the application is run,
            # or absolute if specified in quickfix.cfg.
            # QuickFIX itself will error if these are problematic, but early checks can be useful.

            default_settings = self.settings_file.get() # Get default settings section

            data_dictionary_path = default_settings.getString("DataDictionary")
            if data_dictionary_path and not os.path.exists(data_dictionary_path):
                 # Try to resolve relative to config file path
                if not os.path.isabs(data_dictionary_path):
                    config_dir = os.path.dirname(os.path.abspath(fix_config_file_path))
                    potential_path = os.path.join(config_dir, data_dictionary_path)
                    if os.path.exists(potential_path):
                         # If found, QuickFIX might need it to be relative or absolute depending on its CWD.
                         # For now, just warning. A better fix would be to make path absolute for QuickFIX.
                         print(f"Warning: DataDictionary '{data_dictionary_path}' found relative to config file at '{potential_path}'. Ensure QuickFIX CWD is correct or use absolute paths in config.")
                    else:
                        self._last_error = f"DataDictionary file '{data_dictionary_path}' (from quickfix.cfg) not found."
                        print(self._last_error)
                        # return False # This can be a critical error.

            for path_key in ["FileStorePath", "FileLogPath"]:
                path_value = default_settings.getString(path_key)
                if path_value:
                    # If path is relative, it's relative to CWD QuickFIX runs in.
                    # Create if it doesn't exist.
                    if not os.path.isabs(path_value) and not os.path.exists(path_value):
                        try:
                            os.makedirs(path_value, exist_ok=True)
                            print(f"Created directory for {path_key}: {path_value}")
                        except Exception as e:
                            self._last_error = f"Failed to create directory for {path_key} at '{path_value}': {e}"
                            print(self._last_error)
                            # return False # This can be a critical error.
                else:
                    print(f"Warning: {path_key} is not defined in quickfix.cfg [DEFAULT] section.")


            self.application = Application(self)
            self.store_factory = fix.FileStoreFactory(self.settings_file)
            self.log_factory = fix.FileLogFactory(self.settings_file)

            # Ensure logon and logout events are reset before attempting to connect
            self._logon_event.clear()
            self._logout_event.clear()

            self.initiator = fix.SocketInitiator(
                self.application,
                self.store_factory,
                self.settings_file,
                self.log_factory
            )
            self.initiator.start()

            # Wait for logon event to be set by onLogon callback, with a timeout
            # The timeout value (e.g., 10 seconds) should be configurable or generous
            logon_success = self._logon_event.wait(timeout=10.0)

            if logon_success:
                self.is_connected = True
                self._last_error = ""
                print("FIX session successfully logged on.")
                return True
            else:
                # If logon event timed out, check if logout event was triggered (e.g. immediate disconnect)
                if self._logout_event.is_set():
                    # _last_error might have been set in onLogout or by other means
                    if not self._last_error:
                         self._last_error = "Logout occurred during connection attempt."
                else:
                    self._last_error = "Logon attempt timed out."

                print(f"FIX Logon failed: {self._last_error}")
                # Attempt to stop the initiator if it started but didn't log on
                if self.initiator:
                    self.initiator.stop()
                self.is_connected = False
                return False

        except fix.ConfigError as e:
            self.is_connected = False
            self._last_error = f"FIX Configuration Error: {e}"
            print(self._last_error)
            return False
        except Exception as e:
            self.is_connected = False
            self._last_error = f"FIX Connection Error: {str(e)}"
            print(self._last_error)
            # Ensure initiator is stopped if an exception occurs after it's created
            if hasattr(self, 'initiator') and self.initiator:
                try:
                    self.initiator.stop()
                except Exception as stop_e:
                    print(f"Error stopping initiator: {stop_e}")
            return False

    def get_connection_status(self):
        # Update connection status based on FIX session status if available
        if USE_QUICKFIX and self.initiator:
            try:
                # This part is tricky because SocketInitiator itself doesn't directly expose isLoggedOn.
                # We rely on onLogon/onLogout callbacks to set self.is_connected.
                # However, we can check if any session is logged on as a fallback,
                # though our primary mechanism is _logon_event and _logout_event.
                any_session_logged_on = False
                if fix.Session.getSessions(): # Check if there are any sessions
                    for sid in fix.Session.getSessions():
                        session = fix.Session.lookupSession(sid)
                        if session and session.isLoggedOn():
                            any_session_logged_on = True
                            break
                    # If our internal flag self.is_connected (driven by onLogon/onLogout callbacks)
                    # disagrees with direct session state check, the callback-driven state is primary.
                    # This block is more for deeper diagnostics if needed.
                    # For example, if any_session_logged_on is true but self.is_connected is false,
                    # it might indicate a missed onLogon or premature onLogout call.
                    pass
                else: # No sessions available (e.g., initiator not started or stopped)
                    if self.is_connected: # If we thought we were connected (due to callbacks), but no sessions exist
                        print("Warning: No FIX sessions found (initiator stopped or not started properly), but trader state indicated 'connected'. Updating status.")
                        self.is_connected = False
                        if not self._last_error: # If no specific error, set a generic one
                           self._last_error = "No active FIX sessions."

            except Exception as e:
                print(f"Error checking session status: {e}")
                # Potentially set is_connected to False if we can't verify
                # self.is_connected = False
                # self._last_error = f"Error checking session status: {e}"
                pass # Keep current is_connected state if check fails for now

        return self.is_connected, self._last_error

    def start_heartbeat(self):
        def hb():
            while self.is_connected:
                if USE_QUICKFIX:
                    for sid in fix.Session.getSessions():
                        fix.Session.sendToTarget(fix.Heartbeat(), sid)
                time.sleep(30)
        threading.Thread(target=hb, daemon=True).start()

    def get_account_summary(self) -> dict:
        if not self.is_connected:
            raise RuntimeError("Not connected")
        if not USE_QUICKFIX:
            return {"account_id": "MOCK123", "balance": 10000.0, "equity": 9950.0, "margin": 50.0}
        # TODO: implement real FIX AccountSummaryRequest/Response
        return {"account_id": "...", "balance": 0.0, "equity": 0.0, "margin": 0.0}

    def get_market_price(self, symbol: str) -> float:
        if not self.is_connected:
            raise RuntimeError("Cannot fetch market data when disconnected")
        if not USE_QUICKFIX:
            price = round(random.uniform(1.10, 1.20) + random.uniform(-0.005, 0.005), 5)
        else:
            msg = fix.Message()
            msg.getHeader().setField(fix.MsgType(fix.MsgType_MarketDataRequest))
            msg.setField(fix.MDReqID("MD_" + symbol))
            msg.setField(fix.SubscriptionRequestType(fix.SubscriptionRequestType_SNAPSHOT_PLUS_UPDATES))
            msg.setField(fix.MarketDepth(1))
            grp = fix.NoMDEntryTypes()
            grp.setField(fix.MDEntryType(fix.MDEntryType_BID)); msg.addGroup(grp)
            grp.setField(fix.MDEntryType(fix.MDEntryType_OFFER)); msg.addGroup(grp)
            sym = fix.NoRelatedSym(); sym.setField(fix.Symbol(symbol)); msg.addGroup(sym)
            for sid in fix.Session.getSessions():
                fix.Session.sendToTarget(msg, sid)
            time.sleep(0.5)
            price = self.application.last_price.get(symbol, random.uniform(1.10, 1.20))
        self.price_history.append(price)
        if len(self.price_history) > self.history_size:
            self.price_history.pop(0)
        return price

    def place_market_order(self, symbol: str, side: str, size: float, tp: float, sl: float):
        if not self.is_connected:
            raise RuntimeError("Not connected")
        last_price = self.price_history[-1] if self.price_history else self.get_market_price(symbol)
        if not USE_QUICKFIX:
            print(f"[MOCK ORDER] {side.upper()} {symbol} size={size} TP={tp} SL={sl}")
            return
        order = fix.Message()
        order.getHeader().setField(fix.MsgType(fix.MsgType_NewOrderSingle))
        order.setField(fix.ClOrdID(f"ORD_{int(time.time()*1000)}"))
        order.setField(fix.Symbol(symbol))
        order.setField(fix.Side(fix.Side_BUY if side=='buy' else fix.Side_SELL))
        order.setField(fix.OrdType(fix.OrdType_MARKET))
        order.setField(fix.OrderQty(size))
        order.setField(fix.StopPx(last_price - sl * 0.0001 if side=='buy' else last_price + sl * 0.0001))
        order.setField(fix.TP(last_price + tp * 0.0001 if side=='buy' else last_price - tp * 0.0001))
        for sid in fix.Session.getSessions():
            fix.Session.sendToTarget(order, sid)

    def get_price_history(self) -> List[float]:
        return list(self.price_history)

# QuickFIX application handler if available
if USE_QUICKFIX:
    class Application(fix.Application):
        def __init__(self, trader: Trader):
            super().__init__()
            self.trader = trader
            self.last_price = {}

        def onCreate(self, sessionID):
            print(f"Session created: {sessionID}")
            pass

        def onLogon(self, sessionID):
            print(f"Logon: {sessionID}")
            self.trader.is_connected = True
            self.trader._last_error = ""
            self.trader._logon_event.set() # Signal that logon has occurred
            self.trader._logout_event.clear() # Ensure logout event is not set

        def onLogout(self, sessionID):
            print(f"Logout: {sessionID}")
            self.trader.is_connected = False
            # You might want to set a specific error message or reason for logout
            # For example, if the logout was unexpected.
            if not self.trader._last_error: # Avoid overwriting a more specific error
                self.trader._last_error = "Logged out"
            self.trader._logon_event.clear() # Ensure logon event is not set
            self.trader._logout_event.set() # Signal that logout has occurred

        def toAdmin(self, message, sessionID):
            # Log outgoing admin messages if desired
            # print(f"ToAdmin: {message} (Session: {sessionID})")
            pass

        def toApp(self, message, sessionID):
            # Log outgoing app messages if desired
            # print(f"ToApp: {message} (Session: {sessionID})")
            pass

        def fromAdmin(self, message, sessionID):
            # Handle administrative messages from the counterparty
            # Example: Log Reject messages
            msg_type_field = fix.MsgType()
            message.getHeader().getField(msg_type_field)
            msg_type = msg_type_field.getValue()

            if msg_type == fix.MsgType_Reject: # Session-level Reject
                text_field = fix.Text()
                if message.isSetField(text_field.getField()):
                    message.getField(text_field)
                    reject_reason = text_field.getValue()
                    print(f"Session Level Reject fromAdmin (Session: {sessionID}): {reject_reason}. Message: {message}")
                    # Potentially update trader._last_error or take other actions
                else:
                    print(f"Session Level Reject fromAdmin (Session: {sessionID}): No text reason provided. Message: {message}")
            # Handle other admin messages like Logout, Heartbeat etc. if needed
            pass

        def fromApp(self, message, sessionID):
            msg_type = fix.MsgType(); message.getHeader().getField(msg_type)
            msg_type_value = msg_type.getValue()

            if msg_type_value == fix.MsgType_Reject: # Application-level Reject (e.g., for a business message)
                text_field = fix.Text()
                if message.isSetField(text_field.getField()):
                    message.getField(text_field)
                    reject_reason = text_field.getValue()
                    print(f"Application Level Reject fromApp (Session: {sessionID}): {reject_reason}. Message: {message}")
                    # Update trader._last_error or relevant order status
                else:
                    print(f"Application Level Reject fromApp (Session: {sessionID}): No text reason provided. Message: {message}")

            elif msg_type_value == fix.MsgType_MarketDataSnapshotFullRefresh:
                sym = fix.Symbol(); message.getField(sym)
                px = fix.MDEntryPx(); message.getField(px)
                self.trader.last_price[sym.getValue()] = px.getValue()

            elif msg_type_value == fix.MsgType_ExecutionReport:
                cl_ord_id = fix.ClOrdID()
                exec_type = fix.ExecType()
                ord_status = fix.OrdStatus()
                text = fix.Text() # Optional text message

                message.getField(cl_ord_id)
                message.getField(exec_type)
                message.getField(ord_status)

                exec_type_str = exec_type.getValue()
                ord_status_str = ord_status.getValue()

                log_msg = f"ExecutionReport (Session: {sessionID}): ClOrdID={cl_ord_id.getValue()}, ExecType={exec_type_str}, OrdStatus={ord_status_str}"

                if message.isSetField(text.getField()):
                    message.getField(text)
                    log_msg += f", Text='{text.getValue()}'"

                print(log_msg)

                # TODO: Further processing:
                # - Map ClOrdID to internal order representation.
                # - Update order status based on ExecType and OrdStatus.
                # - Handle fills, partial fills, cancels, rejects.
                # - Potentially update self.trader._last_error if it's a rejection of an order.
            pass
