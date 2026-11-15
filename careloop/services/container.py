"""Wires repository, clock, email sender and services together."""

from __future__ import annotations

from dataclasses import dataclass

from careloop.clock import Clock, SystemClock
from careloop.config import Settings
from careloop.notifications.email_sender import EmailSender, build_email_sender
from careloop.services.auth_service import AuthService
from careloop.services.care_cycle import CareCycle
from careloop.services.circle_service import CircleService
from careloop.services.context import ServiceContext
from careloop.services.dose_service import DoseService
from careloop.services.history_service import HistoryService
from careloop.services.proposal_service import ProposalService
from careloop.services.record_service import RecordService
from careloop.storage import build_repository
from careloop.storage.repository import Repository


@dataclass
class Services:
    """Every service, sharing one ServiceContext."""

    ctx: ServiceContext
    auth: AuthService
    circles: CircleService
    doses: DoseService
    records: RecordService
    proposals: ProposalService
    history: HistoryService
    cycle: CareCycle


def build_services(
    settings: Settings,
    *,
    repo: Repository | None = None,
    clock: Clock | None = None,
    email_sender: EmailSender | None = None,
) -> Services:
    """Create all services; tests pass their own repository, clock and email sender."""
    ctx = ServiceContext(
        repo=repo or build_repository(settings),
        clock=clock or SystemClock(),
        settings=settings,
        email_sender=email_sender
        or build_email_sender(settings.gmail_address, settings.gmail_app_password),
    )
    doses = DoseService(ctx)
    return Services(
        ctx=ctx,
        auth=AuthService(ctx),
        circles=CircleService(ctx),
        doses=doses,
        records=RecordService(ctx, doses),
        proposals=ProposalService(ctx, doses),
        history=HistoryService(ctx),
        cycle=CareCycle(ctx, doses),
    )
