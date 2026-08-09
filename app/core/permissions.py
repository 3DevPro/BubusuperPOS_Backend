import enum

from app.models.user import UserRole


class Permission(str, enum.Enum):
    view_products = "view_products"
    manage_products = "manage_products"
    adjust_inventory = "adjust_inventory"
    create_sale = "create_sale"
    view_sales = "view_sales"
    refund_sale = "refund_sale"
    view_reports = "view_reports"
    manage_staff = "manage_staff"
    manage_settings = "manage_settings"
    view_audit_log = "view_audit_log"
    # Granted to all three roles, unlike manage_products — looking up or
    # adding a customer is a normal part of the checkout flow a cashier
    # performs, not sensitive back-office data.
    manage_customers = "manage_customers"
    # Turbo's micro-insurance module (quote/purchase/claim) — a financial
    # commitment on the tenant's behalf, so it's back-office like
    # view_reports/refund_sale, not a cashier-level action.
    manage_insurance = "manage_insurance"


ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.owner: set(Permission),
    UserRole.manager: {
        Permission.view_products,
        Permission.manage_products,
        Permission.adjust_inventory,
        Permission.create_sale,
        Permission.view_sales,
        Permission.refund_sale,
        Permission.view_reports,
        Permission.manage_customers,
        Permission.manage_insurance,
    },
    UserRole.cashier: {
        Permission.view_products,
        Permission.create_sale,
        Permission.view_sales,
        Permission.manage_customers,
    },
    # branch_champion never has tenant_id, so get_tenant_context already
    # rejects any request that would reach a Permission check for this role
    # (see app/core/deps.py) — this entry exists only so role_has_permission
    # doesn't KeyError if it's ever consulted directly.
    UserRole.branch_champion: set(),
}


def role_has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
