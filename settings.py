import json
import os
from dataclasses import dataclass

@dataclass
class TradeFIXSettings:
    host: str
    port: int
    sender_comp_id: str
    target_comp_id: str
    password: str # This will now be populated from env var by default

@dataclass
class QuoteFIXSettings:
    config_path: str
    password: str # This will now be populated from env var by default

@dataclass
class Settings:
    trade_fix: TradeFIXSettings
    quote_fix: QuoteFIXSettings

    @staticmethod
    def load(path: str = "config.json") -> "Settings":
        # Load passwords from environment variables
        # Fallback to None if not set, allowing config file to be the source if env var is missing
        trade_password_env = os.environ.get("FIX_TRADE_PASSWORD")
        quote_password_env = os.environ.get("FIX_QUOTE_PASSWORD")

        if not trade_password_env:
            print("Warning: FIX_TRADE_PASSWORD environment variable not set. Trading connection might fail if password is required and not in config.json.")
        if not quote_password_env:
            print("Warning: FIX_QUOTE_PASSWORD environment variable not set. Quote connection might fail if password is required and not in config.json (or quickfix.cfg if it overrides).")

        with open(path, "r") as f:
            cfg = json.load(f)

        trade_cfg = cfg.get("trade_fix", {})
        quote_cfg = cfg.get("quote_fix", {})

        # Use environment variable password if set, otherwise use password from config.json (if any)
        trade_password = trade_password_env if trade_password_env is not None else trade_cfg.get("password")
        quote_password = quote_password_env if quote_password_env is not None else quote_cfg.get("password")

        return Settings(
            trade_fix=TradeFIXSettings(
                host=trade_cfg.get("host"),
                port=trade_cfg.get("port"),
                sender_comp_id=trade_cfg.get("sender_comp_id"),
                target_comp_id=trade_cfg.get("target_comp_id"),
                password=trade_password, # Use resolved password
            ),
            quote_fix=QuoteFIXSettings(
                config_path=quote_cfg.get("config_path", "quickfix.cfg"), # Default to quickfix.cfg
                password=quote_password, # Use resolved password
            )
        )
