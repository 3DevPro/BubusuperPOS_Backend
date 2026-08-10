"""Single place for the numeric assumptions behind Turbo's income/insurance
features. Every constant here is a demo-stage hypothesis (see the case
booklet's own appendix, which says the same about its numbers) — not a
calibrated business figure. Keeping them all in one file means calibrating
before a real pitch/launch is one edit, not a hunt through
income_service/insurance_service/claim_service."""

from decimal import Decimal

# ── Income certificate / credit eligibility ──
STREAK_DAYS_REQUIRED = 30
# Cash is self-reported and unverifiable; QR/card land in a bank record the
# tenant didn't type themselves. Counting cash at partial weight toward the
# credit-eligible average (not toward the plain avg_daily_revenue shown to
# the tenant) is the anti-gaming lever from the case's "data integrity" slide.
CASH_CREDIT_WEIGHT = Decimal("0.5")
# Tier 1 is a flat starter limit unlocked purely by the streak, not scaled to
# revenue — see the case's "วงเงินโตตามประวัติผ่อน ไม่ใช่ตามยอดที่แจ้ง".
# Tiers 2/3 (30,000 / 50,000) require actual repayment history, which this
# codebase doesn't model yet (no loan/repayment tracking exists), so they're
# intentionally not implemented here.
TIER_1_CREDIT_LIMIT = Decimal("10000")
# Tiers 2/3 unlock from the tenant's own repayment history on this app's own
# loans (see app/services/turbo/credit_service.py + loan_service.py) — the
# "grows with repayment history, not with self-reported revenue" lever the
# case's data-integrity slide asks for. Streak alone still gates tier 1;
# on-time installment count gates 2/3 on top of that.
TIER_2_CREDIT_LIMIT = Decimal("30000")
TIER_3_CREDIT_LIMIT = Decimal("50000")
TIER_2_ON_TIME_PAYMENTS = 3
TIER_3_ON_TIME_PAYMENTS = 6
# An installment paid within this many days after its due date still counts
# as "on time" toward tier progress — a same-day bank queue or a day's delay
# shouldn't reset progress the way an actual default should.
LOAN_LATE_GRACE_DAYS = 3

# ── Secured loans (Engine 1 — the case's four collateral products) ──
# Loan-to-value ceiling per collateral kind — approved_amount is also capped
# by the tenant's credit tier (see loan_service.quote), so this only binds
# when the collateral is worth less than the tier would otherwise allow.
LOAN_LTV: dict[str, Decimal] = {
    "motorcycle": Decimal("0.70"),
    "car": Decimal("0.70"),
    "tractor": Decimal("0.60"),
    "land_title": Decimal("0.50"),
}
# Not a real PromptPay biller — this prototype's loan-payment QR encodes this
# placeholder ID via the same buildPromptPayPayload() the POS checkout uses,
# but no settlement/reconciliation against it exists (see loan_service.pay_installment).
TURBO_BILLER_PROMPTPAY_ID = "0000000000000"
# An installment due within this many days surfaces the "ครบกำหนดชำระ" banner.
DUE_REMINDER_DAYS = 7

# ── Micro-insurance (Engine 1) ──
# Quoted daily benefit = 50% of avg_daily_revenue — set at half on purpose so
# there's no financial incentive to stay "sick" rather than reopen the shop.
DAILY_BENEFIT_RATIO = Decimal("0.5")
# Illustrative only — the case booklet's own pitch deck says as much about
# every number in it ("เบี้ยเป็นช่วงตัวอย่างเพื่อสื่อขนาดการตัดสินใจ ต้อง
# กำหนดร่วมกับพันธมิตรผู้รับประกันและกรอบของ คปภ."). Daily premium =
# daily_benefit * this rate, e.g. a ฿1,500 daily benefit prices at ~฿5/day.
DAILY_INCOME_PREMIUM_RATE = Decimal("0.0035")
# Reasons a DailyClose is treated as insurance-eligible lost income — a
# "holiday" or "other" close is a real record but not a claimable event.
CLAIMABLE_CLOSE_REASONS = ("sick", "accident")
