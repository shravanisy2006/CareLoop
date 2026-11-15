"""Read-only access to the audit trail and notification history."""

from __future__ import annotations

from careloop.domain.models import AuditEvent, Member, Notification
from careloop.domain.permissions import Action, authorize
from careloop.services.context import ServiceContext


class HistoryService:
    """Read-only access to the audit trail and email history."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def audit_events(self, viewer: Member, limit: int = 200) -> list[AuditEvent]:
        """The circle's activity log, newest first."""
        authorize(viewer, Action.VIEW_AUDIT, viewer.circle_id)
        return self._ctx.repo.list_audit_events(viewer.circle_id, limit)

    def notifications(self, viewer: Member, limit: int = 100) -> list[Notification]:
        """The circle's emails and what happened to each, newest first."""
        authorize(viewer, Action.VIEW_AUDIT, viewer.circle_id)
        return self._ctx.repo.list_notifications(viewer.circle_id, limit)
