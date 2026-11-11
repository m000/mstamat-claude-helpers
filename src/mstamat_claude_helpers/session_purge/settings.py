"""The SessionEnd hook entry in Claude Code's settings."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mstamat_claude_helpers.common.files import atomic_write
from mstamat_claude_helpers.session_purge.types import HookChange

# Marks this helper's entry, so that an install from a new location updates the entry in place.
HOOK_MARKER = '# claude-session-purge'
HOOK_TIMEOUT = 10  # seconds; on a cold uv cache the first run resolves the dependencies


def hook_entry(command: str) -> dict[str, Any]:
    """The hook entry that runs `<command> record`, marked as this helper's."""
    return {
        'type': 'command',
        'command': f'{command} record  {HOOK_MARKER}',
        'timeout': HOOK_TIMEOUT,
    }


def is_ours(hook: dict[str, Any]) -> bool:
    return HOOK_MARKER in hook.get('command', '')


@dataclass(frozen=True)
class HookSettings:
    """The SessionEnd hooks of one settings file.

    A change rewrites the whole file in one rename, so Claude Code never reads it half written.
    A file that is not valid JSON raises json.JSONDecodeError and stays as it is.
    """

    path: Path

    def install(self, entry: dict[str, Any]) -> HookChange:
        """Add the entry, or replace this helper's existing entry in place. Duplicates go."""
        settings = self._load()
        groups = settings.setdefault('hooks', {}).setdefault('SessionEnd', [])
        ours = [h for g in groups for h in g['hooks'] if is_ours(h)]
        if ours == [entry]:
            return HookChange.UNCHANGED
        if ours:
            for g in groups:
                g['hooks'] = [h for h in g['hooks'] if not is_ours(h) or h is ours[0]]
            groups[:] = [g for g in groups if g['hooks']]
            ours[0].clear()
            ours[0].update(entry)
        else:
            groups.append({'hooks': [entry]})
        self._save(settings)
        return HookChange.UPDATED if ours else HookChange.INSTALLED

    def uninstall(self) -> bool:
        """Remove this helper's entries, and the containers they leave empty. Returns whether any
        entry was there."""
        settings = self._load()
        groups = settings.get('hooks', {}).get('SessionEnd', [])
        if not any(is_ours(h) for g in groups for h in g['hooks']):
            return False
        for g in groups:
            g['hooks'] = [h for h in g['hooks'] if not is_ours(h)]
        groups[:] = [g for g in groups if g['hooks']]
        if not groups:
            del settings['hooks']['SessionEnd']
        if not settings['hooks']:
            del settings['hooks']
        self._save(settings)
        return True

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def _save(self, settings: dict[str, Any]) -> None:
        # NB: Writes an indentation of 2, as Claude Code itself does.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(settings, indent=2, ensure_ascii=False) + '\n')
