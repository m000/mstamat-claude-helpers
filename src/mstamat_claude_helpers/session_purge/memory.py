"""The memory notes Claude Code keeps per project, and the sessions that created them.

A purge reports a session's memory notes but keeps them: a note is meant to outlive its session,
later sessions may have edited it, and the memory directory keeps no versions to undo an edit.
"""

import re
from collections import defaultdict
from pathlib import Path

from mstamat_claude_helpers.common.claude_paths import ClaudePaths

INDEX = 'MEMORY.md'
ORIGIN_RE = re.compile(r'^\s*originSessionId:\s*(\S+)\s*$', re.MULTILINE)


def origin_of(note: Path) -> str | None:
    """The session that created a memory note, as its frontmatter records it.

    NB: Claude Code records the origin only for some notes, depending on how a note was written,
        so a note without one may still belong to a session.
    """
    text = note.read_text(encoding='utf-8')
    if not text.startswith('---\n') or (end := text.find('\n---', 4)) == -1:
        return None
    match = ORIGIN_RE.search(text, 4, end)
    return match.group(1) if match else None


def memory_notes(claude_paths: ClaudePaths) -> dict[str, list[Path]]:
    """Every memory note with a recorded origin, by the session that created it."""
    notes: dict[str, list[Path]] = defaultdict(list)
    for note in sorted(claude_paths.projects.glob('*/memory/*.md')):
        if note.name != INDEX and (origin := origin_of(note)):
            notes[origin].append(note)
    return notes
