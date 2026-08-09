from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.ai.product_lookup_provider import ProductLookupProvider
from app.core.config import settings
from app.core.tenancy import TenantContext
from app.models.product import BarcodeLookupCache
from app.schemas.product import ProductLookupResponse


def _cache_row_to_response(row: BarcodeLookupCache, cached: bool) -> ProductLookupResponse:
    return ProductLookupResponse(
        found=row.found,
        name=row.name,
        image_url=row.image_url,
        price=row.price,
        currency=row.currency,
        brand=row.brand,
        source=row.source,
        cached=cached,
    )


async def lookup_barcode(
    ctx: TenantContext, provider: ProductLookupProvider, barcode: str
) -> ProductLookupResponse:
    """Shared across every tenant (see BarcodeLookupCache's docstring) so a
    barcode is only ever sent to the external provider once system-wide,
    keeping a free-tier daily quota from being exhausted by duplicate scans
    of the same product across different shops."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.product_lookup_cache_days)
    cached_row = await ctx.db.scalar(
        select(BarcodeLookupCache).where(
            BarcodeLookupCache.barcode == barcode, BarcodeLookupCache.fetched_at >= cutoff
        )
    )
    if cached_row is not None:
        return _cache_row_to_response(cached_row, cached=True)

    info = await provider.lookup(barcode)

    row = await ctx.db.get(BarcodeLookupCache, barcode)
    if row is None:
        row = BarcodeLookupCache(barcode=barcode)
        ctx.db.add(row)

    row.found = info is not None
    row.name = info.name if info else None
    row.image_url = info.image_url if info else None
    row.price = info.price if info else None
    row.currency = info.currency if info else None
    row.brand = info.brand if info else None
    row.source = info.source if info else None
    row.fetched_at = datetime.now(timezone.utc)
    await ctx.db.commit()
    await ctx.db.refresh(row)

    return _cache_row_to_response(row, cached=False)
