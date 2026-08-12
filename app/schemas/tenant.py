from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class TenantSettingsResponse(BaseModel):
    name: str
    business_type: str | None
    currency: str
    timezone: str
    promptpay_id: str | None
    vat_enabled: bool
    vat_rate: Decimal
    price_includes_tax: bool
    loyalty_enabled: bool
    baht_per_point: Decimal
    point_value_baht: Decimal

    model_config = {"from_attributes": True}


class TenantSettingsUpdateRequest(BaseModel):
    # Every field is optional and only applied when explicitly sent (see
    # exclude_unset in the endpoint) — otherwise updating one setting would
    # silently wipe out the others.
    # Accept None explicitly to allow clearing a previously-set PromptPay ID.
    promptpay_id: str | None = Field(default=None)
    vat_enabled: bool | None = Field(default=None)
    vat_rate: Decimal | None = Field(default=None, gt=0, le=100)
    price_includes_tax: bool | None = Field(default=None)
    loyalty_enabled: bool | None = Field(default=None)
    baht_per_point: Decimal | None = Field(default=None, gt=0)
    point_value_baht: Decimal | None = Field(default=None, gt=0)

    @field_validator("promptpay_id")
    @classmethod
    def _valid_promptpay_id(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) not in (10, 13):
            raise ValueError("promptpay_id must be a 10-digit phone number or 13-digit citizen/tax ID")
        return digits
