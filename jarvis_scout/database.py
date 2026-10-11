"""SQLite persistence with atomic scans, upserts and immutable decision snapshots."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from jarvis_scout.models import SupplierProduct, json_safe, supplier_from_dict, utc_now


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Database was created by a newer JARVIS SCOUT version")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS products (
                    product_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    qualified INTEGER NOT NULL,
                    review INTEGER NOT NULL,
                    unmatched INTEGER NOT NULL,
                    settings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
                    product_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    review_note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS opportunities_scan ON opportunities(scan_id);
                PRAGMA user_version = 1;
            """)

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
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO products VALUES (?, ?, ?) ON CONFLICT(product_key) DO UPDATE SET payload=excluded.payload, imported_at=excluded.imported_at",
                [(product.product_key, json.dumps(json_safe(product)), utc_now()) for product in products],
            )
        return len(products)

    def products(self) -> list[SupplierProduct]:
        with self.connect() as connection:
            return [supplier_from_dict(json.loads(row[0])) for row in connection.execute(
                "SELECT payload FROM products ORDER BY imported_at, product_key")]

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
                payload = json_safe(row)
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
        return [{**json.loads(row["payload"]), "id": row["id"], "scan_id": row["scan_id"],
                 "review_note": row["review_note"]} for row in rows]

    def set_review_note(self, record_id: int, note: str):
        if len(note) > 4000:
            raise ValueError("Review notes must be at most 4000 characters")
        with self.connect() as connection:
            cursor = connection.execute("UPDATE opportunities SET review_note=? WHERE id=?", (note, record_id))
            if cursor.rowcount != 1:
                raise ValueError("Record does not exist")
