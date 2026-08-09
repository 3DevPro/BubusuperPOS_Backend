import uuid

from .conftest import auth_headers, create_customer, create_product, enable_loyalty, signup


async def _checkout(client, headers, product_id, qty=1, customer_id=None, redeem_points=0):
    body = {
        "client_uuid": str(uuid.uuid4()),
        "items": [{"product_id": product_id, "qty": qty}],
        "payment_method": "cash",
    }
    if customer_id is not None:
        body["customer_id"] = customer_id
    if redeem_points:
        body["redeem_points"] = redeem_points
    return await client.post("/api/v1/sales", json=body, headers=headers)


async def test_points_earned_on_sale_with_customer_and_loyalty_enabled(client):
    tokens = await signup(client, "Shop A", "Owner A", "loyalty-a@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers)  # 25 baht/point, 1 baht/point redemption
    product = await create_product(client, headers, "เค้ก", sell_price="250.00", stock_qty=5)
    customer = await create_customer(client, headers, "คุณลูกค้า")

    resp = await _checkout(client, headers, product["id"], customer_id=customer["id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["points_earned"] == 10
    assert body["customer_id"] == customer["id"]

    fetched = await client.get(f"/api/v1/customers/{customer['id']}", headers=headers)
    assert fetched.json()["points_balance"] == 10


async def test_no_points_earned_without_customer(client):
    tokens = await signup(client, "Shop B", "Owner B", "loyalty-b@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers)
    product = await create_product(client, headers, "ขนม", sell_price="100.00", stock_qty=5)

    resp = await _checkout(client, headers, product["id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["points_earned"] == 0


async def test_no_points_earned_when_loyalty_disabled(client):
    tokens = await signup(client, "Shop C", "Owner C", "loyalty-c@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "น้ำ", sell_price="100.00", stock_qty=5)
    customer = await create_customer(client, headers, "คุณลูกค้าซี")

    resp = await _checkout(client, headers, product["id"], customer_id=customer["id"])
    assert resp.status_code == 201, resp.text
    assert resp.json()["points_earned"] == 0


async def test_redeem_points_reduces_total_and_balance(client):
    tokens = await signup(client, "Shop D", "Owner D", "loyalty-d@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers)
    product = await create_product(client, headers, "กาแฟ", sell_price="250.00", stock_qty=10)
    customer = await create_customer(client, headers, "คุณลูกค้าดี")

    # First sale earns 10 points (250 / 25).
    first = await _checkout(client, headers, product["id"], customer_id=customer["id"])
    assert first.json()["points_earned"] == 10

    # Second sale redeems 5 of them — point_value_baht defaults to 1.00.
    second = await _checkout(client, headers, product["id"], customer_id=customer["id"], redeem_points=5)
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["points_redeemed"] == 5
    assert body["points_discount"] == "5.00"
    assert body["total"] == "245.00"

    fetched = await client.get(f"/api/v1/customers/{customer['id']}", headers=headers)
    # 10 earned - 5 redeemed + 9 earned on the ฿245 paid (245 // 25 = 9) = 14
    assert fetched.json()["points_balance"] == 14


async def test_redeem_more_points_than_balance_is_rejected(client):
    tokens = await signup(client, "Shop E", "Owner E", "loyalty-e@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers)
    product = await create_product(client, headers, "ชาเย็น", sell_price="100.00", stock_qty=5)
    customer = await create_customer(client, headers, "คุณลูกค้าอี")

    resp = await _checkout(client, headers, product["id"], customer_id=customer["id"], redeem_points=5)
    assert resp.status_code == 400


async def test_redeem_points_without_customer_is_rejected(client):
    tokens = await signup(client, "Shop F", "Owner F", "loyalty-f@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers)
    product = await create_product(client, headers, "ขนมปัง", sell_price="100.00", stock_qty=5)

    resp = await _checkout(client, headers, product["id"], redeem_points=1)
    assert resp.status_code == 400


async def test_redeem_points_when_loyalty_disabled_is_rejected(client):
    tokens = await signup(client, "Shop G", "Owner G", "loyalty-g@example.com")
    headers = auth_headers(tokens)
    product = await create_product(client, headers, "โดนัท", sell_price="100.00", stock_qty=5)
    customer = await create_customer(client, headers, "คุณลูกค้าจี")

    resp = await _checkout(client, headers, product["id"], customer_id=customer["id"], redeem_points=1)
    assert resp.status_code == 400


async def test_points_discount_never_exceeds_sale_total(client):
    """point_value_baht is set high enough that redeeming the customer's full
    balance would overshoot the bill — the discount must cap at the total,
    never go negative."""
    tokens = await signup(client, "Shop H", "Owner H", "loyalty-h@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers, baht_per_point="25.00", point_value_baht="10.00")
    product = await create_product(client, headers, "เค้กชิ้นใหญ่", sell_price="250.00", stock_qty=10)
    customer = await create_customer(client, headers, "คุณลูกค้าเอช")

    first = await _checkout(client, headers, product["id"], customer_id=customer["id"])
    assert first.json()["points_earned"] == 10  # 10 points, worth ฿100 each if redeemed = ฿1000

    small_sale_product = await create_product(client, headers, "ลูกอม", sell_price="20.00", stock_qty=10)
    second = await _checkout(
        client, headers, small_sale_product["id"], customer_id=customer["id"], redeem_points=10
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["total"] == "0.00"
    assert body["points_discount"] == "20.00"


async def test_refund_does_not_claw_back_points(client):
    tokens = await signup(client, "Shop I", "Owner I", "loyalty-i@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers)
    product = await create_product(client, headers, "พิซซ่า", sell_price="250.00", stock_qty=5)
    customer = await create_customer(client, headers, "คุณลูกค้าไอ")

    sale = (await _checkout(client, headers, product["id"], customer_id=customer["id"])).json()
    assert sale["points_earned"] == 10

    refund = await client.post(f"/api/v1/sales/{sale['id']}/refunds", json={"client_uuid": str(uuid.uuid4())}, headers=headers)
    assert refund.status_code == 201, refund.text

    fetched = await client.get(f"/api/v1/customers/{customer['id']}", headers=headers)
    assert fetched.json()["points_balance"] == 10


async def _multi_item_checkout(client, headers, product_ids, customer_id, redeem_points):
    return await client.post(
        "/api/v1/sales",
        json={
            "client_uuid": str(uuid.uuid4()),
            "items": [{"product_id": pid, "qty": 1} for pid in product_ids],
            "customer_id": customer_id,
            "redeem_points": redeem_points,
        },
        headers=headers,
    )


async def test_partial_refund_of_points_paid_sale_returns_no_cash(client):
    """A bill settled entirely with points cost the customer nothing in cash,
    so refunding one of its lines must hand back nothing — the line's value
    was paid in points, not money."""
    tokens = await signup(client, "Shop J", "Owner J", "loyalty-j@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers, baht_per_point="25.00", point_value_baht="10.00")
    customer = await create_customer(client, headers, "คุณลูกค้าเจ")

    big = await create_product(client, headers, "เค้กใหญ่", sell_price="250.00", stock_qty=10)
    first = await _checkout(client, headers, big["id"], customer_id=customer["id"])
    assert first.json()["points_earned"] == 10  # worth ฿100 each if redeemed

    candy = await create_product(client, headers, "ลูกอม", sell_price="20.00", stock_qty=10)
    gum = await create_product(client, headers, "หมากฝรั่ง", sell_price="20.00", stock_qty=10)
    sale = (
        await _multi_item_checkout(client, headers, [candy["id"], gum["id"]], customer["id"], 10)
    ).json()
    assert sale["total"] == "0.00"
    assert sale["points_discount"] == "40.00"

    candy_line = next(i for i in sale["items"] if i["product_id"] == candy["id"])
    refund = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": candy_line["id"], "qty": 1}]},
        headers=headers,
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["refund_amount"] == "0.00"

    gum_line = next(i for i in sale["items"] if i["product_id"] == gum["id"])
    second_refund = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": gum_line["id"], "qty": 1}]},
        headers=headers,
    )
    assert second_refund.status_code == 201, second_refund.text
    # Never negative: the closing refund reconciles against what's left, and
    # nothing before it was allowed to overshoot.
    assert second_refund.json()["refund_amount"] == "0.00"

    fetched = (await client.get(f"/api/v1/sales/{sale['id']}", headers=headers)).json()
    assert fetched["refunded_total"] == "0.00"
    assert fetched["status"] == "refunded"


async def test_partial_refund_of_part_points_paid_sale_returns_cash_share_only(client):
    """Points covered half the bill — a refunded line gives back only the
    half the customer actually paid in cash, and the refunds still sum to
    exactly what the sale was worth."""
    tokens = await signup(client, "Shop K", "Owner K", "loyalty-k@example.com")
    headers = auth_headers(tokens)
    await enable_loyalty(client, headers, baht_per_point="25.00", point_value_baht="10.00")
    customer = await create_customer(client, headers, "คุณลูกค้าเค")

    big = await create_product(client, headers, "เค้กใหญ่", sell_price="250.00", stock_qty=10)
    assert (await _checkout(client, headers, big["id"], customer_id=customer["id"])).json()["points_earned"] == 10

    a = await create_product(client, headers, "น้ำส้ม", sell_price="50.00", stock_qty=10)
    b = await create_product(client, headers, "น้ำแอปเปิล", sell_price="50.00", stock_qty=10)
    # 100 baht bill, 4 points redeemed = 40 baht off, so 60 baht paid in cash.
    sale = (await _multi_item_checkout(client, headers, [a["id"], b["id"]], customer["id"], 4)).json()
    assert sale["total"] == "60.00"
    assert sale["points_discount"] == "40.00"

    a_line = next(i for i in sale["items"] if i["product_id"] == a["id"])
    first = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": a_line["id"], "qty": 1}]},
        headers=headers,
    )
    assert first.json()["refund_amount"] == "30.00"

    b_line = next(i for i in sale["items"] if i["product_id"] == b["id"])
    second = await client.post(
        f"/api/v1/sales/{sale['id']}/refunds",
        json={"client_uuid": str(uuid.uuid4()), "items": [{"sale_item_id": b_line["id"], "qty": 1}]},
        headers=headers,
    )
    assert second.json()["refund_amount"] == "30.00"

    fetched = (await client.get(f"/api/v1/sales/{sale['id']}", headers=headers)).json()
    assert fetched["refunded_total"] == "60.00"  # exactly the cash the customer paid
