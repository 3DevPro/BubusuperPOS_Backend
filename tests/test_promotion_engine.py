import uuid
from datetime import datetime, time, timezone
from decimal import Decimal

from app.services.promotion_engine import (
    CartLineProduct,
    LoadedPromotion,
    LoadedPromotionTarget,
    RequestedLine,
    resolve,
)

_NOON_MONDAY = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)  # a Monday


def _product(price="50.00", cost="20.00", category_id=None):
    return CartLineProduct(
        id=uuid.uuid4(), name="สินค้า", sell_price=Decimal(price), cost_price=Decimal(cost), category_id=category_id
    )


def _promo(
    kind,
    scope="all",
    percent_off=None,
    amount_off=None,
    buy_qty=None,
    get_qty=None,
    bundle_price=None,
    coupon_code=None,
    stackable=False,
    priority=100,
    targets=None,
    starts_at=None,
    ends_at=None,
    active_weekdays=None,
    daily_start_time=None,
    daily_end_time=None,
    max_uses=None,
    uses_count=0,
):
    return LoadedPromotion(
        id=uuid.uuid4(),
        name="โปรโมชั่น",
        kind=kind,
        scope=scope,
        percent_off=Decimal(percent_off) if percent_off else None,
        amount_off=Decimal(amount_off) if amount_off else None,
        buy_qty=buy_qty,
        get_qty=get_qty,
        bundle_price=Decimal(bundle_price) if bundle_price else None,
        coupon_code=coupon_code,
        starts_at=starts_at,
        ends_at=ends_at,
        active_weekdays=active_weekdays,
        daily_start_time=daily_start_time,
        daily_end_time=daily_end_time,
        max_uses=max_uses,
        uses_count=uses_count,
        priority=priority,
        stackable=stackable,
        targets=targets or [],
    )


def test_no_promotions_leaves_line_untouched():
    product = _product()
    req = RequestedLine(product=product, qty=2, manual_discount=Decimal("0"))
    result = resolve([req], [], None, _NOON_MONDAY)

    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.promo_discount == Decimal("0")
    assert line.line_total == Decimal("100.00")
    assert result.applied == []


def test_percent_off_scope_all_applies():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    promo = _promo("percent_off", scope="all", percent_off="10")
    result = resolve([req], [promo], None, _NOON_MONDAY)

    assert result.lines[0].promo_discount == Decimal("10.00")
    assert result.lines[0].line_total == Decimal("90.00")
    assert result.applied[0].discount_amount == Decimal("10.00")


def test_percent_off_scope_product_only_applies_to_targeted_product():
    target_product = _product(price="100.00")
    other_product = _product(price="100.00")
    promo = _promo(
        "percent_off", scope="product", percent_off="10", targets=[LoadedPromotionTarget(product_id=target_product.id, category_id=None)]
    )
    req_target = RequestedLine(product=target_product, qty=1, manual_discount=Decimal("0"))
    req_other = RequestedLine(product=other_product, qty=1, manual_discount=Decimal("0"))
    result = resolve([req_target, req_other], [promo], None, _NOON_MONDAY)

    by_product = {line.product_id: line for line in result.lines}
    assert by_product[target_product.id].promo_discount == Decimal("10.00")
    assert by_product[other_product.id].promo_discount == Decimal("0")


def test_amount_off_never_exceeds_gross():
    product = _product(price="5.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    promo = _promo("amount_off", scope="all", amount_off="20.00")
    result = resolve([req], [promo], None, _NOON_MONDAY)

    assert result.lines[0].promo_discount == Decimal("5.00")
    assert result.lines[0].line_total == Decimal("0.00")


def test_best_of_two_competing_auto_promotions_wins():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    weak = _promo("percent_off", scope="all", percent_off="5")
    strong = _promo("amount_off", scope="all", amount_off="30.00")
    result = resolve([req], [weak, strong], None, _NOON_MONDAY)

    assert result.lines[0].promo_discount == Decimal("30.00")
    assert result.lines[0].promotion_id == strong.id


def test_buy_n_get_m_splits_into_paid_and_free_lines():
    product = _product(price="50.00")
    req = RequestedLine(product=product, qty=3, manual_discount=Decimal("0"))
    promo = _promo("buy_n_get_m", scope="all", buy_qty=2, get_qty=1)
    result = resolve([req], [promo], None, _NOON_MONDAY)

    assert len(result.lines) == 2
    paid = next(l for l in result.lines if not l.is_free_gift)
    free = next(l for l in result.lines if l.is_free_gift)
    assert paid.qty == 2
    assert paid.promo_discount == Decimal("0")
    assert free.qty == 1
    assert free.promo_discount == Decimal("50.00")
    assert free.line_total == Decimal("0.00")
    assert free.cost_snapshot == Decimal("20.00")  # real cost still tracked


def test_buy_n_get_m_incomplete_group_gets_no_free_unit():
    # buy 2 get 1 (group size 3); qty=5 -> only one full group of 3, 2
    # units left over don't form a second group.
    product = _product(price="50.00")
    req = RequestedLine(product=product, qty=5, manual_discount=Decimal("0"))
    promo = _promo("buy_n_get_m", scope="all", buy_qty=2, get_qty=1)
    result = resolve([req], [promo], None, _NOON_MONDAY)

    paid = next(l for l in result.lines if not l.is_free_gift)
    free = next(l for l in result.lines if l.is_free_gift)
    assert paid.qty == 4
    assert free.qty == 1


def test_buy_n_for_price_bundles_and_prices_remainder_normally():
    product = _product(price="50.00")
    req = RequestedLine(product=product, qty=4, manual_discount=Decimal("0"))
    promo = _promo("buy_n_for_price", scope="all", buy_qty=3, bundle_price="120.00")
    result = resolve([req], [promo], None, _NOON_MONDAY)

    bundled = next(l for l in result.lines if l.promotion_id is not None)
    remainder = next(l for l in result.lines if l.promotion_id is None)
    assert bundled.qty == 3
    assert bundled.promo_discount == Decimal("30.00")  # 150 - 120
    assert remainder.qty == 1
    assert remainder.promo_discount == Decimal("0")
    assert remainder.line_total == Decimal("50.00")


def test_promotion_outside_date_window_does_not_apply():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    promo = _promo(
        "percent_off",
        scope="all",
        percent_off="10",
        starts_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    result = resolve([req], [promo], None, _NOON_MONDAY)
    assert result.lines[0].promo_discount == Decimal("0")


def test_promotion_restricted_to_weekday_skips_wrong_day():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    # Tuesday-only ("0100000"), evaluated on a Monday.
    promo = _promo("percent_off", scope="all", percent_off="10", active_weekdays="0100000")
    result = resolve([req], [promo], None, _NOON_MONDAY)
    assert result.lines[0].promo_discount == Decimal("0")


def test_promotion_daily_time_window_wraps_midnight():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    # "Happy hour" 22:00-02:00 — noon is outside it.
    promo = _promo(
        "percent_off",
        scope="all",
        percent_off="10",
        daily_start_time=time(22, 0),
        daily_end_time=time(2, 0),
    )
    result = resolve([req], [promo], None, _NOON_MONDAY)
    assert result.lines[0].promo_discount == Decimal("0")

    late_night = _NOON_MONDAY.replace(hour=23)
    result2 = resolve([req], [promo], None, late_night)
    assert result2.lines[0].promo_discount == Decimal("10.00")


def test_max_uses_exhausted_excludes_promotion():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    promo = _promo("percent_off", scope="all", percent_off="10", max_uses=5, uses_count=5)
    result = resolve([req], [promo], None, _NOON_MONDAY)
    assert result.lines[0].promo_discount == Decimal("0")


def test_stackable_coupon_layers_on_top_of_auto_promo():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    auto = _promo("percent_off", scope="all", percent_off="10")
    coupon = _promo("amount_off", scope="all", amount_off="5.00", coupon_code="SAVE5", stackable=True)
    result = resolve([req], [auto, coupon], "save5", _NOON_MONDAY)

    assert result.lines[0].promo_discount == Decimal("15.00")  # 10 + 5
    assert result.coupon_matched is True


def test_non_stackable_weaker_coupon_does_not_override_stronger_auto_promo():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    strong_auto = _promo("amount_off", scope="all", amount_off="30.00")
    weak_coupon = _promo("percent_off", scope="all", percent_off="5", coupon_code="WEAK5", stackable=False)
    result = resolve([req], [strong_auto, weak_coupon], "WEAK5", _NOON_MONDAY)

    assert result.lines[0].promo_discount == Decimal("30.00")
    assert result.lines[0].promotion_id == strong_auto.id
    assert result.coupon_matched is True


def test_non_stackable_stronger_coupon_wins_alone():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    weak_auto = _promo("percent_off", scope="all", percent_off="5")
    strong_coupon = _promo("amount_off", scope="all", amount_off="40.00", coupon_code="BIG40", stackable=False)
    result = resolve([req], [weak_auto, strong_coupon], "BIG40", _NOON_MONDAY)

    assert result.lines[0].promo_discount == Decimal("40.00")
    assert result.lines[0].promotion_id == strong_coupon.id


def test_unknown_coupon_code_matches_nothing_but_does_not_error():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("0"))
    result = resolve([req], [], "NOPE", _NOON_MONDAY)
    assert result.coupon_matched is False
    assert result.lines[0].promo_discount == Decimal("0")


def test_manual_discount_combines_with_promo_discount_in_line_total():
    product = _product(price="100.00")
    req = RequestedLine(product=product, qty=1, manual_discount=Decimal("10.00"))
    promo = _promo("percent_off", scope="all", percent_off="10")
    result = resolve([req], [promo], None, _NOON_MONDAY)

    line = result.lines[0]
    assert line.promo_discount == Decimal("10.00")
    assert line.manual_discount == Decimal("10.00")
    assert line.line_total == Decimal("80.00")


def test_category_scope_matches_only_targeted_category():
    category_id = uuid.uuid4()
    in_category = _product(price="100.00", category_id=category_id)
    out_of_category = _product(price="100.00", category_id=uuid.uuid4())
    promo = _promo(
        "percent_off", scope="category", percent_off="10", targets=[LoadedPromotionTarget(product_id=None, category_id=category_id)]
    )
    req_in = RequestedLine(product=in_category, qty=1, manual_discount=Decimal("0"))
    req_out = RequestedLine(product=out_of_category, qty=1, manual_discount=Decimal("0"))
    result = resolve([req_in, req_out], [promo], None, _NOON_MONDAY)

    by_product = {line.product_id: line for line in result.lines}
    assert by_product[in_category.id].promo_discount == Decimal("10.00")
    assert by_product[out_of_category.id].promo_discount == Decimal("0")
