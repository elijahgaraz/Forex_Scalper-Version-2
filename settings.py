import json
import os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class OpenAPISettings:
    # URLs for cTrader Open API
    auth_url: str = ""  # e.g., "https://connect.spotware.com/apps/auth"
    token_url: str = "" # e.g., "https://connect.spotware.com/apps/token"
    api_ws_url: str = "" # e.g., "wss://demo.ctraderapi.com/" or "wss://live.ctraderapi.com/"

    # Credentials - preferentially loaded from environment variables
    client_id: Optional[str] = None
    client_secret: Optional[str] = None

    # Optional: if you want to always trade with/monitor a specific account ID provided by cTrader API
    # This is NOT the SenderCompID from FIX. This would be a cTrader account number.
    default_account_id_str: Optional[str] = None # Store as string as API might provide it as such

    # Store tokens - these are typically not set in config.json but populated at runtime
    access_token: Optional[str] = field(default=None, repr=False) # Don't show token in repr
    refresh_token: Optional[str] = field(default=None, repr=False) # Don't show token in repr
    token_expiry_time: Optional[float] = field(default=None, repr=False) # Store as timestamp


@dataclass
class GeneralSettings:
    default_symbol: str = "EUR/USD"
    chart_update_interval_ms: int = 500
    # Add other general app settings here if any

@dataclass
class Settings:
    openapi: OpenAPISettings
    general: GeneralSettings

    @staticmethod
    def load(path: str = "config.json") -> "Settings":
        # Load secrets from environment variables first
        env_client_id = os.environ.get("CTRADER_CLIENT_ID")
        env_client_secret = os.environ.get("CTRADER_CLIENT_SECRET")

        try:
            with open(path, 'r') as f:
                cfg_data = json.load(f)
        except FileNotFoundError:
            print(f"Warning: Settings file '{path}' not found. Using default values and environment variables.")
            cfg_data = {}
        except json.JSONDecodeError:
            print(f"Warning: Error decoding JSON from '{path}'. Using default values and environment variables.")
            cfg_data = {}

        openapi_cfg = cfg_data.get("openapi", {})
        general_cfg = cfg_data.get("general", {})

        # Prioritize env vars for secrets, then config file, then None
        client_id = env_client_id if env_client_id else openapi_cfg.get("client_id")
        client_secret = env_client_secret if env_client_secret else openapi_cfg.get("client_secret")

        if not client_id:
            print("Warning: cTrader Client ID not found in environment variables (CTRADER_CLIENT_ID) or config.json.")
        if not client_secret:
            print("Warning: cTrader Client Secret not found in environment variables (CTRADER_CLIENT_SECRET) or config.json.")

        openapi_settings = OpenAPISettings(
            auth_url=openapi_cfg.get("auth_url", "https://connect.spotware.com/apps/auth"), # Default example
            token_url=openapi_cfg.get("token_url", "https://connect.spotware.com/apps/token"), # Default example
            api_ws_url=openapi_cfg.get("api_ws_url", "wss://demo.ctraderapi.com/"), # Default example (DEMO)
            client_id=client_id,
            client_secret=client_secret,
            default_account_id_str=openapi_cfg.get("default_account_id_str")
        )

        general_settings = GeneralSettings(
            default_symbol=general_cfg.get("default_symbol", "EUR/USD"),
            chart_update_interval_ms=general_cfg.get("chart_update_interval_ms", 500)
        )

        return Settings(openapi=openapi_settings, general=general_settings)

    def save(self, path: str = "config.json") -> None:
        # Create a representation of settings that is safe to save (e.g., without tokens)
        # Only save configurable parts, not runtime state like access tokens.
        openapi_to_save = {
            "auth_url": self.openapi.auth_url,
            "token_url": self.openapi.token_url,
            "api_ws_url": self.openapi.api_ws_url,
            # Only save client_id and client_secret if they were NOT from env vars
            # and the user explicitly wants to save them (generally not recommended for secrets).
            # For simplicity here, we'll save them if they exist, but warn about it.
            "client_id": self.openapi.client_id if not os.environ.get("CTRADER_CLIENT_ID") else None,
            "client_secret": self.openapi.client_secret if not os.environ.get("CTRADER_CLIENT_SECRET") else None,
            "default_account_id_str": self.openapi.default_account_id_str,
        }
        # Filter out None values from client_id/secret if they were from env, to avoid writing "null"
        openapi_to_save = {k: v for k, v in openapi_to_save.items() if v is not None}


        if openapi_to_save.get("client_id") or openapi_to_save.get("client_secret"):
            print(f"Warning: Saving Client ID or Client Secret to '{path}'. "
                  "It's generally recommended to use environment variables for these.")

        data_to_save = {
            "openapi": openapi_to_save,
            "general": {
                "default_symbol": self.general.default_symbol,
                "chart_update_interval_ms": self.general.chart_update_interval_ms,
            }
        }
        with open(path, 'w') as f:
            json.dump(data_to_save, f, indent=4)
