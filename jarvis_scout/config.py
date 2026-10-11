"""Local configuration. Credentials are read from environment, never stored in scans."""
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AppConfig:
    db_path: Path
    amazon_catalog_path: Path
    feed_path: Path
    telegram_configured: bool

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv(ROOT / ".env", override=False)
        return cls(
            db_path=Path(os.getenv("SCOUT_DB_PATH", str(ROOT / "var" / "scout.sqlite3"))).expanduser(),
            amazon_catalog_path=Path(os.getenv("SCOUT_AMAZON_CATALOG", str(ROOT / "data" / "amazon_demo.json"))).expanduser(),
            feed_path=Path(os.getenv("SCOUT_FEED_PATH", str(ROOT / "data" / "supplier_demo.csv"))).expanduser(),
            telegram_configured=bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
        )
