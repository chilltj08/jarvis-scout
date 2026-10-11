"""Opt-in Telegram alerts with persistent reservations to prevent alert floods.

Credentials are read from environment variables and never stored in SQLite.
Definite rejections can be retried immediately. A network failure after a
request may have been delivered is held for the cooldown: remote exactly-once
delivery cannot be guaranteed by Telegram's sendMessage API.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import math
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Protocol
from urllib.parse import quote, urlsplit
import uuid

import httpx


class NotificationError(RuntimeError):
    """A safe error message, with whether delivery may already have happened."""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


class Notifier(Protocol):
    def send(self, opportunity: dict[str, Any]) -> None: ...


class _TelegramUrlRedactor(logging.Filter):
    """httpx INFO logs otherwise include the credential in Telegram API URLs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = re.sub(r"(https://api\.telegram\.org/bot)[^/\s]+", r"\1[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.username is not None or parsed.password is not None):
            return None
        # Reading the port also validates that it is a valid numeric port.
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    return value


def _number(value: Any, *, money: bool = False) -> str:
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "unavailable"
        return f"£{number:.2f}" if money else f"{number:.1f}%"
    except (InvalidOperation, ValueError, TypeError):
        return "unavailable"


def is_simulated(opportunity: dict[str, Any]) -> bool:
    """Missing provenance is conservatively treated as simulated."""
    listing = opportunity.get("listing")
    if not isinstance(listing, dict):
        return True
    return opportunity.get("simulated", True) is not False or listing.get("simulated", True) is not False


def format_deal_message(opportunity: dict[str, Any]) -> str:
    supplier = opportunity.get("supplier", {})
    listing = opportunity.get("listing", {})
    profitability = opportunity.get("profitability", {})
    simulated = is_simulated(opportunity)
    title = str(supplier.get("title", "Product"))[:300]
    lines = [
        "JARVIS SCOUT · SIMULATED DEMO" if simulated else "JARVIS SCOUT · Resale candidate",
        title,
        f"Supplier cost: {_number(supplier.get('unit_cost'), money=True)}",
        f"Amazon price: {_number(listing.get('price'), money=True)}",
        f"Estimated profit: {_number(profitability.get('profit'), money=True)}",
        f"ROI: {_number(profitability.get('roi_percent'))} | Margin: {_number(profitability.get('margin_percent'))}",
    ]
    asin = str(listing.get("asin", ""))
    if simulated:
        # Synthetic IDs must not open real Amazon listings, even when an
        # adapter supplies a product URL alongside demonstration data.
        lines.append(f"Simulated ASIN: {asin[:100] or 'unavailable'}")
    else:
        listing_url = _https_url(listing.get("listing_url"))
        if listing_url is None and re.fullmatch(r"[A-Z0-9]{10}", asin):
            listing_url = f"https://www.amazon.co.uk/dp/{asin}"
        if listing_url:
            lines.append(f"Amazon: {listing_url[:1000]}")
    supplier_url = _https_url(supplier.get("supplier_url"))
    if supplier_url:
        lines.append(f"Supplier: {supplier_url[:1000]}")
    lines.append("Verify prices, eligibility and costs before purchasing.")
    if simulated:
        lines.append("Demonstration only: Amazon prices and demand are simulated.")
    # Telegram supports 4096 characters; leave room for API differences.
    return "\n".join(lines)[:3800]


class TelegramNotifier:
    """Plaintext alerts. Instantiate explicitly to opt into sending messages."""

    def __init__(self, token: str | None = None, chat_id: str | None = None, *,
                 client: httpx.Client | None = None, timeout_seconds: float = 10) -> None:
        self._token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self._client = client
        logger = logging.getLogger("httpx")
        if not any(isinstance(item, _TelegramUrlRedactor) for item in logger.filters):
            logger.addFilter(_TelegramUrlRedactor())
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Telegram timeout must be positive and finite.")
        self._timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, opportunity: dict[str, Any]) -> None:
        if not self.configured:
            raise NotificationError("Telegram credentials are missing; configure environment variables.")
        payload = {"chat_id": self._chat_id, "text": format_deal_message(opportunity),
                   "disable_web_page_preview": True}
        # Never include this URL in an exception or log: it contains the token.
        url = f"https://api.telegram.org/bot{quote(self._token, safe=':')}/sendMessage"
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload, timeout=self._timeout_seconds,
                                             follow_redirects=False)
            else:
                with httpx.Client() as client:
                    response = client.post(url, json=payload, timeout=self._timeout_seconds,
                                           follow_redirects=False)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise NotificationError("Telegram connection failed before delivery.") from None
        except httpx.InvalidURL:
            raise NotificationError("Telegram credentials could not form a valid API request.") from None
        except httpx.HTTPError:
            raise NotificationError("Telegram delivery is uncertain after a network failure.",
                                    uncertain=True) from None
        except Exception:
            # Custom transports can raise other exception types whose messages
            # include the authenticated URL. Preserve the same safe boundary.
            raise NotificationError("Telegram delivery could not be confirmed.", uncertain=True) from None
        if response.status_code >= 500:
            raise NotificationError("Telegram delivery is uncertain after a server failure.",
                                    uncertain=True)
        if not 200 <= response.status_code < 300:
            raise NotificationError(f"Telegram rejected the request (HTTP {response.status_code}).")
        try:
            result = response.json()
        except (ValueError, TypeError):
            raise NotificationError("Telegram returned an unreadable response; delivery is uncertain.",
                                    uncertain=True) from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            # Only an explicit rejection establishes that Telegram did not send it.
            if isinstance(result, dict) and result.get("ok") is False:
                raise NotificationError("Telegram rejected the message.")
            raise NotificationError("Telegram did not confirm delivery.", uncertain=True)


class AlertService:
    """Reserve product/ASIN alerts atomically before making a network request.

    A disk-backed database preserves cooldowns across process restarts. Price
    changes do not override cooldowns. Pending reservations from an interrupted
    process expire at the same cooldown as successfully delivered alerts.
    """

    def __init__(self, db_path: str | Path, notifier: Notifier, cooldown_hours: float = 24,
                 allow_simulated: bool = False, *, clock: Callable[[], float] = time.time) -> None:
        if not math.isfinite(cooldown_hours) or cooldown_hours <= 0:
            raise ValueError("Alert cooldown must be positive and finite.")
        if str(db_path) == ":memory:":
            raise ValueError("Alert deduplication requires a disk-backed SQLite database.")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.notifier = notifier
        self.cooldown_seconds = cooldown_hours * 3600
        self.allow_simulated = allow_simulated
        self.clock = clock
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS alert_events (
                product_key TEXT NOT NULL,
                asin TEXT NOT NULL,
                reservation_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'sent', 'failed', 'uncertain')),
                attempted_at REAL NOT NULL,
                completed_at REAL,
                PRIMARY KEY(product_key, asin)
            )""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _reserve(self, product_key: str, asin: str) -> str | None:
        now = self.clock()
        reservation_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, attempted_at FROM alert_events WHERE product_key=? AND asin=?",
                (product_key, asin)).fetchone()
            if row and row[0] != "failed" and now - row[1] < self.cooldown_seconds:
                return None
            connection.execute("""INSERT INTO alert_events
                (product_key, asin, reservation_id, status, attempted_at, completed_at)
                VALUES (?, ?, ?, 'pending', ?, NULL)
                ON CONFLICT(product_key, asin) DO UPDATE SET
                reservation_id=excluded.reservation_id, status='pending',
                attempted_at=excluded.attempted_at, completed_at=NULL""",
                (product_key, asin, reservation_id, now))
        return reservation_id

    def _finish(self, product_key: str, asin: str, reservation_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute("""UPDATE alert_events SET status=?, completed_at=?
                WHERE product_key=? AND asin=? AND reservation_id=?""",
                (status, self.clock(), product_key, asin, reservation_id))

    def dispatch(self, opportunities: list[dict[str, Any]]) -> dict[str, int]:
        report = {"sent": 0, "suppressed": 0, "failed": 0, "skipped_simulated": 0,
                  "skipped_unqualified": 0, "invalid": 0}
        for opportunity in opportunities:
            if opportunity.get("qualified") is not True:
                report["skipped_unqualified"] += 1
                continue
            match = opportunity.get("match")
            if isinstance(match, dict) and match.get("status", "exact") != "exact":
                report["skipped_unqualified"] += 1
                continue
            if is_simulated(opportunity) and not self.allow_simulated:
                report["skipped_simulated"] += 1
                continue
            listing = opportunity.get("listing")
            product_key = opportunity.get("product_key")
            asin = listing.get("asin") if isinstance(listing, dict) else None
            if not isinstance(product_key, str) or not product_key.strip() or not isinstance(asin, str) or not asin.strip():
                report["invalid"] += 1
                continue
            reservation_id = self._reserve(product_key, asin)
            if reservation_id is None:
                report["suppressed"] += 1
                continue
            try:
                self.notifier.send(opportunity)
            except NotificationError as error:
                self._finish(product_key, asin, reservation_id, "uncertain" if error.uncertain else "failed")
                report["failed"] += 1
            except Exception:
                # An unexpected adapter failure might occur after a successful send.
                self._finish(product_key, asin, reservation_id, "uncertain")
                report["failed"] += 1
            else:
                self._finish(product_key, asin, reservation_id, "sent")
                report["sent"] += 1
        return report
