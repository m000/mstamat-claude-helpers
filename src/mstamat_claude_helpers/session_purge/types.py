"""Data types passed between the modules and the command line."""

from enum import StrEnum
from pathlib import Path
from typing import NamedTuple


class Finding(NamedTuple):
    """One artifact of a session: a file or directory, or a part of a shared file."""

    kind: str
    path: Path
    size: int
    detail: str | None = None


class HookChange(StrEnum):
    """What an install did to the hook entry in the settings."""

    INSTALLED = 'installed'
    UPDATED = 'updated'
    UNCHANGED = 'unchanged'


class SessionPurge(NamedTuple):
    """The outcome of purging a session's artifacts, or of a dry run of it."""

    findings: list[Finding]
    synced_url: str | None
    running_pids: list[int]
    memory_notes: list[Path]  # created by the session, and kept
