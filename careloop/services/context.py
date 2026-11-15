"""Shared dependencies handed to every service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from careloop.clock import Clock
from careloop.config import Settings
from careloop.notifications.email_sender import EmailSender
from careloop.security.identifiers import new_id
from careloop.storage.repository import Repository


@dataclass
class ServiceContext:
    repo: Repository
    clock: Clock
    settings: Settings
    email_sender: EmailSender
    id_factory: Callable[[], str] = field(default=new_id)
