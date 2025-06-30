import json
from dataclasses import dataclass

@dataclass
class TradeFIXSettings:
    host: str
    port: int
    sender_comp_id: str
    target_comp_id: str
    password: str

@dataclass
class QuoteFIXSettings:
    config_path: str
    password: str

@dataclass
class Settings:
    trade_fix: TradeFIXSettings
    quote_fix: QuoteFIXSettings

    @staticmethod
    def load(path: str = "config.json") -> "Settings":
        with open(path, "r") as f:
            cfg = json.load(f)
        trade = cfg.get("trade_fix", {})
        quote = cfg.get("quote_fix", {})
        return Settings(
            trade_fix=TradeFIXSettings(
                host=trade.get("host"),
                port=trade.get("port"),
                sender_comp_id=trade.get("sender_comp_id"),
                target_comp_id=trade.get("target_comp_id"),
                password=trade.get("password"),
            ),
            quote_fix=QuoteFIXSettings(
                config_path=quote.get("config_path"),
                password=quote.get("password"),
            )
        )
