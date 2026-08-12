import uuid

from fastapi import APIRouter, Depends

from app.core.deps import require
from app.core.permissions import Permission
from app.core.tenancy import TenantContext
from app.models.turbo.loan import LoanApplication, LoanProduct
from app.schemas.turbo.loan import (
    CreditStandingResponse,
    LoanAccountResponse,
    LoanAccountSummaryResponse,
    LoanApplicationCreateRequest,
    LoanApplicationDetailResponse,
    LoanApplicationResponse,
    LoanEligibilityResponse,
    LoanInstallmentResponse,
    LoanPaymentRequest,
    LoanProductResponse,
    LoanQuoteRequest,
    LoanQuoteResponse,
)
from app.services.turbo import credit_service, loan_service

# No shared prefix — most routes live under /loans/... but /credit-standing
# deliberately doesn't (it merges the income profile's tier with loan
# history, so it isn't really a loan-account resource), same tags as
# insurance.py's turbo grouping.
router = APIRouter(tags=["turbo"])


@router.get("/loans/products", response_model=list[LoanProductResponse])
async def list_products(
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> list[LoanProduct]:
    return await loan_service.list_products(ctx)


@router.post("/loans/quote", response_model=LoanQuoteResponse)
async def quote(
    body: LoanQuoteRequest,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanQuoteResponse:
    _, quoted, _, _ = await loan_service.quote(
        ctx, body.product_code, body.requested_amount, body.collateral_value, body.term_months
    )
    return quoted


@router.post("/loans/applications", response_model=LoanApplicationResponse, status_code=201)
async def apply(
    body: LoanApplicationCreateRequest,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanApplication:
    return await loan_service.apply(
        ctx,
        body.product_code,
        body.requested_amount,
        body.collateral_value,
        body.term_months,
        body.collateral_detail,
    )


@router.get("/loans/applications", response_model=list[LoanApplicationResponse])
async def list_applications(
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> list[LoanApplication]:
    return await loan_service.list_applications(ctx)


@router.get("/loans/applications/{application_id}", response_model=LoanApplicationDetailResponse)
async def get_application(
    application_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanApplicationDetailResponse:
    return await loan_service.get_application(ctx, application_id)


@router.get("/loans/eligibility", response_model=LoanEligibilityResponse)
async def eligibility(
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanEligibilityResponse:
    return await loan_service.check_eligibility(ctx)


@router.post("/loans/applications/{application_id}/demo/fast-forward", response_model=LoanApplicationDetailResponse)
async def fast_forward(
    application_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanApplicationDetailResponse:
    return await loan_service.fast_forward_application(ctx, application_id)


@router.post("/loans/applications/{application_id}/disburse", response_model=LoanAccountResponse)
async def disburse(
    application_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanAccountResponse:
    account = await loan_service.disburse(ctx, application_id)
    return LoanAccountResponse.model_validate(account)


@router.get("/loans/account", response_model=LoanAccountSummaryResponse | None)
async def account_summary(
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanAccountSummaryResponse | None:
    return await loan_service.get_account_summary(ctx)


@router.get("/loans/account/{account_id}/installments", response_model=list[LoanInstallmentResponse])
async def list_installments(
    account_id: uuid.UUID,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> list[LoanInstallmentResponse]:
    return await loan_service.list_installments(ctx, account_id)


@router.post("/loans/installments/{installment_id}/payment", response_model=LoanInstallmentResponse)
async def pay_installment(
    installment_id: uuid.UUID,
    body: LoanPaymentRequest,
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> LoanInstallmentResponse:
    return await loan_service.pay_installment(ctx, installment_id, body.amount, body.reference)


@router.get("/credit-standing", response_model=CreditStandingResponse)
async def credit_standing(
    ctx: TenantContext = Depends(require(Permission.manage_loans)),
) -> CreditStandingResponse:
    return await credit_service.get_credit_standing(ctx)
