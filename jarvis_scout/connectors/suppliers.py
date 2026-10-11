"""Explicit seams for supplier feeds and supplier-authorised APIs."""
from typing import Iterable, Protocol

from jarvis_scout.models import SupplierProduct


class SupplierConnector(Protocol):
    def fetch_products(self) -> Iterable[SupplierProduct]: ...


class PermittedSupplierAPI:
    """Implement using a supplier's documented API and permission, never browser evasion.

    A real connector must validate currency, prices per pack, delivery allocation,
    source timestamps, API rate limits, pagination and authorisation before ingest.
    Credentials should be read from environment and never written to product records.
    """

    def fetch_products(self) -> Iterable[SupplierProduct]:
        raise NotImplementedError("Connect a supplier-authorised API before using this source.")
