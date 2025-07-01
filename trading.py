import threading
import random
import time
import os # For potential future use, e.g. loading certs for WSS
import json # For JSON message construction and parsing
from typing import List, Dict, Any, Optional

import requests # For OAuth authentication
import websockets # For WebSocket communication
# from .settings import OpenAPISettings # Assuming settings.py is in the same directory or package

# This flag will determine if we attempt to use the Open API or run in mock mode.
# It can be set based on availability of libraries or configuration.
USE_OPENAPI = True
try:
    import websockets
    import requests
except ImportError:
    USE_OPENAPI = False
    print("OpenAPI libraries (websockets, requests) not available: running in stub mode.")


class Trader:
    def __init__(self, settings, history_size: int = 100): # settings will be an instance of Settings class
        self.settings = settings # This should contain an openapi attribute of type OpenAPISettings
        self.is_connected = False
        self._last_error = ""
        self.price_history: List[float] = []
        self.history_size = history_size

        # Attributes for live account data
        self.account_id: str | None = None
        self.balance: float | None = None
        self.equity: float | None = None
        self.margin: float | None = None
        self.currency: str | None = None # Account currency

        # Attributes for Open API
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expiry_time: Optional[float] = None
        self._ws_client: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_listener_thread: Optional[threading.Thread] = None
        self._message_id_counter: int = 1 # For uniquely identifying client messages

    def _next_message_id(self) -> str:
        # Generates a unique client message ID for requests
        msg_id = str(self._message_id_counter)
        self._message_id_counter += 1
        return msg_id

    def _authenticate_openapi(self) -> bool: # Made synchronous
        """
        Authenticates with cTrader Open API using OAuth 2.0 (Client Credentials Grant).
        Stores the access token and refresh token.
        This method is synchronous as it uses the 'requests' library.
        """
        if not self.settings.openapi.client_id or not self.settings.openapi.client_secret:
            self._last_error = "OpenAPI Client ID or Secret not configured."
            print(self._last_error)
            return False

        if not self.settings.openapi.token_url:
            self._last_error = "OpenAPI Token URL not configured."
            print(self._last_error)
            return False

        payload = {
            'grant_type': 'client_credentials',
            'client_id': self.settings.openapi.client_id,
            'client_secret': self.settings.openapi.client_secret,
        }

        try:
            print(f"Attempting OAuth to {self.settings.openapi.token_url}")
            response = requests.post(self.settings.openapi.token_url, data=payload)
            response.raise_for_status()

            token_data = response.json()
            self._access_token = token_data.get('access_token')
            self._refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in')

            if not self._access_token:
                self._last_error = "Failed to get access token from OAuth response."
                print(self._last_error)
                return False

            if expires_in:
                self._token_expiry_time = time.time() + int(expires_in) - 60

            print("Successfully obtained OpenAPI access token.")
            self.settings.openapi.access_token = self._access_token
            self.settings.openapi.refresh_token = self._refresh_token
            self.settings.openapi.token_expiry_time = self._token_expiry_time
            return True

        except requests.exceptions.RequestException as e:
            self._last_error = f"OAuth request failed: {e}"
            if hasattr(e, 'response') and e.response is not None:
                 try:
                    self._last_error += f" - Server response: {e.response.json()}"
                 except json.JSONDecodeError:
                    self._last_error += f" - Server response: {e.response.text}"
            print(self._last_error)
            return False
        except Exception as e: # Catch any other exceptions during auth
            self._last_error = f"Error during OpenAPI authentication: {e}"
            print(self._last_error)
            return False

    async def _ws_connect_and_authorize(self) -> bool:
        """Establishes WebSocket connection and sends initial authorization."""
        if not self._access_token:
            self._last_error = "Cannot connect WebSocket without access token."
            print(self._last_error)
            return False

        if not self.settings.openapi.api_ws_url:
            self._last_error = "OpenAPI WebSocket URL not configured."
            print(self._last_error)
            return False

        try:
            print(f"Connecting to WebSocket: {self.settings.openapi.api_ws_url}")
            # Note: websockets.connect() is an async context manager in typical use
            # For a long-lived client in a class, we manage connection manually.
            self._ws_client = await websockets.connect(self.settings.openapi.api_ws_url)
            print("WebSocket connection established.")

            # Send Application Authorization Request
            # Structure from cTrader Open API documentation (ProtoOAApplicationAuthReq)
            auth_request_payload = {
                "payloadType": 1, # ProtoOAApplicationAuthReq
                "payload": {
                    "clientId": self.settings.openapi.client_id,
                    "clientSecret": self.settings.openapi.client_secret
                }
            }
            await self._ws_client.send(json.dumps(auth_request_payload))
            print("Sent Application Authorization Request.")

            # Wait for ProtoOAApplicationAuthRes
            response_str = await self._ws_client.recv()
            response_json = json.loads(response_str)
            print(f"Received App Auth Response: {response_json}")

            if response_json.get("payloadType") == 2: # ProtoOAApplicationAuthRes
                # Potentially check response details here for success
                print("Application authorized on WebSocket.")
                # Next, authorize account if a default one is set
                if self.settings.openapi.default_account_id_str:
                    # This is also a cTrader specific Proto Message, e.g. ProtoOAAccountAuthReq
                    # For now, assume app auth is enough to start receiving some messages
                    # or that account auth happens as part of other requests (e.g. subscribe to symbols)
                    pass
                return True
            else:
                self._last_error = f"Unexpected response to App Auth: {response_json}"
                print(self._last_error)
                await self._ws_client.close()
                self._ws_client = None
                return False

        except websockets.exceptions.WebSocketException as e:
            self._last_error = f"WebSocket connection/authorization error: {e}"
            print(self._last_error)
            if self._ws_client:
                await self._ws_client.close()
            self._ws_client = None
            return False
        except Exception as e:
            self._last_error = f"General error during WebSocket connect/auth: {e}"
            print(self._last_error)
            if self._ws_client and self._ws_client.open:
                await self._ws_client.close()
            self._ws_client = None
            return False


    async def _listen_ws(self):
        """Listens for incoming messages on the WebSocket."""
        if not self._ws_client or not self._ws_client.open:
            print("WebSocket listener cannot start: not connected.")
            return

        print("Starting WebSocket listener...")
        try:
            async for message_str in self._ws_client:
                try:
                    message_json = json.loads(message_str)
                    print(f"WS RECV: {message_json}") # Basic logging of all messages

                    payload_type = message_json.get("payloadType")
                    payload = message_json.get("payload", {})
                    client_msg_id = message_json.get("clientMsgId")

                    # TODO: Dispatch based on payload_type
                    if payload_type == 5: # ProtoHeartbeatEvent
                        print("Received Heartbeat event from server.")
                        # Respond with a heartbeat if required by API spec (often not needed for client)

                    elif payload_type == 51: # ProtoOASymbolsListRes
                        # Example: if we requested all symbols
                        print(f"Received Symbols List Response for clientMsgId {client_msg_id}")
                        # Process symbols...

                    elif payload_type == 53: # ProtoOASpotEvent (price update)
                        # Example structure, needs to match cTrader Open API spec
                        # symbol_id = payload.get("symbolId")
                        # bid_price = payload.get("bid")
                        # ask_price = payload.get("ask")
                        # if symbol_id and (bid_price or ask_price):
                        #    avg_price = (bid_price + ask_price) / 2 if bid_price and ask_price else bid_price or ask_price
                        #    # self.price_history logic here, map symbol_id to symbol string
                        #    print(f"Price update for {symbol_id}: Bid={bid_price}, Ask={ask_price}")
                        pass


                    # Add more handlers for:
                    # - Account authorization responses
                    # - Account list responses
                    # - Execution reports (ProtoOAExecutionEvent)
                    # - Account balance updates (e.g. ProtoOATraderUpdatedEvent)

                except json.JSONDecodeError:
                    print(f"Error decoding JSON from WebSocket: {message_str}")
                except Exception as e:
                    print(f"Error processing WebSocket message: {e}")
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed.")
        except Exception as e:
            print(f"WebSocket listener error: {e}")
        finally:
            self.is_connected = False
            self._ws_client = None # Ensure it's None so connect can be called again
            print("WebSocket listener stopped.")


    def connect(self) -> bool:
        """Connects to cTrader Open API: Authenticates and establishes WebSocket."""
        if not USE_OPENAPI:
            self._last_error = "OpenAPI libraries not installed."
            # Fallback to mock connection for GUI if needed, or just return False
            # For now, let's simulate a successful mock connection for structure
            print("Running in mock mode for connect().")
            self.is_connected = True # Mock connection
            return True

        import asyncio

        # Run async authentication and connection logic
        # This is simplified; in a real app, especially with Tkinter,
        # you'd need to manage the asyncio event loop carefully,
        # possibly running it in a separate thread.
        try:
            # Authenticate first (synchronous HTTP for token)
            if not self._access_token or (self._token_expiry_time and time.time() >= self._token_expiry_time):
                print("Access token missing or expired, re-authenticating...")
                if not self._authenticate_openapi(): # Call synchronous method directly
                     return False

            # Connect WebSocket and authorize app (asynchronous)
            if not self._ws_client or not self._ws_client.open:
                if not asyncio.run(self._ws_connect_and_authorize()):
                    return False

            self.is_connected = True
            self._last_error = ""

            # Start the WebSocket listener thread
            if self._ws_listener_thread is None or not self._ws_listener_thread.is_alive():
                self._ws_listener_thread = threading.Thread(target=lambda: asyncio.run(self._listen_ws()), daemon=True)
                self._ws_listener_thread.start()

            print("Trader connected successfully via OpenAPI.")
            return True

        except Exception as e:
            self._last_error = f"Failed to connect: {e}"
            print(self._last_error)
            self.is_connected = False
            return False

    def disconnect(self):
        """Disconnects from WebSocket and cleans up."""
        import asyncio
        print("Disconnecting trader...")
        if self._ws_client and self._ws_client.open:
            asyncio.run(self._ws_client.close()) # Ensure loop is running to close
        if self._ws_listener_thread and self._ws_listener_thread.is_alive():
            self._ws_listener_thread.join(timeout=5) # Wait for listener to stop

        self._ws_client = None
        self.is_connected = False
        self._access_token = None # Clear token on disconnect
        self._refresh_token = None
        self._token_expiry_time = None
        self.settings.openapi.access_token = None # Also clear from settings
        self.settings.openapi.refresh_token = None
        self.settings.openapi.token_expiry_time = None
        print("Trader disconnected.")


    def get_connection_status(self):
        return self.is_connected, self._last_error

    def start_heartbeat(self):
        # For WebSocket, server typically sends heartbeats (e.g. ProtoHeartbeatEvent).
        # Client might need to send PING frames or specific heartbeat messages if API requires.
        # cTrader Open API: client sends ProtoPingReq, server responds ProtoPingRes.
        # This should be handled in the _listen_ws loop or a separate timed task.

        async def send_ping_periodically():
            while self.is_connected and self._ws_client and self._ws_client.open:
                try:
                    ping_req_payload = {
                        "payloadType": 3, # ProtoPingReq
                        "payload": {"timestamp": int(time.time() * 1000)}
                    }
                    print("Sending Ping Request to server...")
                    await self._ws_client.send(json.dumps(ping_req_payload))
                    await asyncio.sleep(30) # Send ping every 30 seconds
                except websockets.exceptions.ConnectionClosed:
                    print("Cannot send ping, connection closed.")
                    break
                except Exception as e:
                    print(f"Error sending ping: {e}")
                    break # Stop pinging on error

        if USE_OPENAPI and self.is_connected:
            # Run this in the asyncio event loop managed by the listener thread,
            # or create a new task if running asyncio more globally.
            # For simplicity, if _listen_ws is running, it can manage this.
            # This is a simplified way; proper task management in asyncio is better.
            # Let's assume for now the main listener might handle incoming heartbeats
            # and we'll add outgoing ping sending if explicitly required.
            # The example above is how one might send it.
            print("Heartbeat/Ping mechanism for OpenAPI to be implemented if server doesn't auto-disconnect inactive clients or if required by spec.")
            pass


    def get_account_summary(self) -> dict:
        if not USE_OPENAPI: # If library import failed
             return {"account_id": "MOCK_API_DISABLED", "balance": 0.0, "equity": 0.0, "margin": 0.0, "currency": "N/A"}

        if not self.is_connected:
            raise RuntimeError("Not connected to cTrader Open API.")

        # If live data has been populated by WebSocket listener
        if self.account_id is not None:
            return {
                "account_id": self.account_id,
                "balance": self.balance,
                "equity": self.equity,
                "margin": self.margin,
                "currency": self.currency
            }
        else:
            # TODO: Send a request for account summary if not yet populated
            # For now, return placeholder
            # self._request_account_summary_openapi() # This should be async or queued
            print("Account summary not yet populated from API.")
            return {"account_id": "Fetching...", "balance": None, "equity": None, "margin": None, "currency": None}

    # Placeholder for _request_account_summary_openapi
    async def _request_account_summary_openapi(self):
        if self.is_connected and self._ws_client:
            # Example: Request ProtoOAGetAccountListReq
            # This would involve knowing your cTrader Account ID (ctidTraderAccountId)
            # which you might get after authorizing the account.
            # This is a placeholder for the actual request.
            # account_list_req = {
            #    "payloadType": XYZ, # ProtoOAGetAccountListReq
            #    "clientMsgId": self._next_message_id()
            # }
            # await self._ws_client.send(json.dumps(account_list_req))
            print("Placeholder: _request_account_summary_openapi called.")
            pass


    def get_market_price(self, symbol: str) -> float:
        if not USE_OPENAPI:
            return round(random.uniform(1.10, 1.20) + random.uniform(-0.005, 0.005), 5)

        if not self.is_connected:
            raise RuntimeError("Cannot fetch market data when disconnected from OpenAPI.")

        # Price should be updated by the _listen_ws method from SpotEvents
        # This method would just return the latest stored price
        # For now, let's return a random price if not found in history,
        # but ideally, it should come from self.last_price (which needs to be implemented)

        # TODO: Implement self.last_price dictionary updated by spot events
        # For now, using price_history if available, else random
        if self.price_history:
            # This is not symbol specific, needs improvement
            return self.price_history[-1]
        else:
            # Before subscribing, we won't have prices.
            # Consider sending a subscribe request here if not already subscribed.
            # self.subscribe_to_symbol_openapi(symbol) # This should be async or queued
            print(f"Market price for {symbol} not yet available from stream. Returning random.")
            return round(random.uniform(1.10, 1.20) + random.uniform(-0.005, 0.005), 5)

    # Placeholder for subscribe_to_symbol_openapi
    async def subscribe_to_symbol_openapi(self, symbol_name: str):
        # Needs mapping from symbol_name (e.g. "EUR/USD") to cTrader symbolId (long)
        # This usually comes from a ProtoOASymbolsListRes or similar.
        # Example assuming we have symbol_id:
        # symbol_id_example = 1 # Replace with actual lookup
        # subscribe_req = {
        #    "payloadType": ABC, # ProtoOASubscribeSpotsReq
        #    "payload": {
        #        "ctidTraderAccountId": self.settings.openapi.default_account_id_str, # Or current active account
        #        "symbolId": [symbol_id_example]
        #    },
        #    "clientMsgId": self._next_message_id()
        # }
        # if self.is_connected and self._ws_client:
        #    await self._ws_client.send(json.dumps(subscribe_req))
        print(f"Placeholder: subscribe_to_symbol_openapi({symbol_name}) called.")
        pass


    def place_market_order(self, symbol: str, side: str, size: float, tp: float, sl: float):
        if not USE_OPENAPI:
            print(f"[MOCK ORDER OPENAPI] {side.upper()} {symbol} size={size} TP={tp} SL={sl}")
            return

        if not self.is_connected:
            raise RuntimeError("Not connected to OpenAPI for placing order.")

        # TODO: Implement sending ProtoOANewOrderReq
        # This will require:
        # - ctidTraderAccountId
        # - symbolId (mapping from symbol string)
        # - orderType (MARKET)
        # - tradeSide (BUY/SELL)
        # - volume (converted to cTrader format, e.g. lots * 100)
        # - stopLoss, takeProfit (in absolute price or pips, as per API)
        # - clientMsgId for tracking
        print(f"Placeholder: place_market_order_openapi({symbol}, {side}, {size}) called.")
        # Example structure (highly simplified):
        # order_req = {
        #    "payloadType": DEF, # ProtoOANewOrderReq
        #    "payload": { ... details ... },
        #    "clientMsgId": self._next_message_id()
        # }
        # Needs to be sent via an async task: asyncio.create_task(self._ws_client.send(json.dumps(order_req)))
        pass

    def get_price_history(self) -> List[float]:
        # This will be populated by the WebSocket listener from price updates
        return list(self.price_history)

# Removed the old QuickFIX Application class entirely.
