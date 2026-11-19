import io
import json
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.cli import app, tildify
from mstamat_claude_helpers.session_purge.records import RecordStore, Shell
from tests.conftest import OTHER, SESSION, THIRD, MakeRecord


# purge() ##########################################################################################
@pytest.fixture(name='answers')
def fixture_answers(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Makes the standard input a terminal on which the given lines are typed."""

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    return lambda lines: monkeypatch.setattr(sys, 'stdin', Terminal(lines))


@pytest.mark.usefixtures('claude_env')
def test_purge__latest(capsys: pytest.CaptureFixture[str], make_record: MakeRecord) -> None:
    """Test that a purge without session ids picks the session that ended last in this shell, and
    that the records to remove are the session's own, expired or not, and the expired ones of
    other sessions, each counted once."""
    own = [make_record(SESSION, age=timedelta(days=20)), make_record(SESSION)]
    expired = make_record(OTHER, age=timedelta(days=15), shell=Shell(pid=1, started=0.0))
    make_record(THIRD, age=timedelta(days=2))

    app(['purge', '-n', '--json'], result_action='return_value')

    report = json.loads(capsys.readouterr().out)
    [session] = report['sessions']
    assert (session['session_id'], session['outcome']) == (SESSION, 'dry_run')
    assert sorted(session['records']) == sorted(str(r.path) for r in own)
    assert report['expired_records'] == [str(expired.path)]
    assert all(r.path and r.path.exists() for r in [*own, expired])


@pytest.mark.usefixtures('claude_env', 'session_files')
def test_purge__yes(
    capsys: pytest.CaptureFixture[str], make_record: MakeRecord, claude_paths: ClaudePaths
) -> None:
    """Test that -y purges every given session without a question, an id prefix included, and
    that a session given twice is purged once."""
    records = [make_record(SESSION), make_record(OTHER)]

    app(['purge', '-y', '--json', SESSION[:8], OTHER, SESSION], result_action='return_value')

    report = json.loads(capsys.readouterr().out)
    assert [(s['session_id'], s['outcome']) for s in report['sessions']] == [
        (SESSION, 'purged'),
        (OTHER, 'purged'),
    ]
    assert not list(claude_paths.projects.glob('-proj/*.jsonl'))
    assert not any(r.path and r.path.exists() for r in records)


@pytest.mark.parametrize(
    ('typed', 'purged'),
    (
        ('y\nn\ny\n', {SESSION, THIRD}),
        ('n\na\n', {OTHER, THIRD}),
        ('\n\n\n', set()),
        ('y\ny\nq\n', None),
        ('y\n', None),
    ),
    ids=('some', 'all_the_rest', 'default_no', 'quit', 'end_of_input'),
)
@pytest.mark.usefixtures('claude_env')
def test_purge__interactive(
    make_record: MakeRecord,
    store: RecordStore,
    answers: Callable[[str], None],
    typed: str,
    purged: set[str] | None,
) -> None:
    """Test that only the approved sessions are purged, that the default answer is no, and that
    quitting or the end of the input purges nothing, not even the sessions approved before."""
    sessions = [SESSION, OTHER, THIRD]
    for sid in sessions:
        make_record(sid)
    answers(typed)

    if purged is None:
        with pytest.raises(SystemExit):
            app(['purge', *sessions], result_action='return_value')
    else:
        app(['purge', *sessions], result_action='return_value')

    assert {r.session_id for r in store.all()} == set(sessions) - (purged or set())


@pytest.mark.usefixtures('claude_env')
def test_purge__single_confirmation(
    make_record: MakeRecord, store: RecordStore, answers: Callable[[str], None]
) -> None:
    """Test that -i asks about a single session with a plain yes or no."""
    make_record(SESSION)
    answers('y\n')

    app(['purge', '-i'], result_action='return_value')

    assert store.all() == []


@pytest.mark.parametrize(
    'arguments',
    (['purge'], ['purge', '-y', 'ffff'], ['purge', '-y', '-n'], ['purge', '--dry-run']),
    ids=('no_terminal', 'unknown_id', 'exclusive_modes', 'long_option'),
)
@pytest.mark.usefixtures('claude_env')
def test_purge__refused(
    make_record: MakeRecord,
    store: RecordStore,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    """Test that a purge that cannot ask, cannot find a session, or gets conflicting or unknown
    options exits with an error and removes nothing."""
    make_record(SESSION)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('y\n'))

    with pytest.raises(SystemExit) as exit_info:
        app(arguments, result_action='return_value')

    assert exit_info.value.code != 0
    assert len(store.all()) == 1


@pytest.mark.usefixtures('claude_env', 'session_files')
def test_purge__running(
    make_record: MakeRecord, store: RecordStore, claude_paths: ClaudePaths, claude_process: int
) -> None:
    """Test that one running session among those given stops the purge of them all."""
    make_record(SESSION)
    make_record(OTHER)
    (claude_paths.sessions / f'{claude_process}.json').write_text(
        json.dumps({'pid': claude_process, 'sessionId': OTHER})
    )

    with pytest.raises(SystemExit):
        app(['purge', '-y', SESSION, OTHER], result_action='return_value')

    assert len(store.all()) == 2
    assert (claude_paths.projects / '-proj' / f'{SESSION}.jsonl').exists()


# hook() ###########################################################################################
@pytest.mark.parametrize(
    ('argv0', 'command'),
    (
        ('/home/u/.local/bin/claude-session-purge', '/home/u/.local/bin/claude-session-purge'),
        (
            '/x/session_purge/__main__.py',
            f'{sys.executable} -m mstamat_claude_helpers.session_purge',
        ),
    ),
    ids=('installed_script', 'python_module'),
)
def test_hook__command(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, argv0: str, command: str
) -> None:
    """Test that the hook runs the installed script, or the interpreter with the module when the
    helper runs with `python -m`."""
    monkeypatch.setattr(sys, 'argv', [argv0, 'hook', '--print'])

    app(['hook', '--print'], result_action='return_value')

    [entry] = json.loads(capsys.readouterr().out)['hooks']['SessionEnd'][0]['hooks']
    assert entry['command'].startswith(f'{command} record  #')


# tildify() ########################################################################################
@pytest.mark.parametrize(
    ('path', 'shown'),
    (
        (Path.home() / 'git' / 'project', '~/git/project'),
        (Path(f'{Path.home()}2/project'), f'{Path.home()}2/project'),
        (Path('/srv/project'), '/srv/project'),
    ),
    ids=('under_home', 'home_prefix_only', 'outside_home'),
)
def test_tildify__home(path: Path, shown: str) -> None:
    """Test that a path under the home directory starts with `~`, and any other path stays full,
    also when its text merely starts with the home directory's."""
    assert tildify(path) == shown
