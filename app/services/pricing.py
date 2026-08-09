"""Shared discount/VAT math for checkout and refunds — the two call sites
must never compute totals with different formulas, so both funnel through
calc_totals() here instead of each doing their own arithmetic."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class TotalsResult:
    subtotal: Decimal  # sum of line_totals: post item-discount, pre sale-discount, pre-tax
    discount: Decimal  # sale-level discount actually applied
    tax: Decimal
    total: Decimal


def calc_totals(
    line_totals: list[Decimal],
    sale_discount: Decimal,
    vat_enabled: bool,
    vat_rate: Decimal,
    price_includes_tax: bool,
) -> TotalsResult:
    """Order: per-item discount (baked into line_totals by the caller) -> sum
    to subtotal -> sale-level discount -> VAT computed on the discounted net,
    not on raw subtotal, so a receipt-level discount reduces the taxable base."""
    subtotal = sum(line_totals, Decimal("0"))
    net = subtotal - sale_discount
    if net < 0:
        raise ValueError("discount exceeds subtotal")

    if not vat_enabled or vat_rate == 0:
        return TotalsResult(subtotal, sale_discount, Decimal("0"), _quantize(net))

    if price_includes_tax:
        # Price already contains VAT — back it out for disclosure; total is unchanged.
        tax = net * vat_rate / (Decimal("100") + vat_rate)
        total = net
    else:
        tax = net * vat_rate / Decimal("100")
        total = net + tax

    return TotalsResult(subtotal, sale_discount, _quantize(tax), _quantize(total))


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
