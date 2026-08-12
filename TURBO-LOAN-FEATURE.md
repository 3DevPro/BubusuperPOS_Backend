# Turbo Loan (สินเชื่อรายย่อย) — สถานะปัจจุบัน + สิ่งที่ควรเพิ่ม

เอกสารนี้เจาะเฉพาะโมดูล **สินเชื่อ (Loan)** ในฟีเจอร์ Turbo — เป็นลูกของแผนใหญ่ทั้งฟีเจอร์ Turbo
(`TURBO-FEATURE-PLAN.md` ในโฟลเดอร์โปรเจกต์หลัก, เฟส 1.2/2.1 บันไดวงเงิน)
บันทึกไว้ว่า **ทำอะไรไปแล้ว**, **มันทำงานยังไง**, และ **ฟีเจอร์ที่ควรเพิ่ม**

อัปเดตล่าสุด: 2026-08-11

---

## 1. ทำงานอยู่ตอนนี้ (สถานะ: ใช้งานได้จริง ต่อ API ครบวงจร)

วงจรชีวิตสินเชื่อครบทุกขั้นตอน ตั้งแต่เสนอราคาจนถึงผ่อนชำระ:

```
ดูสินค้าสินเชื่อ (products)
        ↓
ขอใบเสนอราคา (quote) — คำนวณวงเงินที่อนุมัติได้ตาม credit tier + LTV หลักประกัน
        ↓
ยื่นขอ (application) — บันทึก snapshot ของ income profile + credit tier ตอนยื่น
        ↓
อนุมัติจ่ายเงิน (disburse) — auto-approve ระดับ prototype, สร้างตารางผ่อนทั้งหมดทันที
        ↓
บัญชีสินเชื่อ (account) — ดูยอดคงค้าง, งวดถัดไป, สถานะค้างชำระ
        ↓
จ่ายค่างวด (payment) — จ่ายผ่าน QR (PromptPay payload เดียวกับ POS)
```

### Backend (repo นี้)

| Endpoint | ใช้ทำอะไร |
|---|---|
| `GET /api/v1/turbo/loans/products` | รายการสินเชื่อ 4 ประเภท (มอเตอร์ไซค์/รถยนต์/รถไถ/โฉนดที่ดิน) |
| `POST /api/v1/turbo/loans/quote` | คำนวณวงเงินที่อนุมัติได้ (ไม่บันทึก) |
| `POST /api/v1/turbo/loans/applications` | ยื่นขอสินเชื่อ (บันทึก snapshot) |
| `GET /api/v1/turbo/loans/applications` | ดูประวัติที่เคยยื่น |
| `POST /api/v1/turbo/loans/applications/{id}/disburse` | อนุมัติ+จ่ายเงิน → สร้างบัญชี+ตารางผ่อน |
| `GET /api/v1/turbo/loans/account` | สรุปบัญชีที่ active (มีได้บัญชีเดียวต่อร้าน) |
| `GET /api/v1/turbo/loans/account/{id}/installments` | ตารางผ่อนทั้งหมด |
| `POST /api/v1/turbo/loans/installments/{id}/payment` | จ่ายค่างวด |
| `GET /api/v1/turbo/credit-standing` | เครดิตเทียร์ปัจจุบัน (รวม income + ประวัติผ่อน) |

**ไฟล์หลัก:** `app/models/turbo/loan.py`, `app/services/turbo/{loan,credit}_service.py`,
`app/schemas/turbo/loan.py`, `app/api/v1/turbo/loan.py`, ค่าคงที่ทั้งหมดอยู่ที่เดียวใน
`app/core/turbo_config.py`

### Frontend (BubusuperPOS_Frontend repo)

- `lib/features/turbo/loan_apply_screen.dart` — เลือกสินค้า/ขอใบเสนอราคา/ยื่นขอ
- `lib/features/turbo/loan_account_screen.dart` — สรุปบัญชี + ตารางผ่อน
- `lib/features/turbo/loan_payment_screen.dart` — จ่ายค่างวดผ่าน QR
- `lib/features/turbo/turbo_repository.dart` — เรียก API ครบทุก endpoint ข้างบน ไม่มีจุดขาด
- แจ้งเตือนค้างชำระอยู่ 2 จุด: banner บนหน้าแรก Turbo (`turbo_home_screen.dart`) และ tag
  ต่องวดในตารางผ่อน (`loan_account_screen.dart`)

### บันไดวงเงิน (credit tier)

| เทียร์ | เงื่อนไข | วงเงิน |
|---|---|---|
| Tier 1 | streak รายได้ ≥ 30 วัน | ฿10,000 |
| Tier 2 | ผ่อนตรงเวลา ≥ 3 งวด | ฿30,000 |
| Tier 3 | ผ่อนตรงเวลา ≥ 6 งวด | ฿50,000 |

"ตรงเวลา" ให้ grace 3 วันหลังกำหนด (`LOAN_LATE_GRACE_DAYS`) วงเงินที่อนุมัติจริงยังถูก
บีบเพิ่มด้วย LTV ของหลักประกัน (มอเตอร์ไซค์/รถยนต์ 70%, รถไถ 60%, โฉนด 50%)

### อัปเดตล่าสุด (2026-08-11) — แจ้งเตือนค้างชำระแบบมีรายละเอียด

เดิมแจ้งแค่ boolean (`has_overdue` / `is_overdue`) ไม่บอกว่าค้างกี่งวด/เท่าไหร่/กี่วัน
เพิ่มฟิลด์ที่คำนวณจาก `due_date` ตอน request (ไม่มี column ใหม่ ไม่มี migration):

- ต่องวด: `days_overdue: int | None`
- ต่อบัญชี: `overdue_count`, `overdue_amount`, `max_days_overdue`

**ก่อน:** `มีงวดค้างชำระ — ควรชำระโดยเร็ว` / `เกินกำหนด`
**หลัง:** `ค้างชำระ 2 งวด รวม 1,891.20 บาท (ช้าสุด 5 วัน)` / `เกินกำหนด 5 วัน`

Commit: frontend `53c4ad5`, backend `b982168` (ทั้งสอง repo push ขึ้น `main` แล้ว)
ยืนยันแล้วด้วย automated test (`pytest tests/test_turbo_loan.py` ผ่าน 20/20) และ
เรียก API จริงบน dev server เห็นค่าตรงตามที่ตั้งใจ

---

## 2. ข้อจำกัดของ prototype ตอนนี้ (ไม่ใช่บั๊ก แต่ต้องรู้ก่อนขึ้นเวที/ก่อนต่อยอด)

- **ไม่มี e-KYC / เครดิตบูโรจริง** — `disburse()` auto-approve ทุกครั้งที่ยื่น ไม่มีการตรวจสอบภายนอก
- **จ่ายค่างวดเป็น self-reported QR** — `paid_reference` เป็น freeform text ที่ผู้จ่ายพิมพ์เอง
  ไม่ได้ verify กับ settlement feed ของธนาคาร/ผู้ให้บริการจริง
- **ไม่มี webhook/reconciliation ภายนอก** — ทุกสถานะอิงจากสิ่งที่แอปนี้บันทึกเอง
- **PromptPay biller ID เป็น placeholder** (`TURBO_BILLER_PROMPTPAY_ID`) ไม่ใช่ผู้รับเงินจริง
- **ไม่มี late fee / ดอกเบี้ยผิดนัด** — ค้างชำระแค่แสดงผล ไม่มีผลต่อยอดหนี้หรือดอกเบี้ย
- **ไม่มี auto-reminder แบบ push/SMS** — ผู้ใช้ต้องเปิดแอปเองถึงจะเห็น banner

---

## 3. ฟีเจอร์ที่ควรเพิ่ม (แนะนำ ไม่ใช่ backlog ผูกมัด)

เรียงตามคุณค่า/ความง่ายในการทำโดยประมาณ:

1. **Push notification ก่อนครบกำหนด + ตอนเกินกำหนด** — ตอนนี้ต้องเปิดแอปเองถึงเห็น
   `DUE_REMINDER_DAYS` มีอยู่แล้วที่ backend (7 วันก่อนครบกำหนด) แค่ยังไม่มีช่องทางส่ง
   ออกไปนอกแอป เป็นตัวช่วยลดอัตราค้างชำระได้ตรงจุดที่สุด
2. **ค่าปรับ/ดอกเบี้ยผิดนัดชำระ (late fee)** — ตอนนี้ overdue แค่ "แสดงผล" ไม่มีผลทางการเงิน
   จริง ถ้าจะใช้งานจริงต้องมี logic คิดดอกเบี้ยผิดนัดและอัปเดต `amount_due`
3. **ประวัติค้างชำระผูกกับ trust score** — เฟส 2.1 ในแผนใหญ่มี `trust_service.py` อยู่แล้ว
   แต่ยังไม่ได้เอาข้อมูล overdue จากสินเชื่อไปรวมเป็นสัญญาณ ควรผูกสองอย่างนี้เข้าด้วยกัน
4. **ต่อ payment gateway จริง** — แทนที่ QR แบบ self-report ด้วยการยืนยันผ่าน PromptPay
   settlement API จริง หรือ payment provider (Omise/2C2P ฯลฯ) เพื่อปิดช่องโกง
5. **แจ้งเตือนใน chatbot** — เพิ่ม tool อ่านอย่างเดียวใน `BubusuperPOS_chatbot/app/ai/tools.py`
   เช่น `get_overdue_installments` ให้ถาม "ค้างชำระอยู่เท่าไหร่" ผ่านแชทได้ (pattern เดียวกับ
   `get_income_certificate` ที่มีอยู่แล้ว)
6. **Partial payment** — ตอนนี้จ่ายค่างวดต้องจ่ายเต็มจำนวนต่องวด ยังไม่รองรับจ่ายบางส่วน
7. **Early repayment / ปิดบัญชีก่อนกำหนด** — ยังไม่มี endpoint สำหรับโปะเงินต้นหรือปิดสัญญา
8. **Loan restructuring** — กรณีค้างนานผิดปกติ ควรมี flow เจรจา/ยืดงวด แทนที่จะปล่อยค้างเรื่อยๆ
9. **Export ใบแจ้งหนี้/statement เป็น PDF** — ใช้ pattern เดียวกับ `receipt_pdf.dart` ที่มีอยู่แล้ว
   ในระบบ POS หลัก
10. **Real e-KYC + เครดิตบูโร** — จำเป็นก่อนใช้งานจริงนอกเดโม (ปัจจุบัน auto-approve ทั้งหมด)

---

## 4. อ้างอิง

- แผนใหญ่ทั้งฟีเจอร์ Turbo: `TURBO-FEATURE-PLAN.md` (โฟลเดอร์โปรเจกต์หลัก `BubusuperPOS/`)
- ค่าคงที่ทั้งหมด (บันไดวงเงิน, LTV, grace period ฯลฯ): `app/core/turbo_config.py`
- Demo seed (ร้านไก่ทอด, สินเชื่อมอเตอร์ไซค์ ฿10,000): `scripts/seed_demo.py`
