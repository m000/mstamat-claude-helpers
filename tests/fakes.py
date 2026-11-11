"""Test doubles."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeProcess:
    """An ancestor process of the SessionEnd hook, with or without a terminal."""

    pid: int
    tty: str | None

    def terminal(self) -> str | None:
        return self.tty
