import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    actor_name: str
    action: str
    summary: str
    created_at: datetime

    model_config = {"from_attributes": True}
