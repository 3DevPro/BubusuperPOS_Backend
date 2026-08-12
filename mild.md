# แผนงาน: กระบวนการพิจารณาสินเชื่อแบบมีขั้นตอน (Loan Review Pipeline)

## Context

**ปัญหาวันนี้:** ร้านค้ากด "ยื่นขอสินเชื่อ" แล้วหน้าจอถัดไปมีปุ่ม "รับเงินทันที (เดโม)" ให้กดเบิกเงินได้เลย — `loan_service.disburse()` ข้ามจาก `submitted` ไป `disbursed` ตรงๆ ไม่มีการรอ ไม่มีคนตรวจ ไม่มีพนักงานเกี่ยวข้อง ทั้งที่ `LoanApplicationStatus` มีค่า `approved`/`rejected` ประกาศไว้แล้วแต่ **ไม่มีโค้ดไหนเขียนค่าพวกนี้เลย** และ `assigned_branch_id` ถูกเซ็ตไว้ตอนยื่น (`loan_service.py:213`) แต่ **ไม่มี query ไหนอ่านมัน** — พนักงานสาขาจึงมองไม่เห็นคำขอสินเชื่อเลยแม้แต่รายการเดียว

หน้าจอเดิมถึงกับเขียนกำกับตัวเองไว้ว่า *"ปุ่มนี้เป็นการเดโมเท่านั้น — ของจริงต้องรอสาขายืนยันก่อนเบิกจ่าย"* (`loan_apply_screen.dart:364`) งานนี้คือการทำประโยคนั้นให้เป็นจริง

**ผลลัพธ์ที่ต้องการ:** คำขอสินเชื่อเดินผ่าน 5 ขั้น มีพนักงานสาขาเป็นคนตรวจจริงผ่านหน้า `/branch` เดิม ร้านค้าเห็นสถานะขยับเองระหว่างรอ และยังเดโมบนเวทีที่มีเวลาจำกัดได้

**การตัดสินใจที่ล็อกแล้ว:**

| หัวข้อ | ที่เลือก |
|---|---|
| ผู้อนุมัติ | พนักงานสาขา (`branch_champion`) ใช้หน้า `/branch` เดิม เพิ่มแท็บที่ 4 |
| จำนวนขั้น | 5 ขั้น (เพิ่มตรวจเอกสาร + ประเมินหลักประกัน) |
| การอัปเดตฝั่งร้าน | Polling อัตโนมัติทุก ~10 วิ ตอนอยู่หน้ารอผล |
| ทางลัดเดโม | มี — ปุ่มเร่งเวลา + นาฬิกาเลื่อนขั้นอัตโนมัติ |
| เอกสาร/หลักประกัน | Checklist เท่านั้น ไม่มีอัปโหลดไฟล์ (ไม่มี storage infra และจะไม่สร้าง) |
| ถูกปฏิเสธ | ยื่นใหม่ได้ แต่ต้องรอ cooldown 7 วัน + ต้องมีเหตุผลปฏิเสธเสมอ |

---

## ข้อจำกัดของสถาปัตยกรรมที่กำหนดรูปร่างแผนนี้

1. **ไม่มีระบบแจ้งเตือน/ตัวตั้งเวลาใดๆ เลย** — ไม่มี push/email/SMS/websocket/SSE/celery/scheduler/background task `app/main.py` มี 17 บรรทัด (CORS + router) ทุกอย่างเป็น pull-only และคำนวณตอน request
2. **`BranchContext` ไม่ใช่ `TenantContext`** (`app/core/branch_scope.py:10-18` อธิบายเจตนาไว้ชัด) — พนักงานสาขาไม่มี `tenant_id` และไม่มีระบบ permission เลย (`UserRole.branch_champion` → `set()` ที่ `permissions.py:55`) แต่ `audit_service.record()` รับ `TenantContext` เท่านั้น → เรียกจากฝั่งสาขาไม่ได้
3. **`BranchContext.scoped()` ใช้กับ `LoanApplication` ไม่ได้** เพราะกรองด้วย `model.branch_id` แต่คอลัมน์จริงชื่อ `assigned_branch_id` → ต้องกรองเองแบบเดียวกับ `branch_service.list_leads()` (`branch_service.py:114-122` มีคอมเมนต์อธิบายไว้แล้ว)

---

## 1. Data model

### 1.1 ขยาย enum เดิม ไม่สร้างคอลัมน์ `stage` แยก

`app/models/turbo/loan.py:43-47`:

```python
class LoanApplicationStatus(str, enum.Enum):
    submitted = "submitted"
    doc_review = "doc_review"              # ใหม่
    collateral_check = "collateral_check"  # ใหม่
    under_review = "under_review"          # ใหม่
    approved = "approved"
    disbursed = "disbursed"
    rejected = "rejected"
```

`approved`/`rejected` อยู่บนแกนเดียวกับ 5 ขั้นอยู่แล้ว การมี 2 คอลัมน์จะสร้างสถานะที่ขัดกันเอง (`status=approved` แต่ `stage=doc_review`) และ `disburse()` ต้องเช็ค 2 ที่ ฝั่ง Flutter `LoanApplicationDto.status` เป็น `String` ธรรมดาอยู่แล้ว (`turbo_repository.dart:317`) จึงไม่พังตาม

### 1.2 คอลัมน์ใหม่บน `turbo_loan_applications`

| คอลัมน์ | ชนิด | เหตุผล |
|---|---|---|
| `collateral_detail` | `JSONB NOT NULL DEFAULT '{}'` | checklist ที่ร้านพิมพ์เอง เช่น `{"registration_no":"กก 1234","brand_model":"Honda Wave 125i","year":"2562"}` — ใช้ JSONB ตามแบบ `income_profile_snapshot`/`cap_reasons` เดิม รูปร่างคุมด้วย Pydantic ที่ขอบ ไม่ใช่ที่ DB |
| `rejection_reason` | `String(500) NULL` | บังคับกรอกที่ schema (`Field(min_length=5)`) ไม่ใช่ที่ DB — ตามแบบกฎ "จำเป็นเฉพาะ transition นี้" อื่นๆ ในโปรเจกต์ |
| `stage_started_at` | `timestamptz NOT NULL DEFAULT now()` | จุดอ้างอิงของนาฬิกาเลื่อนขั้น **รีเซ็ตทุกครั้งที่เปลี่ยนสถานะ** |
| `reviewed_by_user_id` | `UUID NULL FK users.id` | พนักงานคนล่าสุดที่กด สำหรับแสดง "ตรวจโดย …" |

`decided_at` เดิมคงไว้ แต่ตอนนี้ถูกเซ็ตตอน `approved`/`rejected`/`disbursed` (เดิมมีแค่ `disbursed`) — cooldown อ่านจากตัวนี้

**index ใหม่:** `ix_turbo_loan_applications_assigned_branch_id` — คอลัมน์นี้ถูกเขียนตั้งแต่ `loan_service.py:213` แต่ไม่เคยมีใครอ่าน ตอนนี้คิวของสาขาจะกรองด้วยมันทุก 10 วินาที และยังไม่มี index

### 1.3 ตารางใหม่ `turbo_loan_application_events`

```python
class LoanApplicationEvent(Base):
    """ประวัติการเปลี่ยนสถานะ — จงใจ*ไม่*ใช้ TenantScopedMixin เพราะผู้เขียน
    แถวนี้เป็นได้ทั้งเจ้าของร้าน (TenantContext) และพนักงานสาขา (BranchContext
    ที่ไม่มี tenant_id) จึงเก็บ actor_user_id ตรงๆ  AuditLog ใช้แทนไม่ได้
    เพราะไม่มี entity_id/entity_type (audit_log.py:13-25)"""
    __tablename__ = "turbo_loan_application_events"

    id              UUID pk
    application_id  UUID FK turbo_loan_applications.id, index
    tenant_id       UUID FK tenants.id, index      # denormalize ให้ร้านอ่าน timeline ตัวเองได้
    branch_id       UUID FK turbo_branches.id NULL # เซ็ตเมื่อพนักงานสาขาเป็นคนกด
    from_status     String(32) NULL               # NULL = แถวแรกตอนยื่น
    to_status       String(32)                    # String ไม่ใช่ PG enum — ดู §7
    actor_user_id   UUID FK users.id NULL         # NULL = ระบบเลื่อนเอง
    actor_name      String(255)                   # denormalize ตอนเขียน แบบ AuditLog.actor_name
    actor_kind      String(16)                    # 'merchant' | 'champion' | 'system'
    note            String(500) NULL              # เหตุผลปฏิเสธ / โน้ตพนักงาน
    created_at      timestamptz server_default now()
```

`to_status` เป็น `String(32)` โดยตั้งใจ — ledger แบบ append-only ไม่ควรต้อง migration ทุกครั้งที่ pipeline ยาวขึ้น และเลี่ยงข้อห้าม "ใช้ค่า enum ที่เพิ่งเพิ่มใน transaction เดียวกันไม่ได้" ไปเลย (§7)

### 1.4 Cooldown — คำนวณเอา ไม่เก็บ

ไม่มีคอลัมน์ `apply()` เช็คด้วย `SELECT max(decided_at) ... WHERE status='rejected'` เทียบกับ `LOAN_REJECT_COOLDOWN_DAYS` — ปรัชญาเดียวกับ `LoanInstallment.is_overdue` และ `credit_service.is_on_time` คือไม่ต้องมี scheduler มาคอยทำให้ถูก

---

## 2. แก้ปัญหา audit ข้ามขอบเขต (BranchContext)

**กฎขอบเขตที่ทำให้ปลอดภัย:** พนักงานสาขาไม่เคยได้ `tenant_id` จาก token ของตัวเอง แต่ได้จาก**แถวที่เขามีสิทธิ์อ่าน** ซึ่งถูกกรองด้วย `assigned_branch_id == ctx.branch_id` มาก่อนแล้ว — สะท้อน `branch_service.respond_lead` ที่ใช้ `Lead.assigned_branch_id == ctx.branch_id` ทุกประการ การข้าม scope เกิดขึ้นทางเดียว ผ่าน query ที่กรองแล้วจุดเดียว

`app/services/audit_service.py` — แยกแกนกลางออกมาโดยไม่แตะ signature เดิม (call site เดิม 25 จุดไม่ต้องแก้):

```python
async def record(ctx: TenantContext, action: str, summary: str) -> None:
    """signature เดิมทุกอย่าง — กลายเป็น delegate บางๆ เพื่อให้ AuditLog
    ถูกสร้างจากที่เดียว"""
    await record_external(ctx.db, ctx.tenant_id, ctx.user_id, action, summary)


async def record_external(db, tenant_id, actor_user_id, action, summary) -> None:
    """สำหรับผู้กระทำที่มีสิทธิ์กับข้อมูลของ tenant โดยไม่ได้เป็น user ของ
    tenant นั้น — วันนี้มีแค่ Branch Champion ที่ตรวจ LoanApplication
    (ดู app/core/branch_scope.py) tenant_id ต้องมาจากแถวที่กำลังกระทำ
    ไม่ใช่จาก token ของผู้เรียก: ผู้เรียกพิสูจน์สิทธิ์ต่อแถวนั้นด้วยการ
    match assigned_branch_id มาก่อนแล้ว  เหมือน record() คือไม่ commit เอง"""
```

ผลคือเจ้าของร้านเห็นในหน้า audit log ของตัวเองว่า *"สาขาอนุมัติคำขอสินเชื่อ"* ซึ่งควรเห็นอยู่แล้ว เพราะเป็นสินเชื่อของเขา

**ทางเลือกที่ตัดทิ้ง:** ผ่อน `record()` ให้รับ tenant_id/user_id เป็น optional (เปิดรูปแบบเรียกที่อันตรายให้ call site เดิม 25 จุดเพื่อประโยชน์ของจุดเดียว) · ตารางาudit ฝั่งสาขาแยก (ไม่มีใครอ่าน) · ไม่เขียน AuditLog เลย (เจ้าของร้านจะเห็นแค่ "ยื่นขอสินเชื่อ" แล้วอยู่ๆ ก็มี `LoanAccount` โผล่มาโดยไม่รู้ว่าใครอนุมัติ)

**โค้ดอยู่ไฟล์ใหม่ `app/services/turbo/loan_review_service.py`** — `loan_service.py` เป็น `TenantContext` ล้วนทั้งไฟล์ การ import `BranchContext` เข้าไปจะทำให้เส้นแบ่งที่ `branch_scope.py` ปกป้องอยู่เบลอ ส่วน `branch_service.py` ไม่รู้จักสินเชื่อเลย แยกไฟล์ทำให้จุดเดียวที่ 2 scope มาเจอกัน grep เจอได้

---

## 3. Backend — services และ endpoints

### 3.1 `app/core/turbo_config.py` — ส่วนใหม่

```python
# ── กระบวนการพิจารณาสินเชื่อ (Engine 1) ──
LOAN_REVIEW_STAGES = ("submitted", "doc_review", "collateral_check", "under_review")
# วินาทีที่แต่ละขั้นค้างอยู่ก่อนนาฬิกา (loan_service._auto_advance) เลื่อนเอง
# ตั้งให้เดินครบใน ~2 นาที เพื่อให้จบใน slot การพิตช์โดยไม่ต้องแตะเครื่องสาขา
LOAN_STAGE_AUTO_ADVANCE_SECONDS = {
    "submitted": 20, "doc_review": 30, "collateral_check": 30, "under_review": 40,
}
LOAN_AUTO_ADVANCE_ENABLED = True      # เปิดนาฬิกาทั้งระบบ
LOAN_AUTO_APPROVE_ENABLED = True      # under_review -> approved โดยไม่มีคน — เดโมเท่านั้น
LOAN_DEMO_FAST_FORWARD_ENABLED = True # เปิด endpoint เร่งเวลา
LOAN_REJECT_COOLDOWN_DAYS = 7         # ถูกปฏิเสธแล้วรอกี่วันจึงยื่นใหม่ได้
```

### 3.2 ตารางการเปลี่ยนสถานะ — dict ธรรมดา ไม่ใช่ state-machine class

ทั้งโปรเจกต์ไม่มี abstraction แบบนั้น และไม่ควรมีเพราะงานนี้:

```python
_ALLOWED_TRANSITIONS = {
    submitted:        (doc_review, rejected),
    doc_review:       (collateral_check, rejected),
    collateral_check: (under_review, rejected),
    under_review:     (approved, rejected),
    approved:         (),   # มีแต่ disburse() ฝั่งร้านที่เดินต่อได้
    rejected:         (),
    disbursed:        (),
}
```

guard ใช้ทรงเดิมของบ้าน: `if to_status not in _ALLOWED_TRANSITIONS[app.status]: raise HTTPException(400, "ไม่สามารถข้ามขั้นตอนได้")`

### 3.3 `loan_service.py` — ของเดิมที่ต้องแก้

**`apply()`** — เพิ่ม guard 2 ชั้นก่อน quote:

```python
_IN_FLIGHT = (submitted, doc_review, collateral_check, under_review, approved)

# 1. ทีละคำขอเท่านั้น
existing = await ctx.db.scalar(ctx.scoped(LoanApplication).where(LoanApplication.status.in_(_IN_FLIGHT)))
if existing is not None:
    raise HTTPException(400, "มีคำขอสินเชื่อที่กำลังพิจารณาอยู่แล้ว")

# 2. cooldown หลังถูกปฏิเสธ
last_rejected_at = await ctx.db.scalar(
    select(func.max(LoanApplication.decided_at)).where(
        LoanApplication.tenant_id == ctx.tenant_id,
        LoanApplication.status == LoanApplicationStatus.rejected,
    )
)
if last_rejected_at is not None:
    available_at = last_rejected_at + timedelta(days=LOAN_REJECT_COOLDOWN_DAYS)
    if now < available_at:
        days = (available_at - now).days + 1
        raise HTTPException(400, f"คำขอก่อนหน้าถูกปฏิเสธ ยื่นใหม่ได้ในอีก {days} วัน")
```

แล้วเซ็ต `collateral_detail`, `stage_started_at=now` และเขียน `LoanApplicationEvent` แถวแรก (`from_status=None`, `to_status="submitted"`, `actor_kind="merchant"`) ก่อน `commit()` เดิมที่มีอยู่แล้ว

**`disburse()`** — เปลี่ยน guard โดยเรียงลำดับให้ข้อความเฉพาะเจาะจงยังอยู่:

```python
if application.status == LoanApplicationStatus.disbursed:
    raise HTTPException(400, "สินเชื่อนี้เบิกจ่ายไปแล้ว")
if application.status != LoanApplicationStatus.approved:
    raise HTTPException(400, "คำขอนี้ยังไม่ได้รับอนุมัติ ไม่สามารถเบิกจ่ายได้")
```

ที่เหลือใน `disburse()` ไม่แตะเลย — `with_for_update()`, `IntegrityError` backstop, การเขียน installment ทั้งหมดคงไว้ เพิ่มแค่ event แถว `disbursed` ควบคู่กับ `audit_service.record("loan.disburse", ...)` เดิม

**ฟังก์ชันใหม่:** `_auto_advance(db, application, now) -> bool` (ดู §4) · `get_application(ctx, id)` คืน application + events + `next_stage_eta_seconds` + `can_reapply_at` (ตัวที่ร้าน poll) · `check_eligibility(ctx)` คืน `{can_apply, reason, cooldown_until, in_flight_application_id}` ให้ปุ่มยื่นถูก disable แทนที่จะโยน 400 ใส่หน้าผู้ใช้

`list_applications()` ก็เรียก `_auto_advance` ด้วย เพื่อให้หน้า turbo home ตรงกับหน้าสถานะ

### 3.4 `loan_review_service.py` — ฝั่งสาขา (รับ `BranchContext` ทุกตัว)

```python
async def list_review_queue(ctx, include_decided=False) -> list[LoanReviewItemResponse]
async def get_review_detail(ctx, application_id) -> LoanReviewDetailResponse
async def advance(ctx, application_id, to_status, note) -> LoanReviewDetailResponse
async def reject(ctx, application_id, reason) -> LoanReviewDetailResponse
```

`advance()`/`reject()` ใช้ `_load_for_update()` ร่วมกัน:

```python
application = await ctx.db.scalar(
    select(LoanApplication)
    .where(LoanApplication.id == application_id,
           LoanApplication.assigned_branch_id == ctx.branch_id)   # เส้นแบ่ง scope
    .with_for_update()
)
if application is None:
    raise HTTPException(404, "loan application not found")   # 404 ไม่ใช่ 403 ตามแบบ respond_lead
```

`with_for_update()` สำคัญตรงนี้ เพราะนาฬิกาอาจทำงานตอนร้าน poll พอดีกับจังหวะที่พนักงานกดปุ่ม

จากนั้นใช้ทรงเดิมของบ้านเป๊ะ: guard → assign field (`status`, `stage_started_at=now`, `reviewed_by_user_id=ctx.user_id`, `decided_at=now` เมื่อจบ, `rejection_reason`) → `LoanApplicationEvent(...)` → `audit_service.record_external(...)` → `commit()` ครั้งเดียว

### 3.5 Routes

**ฝั่งร้าน** — `app/api/v1/turbo/loan.py` ทุกอันใช้ `require(Permission.manage_loans)` เหมือนเดิม:

```
GET  /loans/applications/{application_id}                     -> LoanApplicationDetailResponse
GET  /loans/eligibility                                       -> LoanEligibilityResponse
POST /loans/applications/{application_id}/demo/fast-forward   -> LoanApplicationDetailResponse
```

`POST /loans/applications` เพิ่ม body `collateral_detail: LoanCollateralDetail` (Pydantic model ที่มี 4 field optional + `max_length` เพื่อให้รูปร่าง JSONB ถูก validate ที่ขอบ)

**ฝั่งสาขา** — `app/api/v1/turbo/branch.py` ใช้ `CurrentBranch`:

```
GET  /branch/loan-applications                                 -> list[LoanReviewItemResponse]
GET  /branch/loan-applications/{application_id}                -> LoanReviewDetailResponse
POST /branch/loan-applications/{application_id}/advance        body: {to_status, note?}
POST /branch/loan-applications/{application_id}/reject         body: {reason}
```

`LoanRejectRequest.reason: str = Field(min_length=5, max_length=500)` — ตรงนี้คือที่ที่ "เหตุผลจำเป็น" ถูกบังคับจริง และได้ 422 ที่ frontend รู้จักแสดงอยู่แล้ว

⚠️ **`LoanReviewItemResponse` ต้องไม่มี `income_profile_snapshot`** — นี่เป็นครั้งแรกที่พนักงานสาขาเห็นข้อมูลของ tenant response model จึงต้องระบุชัดว่าอะไรข้ามได้บ้าง พร้อม docstring กำกับ ประวัติยอดขายรายวันดิบไม่ควรข้ามไป `credit_tier_snapshot` พอสำหรับการพิจารณา (ส่งชื่อร้าน/เบอร์โทรที่ join จาก `Tenant`/`User` ได้)

---

## 4. นาฬิกาเลื่อนขั้นอัตโนมัติ + ปุ่มเร่งเวลา

### ทำไมต้อง "เขียนจริง" ไม่ใช่คำนวณตอนอ่าน

ต่างจาก `is_overdue` ตรงที่: การตัดสินใจของพนักงานต้องถูกบันทึก · `disburse()` ต้อง guard บน `approved` ที่มีอยู่จริงใน DB · timeline ต้องมีแถวจริง → จึงใช้ **lazy persisted advance** (นาฬิกาแบบ write-behind)

```
_auto_advance(db, application, now):
    if not LOAN_AUTO_ADVANCE_ENABLED: return False
    while application.status in LOAN_REVIEW_STAGES:
        elapsed = now - application.stage_started_at
        if elapsed < LOAN_STAGE_AUTO_ADVANCE_SECONDS[status]: break
        next_status = ตัวที่ไม่ใช่ rejected ใน _ALLOWED_TRANSITIONS[status]
        if next_status is approved and not LOAN_AUTO_APPROVE_ENABLED: break
        เปลี่ยนสถานะ; stage_started_at += วินาทีของขั้นนั้น  (ไม่ใช่ = now
            เพื่อให้การเว้นช่วงนานๆ ไล่เก็บหลายขั้นได้ถูกต้อง)
        เขียน event actor_kind='system', actor_name='ระบบ', actor_user_id=None
    return changed
```

**กฎเหล็ก: นาฬิกาไม่เคยปฏิเสธ และไม่เคยเบิกจ่าย** ปฏิเสธได้เฉพาะคน เบิกจ่ายได้เฉพาะร้าน

กฎข้อเดียวนี้คือสิ่งที่ทำให้ 2 ความต้องการอยู่ด้วยกันได้:
- **เดโมคนเดียวไม่มีพนักงาน** — 4 ขั้นเดินเองจนถึง `approved` ใน ~2 นาที
- **เดโม 2 เครื่อง (อันที่ดี)** — พนักงานกดเร็วกว่านาฬิกาเสมอ การกดจึงชนะทุกครั้ง และ `stage_started_at` รีเซ็ตทุกการกด นาฬิกาเป็นแค่ตาข่ายกันพิตช์ค้างถ้าไม่มีใครขับเครื่องที่สอง
- **โหมดของจริง** — ตั้ง `LOAN_AUTO_APPROVE_ENABLED = False` แล้ว pipeline จะจอดที่ `under_review` รอคนจริง ซึ่งคือสิ่งที่ production ต้องการ · ตั้ง `LOAN_AUTO_ADVANCE_ENABLED = False` แล้วทุกขั้นต้องใช้คน

### ปุ่มเร่งเวลา

`POST /loans/applications/{id}/demo/fast-forward` → ถ้าปิดอยู่ 404 มิฉะนั้นเซ็ต `stage_started_at = now - วินาทีของขั้นปัจจุบัน` แล้วเรียก `_auto_advance` ทันที คือ "ทำให้นาฬิกาบอกว่าขั้นนี้เสร็จแล้ว" — เดินผ่าน transition path เดียวกัน เขียน event เหมือนกัน และ**ข้าม `under_review → approved` ไม่ได้ถ้าปิด auto-approve** มันไม่ใช่ประตูหลังสู่ `approved`

นี่คือของที่มาแทนปุ่ม "รับเงินทันที (เดโม)" เดิมอย่างซื่อสัตย์ — ยังกดทีเดียวข้ามการรอได้ แต่เดินผ่าน state machine จริง

### จุดเรียก `_auto_advance` มี 3 ที่เท่านั้น

`loan_service.get_application()` · `loan_service.list_applications()` · `loan_review_service.list_review_queue()`
**ไม่เรียกใน `disburse()`** — การ advance ในบล็อก `with_for_update` นั้นทำให้ lock กำกวม และร้านที่ชนะนาฬิกาควรได้ "ยังไม่ได้รับอนุมัติ" อย่างถูกต้อง

### ⚠️ จุดที่ขัดกับสถาปัตยกรรมเดิม (ยอมรับโดยรู้ตัว)

**GET endpoint เขียน DB และ commit** ซึ่ง GET อื่นทั้งโปรเจกต์ pure หมด ไม่มีทางเลี่ยงอย่างซื่อสัตย์เมื่อไม่มี scheduler/background task ลดผลกระทบด้วย: เขียนเฉพาะเมื่อมีอะไรเปลี่ยนจริง (`if changed: await db.commit()`) · row-locked · idempotent · ใส่ docstring น้ำเสียงเดียวกับ `LoanInstallment` ("ตารางนี้ไม่ต้องมี cron มาทำให้ถูก")

ทางเลือก `POST /loans/applications/{id}/refresh` ให้ poller เรียกนั้นซื่อสัตย์เชิงความหมาย แต่เพิ่ม round trip เท่าตัวและทำให้คิวฝั่งสาขาค้างเว้นแต่จะ poll ด้วย — **เลือก GET-ที่เขียน + docstring**

---

## 5. Frontend ฝั่งร้านค้า

### 5.1 หน้าใหม่ `lib/features/turbo/loan_status_screen.dart` — route `/turbo/loans/status/:applicationId`

`router.dart:82` มี `if (path.startsWith('/turbo/loans')) return _ownerAndManager;` อยู่แล้ว → route ใหม่ถูกคุ้มครองอัตโนมัติ ไม่ต้องแก้ guard แต่**ต้องเพิ่มเคสใน `test/router_role_guard_test.dart`**

โครงหน้าจากบนลงล่าง:
1. การ์ดหัว: ชื่อผลิตภัณฑ์ · `formatBaht(approvedAmount)` · ค่างวด/เดือน · จำนวนงวด
2. **Timeline 5 ขั้น** — แต่ละแถวเป็น `CircleAvatar` + label + เวลา สร้างแบบเดียวกับที่ `loan_account_screen.dart:140-170` สร้างแถวงวดผ่อน · เสร็จแล้ว = `Icons.check` บน `0xFF66BB6A` · ขั้นปัจจุบัน = `Icons.hourglass_top` บน `0xFFFFA726` (คู่ไอคอน/สีเดียวกับคิวขายออฟไลน์ `pos_screen.dart:149-157`) · ยังไม่ถึง = เทา — **ไม่ใช้ `Stepper`** เพราะทั้งโปรเจกต์ไม่เคยใช้ และมันดูไม่เข้ากับแอปนี้เลย
3. `const Map<String,String> _loanStageLabels` + `_loanStageColors` ที่หัวไฟล์ ตามธรรมเนียม `_prospectStatusLabels` (`branch_home_screen.dart:11`) และ `_statusLabels`/`_statusColors` (`purchase_order_list_screen.dart:8-20`) — ป้าย: `ยื่นคำขอ / ตรวจเอกสาร / ตรวจหลักประกัน / พิจารณาอนุมัติ / อนุมัติแล้ว / รับเงินแล้ว / ไม่อนุมัติ`
4. คำบรรยายขั้นปัจจุบัน + ตัวนับ `mm:ss` สด — `_ElapsedTicker` ส่วนตัวที่ลอก lifecycle ของ `_SlaCountdown` (`branch_home_screen.dart:439-486`: สร้างใน `initState` ยกเลิกใน `dispose`) **ลอกมา ไม่ import** เพราะคลาสนั้น private และไฟล์นั้นยาว 530 บรรทัดอยู่แล้ว
5. แสดง `collateralDetail` ที่ร้านพิมพ์ไว้ (read-only)
6. timeline ของ events
7. สถานะปลายทาง: `approved` → ปุ่มเบิกเงิน (ย้ายโค้ดมาจาก `_SubmittedSheet`) · `rejected` → การ์ดแดง `0xFFEF5350` + `rejectionReason` + "ยื่นใหม่ได้อีก N วัน" · `disbursed` → `pushReplacement('/turbo/loans/account')`
8. `OutlinedButton` เล็ก "เร่งเวลา (เดโม)" แสดงเฉพาะตอนยังไม่จบ

### 5.2 Polling — ใช้ `StreamProvider.autoDispose.family`

```dart
const loanStatusPollInterval = Duration(seconds: 10);

final loanApplicationDetailProvider =
    StreamProvider.autoDispose.family<LoanApplicationDetailDto, String>((ref, applicationId) async* {
  final repo = ref.watch(turboRepositoryProvider);
  var failures = 0;
  while (true) {
    try {
      final detail = await repo.loanApplication(applicationId);
      failures = 0;
      yield detail;
      if (isTerminalLoanStatus(detail.status)) return;   // poll หยุดเอง
    } on DioException {
      // เน็ตสะดุดชั่วคราวต้องไม่จบ stream — StreamProvider ที่ throw แล้ว
      // จบเลย ผู้ใช้จะหยุดได้รับอัปเดตโดยไม่รู้ตัว
      if (++failures >= 3) rethrow;
    }
    await Future<void>.delayed(loanStatusPollInterval);
  }
});
```

**ทำไมไม่ใช้ `Timer` ใน `ConsumerStatefulWidget`:** `autoDispose` ยกเลิก subscription เองเมื่อไม่มีคนฟัง → ออกจากหน้าแล้ว poll ตายเองฟรีๆ ไม่มี Timer ค้าง ไม่ต้อง `mounted` guard ไม่ต้องยกเลิก 2 ที่ · `.when()` ใช้ได้เหมือนเดิม หน้าจอเป็น `ConsumerWidget` ธรรมดา · `await` **ก่อน** delay ทำให้ request ไม่ซ้อนกันตอนเน็ตช้า ซึ่ง `Timer.periodic` ทำ · หยุดที่สถานะปลายทางด้วย `return` บรรทัดเดียว

เนื่องจากนี่เป็น polling ตัวแรกของแอป ให้ใส่คอมเมนต์กำกับไว้ว่า `_SlaCountdown` คือ `Timer` ตัวเดียวที่มีมาก่อน และมันแค่ repaint ไม่ได้ refetch

แยก `bool isTerminalLoanStatus(String status)` เป็น top-level function ใน `turbo_repository.dart` เพื่อให้ unit test ได้โดยไม่ต้อง pump widget

ในหน้าจอใช้ `ref.listen` แล้วเมื่อถึงสถานะปลายทางครั้งแรกให้ invalidate `loanApplicationsProvider`, `loanAccountSummaryProvider`, `incomeProfileProvider`

### 5.3 แก้ `_SubmittedSheet` (`loan_apply_screen.dart:292-376`)

**ลบ** `_disburseNow()` และปุ่ม "รับเงินทันที (เดโม)" ทั้งก้อน — ย้ายไป `loan_status_screen` · เก็บไอคอนติ๊กและตัวเลขไว้ · เปลี่ยนข้อความเป็น "สาขาใกล้คุณกำลังตรวจสอบคำขอ" · ปุ่มเดียวที่เหลือเป็น **ติดตามสถานะ** → `Navigator.pop()` แล้ว `pushReplacement('/turbo/loans/status/${application.id}')` · กลับไปเป็น `ConsumerWidget` ได้เพราะไม่มี `_disbursing`/`_error` แล้ว

`_apply()` ไม่ต้องแก้เพื่อรองรับ 400 ตัวใหม่ — บล็อก `DioException` เดิมแสดง `data['detail']` อยู่แล้ว

ฟอร์มยื่นเพิ่ม `TextField` 4 ช่องสำหรับ `collateralDetail` (เลขทะเบียน / ยี่ห้อ-รุ่น / ปี / หมายเหตุ) เปลี่ยนป้ายตาม `collateralKind` (`land_title` → เลขที่โฉนด / ตำบล-อำเภอ / เนื้อที่)

### 5.4 หน้า `/turbo`

`_NoLoanAccountCard` (`turbo_home_screen.dart:304`) เปลี่ยนเป็นมีเงื่อนไขจาก `loanApplicationsProvider` — **หน้าจอแรกที่อ่าน provider ตัวนี้** (ประกาศไว้ที่ `turbo_providers.dart:64` แต่ไม่เคยมีใครอ่าน) ถ้ามีคำขอค้างอยู่ให้แสดงการ์ดส้ม "กำลังพิจารณาคำขอสินเชื่อ · ขั้นตอน {stage}" กดไปหน้าสถานะ ไม่งั้นแสดงการ์ดเดิม

---

## 6. Frontend ฝั่งพนักงานสาขา

`branch_home_screen.dart`: `length: 3` → `4` (L39) แทรกแท็บใหม่เป็นอันที่ 3 เพื่อไม่ให้ความเคยชินกับแท็บ 1–2 เสีย:

```dart
Tab(icon: Icon(Icons.fact_check_outlined), text: 'คำขอสินเชื่อ'),
```

**ไฟล์ใหม่ `lib/features/branch/branch_loan_review_tab.dart`** — `branch_home_screen.dart` ยาว 530 บรรทัดแล้ว และอันนี้เพิ่มอีก ~300

**ไฟล์ใหม่ `lib/features/branch/branch_labels.dart`** — ย้าย label map ที่ 2 ไฟล์ต้องใช้ร่วมกัน (`collateralKindLabels`, `loanStageLabels`, ฯลฯ) จาก private เป็น public ยังเป็น file-level const map เหมือนเดิม ไม่ได้เพิ่ม i18n

`LoanReviewTab` (`ConsumerWidget`): `ref.watch(branchLoanApplicationsProvider).when(...)` → `RefreshIndicator` → `ListView.builder` **ฝั่งนี้ไม่ต้อง poll** เพราะพนักงานเป็นผู้กระทำเอง และ pull-to-refresh ตรงกับอีก 3 แท็บ

`_LoanReviewCard`: ชื่อร้าน + เบอร์โทร · วงเงิน/ค่างวด/จำนวนงวด · ประเภทหลักประกัน · รายละเอียดที่ร้านพิมพ์เป็นบล็อก key/value · pill สถานะ (`Container` + `color.withAlpha(30)` เหมือน `_ProspectCard` L142-149) · "รอมาแล้ว mm:ss"

ปุ่มตามขั้น:

| ขั้น | ปุ่มหลัก |
|---|---|
| `submitted` | **รับเรื่อง** → `doc_review` |
| `doc_review` | `CheckboxListTile` checklist (สำเนาบัตรประชาชน / เล่มทะเบียน / สมุดบัญชีธนาคาร) — **เป็น local widget state ล้วน ไม่มีอัปโหลด** — กดผ่านได้เมื่อติ๊กครบ → **เอกสารครบ** → `collateral_check` (รายการที่ติ๊กถูกรวมเป็น `note` ของ transition เพื่อให้ลงไปใน event log) |
| `collateral_check` | แสดงรายละเอียดยานพาหนะที่ร้านพิมพ์เด่นๆ → **หลักประกันผ่าน** → `under_review` |
| `under_review` | **อนุมัติ** → `approved` |
| ทุกขั้นที่ยังไม่จบ | `OutlinedButton` **ปฏิเสธ** สี `0xFFEF5350` → `_RejectDialog` |

`_RejectDialog` (`AlertDialog`): `TextField` หลายบรรทัด ป้าย `เหตุผลที่ปฏิเสธ (จำเป็น)` · `ChoiceChip` 3 ตัวเติมข้อความให้ (`เอกสารไม่ครบ` / `หลักประกันไม่ผ่านเกณฑ์` / `รายได้ไม่เพียงพอ`) · ปุ่มยืนยัน disabled จนกว่า `text.trim().length >= 5` ให้ตรงกับ `Field(min_length=5)` ฝั่ง server

⚠️ **บทเรียนจาก code review ของ PR ที่เปิดค้างอยู่:** `ProspectDto.fromJson`/`LeaderboardEntryDto.fromJson` cast field ที่ backend ไม่ส่งมาเป็น non-null จนหน้าจอพัง → **DTO ใหม่ทุกตัวต้องใช้ nullable cast** (`json['x'] as String?`) กับ field ที่ไม่การันตี และ **merge backend ก่อน frontend เสมอ**

---

## 7. Migration

ไฟล์เดียว `down_revision = 'c1a9f6d2e7b3'` เช่น `d2e8b4c1f5a7_add_loan_review_stages.py`

**เรื่อง transaction:** infra ใช้ `postgres:18-alpine` (`BubusuperPOS_Infra/docker-compose.yml:3`) และ PG ≥ 12 อนุญาตให้ `ALTER TYPE ... ADD VALUE` อยู่ใน transaction block ได้ → **ไม่ต้องใช้ `autocommit_block()`** (`b7d4f2a91c3e` พิสูจน์แล้วบนสแตกนี้) ข้อห้ามที่ยังเหลือคือ **ห้ามอ้างถึงค่า enum ที่เพิ่งเพิ่มใน transaction เดียวกัน** — migration นี้ไม่อ้างเลย (ไม่มี `UPDATE ... SET status='doc_review'` และ `to_status` เป็น `String(32)` ก็เพื่อเลี่ยงข้อนี้พอดี) ให้ใส่เหตุผลนี้เป็นคอมเมนต์ไว้ เพราะเป็นกับดักที่คนถัดไปจะเจอ

ลำดับ:
1. `op.execute("ALTER TYPE loanapplicationstatus ADD VALUE IF NOT EXISTS '<ค่า>'")` × 3
2. `add_column` 4 ตัว — `stage_started_at` ต้องมี `server_default=sa.func.now()` ไม่งั้นแถวเดิม NULL
3. `create_foreign_key` สำหรับ `reviewed_by_user_id` + `create_index` บน `assigned_branch_id`
4. `create_table('turbo_loan_application_events')` + index บน `application_id` และ `tenant_id`
5. `downgrade()` — คอมเมนต์กำกับว่า PG ลบค่า enum เดี่ยวไม่ได้ ตามแบบ `b7d4f2a91c3e:205-207` และ `2fbbe7ec560d`

`stage_started_at` ที่ default now() จะ backfill แถวเดิมเป็น "ตอนนี้" — ไม่มีปัญหาเพราะแถวเดิมทุกแถวเป็น `disbursed` และไม่กลับเข้า pipeline อีก

### ⚠️ กับดักสำคัญที่สุดของงานนี้

**`tests/conftest.py` สร้าง schema ด้วย `Base.metadata.create_all` ไม่ได้รันผ่าน alembic** → enum ถูกสร้างใหม่พร้อมสมาชิกครบทุกตัวจากฝั่ง Python แปลว่า **migration เขียนผิดยังไงเทสต์ก็ยังเขียว** ต้องรัน `alembic upgrade head` กับ Postgres จริงและตรวจ `\d turbo_loan_applications` ด้วยตาแยกต่างหาก ถึงจะถือว่าเสร็จ

---

## 8. Tests

### `tests/test_turbo_loan.py` — ของเดิมพังก่อน

เทสต์เดิมทุกตัวที่เรียก `/disburse` (`test_disburse_creates_account_with_full_schedule`, `test_cannot_disburse_same_application_twice`, `test_cannot_disburse_second_loan_while_one_is_active`, `test_account_summary_*`, `test_pay_installment_*`, `test_account_closes_*`, `test_concurrent_disburse_*`) ต้องพาคำขอไปถึง `approved` ก่อน

⚠️ **กับดักที่ทำให้เทสต์ flaky:** `_seed_loan_products` สร้าง `Branch` เดียว (`LOAN-TEST-01`) แต่ `pick_branch_for_province` **สุ่ม** ข้ามทุกสาขา ถ้า helper สมัคร champion ด้วย branch code ใหม่ มันจะสร้างสาขาที่ 2 แล้วเทสต์จะ flaky — helper ต้อง join สาขาเดิม:

```python
async def _approve(client, application_id, code="LOAN-TEST-01"):
    """สมัคร champion เข้าสาขา*เดิม*ที่ fixture สร้างไว้ — ถ้าใช้ branch_code
    ใหม่จะได้ Branch ที่ 2 และ pick_branch_for_province สุ่มเลือก ทำให้ทุก
    เทสต์ที่ยื่นขอสินเชื่อ flaky"""
```

สำหรับเทสต์ที่สนใจแค่กลไกการเบิกจ่าย ให้ `UPDATE` แถวตรงๆ ผ่าน `async_sessionmaker` (เทคนิคที่ `test_account_summary_reports_overdue_*` ใช้อยู่แล้ว) เร็วกว่าและโฟกัสกว่า ส่วนเทสต์เชิง integration 2 ตัวค่อยเดินผ่าน champion เต็มทาง

เคสใหม่: `test_disburse_rejected_before_approval` (400 บน `submitted`) · `test_cannot_apply_while_another_application_is_in_flight` · `test_rejected_application_blocks_reapply_within_cooldown` · `test_reapply_allowed_after_cooldown` (ย้อน `decided_at` 8 วัน) · `test_auto_advance_moves_stage_after_configured_seconds` (ย้อน `stage_started_at` แล้ว GET แล้วเช็คทั้งสถานะและว่ามี event `system`) · `test_auto_advance_never_rejects_and_never_disburses` · `test_auto_advance_catches_up_multiple_stages_in_one_read` · `test_fast_forward_advances_exactly_one_stage` · `test_application_detail_includes_event_timeline` · ต่อ `test_cashier_cannot_access_loan_endpoints` ด้วย route ใหม่

### `tests/test_turbo_branch.py` — เคส scope isolation คือหัวใจ

- `test_champion_sees_only_own_branch_loan_applications` — 2 สาขา 2 tenant; เพราะการมอบหมายสาขาสุ่ม ต้องเซ็ต `assigned_branch_id` ตรงๆ ผ่าน session หลัง `apply()`
- `test_champion_cannot_advance_another_branchs_application` → **404** (ตรงกับ `test_visit_prospect_from_other_branch_is_404`)
- `test_champion_cannot_skip_a_stage` — `submitted → under_review` → 400
- `test_reject_requires_a_reason` → 422 · `test_reject_records_the_reason_and_blocks_further_transitions`
- `test_shop_owner_cannot_call_branch_loan_review_endpoints` → 403 · `test_champion_cannot_disburse` → 403
- `test_advance_writes_an_event_with_the_champion_as_actor`
- **`test_champion_action_appears_in_the_shop_owners_audit_log`** — หลักฐานตรงว่า `record_external` ทำงาน: พนักงานกด advance แล้วเจ้าของร้าน `GET /api/v1/audit-log` เห็นแถวนั้น **เทสต์ใหม่ที่มีค่าที่สุด**

### Frontend
- `test/router_role_guard_test.dart` — เพิ่ม `/turbo/loans/status/abc123`
- ไฟล์ใหม่ `test/loan_status_test.dart` — unit test ล้วนของ `isTerminalLoanStatus()` และ helper คำนวณ index ของ timeline ไม่ต้อง pump widget

### Seeds
- `scripts/seed_branch_demo.py` — เพิ่ม `LoanApplication` 2 แถวผูกกับ `BKK-CENTRAL` ค้างที่ `doc_review` และ `under_review` ให้แท็บใหม่มีของทันทีที่เปิดแอป (เหตุผลเดียวกับที่ docstring ของมันเขียนไว้อยู่แล้ว)
- `scripts/seed_demo.py` — ไม่ต้องแก้ (สินเชื่อของมันเป็น `disbursed` อยู่แล้ว) แค่เพิ่ม docstring ถึง pipeline ใหม่

---

## 9. ลำดับการทำ

| เฟส | งาน | เดโมได้แค่ไหน |
|---|---|---|
| **0** | enum + 4 คอลัมน์ + model `LoanApplicationEvent` + migration + ค่าคงที่ใน `turbo_config.py` ยังไม่มีใครอ่าน · **รัน `alembic upgrade head` ตรวจด้วยตา** | เหมือนเดิมทุกอย่าง |
| **1** | guard ใน `apply()` + event แถวแรก + `_auto_advance` + `GET /loans/applications/{id}` + `/loans/eligibility` + `disburse()` ต้องการ `approved` + fast-forward · แก้เทสต์เดิม + เพิ่มเทสต์ฝั่งร้าน | ยื่น → poll ด้วย curl → เห็นมันเดินถึง `approved` → เบิกจ่าย (headless แต่ของจริง) |
| **2** | `record_external` + `loan_review_service.py` + 4 routes ฝั่งสาขา + เทสต์ scope isolation | ขับกระบวนการตรวจทั้งหมดจาก Swagger ในฐานะ champion |
| **3** | DTO + repo + `StreamProvider` poller + `loan_status_screen.dart` + เขียน `_SubmittedSheet` ใหม่ + route + การ์ดบน turbo home + ช่องกรอกหลักประกัน | **ครบพอขึ้นเวทีด้วยเครื่องเดียว** — ยื่น เห็น timeline เดิน นาฬิกาพาไปถึงอนุมัติ หรือกดเร่งเวลา |
| **4** | `branch_labels.dart` + `branch_loan_review_tab.dart` + แท็บที่ 4 + repo/provider + dialog ปฏิเสธ | **เดโม 2 เครื่องของจริง** — ร้านยื่นบนเครื่อง A พนักงานอนุมัติบนเครื่อง B จอ A ขยับเองใน 10 วิ |
| **5** | seed คิวให้สาขา + eligibility ผูกกับปุ่มยื่น + ข้อความ cooldown + จูน `LOAN_STAGE_AUTO_ADVANCE_SECONDS` ตามจังหวะพิตช์จริง | ครบตามแผน |

---

## 10. การตรวจสอบ

```bash
# Backend
docker compose -f BubusuperPOS_Infra/docker-compose.yml up -d
cd BubusuperPOS_Backend
alembic upgrade head          # ← ต้องรันจริง เทสต์จับ migration พังไม่ได้
psql ... -c '\d turbo_loan_applications'
pytest tests/test_turbo_loan.py tests/test_turbo_branch.py -v
python scripts/seed_branch_demo.py

# Frontend
cd BubusuperPOS_Frontend && flutter test && flutter run
```

**เดโม end-to-end 2 จอ:**
1. จอ A ล็อกอินร้านไก่ทอด → `/turbo` → ยื่นขอสินเชื่อ (กรอกทะเบียนรถ) → เด้งหน้าสถานะ เห็น "รอสาขารับเรื่อง" + นาฬิกาเดิน
2. จอ B ล็อกอิน branch champion → แท็บ "คำขอสินเชื่อ" → เห็นคำขอของร้านไก่ทอด → กด "รับเรื่อง" → ติ๊ก checklist เอกสาร → "เอกสารครบ"
3. **จอ A ขยับเองภายใน 10 วิ** โดยไม่ต้องดึงรีเฟรช ← จุดที่ต้องโชว์บนเวที
4. จอ B กด "หลักประกันผ่าน" → "อนุมัติ" → จอ A ขึ้นปุ่มรับเงิน → กด → เข้าหน้าบัญชีสินเชื่อพร้อมตารางผ่อน
5. ถ้าไม่มีเครื่องที่สอง: ปล่อยนาฬิกาเดินเอง ~2 นาที หรือกด "เร่งเวลา (เดโม)" ทีละขั้น
6. ทดสอบ path ปฏิเสธ: ยื่นใหม่ → จอ B กดปฏิเสธพร้อมเหตุผล → จอ A เห็นการ์ดแดง + "ยื่นใหม่ได้อีก 7 วัน" → ลองยื่นซ้ำต้องโดนบล็อก

---

## จุดที่สถาปัตยกรรมเดิมทำให้อึดอัด (รู้ตัวและยอมรับ)

1. **GET ที่เขียน DB** — ไม่มี scheduler นาฬิกาจึงต้องเดินตอนมี request ลดผลกระทบด้วย row lock + เขียนเฉพาะเมื่อเปลี่ยนจริง + docstring ทางแก้จริงคือ background scheduler ซึ่งอยู่นอกขอบเขต
2. **`BranchContext` ไม่มีระบบ permission** — ทุก route ใต้ `/branch` เปิดให้ champion ทุกคน ไม่แก้ในงานนี้ (สร้างระบบ permission เพื่อ role เดียวคือ over-engineering) แต่คุมด้วยการวาง route ตรวจสอบทั้งหมดไว้ใต้ prefix `/branch` เพื่อให้ "ใต้ `/branch` = champion เท่านั้น" ยังเป็น invariant บรรทัดเดียวที่ทั้ง `deps.py` และ router ฝั่ง Flutter บังคับอยู่แล้ว
3. **เทสต์จับ migration พังไม่ได้** เพราะ conftest ใช้ `create_all` → เป็นขั้นตอนตรวจด้วยมือในเฟส 0
4. **`pick_branch_for_province` สุ่ม** → เทสต์ที่มี 2 สาขา flaky ถ้าไม่เซ็ต `assigned_branch_id` ตรงๆ
5. **พนักงานสาขาเห็นข้อมูล tenant เป็นครั้งแรก** → คุมด้วย response model ที่ระบุชัดว่าอะไรข้ามได้ ไม่รวม `income_profile_snapshot`
6. **ไม่มี i18n** ข้อความไทยใหม่ทุกอันเป็น inline ตามโปรเจกต์ แต่ label map ควรเป็น file-level const และแชร์ผ่าน `branch_labels.dart` เมื่อ 2 ไฟล์ต้องใช้
