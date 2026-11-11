import json
import os

import psutil
import pytest

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.artifacts import (
    Session,
    SessionRunningError,
    purge_session,
)
from mstamat_claude_helpers.session_purge.records import Record
from tests.conftest import OTHER, SESSION, MakeRecord


# purge_session() ##################################################################################
@pytest.mark.usefixtures('session_files')
def test_purge_session__dry_run(claude_paths: ClaudePaths, make_record: MakeRecord) -> None:
    """Test that a dry run finds every kind of artifact of the session, and removes nothing."""
    session = Session(SESSION, [make_record()], claude_paths)
    before = sorted(claude_paths.claude_dir.rglob('*'))

    result = purge_session(session, dry_run=True)

    assert {f.kind for f in result.findings} == {
        'transcript',
        'sidecar',
        'file history',
        'session env',
        'process record',
        'scratch',
        'history',
        'paste',
    }
    assert result.synced_url == 'https://claude.ai/code/x'
    assert sorted(claude_paths.claude_dir.rglob('*')) == before


@pytest.mark.usefixtures('session_files')
def test_purge_session__removes(claude_paths: ClaudePaths, make_record: MakeRecord) -> None:
    """Test that a purge removes the session's artifacts and keeps those of other sessions.

    The history keeps the other session's prompts and its file mode. The paste cache keeps the
    pastes that another session references. The key file of the process record goes with it.
    """
    session = Session(SESSION, [make_record()], claude_paths)

    purge_session(session, dry_run=False)

    remaining = sorted(
        str(p.relative_to(claude_paths.claude_dir)) for p in claude_paths.claude_dir.rglob('*')
    )
    assert remaining == [
        'file-history',
        'history.jsonl',
        'paste-cache',
        'paste-cache/shared.txt',
        'paste-cache/theirs.txt',
        'projects',
        'projects/-proj',
        f'projects/-proj/{OTHER}.jsonl',
        'session-env',
        'sessions',
    ]
    history = [
        json.loads(line)['sessionId'] for line in claude_paths.history.read_text().splitlines()
    ]
    assert history == [OTHER]
    assert claude_paths.history.stat().st_mode & 0o777 == 0o600
    assert not (claude_paths.tmp_root / '-proj' / SESSION).exists()


@pytest.mark.usefixtures('session_files')
def test_purge_session__running(claude_paths: ClaudePaths, make_record: MakeRecord) -> None:
    """Test that a running session raises SessionRunningError, and that a dry run reports its
    pid instead and removes nothing."""
    started = psutil.Process(os.getpid()).create_time()
    running = {'pid': os.getpid(), 'sessionId': SESSION, 'startedAt': int(started * 1000)}
    (claude_paths.sessions / f'{os.getpid()}.json').write_text(json.dumps(running))
    session = Session(SESSION, [make_record()], claude_paths)

    with pytest.raises(SessionRunningError):
        purge_session(session, dry_run=False)
    assert purge_session(session, dry_run=True).running_pids == [os.getpid()]
    assert (claude_paths.projects / '-proj' / f'{SESSION}.jsonl').exists()


# Session ##########################################################################################
def test_session_synced_url__process_record(claude_paths: ClaudePaths) -> None:
    """Test that without a URL in the transcript, the URL comes from the bridge session id of a
    process record."""
    claude_paths.sessions.mkdir(parents=True)
    record = {'pid': 999999, 'sessionId': SESSION, 'bridgeSessionId': 'session_B'}
    (claude_paths.sessions / '999999.json').write_text(json.dumps(record))
    no_records: list[Record] = []

    assert (
        Session(SESSION, no_records, claude_paths).synced_url()
        == 'https://claude.ai/code/session_B'
    )
