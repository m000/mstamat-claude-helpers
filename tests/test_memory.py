from pathlib import Path

import pytest

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.memory import memory_notes, origin_of
from tests.conftest import OTHER, SESSION


# memory_notes() ###################################################################################
@pytest.mark.usefixtures('session_files')
def test_memory_notes__origin(claude_paths: ClaudePaths) -> None:
    """Test that the notes are grouped by the session that created them, and that notes without a
    recorded origin and the MEMORY.md index are left out."""
    memory = claude_paths.projects / '-proj' / 'memory'

    assert memory_notes(claude_paths) == {
        SESSION: [memory / 'mine.md'],
        OTHER: [memory / 'theirs.md'],
    }


# origin_of() ######################################################################################
def test_origin_of__body(tmp_path: Path) -> None:
    """Test that only the frontmatter names the origin, not a mention in the body of the note."""
    note = tmp_path / 'note.md'
    note.write_text(f'---\nname: note\n---\n\noriginSessionId: {SESSION}\n')

    assert origin_of(note) is None
