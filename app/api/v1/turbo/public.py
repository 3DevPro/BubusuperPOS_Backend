from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rate_limit import (
    FailureLimiter,
    public_loan_quote_limiter,
    public_loan_term_bounds_limiter,
    public_quote_limiter,
)
from app.schemas.turbo.public import (
    LoanTermBoundsResponse,
    PublicLoanQuoteRequest,
    PublicLoanQuoteResponse,
    PublicQuoteRequest,
    PublicQuoteResponse,
)
from app.services.turbo import public_quote_service

router = APIRouter(prefix="/public", tags=["turbo-public"])


def _check_rate_limit(request: Request, limiter: FailureLimiter) -> str:
    # Anonymous endpoints — keyed on caller IP since there's no account to
    # attribute the request to. Each route passes its own limiter (see
    # app/core/rate_limit.py) so hammering one surface can't also throttle
    # the others.
    limit_key = request.client.host if request.client else "unknown"
    retry_after = limiter.retry_after(limit_key)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "ขอราคาบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่",
            headers={"Retry-After": str(retry_after)},
        )
    limiter.record_failure(limit_key)
    return limit_key


@router.post("/quote", response_model=PublicQuoteResponse, status_code=status.HTTP_201_CREATED)
async def public_quote(
    body: PublicQuoteRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicQuoteResponse:
    _check_rate_limit(request, public_quote_limiter)
    return await public_quote_service.quote_and_create_lead(db, body)


@router.post("/loan-quote", response_model=PublicLoanQuoteResponse, status_code=status.HTTP_201_CREATED)
async def public_loan_quote(
    body: PublicLoanQuoteRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PublicLoanQuoteResponse:
    _check_rate_limit(request, public_loan_quote_limiter)
    return await public_quote_service.quote_loan_and_create_lead(db, body)


@router.get("/loan-term-bounds", response_model=list[LoanTermBoundsResponse])
async def loan_term_bounds(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[LoanTermBoundsResponse]:
    _check_rate_limit(request, public_loan_term_bounds_limiter)
    return await public_quote_service.loan_term_bounds(db)
