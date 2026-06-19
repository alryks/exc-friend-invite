from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


FLOW_AWAITING_DOCUMENTS = "awaiting_documents"
FLOW_PREVIEW = "preview"
FLOW_EDITING = "editing"


@dataclass
class FlowSession:
    mattermost_user_id: str
    surrogate_user_id: int
    channel_id: str
    team_id: str | None = None
    application_id: str | None = None
    state: str = FLOW_AWAITING_DOCUMENTS
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    document_count: int = 0
    enforce_facility_access: bool = True
    flow_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def is_expired(self, ttl: timedelta) -> bool:
        return datetime.now(timezone.utc) - self.updated_at > ttl
