"""Barcode -> product data providers, mirroring the LLMProvider pattern in
llm_provider.py: a narrow Protocol so the service/endpoint layer never depends
on a specific vendor, concrete classes are cheap to construct (no network at
__init__), and every failure mode (timeout, 429, malformed response, unknown
barcode) collapses to `return None` rather than an exception — a scanned
barcode either resolves to product data or it doesn't, and either way the
caller just falls back to manual entry."""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ProductInfo:
    name: str
    image_url: str | None
    price: Decimal | None
    currency: str | None
    brand: str | None
    source: str


class ProductLookupProvider(Protocol):
    async def lookup(self, barcode: str) -> ProductInfo | None:
        """Returns product data for a barcode, or None if not found / the
        provider is unreachable. Never raises."""
        ...


class OpenFoodFactsProvider:
    """https://world.openfoodfacts.org — free, no API key, food/grocery only.
    Carries no price data at all."""

    _BASE_URL = "https://world.openfoodfacts.org/api/v2/product"

    def __init__(self, timeout_seconds: int = 8):
        self._timeout = timeout_seconds

    async def lookup(self, barcode: str) -> ProductInfo | None:
        url = f"{self._BASE_URL}/{barcode}.json"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await asyncio.wait_for(
                    client.get(url, headers={"User-Agent": "loyverse-clone/1.0 (POS product lookup)"}),
                    timeout=self._timeout,
                )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, asyncio.TimeoutError, TimeoutError, ValueError) as exc:
            logger.warning("OpenFoodFacts lookup failed for %s: %s", barcode, exc)
            return None

        if data.get("status") != 1:
            return None
        product = data.get("product") or {}
        name = product.get("product_name_th") or product.get("product_name")
        if not name:
            return None
        return ProductInfo(
            name=name,
            image_url=product.get("image_url") or product.get("image_front_url"),
            price=None,
            currency=None,
            brand=product.get("brands"),
            source="openfoodfacts",
        )


class UpcItemDbProvider:
    """https://www.upcitemdb.com — free trial endpoint (~100 lookups/day,
    shared per server IP, no API key needed), general merchandise. Reported
    prices are aggregated third-party offers, almost always in USD."""

    _URL = "https://api.upcitemdb.com/prod/trial/lookup"

    def __init__(self, timeout_seconds: int = 8):
        self._timeout = timeout_seconds

    async def lookup(self, barcode: str) -> ProductInfo | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await asyncio.wait_for(
                    client.get(self._URL, params={"upc": barcode}),
                    timeout=self._timeout,
                )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, asyncio.TimeoutError, TimeoutError, ValueError) as exc:
            logger.warning("UPCitemdb lookup failed for %s: %s", barcode, exc)
            return None

        items = data.get("items") or []
        if not items:
            return None
        item = items[0]
        name = item.get("title")
        if not name:
            return None

        price, currency = None, None
        offers = item.get("offers") or []
        if offers:
            try:
                price = Decimal(str(offers[0]["price"]))
                currency = offers[0].get("currency") or "USD"
            except (KeyError, InvalidOperation, TypeError):
                price = None
                currency = None

        images = item.get("images") or []
        return ProductInfo(
            name=name,
            image_url=images[0] if images else None,
            price=price,
            currency=currency,
            brand=item.get("brand"),
            source="upcitemdb",
        )


class ChainedProductLookup:
    """Tries each provider in order, returning the first hit. A provider
    that errors or finds nothing is indistinguishable to the caller — both
    just mean "try the next one"."""

    def __init__(self, providers: list[ProductLookupProvider]):
        self._providers = providers

    async def lookup(self, barcode: str) -> ProductInfo | None:
        for provider in self._providers:
            result = await provider.lookup(barcode)
            if result is not None:
                return result
        return None
