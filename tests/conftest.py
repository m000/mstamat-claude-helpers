import json
import shutil
import subprocess
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.records import Record, RecordStore, Shell

SESSION = '11111111-2222-3333-4444-555555555555'
OTHER = '99999999-8888-7777-6666-555555555555'

type MakeRecord = Callable[..., Record]


@pytest.fixture(name='claude_paths')
def fixture_layout(tmp_path: Path) -> ClaudePaths:
    """Empty Claude Code directories below the test's temporary directory."""
    return ClaudePaths(tmp_path / 'claude', tmp_path / 'tmp')


@pytest.fixture(name='store')
def fixture_store(tmp_path: Path) -> RecordStore:
    return RecordStore(tmp_path / 'state' / 'claude-session-purge')


@pytest.fixture(name='make_record')
def fixture_make_record(store: RecordStore, claude_paths: ClaudePaths) -> MakeRecord:
    """Saves a record of a session that ended `age` ago in `shell`, the test's own by default."""

    def make(
        session_id: str = SESSION,
        *,
        age: timedelta = timedelta(hours=1),
        shell: Shell | None = None,
    ) -> Record:
        record = Record(
            session_id=session_id,
            reason='prompt_input_exit',
            ended=datetime.now().astimezone() - age,
            title='A title',
            directory=claude_paths.claude_dir.parent / 'project',
            terminal='/dev/pts/9',
            shell=shell or Shell.current(),
            claude_pid=None,
            hook_input={
                'session_id': session_id,
                'cwd': str(claude_paths.claude_dir.parent / 'project'),
                'transcript_path': str(claude_paths.projects / '-proj' / f'{session_id}.jsonl'),
                'scratchpad_dir': str(claude_paths.tmp_root / '-proj' / session_id / 'scratchpad'),
            },
        )
        store.save(record)
        return record

    return make


@pytest.fixture(name='session_files')
def fixture_session_files(claude_paths: ClaudePaths) -> None:
    """The artifacts of SESSION, next to those of OTHER that a purge of SESSION must keep.

    Of the memory notes, `mine` was created by SESSION, `theirs` by OTHER, and `unknown` records no
    origin. The index MEMORY.md names SESSION too, but is no note.

    Of the pastes, `mine` is referenced by SESSION only, `shared` by both sessions, and `theirs`
    by OTHER only. The process record of SESSION belongs to an exited process.
    """
    project = claude_paths.projects / '-proj'
    (project / SESSION / 'tool-results').mkdir(parents=True)
    (project / SESSION / 'tool-results' / 'r1.txt').write_text('result')
    entries = [
        {'type': 'ai-title', 'aiTitle': 'Early title'},
        {
            'type': 'attachment',
            'attachment': {'type': 'remote_session_change', 'url': 'https://claude.ai/code/x'},
        },
        {'type': 'ai-title', 'aiTitle': 'Fix the parser'},
    ]
    (project / f'{SESSION}.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in entries))
    (project / f'{OTHER}.jsonl').write_text('{}\n')
    for directory in (claude_paths.file_history / SESSION, claude_paths.session_env / SESSION):
        directory.mkdir(parents=True)
        (directory / 'f').write_text('x')
    (claude_paths.tmp_root / '-proj' / SESSION / 'scratchpad').mkdir(parents=True)
    claude_paths.sessions.mkdir()
    (claude_paths.sessions / '999999.json').write_text(
        json.dumps({'pid': 999999, 'sessionId': SESSION})
    )
    (claude_paths.sessions / '999999.abc.key').write_text('key')
    claude_paths.paste_cache.mkdir()
    for name in ('mine', 'shared', 'theirs'):
        (claude_paths.paste_cache / f'{name}.txt').write_text(name)
    prompts = [(SESSION, ['mine', 'shared']), (OTHER, ['shared', 'theirs']), (SESSION, [])]
    lines = [
        {
            'display': 'p',
            'pastedContents': {str(i): {'contentHash': h} for i, h in enumerate(hashes)},
            'sessionId': sid,
        }
        for sid, hashes in prompts
    ]
    memory = claude_paths.projects / '-proj' / 'memory'
    memory.mkdir()
    for name, origin in (('mine', SESSION), ('theirs', OTHER), ('unknown', None)):
        meta = f'  originSessionId: {origin}\n' if origin else ''
        (memory / f'{name}.md').write_text(f'---\nname: {name}\nmetadata:\n{meta}---\n\nA note.\n')
    (memory / 'MEMORY.md').write_text(f'- [mine](mine.md) originSessionId: {SESSION}\n')
    # Claude Code writes the history compactly, which the purge's line match relies on.
    claude_paths.history.write_text(
        ''.join(json.dumps(x, separators=(',', ':')) + '\n' for x in lines)
    )
    claude_paths.history.chmod(0o600)


@pytest.fixture(name='claude_process')
def fixture_claude_process(tmp_path: Path) -> Iterator[int]:
    """The pid of a running child process named `claude`: `sleep` behind a symlink of that name."""
    claude = tmp_path / 'bin' / 'claude'
    claude.parent.mkdir()
    claude.symlink_to(shutil.which('sleep') or '/bin/sleep')
    process = subprocess.Popen([claude, '60'])
    yield process.pid
    process.kill()
    process.wait()


@pytest.fixture(name='other_process')
def fixture_other_process() -> Iterator[int]:
    """The pid of a running child process that is not Claude Code."""
    process = subprocess.Popen([shutil.which('sleep') or '/bin/sleep', '60'])
    yield process.pid
    process.kill()
    process.wait()


@pytest.fixture(name='claude_env')
def fixture_claude_env(
    monkeypatch: pytest.MonkeyPatch, claude_paths: ClaudePaths, store: RecordStore
) -> None:
    """Points the command line's default Claude Code paths and record store at the test's
    directories."""
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(claude_paths.claude_dir))
    monkeypatch.setenv('XDG_STATE_HOME', str(store.directory.parent))
