import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.records import (
    Record,
    RecordStore,
    SelectionError,
    Shell,
    title_of,
)
from tests.conftest import OTHER, SESSION, MakeRecord
from tests.fakes import FakeProcess


# Record ###########################################################################################
@pytest.mark.parametrize(
    ('ancestors', 'terminal'),
    (
        ([FakeProcess(os.getpid(), None), FakeProcess(os.getpid(), '/dev/pts/9')], '/dev/pts/9'),
        ([FakeProcess(os.getpid(), None)], None),
    ),
    ids=('terminal_ancestor', 'no_terminal'),
)
def test_record_from_hook__ancestors(
    claude_paths: ClaudePaths, ancestors: list[FakeProcess], terminal: str | None
) -> None:
    """Test that the record takes the terminal and the shell from the first ancestor with a
    terminal, and has neither when no ancestor has one."""
    hook_input = {
        'session_id': SESSION,
        'reason': 'clear',
        'cwd': '/work/project',
        'transcript_path': str(claude_paths.projects / 'missing.jsonl'),
    }

    record = Record.from_hook(hook_input, ancestors)

    assert record.terminal == terminal
    assert record.shell == (Shell.of_session(os.getsid(os.getpid())) if terminal else None)
    assert record.title is None
    assert record.directory == Path('/work/project')
    assert record.hook_input == hook_input


@pytest.mark.parametrize(
    ('entries', 'title'),
    (
        (
            [
                {'type': 'last-prompt', 'lastPrompt': 'Fix\n  the parser'},
                {'type': 'ai-title', 'aiTitle': 'Early title'},
                {'type': 'ai-title', 'aiTitle': 'Parser fix'},
            ],
            'Parser fix',
        ),
        (
            [
                {'type': 'last-prompt', 'lastPrompt': 'Fix\n  the parser'},
                {'type': 'last-prompt', 'lastPrompt': 'And the lexer'},
            ],
            'Fix the parser',
        ),
    ),
    ids=('latest_ai_title', 'first_prompt'),
)
def test_title_of__entries(tmp_path: Path, entries: list[dict[str, str]], title: str) -> None:
    """Test that the title is the latest AI title, or else the first prompt on one line."""
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(''.join(json.dumps(e) + '\n' for e in entries))

    assert title_of(transcript) == title


# RecordStore ######################################################################################
def test_record_store_save__roundtrip(store: RecordStore, make_record: MakeRecord) -> None:
    """Test that a saved record loads back equal, from a file indented by 2."""
    record = make_record()

    assert record.path is not None
    assert record.path.read_text().startswith('{\n  "version": 1,')
    assert store.all() == [record]


def test_record_store_latest__shell(store: RecordStore, make_record: MakeRecord) -> None:
    """Test that the latest record is the newest of the given shell, and that a shell without
    records raises SelectionError."""
    elsewhere = Shell(pid=1, started=0.0)
    make_record(OTHER, age=timedelta(hours=2))
    newest_here = make_record(SESSION, age=timedelta(hours=1))
    make_record('22222222-0000-0000-0000-000000000000', age=timedelta(minutes=1), shell=elsewhere)

    assert store.latest(Shell.current()) == newest_here
    with pytest.raises(SelectionError):
        store.latest(Shell(pid=2, started=0.0))


@pytest.mark.parametrize(
    ('given', 'resolved'),
    (
        ('33333333-0000-0000-0000-000000000000', '33333333-0000-0000-0000-000000000000'),
        ('1111', SESSION),
        ('', None),
        ('5', None),
    ),
    ids=('unrecorded_full_id', 'unique_prefix', 'ambiguous', 'unknown'),
)
def test_record_store_resolve__prefix(
    store: RecordStore, make_record: MakeRecord, given: str, resolved: str | None
) -> None:
    """Test that a full id resolves as given, a unique prefix to its recorded session, and any
    other prefix raises SelectionError."""
    make_record(SESSION)
    make_record(OTHER)

    if resolved is None:
        with pytest.raises(SelectionError):
            store.resolve(given)
    else:
        assert store.resolve(given) == resolved


@pytest.mark.parametrize('dry_run', (True, False), ids=('dry_run', 'real'))
def test_record_store_purge_sibling_records__dry_run(
    store: RecordStore, make_record: MakeRecord, dry_run: bool
) -> None:
    """Test that every record of the session is returned, from any shell, and deleted unless the
    run is dry. The records of other sessions stay."""
    mine = [
        make_record(SESSION, age=timedelta(hours=2)),
        make_record(SESSION, age=timedelta(hours=1), shell=Shell(pid=1, started=0.0)),
    ]
    make_record(OTHER)

    siblings = store.purge_sibling_records(SESSION, dry_run=dry_run)

    assert sorted(r.ended for r in siblings) == sorted(r.ended for r in mine)
    assert {r.session_id for r in store.all()} == ({SESSION, OTHER} if dry_run else {OTHER})


def test_record_store_purge_stale_records__keep_session(
    store: RecordStore, make_record: MakeRecord
) -> None:
    """Test that expired records are deleted, except those of the kept session. Fresh records
    stay."""
    make_record(SESSION, age=timedelta(days=20))
    expired = make_record(OTHER, age=timedelta(days=15))
    fresh = make_record('22222222-0000-0000-0000-000000000000', age=timedelta(days=13))

    stale = store.purge_stale_records(dry_run=False, keep_session=SESSION)

    assert stale == [expired]
    assert {r.session_id for r in store.all()} == {SESSION, fresh.session_id}
