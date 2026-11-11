"""Where Claude Code keeps its session data.

NB: Written against the on-disk layout of Claude Code 2.1.322, which is not a documented interface.
    Check with `purge --dry-run` after upgrading.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class ClaudePaths:
    """The two roots of Claude Code's session data: its configuration directory, and the user's
    directory in the temporary directory."""

    claude_dir: Path
    tmp_root: Path

    @classmethod
    def default(cls) -> Self:
        """The paths Claude Code itself uses, honoring CLAUDE_CONFIG_DIR."""
        claude_dir = os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude'
        return cls(Path(claude_dir), Path(f'/tmp/claude-{os.getuid()}'))

    @property
    def settings(self) -> Path:
        return self.claude_dir / 'settings.json'

    @property
    def history(self) -> Path:
        """The prompts of every session, one JSON object per line."""
        return self.claude_dir / 'history.jsonl'

    @property
    def projects(self) -> Path:
        """One directory per project, holding the transcripts and their sidecar directories."""
        return self.claude_dir / 'projects'

    @property
    def sessions(self) -> Path:
        """The process records of running Claude Code processes, each with a key file."""
        return self.claude_dir / 'sessions'

    @property
    def file_history(self) -> Path:
        return self.claude_dir / 'file-history'

    @property
    def session_env(self) -> Path:
        return self.claude_dir / 'session-env'

    @property
    def paste_cache(self) -> Path:
        return self.claude_dir / 'paste-cache'
