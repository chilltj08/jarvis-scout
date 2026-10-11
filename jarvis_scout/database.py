"""SQLite persistence with atomic scans, upserts and immutable decision snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from jarvis_scout.models import AmazonListing, SupplierProduct, json_safe, supplier_from_dict, utc_now


SCHEMA_VERSION = 2
LEGACY_DEMO_SOURCE = "Demo UK Supplier (fictional)"


def _observation_time(value: str) -> datetime:
    """Compare instants, rather than lexicographically sorting timezone offsets."""
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Supplier observed_at must be an ISO timestamp with a timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Supplier observed_at must include a timezone")
    return result.astimezone(timezone.utc)


def _with_provenance(payload: dict) -> dict:
    """Annotate returned legacy snapshots without rewriting historical evidence."""
    result = dict(payload)
    supplier = result.get("supplier")
    if isinstance(supplier, dict):
        supplier = dict(supplier)
        if "is_demo" not in supplier:
            supplier["is_demo"] = supplier.get("source") == LEGACY_DEMO_SOURCE
        result["supplier"] = supplier
    is_demo = isinstance(supplier, dict) and supplier.get("is_demo") is True
    listing = result.get("listing")
    simulated = listing.get("simulated", result.get("simulated", False)) if isinstance(listing, dict) else result.get("simulated", False)
    result["data_mode"] = "demo" if is_demo else "mixed" if simulated else "real"
    return result


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            # executescript implicitly commits before execution. Individual
            # statements keep schema and payload migration in one transaction.
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database was created by a newer JARVIS SCOUT version")
            statements = ("""
                CREATE TABLE IF NOT EXISTS products (
                    product_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""", """
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    qualified INTEGER NOT NULL,
                    review INTEGER NOT NULL,
                    unmatched INTEGER NOT NULL,
                    settings TEXT NOT NULL
                )""", """
                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
                    product_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    review_note TEXT NOT NULL DEFAULT ''
                )""", "CREATE INDEX IF NOT EXISTS opportunities_scan ON opportunities(scan_id)")
            for statement in statements:
                connection.execute(statement)
            if version < SCHEMA_VERSION:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(products)")}
                if "updated_at" not in columns:
                    connection.execute("ALTER TABLE products ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
                    connection.execute("UPDATE products SET updated_at=imported_at")
                for row in connection.execute("SELECT * FROM products").fetchall():
                    payload = json.loads(row["payload"])
                    if "is_demo" not in payload:
                        payload["is_demo"] = payload.get("source") == LEGACY_DEMO_SOURCE
                    key = supplier_from_dict(payload).product_key if payload["is_demo"] is True else row["product_key"]
                    connection.execute("UPDATE products SET product_key=?, payload=? WHERE product_key=?",
                                       (key, json.dumps(payload), row["product_key"]))
            connection.execute("""
                CREATE TABLE IF NOT EXISTS manual_comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_key TEXT NOT NULL REFERENCES products(product_key),
                    asin TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(product_key, asin)
                )""")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def upsert_products(self, products: list[SupplierProduct]) -> int:
        latest: dict[str, tuple[SupplierProduct, datetime]] = {}
        for product in products:
            if type(product.is_demo) is not bool:
                raise ValueError("Supplier is_demo must be a boolean")
            observed = _observation_time(product.observed_at)
            previous = latest.get(product.product_key)
            if previous is None or observed >= previous[1]:
                latest[product.product_key] = (product, observed)
        applied = 0
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for key, (product, observed) in latest.items():
                previous = connection.execute("SELECT payload FROM products WHERE product_key=?", (key,)).fetchone()
                payload = json_safe(product)
                if previous is not None:
                    previous_payload = json.loads(previous["payload"])
                    try:
                        previous_time = _observation_time(previous_payload.get("observed_at"))
                    except ValueError:
                        previous_time = None
                    if previous_time is not None and observed < previous_time:
                        continue
                    if previous_payload == payload:
                        continue
                now = utc_now()
                connection.execute("""
                    INSERT INTO products(product_key,payload,imported_at,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(product_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at
                """, (key, json.dumps(payload), now, now))
                applied += 1
        return applied

    def products(self, is_demo: bool | None = None) -> list[SupplierProduct]:
        return [supplier_from_dict(row["supplier"]) for row in self.product_rows(is_demo)]

    def product_rows(self, is_demo: bool | None = None) -> list[dict]:
        if is_demo is not None and type(is_demo) is not bool:
            raise ValueError("is_demo filter must be a boolean or None")
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM products ORDER BY imported_at, product_key").fetchall()
        result = []
        for row in rows:
            supplier = json_safe(supplier_from_dict(json.loads(row["payload"])))
            if is_demo is not None and supplier.get("is_demo", False) is not is_demo:
                continue
            result.append({"product_key": row["product_key"], "supplier": supplier,
                           "imported_at": row["imported_at"], "updated_at": row["updated_at"],
                           "observed_at": supplier.get("observed_at")})
        return result

    def save_manual_comparison(self, product_key: str, listing: AmazonListing) -> int:
        from jarvis_scout.manual import validate_manual_listing

        if not isinstance(listing, AmazonListing) or listing.simulated:
            raise ValueError("Manual comparison requires a real Amazon observation")
        validate_manual_listing(listing)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            product = connection.execute("SELECT payload FROM products WHERE product_key=?", (product_key,)).fetchone()
            if product is None:
                raise ValueError("Supplier product does not exist")
            if json.loads(product["payload"]).get("is_demo", False):
                raise ValueError("Manual comparison requires a real supplier product")
            connection.execute("""
                INSERT INTO manual_comparisons(product_key,asin,payload,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(product_key,asin) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at
            """, (product_key, listing.asin, json.dumps(json_safe(listing)), utc_now()))
            row = connection.execute("SELECT id FROM manual_comparisons WHERE product_key=? AND asin=?",
                                     (product_key, listing.asin)).fetchone()
        return row["id"]

    def manual_comparisons(self, product_key: str | None = None) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM manual_comparisons" +
                                      (" WHERE product_key=?" if product_key is not None else "") +
                                      " ORDER BY id DESC", (product_key,) if product_key is not None else ()).fetchall()
        return [{"id": row["id"], "product_key": row["product_key"],
                 "listing": json.loads(row["payload"]), "updated_at": row["updated_at"]} for row in rows]

    def delete_manual_comparison(self, comparison_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM manual_comparisons WHERE id=?", (comparison_id,))
            if cursor.rowcount != 1:
                raise ValueError("Manual comparison does not exist")

    def save_scan(self, records: list[dict], settings: dict) -> tuple[int, list[dict]]:
        counts = {
            "total": len(records), "qualified": sum(bool(row["qualified"]) for row in records),
            "review": sum(row["match"]["status"] == "review" for row in records),
            "unmatched": sum(row["match"]["status"] == "unmatched" for row in records),
        }
        saved = []
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO scans(created_at,total,qualified,review,unmatched,settings) VALUES(?,?,?,?,?,?)",
                (utc_now(), *counts.values(), json.dumps(json_safe(settings))),
            )
            scan_id = cursor.lastrowid
            for row in records:
                payload = _with_provenance(json_safe(row))
                cursor = connection.execute(
                    "INSERT INTO opportunities(scan_id,product_key,payload) VALUES(?,?,?)",
                    (scan_id, row["product_key"], json.dumps(payload)),
                )
                saved.append({**payload, "id": cursor.lastrowid, "scan_id": scan_id, "review_note": ""})
        return scan_id, saved

    def latest_scan_id(self) -> int | None:
        with self.connect() as connection:
            return connection.execute("SELECT MAX(scan_id) FROM scans").fetchone()[0]

    def list_scans(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM scans ORDER BY scan_id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "settings": json.loads(row["settings"])} for row in rows]

    def opportunities(self, scan_id: int | None = None) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM opportunities" + (" WHERE scan_id=?" if scan_id is not None else "") + " ORDER BY id DESC",
                (scan_id,) if scan_id is not None else (),
            ).fetchall()
        return [{**_with_provenance(json.loads(row["payload"])), "id": row["id"], "scan_id": row["scan_id"],
                 "review_note": row["review_note"]} for row in rows]

    def set_review_note(self, record_id: int, note: str):
        if len(note) > 4000:
            raise ValueError("Review notes must be at most 4000 characters")
        with self.connect() as connection:
            cursor = connection.execute("UPDATE opportunities SET review_note=? WHERE id=?", (note, record_id))
            if cursor.rowcount != 1:
                raise ValueError("Record does not exist")
