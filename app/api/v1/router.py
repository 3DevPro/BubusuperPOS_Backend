from fastapi import APIRouter

from app.api.v1 import (
    audit_log,
    auth,
    categories,
    customers,
    health,
    inventory,
    notifications,
    products,
    purchase_orders,
    reports,
    sales,
    staff,
    suppliers,
    tenant,
)
from app.api.v1.turbo import router as turbo_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(staff.router)
api_router.include_router(audit_log.router)
api_router.include_router(products.router)
api_router.include_router(categories.router)
api_router.include_router(customers.router)
api_router.include_router(sales.router)
api_router.include_router(inventory.router)
api_router.include_router(reports.router)
api_router.include_router(tenant.router)
api_router.include_router(suppliers.router)
api_router.include_router(purchase_orders.router)
api_router.include_router(notifications.router)
api_router.include_router(turbo_router)
