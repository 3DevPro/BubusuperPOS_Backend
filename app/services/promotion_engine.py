"""Pure discount-resolution logic for the cart — mirrors app/services/
pricing.py in being DB-free and therefore unit-testable in isolation, and in
running entirely *before* pricing.py: this module turns a requested cart
into per-line totals, and pricing.calc_totals() sums those into subtotal/
tax/total unchanged. pricing.py needs no edits because of this ordering.

Order of discounts on a line:
  1. gross = price * qty            (server-read from catalog, never client)
  2. promo_discount, computed here against gross
  3. the cashier's manual discount, subtracted after promo_discount
  4. discount + promo_discount must never exceed gross

At most one *auto-applied* promotion (no coupon_code) wins per product:
whichever yields the highest baht value across percent_off/amount_off/
buy_n_get_m/buy_n_for_price, ties broken by (priority ascending, id). A
stackable coupon may additionally layer a percent/amount discount on top of
that; coupons don't stack with buy_n_get_m/buy_n_for_price — a giveaway
combined with a coupon has no unambiguous single interpretation, so v1
doesn't attempt one.

"buy N get M free" and "buy N for price" both potentially split one
requested line into two ResolvedLines (paid + free, or bundled + leftover)
because a free-gift unit must render as its own "(แถมฟรี)" line on the
receipt and carry its own is_free_gift flag for report_service's profit
math — folding its value into the paid line's discount would hide it from
the receipt and understate stock decremented per line.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class CartLineProduct:
    """The subset of Product fields the engine needs, passed in rather than
    the ORM model so this module stays DB-free."""

    id: UUID
    name: str
    sell_price: Decimal
    cost_price: Decimal
    category_id: UUID | None


@dataclass
class RequestedLine:
    product: CartLineProduct
    qty: int
    manual_discount: Decimal


@dataclass
class LoadedPromotionTarget:
    product_id: UUID | None
    category_id: UUID | None


@dataclass
class LoadedPromotion:
    id: UUID
    name: str
    kind: str  # PromotionKind value
    scope: str  # PromotionScope value
    percent_off: Decimal | None
    amount_off: Decimal | None
    buy_qty: int | None
    get_qty: int | None
    bundle_price: Decimal | None
    coupon_code: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    active_weekdays: str | None
    daily_start_time: time | None
    daily_end_time: time | None
    max_uses: int | None
    uses_count: int
    priority: int
    stackable: bool
    targets: list[LoadedPromotionTarget] = field(default_factory=list)

    def matches_product(self, product: CartLineProduct) -> bool:
        if self.scope == "all":
            return True
        if self.scope == "product":
            return any(t.product_id == product.id for t in self.targets)
        if self.scope == "category":
            return product.category_id is not None and any(
                t.category_id == product.category_id for t in self.targets
            )
        return False

    def is_valid_at(self, when: datetime) -> bool:
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False
        if self.starts_at is not None and when < self.starts_at:
            return False
        if self.ends_at is not None and when > self.ends_at:
            return False
        if self.active_weekdays is not None and len(self.active_weekdays) == 7:
            if self.active_weekdays[when.weekday()] != "1":  # Monday=0, matches datetime.weekday()
                return False
        if self.daily_start_time is not None and self.daily_end_time is not None:
            t = when.time()
            if self.daily_start_time <= self.daily_end_time:
                if not (self.daily_start_time <= t <= self.daily_end_time):
                    return False
            elif not (t >= self.daily_start_time or t <= self.daily_end_time):  # wraps midnight
                return False
        return True


@dataclass
class ResolvedLine:
    product_id: UUID
    name_snapshot: str
    price_snapshot: Decimal
    cost_snapshot: Decimal
    qty: int
    manual_discount: Decimal
    promo_discount: Decimal
    promotion_id: UUID | None
    promotion_name_snapshot: str | None
    is_free_gift: bool

    @property
    def gross(self) -> Decimal:
        return self.price_snapshot * self.qty

    @property
    def line_total(self) -> Decimal:
        return self.gross - self.manual_discount - self.promo_discount


@dataclass
class AppliedPromotion:
    promotion_id: UUID
    name: str
    discount_amount: Decimal


@dataclass
class ResolvedCart:
    lines: list[ResolvedLine]
    applied: list[AppliedPromotion]
    # True only when a coupon_code was given AND matched a promotion — lets
    # the caller reject an entered-but-invalid code instead of silently
    # applying nothing (see sales_service.create_sale / sales.preview).
    coupon_matched: bool
    # Always zero in v1 — the seam for a future order-total-threshold
    # discount pass, which would run after subtotal is known, without
    # changing this function's signature.
    sale_level_discount: Decimal = Decimal("0")


def _percent_or_amount_discount(promo: LoadedPromotion, gross: Decimal) -> Decimal:
    if promo.kind == "percent_off":
        return _quantize(gross * (promo.percent_off or Decimal(0)) / Decimal(100))
    if promo.kind == "amount_off":
        return min(promo.amount_off or Decimal(0), gross)
    return Decimal("0")


def _promo_value(promo: LoadedPromotion, req: RequestedLine) -> Decimal:
    """Total baht value the promo would deliver on this line — used only to
    rank candidate auto-promotions against each other, not to build the
    final ResolvedLine(s)."""
    price = req.product.sell_price
    qty = req.qty
    if promo.kind in ("percent_off", "amount_off"):
        return _percent_or_amount_discount(promo, price * qty)
    if promo.kind == "buy_n_get_m":
        if not promo.buy_qty or not promo.get_qty:
            return Decimal("0")
        group = promo.buy_qty + promo.get_qty
        free_units = (qty // group) * promo.get_qty
        return price * free_units
    if promo.kind == "buy_n_for_price":
        if not promo.buy_qty or promo.bundle_price is None:
            return Decimal("0")
        groups = qty // promo.buy_qty
        per_group_value = max(price * promo.buy_qty - promo.bundle_price, Decimal("0"))
        return groups * per_group_value
    return Decimal("0")


def _build_lines_for_promo(promo: LoadedPromotion | None, req: RequestedLine) -> list[ResolvedLine]:
    price = req.product.sell_price
    cost = req.product.cost_price
    qty = req.qty
    base = {
        "product_id": req.product.id,
        "name_snapshot": req.product.name,
        "price_snapshot": price,
        "cost_snapshot": cost,
    }

    if promo is None or promo.kind in ("percent_off", "amount_off"):
        discount = _percent_or_amount_discount(promo, price * qty) if promo else Decimal("0")
        return [
            ResolvedLine(
                **base,
                qty=qty,
                manual_discount=req.manual_discount,
                promo_discount=discount,
                promotion_id=promo.id if promo else None,
                promotion_name_snapshot=promo.name if promo else None,
                is_free_gift=False,
            )
        ]

    if promo.kind == "buy_n_get_m" and promo.buy_qty and promo.get_qty:
        group = promo.buy_qty + promo.get_qty
        free_units = (qty // group) * promo.get_qty
        paid_units = qty - free_units
        lines = []
        if paid_units > 0:
            lines.append(
                ResolvedLine(
                    **base,
                    qty=paid_units,
                    manual_discount=req.manual_discount,
                    promo_discount=Decimal("0"),
                    promotion_id=promo.id,
                    promotion_name_snapshot=promo.name,
                    is_free_gift=False,
                )
            )
        if free_units > 0:
            lines.append(
                ResolvedLine(
                    **base,
                    qty=free_units,
                    manual_discount=Decimal("0"),
                    promo_discount=price * free_units,
                    promotion_id=promo.id,
                    promotion_name_snapshot=promo.name,
                    is_free_gift=True,
                )
            )
        return lines or [
            ResolvedLine(
                **base,
                qty=qty,
                manual_discount=req.manual_discount,
                promo_discount=Decimal("0"),
                promotion_id=None,
                promotion_name_snapshot=None,
                is_free_gift=False,
            )
        ]

    if promo.kind == "buy_n_for_price" and promo.buy_qty and promo.bundle_price is not None:
        groups = qty // promo.buy_qty
        bundled_units = groups * promo.buy_qty
        remainder_units = qty - bundled_units
        lines = []
        if bundled_units > 0:
            bundle_discount = max(price * bundled_units - groups * promo.bundle_price, Decimal("0"))
            lines.append(
                ResolvedLine(
                    **base,
                    qty=bundled_units,
                    manual_discount=req.manual_discount if remainder_units == 0 else Decimal("0"),
                    promo_discount=bundle_discount,
                    promotion_id=promo.id,
                    promotion_name_snapshot=promo.name,
                    is_free_gift=False,
                )
            )
        if remainder_units > 0:
            lines.append(
                ResolvedLine(
                    **base,
                    qty=remainder_units,
                    manual_discount=req.manual_discount if bundled_units == 0 else Decimal("0"),
                    promo_discount=Decimal("0"),
                    promotion_id=None,
                    promotion_name_snapshot=None,
                    is_free_gift=False,
                )
            )
        return lines

    return [
        ResolvedLine(
            **base,
            qty=qty,
            manual_discount=req.manual_discount,
            promo_discount=Decimal("0"),
            promotion_id=None,
            promotion_name_snapshot=None,
            is_free_gift=False,
        )
    ]


def resolve(
    requested: list[RequestedLine],
    promotions: list[LoadedPromotion],
    coupon_code: str | None,
    evaluated_at: datetime,
) -> ResolvedCart:
    valid = [p for p in promotions if p.is_valid_at(evaluated_at)]
    auto_promos = [p for p in valid if p.coupon_code is None]
    coupon_promo = None
    if coupon_code:
        coupon_promo = next(
            (p for p in valid if p.coupon_code and p.coupon_code.upper() == coupon_code.strip().upper()), None
        )

    all_lines: list[ResolvedLine] = []

    for req in requested:
        # A coupon — stackable or not — competes as a normal candidate for
        # "best single promotion" on this line: entering a coupon that's
        # worse than an already-running auto-promo must not make the
        # customer worse off. "Stackable" only controls the separate layer
        # applied below, on top of whichever promo wins here.
        candidates = [p for p in auto_promos if p.matches_product(req.product)]
        if coupon_promo is not None and coupon_promo.matches_product(req.product):
            candidates = [*candidates, coupon_promo]

        best: LoadedPromotion | None = None
        best_value = Decimal("0")
        for p in candidates:
            value = _promo_value(p, req)
            if value <= 0:
                continue
            if value > best_value or (
                value == best_value and best is not None and (p.priority, p.id) < (best.priority, best.id)
            ):
                best, best_value = p, value

        lines = _build_lines_for_promo(best, req)

        # A stackable coupon layers an *additional* percent/amount discount
        # on top of the auto-promo that won above — but only when the
        # coupon itself isn't already the winner (no double-counting), and
        # never on split free-gift/bundle lines (see module docstring).
        if (
            coupon_promo is not None
            and best is not coupon_promo
            and coupon_promo.stackable
            and coupon_promo.kind in ("percent_off", "amount_off")
            and coupon_promo.matches_product(req.product)
            and len(lines) == 1
            and not lines[0].is_free_gift
        ):
            line = lines[0]
            remaining = line.gross - line.manual_discount - line.promo_discount
            extra = min(_percent_or_amount_discount(coupon_promo, line.gross), max(remaining, Decimal("0")))
            if extra > 0:
                line.promo_discount += extra
                if line.promotion_id is None:
                    line.promotion_id = coupon_promo.id
                    line.promotion_name_snapshot = coupon_promo.name

        all_lines.extend(lines)

    applied_totals: dict[UUID, AppliedPromotion] = {}
    for line in all_lines:
        if line.promotion_id is None or line.promo_discount <= 0:
            continue
        if line.promotion_id in applied_totals:
            applied_totals[line.promotion_id].discount_amount += line.promo_discount
        else:
            applied_totals[line.promotion_id] = AppliedPromotion(
                promotion_id=line.promotion_id,
                name=line.promotion_name_snapshot or "",
                discount_amount=line.promo_discount,
            )

    return ResolvedCart(
        lines=all_lines,
        applied=list(applied_totals.values()),
        # A valid, unexpired code that just doesn't match anything in this
        # particular cart still counts as "matched" — whether it discounts
        # anything depends on cart contents, which is normal coupon
        # behavior, not an invalid code.
        coupon_matched=coupon_promo is not None,
    )
