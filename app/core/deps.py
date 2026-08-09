from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.product_lookup_provider import (
    ChainedProductLookup,
    OpenFoodFactsProvider,
    ProductLookupProvider,
    UpcItemDbProvider,
)
from app.core.branch_scope import BranchContext
from app.core.config import settings
from app.core.db import get_db
from app.core.permissions import Permission, role_has_permission
from app.core.security import TokenPayload, TokenType, decode_token
from app.core.tenancy import TenantContext
from app.models.user import UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_token_payload(token: str | None = Depends(oauth2_scheme)) -> TokenPayload:
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if payload.type != TokenType.access:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "an access token is required")
    return payload


async def get_tenant_context(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantContext:
    if payload.tid is None:
        # A branch_champion token — it has no tenant_id by design (see
        # UserRole.branch_champion). Reject outright rather than passing
        # None through, which would silently scope every query to nothing.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account is not a shop account")
    return TenantContext(db=db, tenant_id=payload.tid, user_id=payload.sub, role=UserRole(payload.role))


CurrentTenant = Annotated[TenantContext, Depends(get_tenant_context)]


async def get_branch_context(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BranchContext:
    if payload.bid is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account is not a branch account")
    return BranchContext(db=db, branch_id=payload.bid, user_id=payload.sub)


CurrentBranch = Annotated[BranchContext, Depends(get_branch_context)]


def require(permission: Permission):
    def _check(ctx: CurrentTenant) -> TenantContext:
        if not role_has_permission(ctx.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing permission: {permission.value}")
        return ctx

    return _check


# Constructing these never touches the network, so the singleton is safe to
# build unconditionally. Overridden in tests via app.dependency_overrides.
_product_lookup_provider: ProductLookupProvider | None = None


def get_product_lookup_provider() -> ProductLookupProvider:
    global _product_lookup_provider
    if _product_lookup_provider is None:
        timeout = settings.product_lookup_timeout_seconds
        _product_lookup_provider = ChainedProductLookup(
            [OpenFoodFactsProvider(timeout_seconds=timeout), UpcItemDbProvider(timeout_seconds=timeout)]
        )
    return _product_lookup_provider
