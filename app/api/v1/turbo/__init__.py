from fastapi import APIRouter

from app.api.v1.turbo import branch, daily_close, income, insurance, loan, public

router = APIRouter(prefix="/turbo")
router.include_router(daily_close.router)
router.include_router(income.router)
router.include_router(insurance.router)
router.include_router(loan.router)
router.include_router(branch.router)
router.include_router(public.router)
