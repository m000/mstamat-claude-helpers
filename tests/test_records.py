import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import psutil
import pytest

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.records import (
    Record,
    RecordStore,
    SelectionError,
    Shell,
    branch_of,
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
    assert record.branch is None
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
        (
            [
                {'type': 'last-prompt', 'leafUuid': '0000', 'explicit': True},
                {'type': 'last-prompt', 'lastPrompt': 'Fix the parser'},
            ],
            'Fix the parser',
        ),
    ),
    ids=('latest_ai_title', 'first_prompt', 'marker_without_prompt'),
)
def test_title_of__entries(tmp_path: Path, entries: list[dict[str, object]], title: str) -> None:
    """Test that the title is the latest AI title, or else the first prompt on one line."""
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(''.join(json.dumps(e) + '\n' for e in entries))

    assert title_of(transcript) == title


def test_branch_of__repository(tmp_path: Path) -> None:
    """Test that the branch is the one checked out in a repository, and None on a detached HEAD,
    outside a repository and in a missing directory."""
    repo, plain = tmp_path / 'repo', tmp_path / 'plain'
    plain.mkdir()
    git = ['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.com']
    subprocess.run(['git', 'init', '--quiet', '--initial-branch', 'feature/x', repo], check=True)

    assert branch_of(repo) == 'feature/x'

    subprocess.run([*git, 'commit', '--quiet', '--allow-empty', '-m', 'c'], check=True)
    subprocess.run([*git, 'checkout', '--quiet', '--detach'], check=True)

    assert branch_of(repo) is None
    assert branch_of(plain) is None
    assert branch_of(tmp_path / 'missing') is None


def test_branch_of__no_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that the branch is None when git is not installed."""
    monkeypatch.setenv('PATH', str(tmp_path))

    assert branch_of(tmp_path) is None


def test_record_load__no_branch(store: RecordStore, make_record: MakeRecord) -> None:
    """Test that a record written before the branch was recorded loads without one."""
    record = make_record()
    assert record.path is not None
    data = json.loads(record.path.read_text())
    del data['branch']
    record.path.write_text(json.dumps(data))

    [loaded] = store.all()

    assert loaded.branch is None


def test_record_load__no_boot(store: RecordStore, make_record: MakeRecord) -> None:
    """Test that a record written before the shell named its boot loads with a shell without one."""
    record = make_record()
    assert record.path is not None
    data = json.loads(record.path.read_text())
    del data['shell']['boot']
    record.path.write_text(json.dumps(data))

    [loaded] = store.all()

    assert loaded.shell is not None
    assert loaded.shell.boot is None


# Shell ############################################################################################
def test_shell_current__clock_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a shell keeps its key when a step of the system clock moves the start date of its
    process."""
    shell = Shell.current()
    create_time = psutil.Process.create_time
    monkeypatch.setattr(psutil.Process, 'create_time', lambda self: create_time(self) + 2)

    assert shell.boot is not None
    assert Shell.current() == shell


def test_shell_of_session__name_with_spaces(tmp_path: Path) -> None:
    """Test that the start of a process whose name holds spaces and parentheses is the one psutil
    reads, less the boot time."""
    program = tmp_path / 'a (b) c'
    program.symlink_to(shutil.which('sleep') or '/bin/sleep')
    process = subprocess.Popen([program, '60'])
    try:
        shell = Shell.of_session(process.pid)
        started = psutil.Process(process.pid).create_time() - psutil.boot_time()
    finally:
        process.kill()
        process.wait()

    assert shell.started == pytest.approx(started, abs=0.01)


def test_shell_of_session__other_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a platform without a form of its own keys the shell by the start date of its
    process, without a boot."""
    monkeypatch.setattr(sys, 'platform', 'darwin')

    shell = Shell.of_session(os.getpid())

    assert shell == Shell(os.getpid(), round(psutil.Process().create_time(), 2))


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

    stale = store.purge_stale_records(dry_run=False, keep_sessions={SESSION})

    assert stale == [expired]
    assert {r.session_id for r in store.all()} == {SESSION, fresh.session_id}
