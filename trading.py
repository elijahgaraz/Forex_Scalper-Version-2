import threading
import random
import time
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

    def connect(self) -> bool:
        if not USE_QUICKFIX:
            self.is_connected = True
            return True
        try:
            self.settings_file = fix.SessionSettings(self.settings.fix_config_path)
            self.application = Application(self)
            self.store_factory = fix.FileStoreFactory(self.settings_file)
            self.log_factory = fix.FileLogFactory(self.settings_file)
            self.initiator = fix.SocketInitiator(
                self.application,
                self.store_factory,
                self.settings_file,
                self.log_factory
            )
            self.initiator.start()
            time.sleep(1)
            for sid in fix.Session.getSessions():
                sess = fix.Session.lookupSession(sid)
                if sess.isLoggedOn():
                    self.is_connected = True
                    return True
            self._last_error = "Logon failed or timeout"
            return False
        except Exception as e:
            self.is_connected = False
            self._last_error = str(e)
            return False

    def get_connection_status(self):
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
        def onCreate(self, sessionID): pass
        def onLogon(self, sessionID): print(f"Logon: {sessionID}")
        def onLogout(self, sessionID): print(f"Logout: {sessionID}")
        def toAdmin(self, message, sessionID): pass
        def toApp(self, message, sessionID): pass
        def fromAdmin(self, message, sessionID): pass
        def fromApp(self, message, sessionID):
            msg_type = fix.MsgType(); message.getHeader().getField(msg_type)
            if msg_type.getValue() == fix.MsgType_MarketDataSnapshotFullRefresh:
                sym = fix.Symbol(); message.getField(sym)
                px = fix.MDEntryPx(); message.getField(px)
                self.trader.last_price[sym.getValue()] = px.getValue()
            # handle ExecutionReport if needed
            pass
