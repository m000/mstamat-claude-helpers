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


# list_records() ###################################################################################
@pytest.mark.usefixtures('claude_env')
def test_list__branch(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, make_record: MakeRecord
) -> None:
    """Test that the list shows the branch of each session, and N/A for a record without one, as
    the records from before the branch was recorded are."""
    monkeypatch.setenv('COLUMNS', '300')  # wide enough that no column is cut
    make_record(SESSION, branch='feature/x')
    old = make_record(OTHER, age=timedelta(hours=2))
    assert old.path is not None
    data = json.loads(old.path.read_text())
    del data['branch']
    old.path.write_text(json.dumps(data))

    app(['l'], result_action='return_value')

    header, newest, oldest = capsys.readouterr().out.splitlines()
    assert 'Branch' in header
    assert (SESSION[:8] in newest, 'feature/x' in newest) == (True, True)
    assert (OTHER[:8] in oldest, 'N/A' in oldest) == (True, True)


@pytest.mark.usefixtures('claude_env')
def test_list__marks(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    make_record: MakeRecord,
    claude_paths: ClaudePaths,
    claude_process: int,
) -> None:
    """Test that the list of every shell marks a running session with ▶ and a session that ended
    in this shell with ●, each mark in its own place, and explains the marks it shows."""
    monkeypatch.setenv('COLUMNS', '300')  # wide enough that no column is cut
    elsewhere = Shell(pid=1, started=0.0)
    make_record(SESSION)
    make_record(OTHER, age=timedelta(hours=2), shell=elsewhere)
    make_record(THIRD, age=timedelta(hours=3), shell=elsewhere)
    claude_paths.sessions.mkdir(parents=True, exist_ok=True)
    for pid_file, session_id in ((claude_process, SESSION), (claude_process + 1, OTHER)):
        (claude_paths.sessions / f'{pid_file}.json').write_text(
            json.dumps({'pid': claude_process, 'sessionId': session_id})
        )

    app(['l', '--all'], result_action='return_value')

    _, both, running, neither, _, legend = capsys.readouterr().out.splitlines()
    assert both.startswith(f' {SESSION[:8]} ▶●  ')
    assert running.startswith(f' {OTHER[:8]} ▶   ')
    assert neither.startswith(f' {THIRD[:8]}     ')
    assert legend == '▶ running again   ● ended in this shell'


@pytest.mark.usefixtures('claude_env')
def test_list__this_shell(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, make_record: MakeRecord
) -> None:
    """Test that the list of this shell alone leaves out the sessions of other shells, the mark of
    this shell, and the legend when it shows no mark."""
    monkeypatch.setenv('COLUMNS', '300')  # wide enough that no column is cut
    make_record(SESSION)
    make_record(OTHER, shell=Shell(pid=1, started=0.0))

    app(['l'], result_action='return_value')

    _, row = capsys.readouterr().out.splitlines()
    assert (SESSION[:8] in row, '●' in row) == (True, False)


@pytest.mark.usefixtures('claude_env')
def test_list__dirs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    make_record: MakeRecord,
    tmp_path: Path,
) -> None:
    """Test that --dir keeps the sessions of every shell in the given directories and below them,
    that it can be repeated, that `.` names the current directory, and that it leaves out a
    directory that only shares a prefix of its name."""
    monkeypatch.setenv('COLUMNS', '300')  # wide enough that no column is cut
    for d in ('a/sub', 'b', 'ab'):
        (tmp_path / d).mkdir(parents=True)
    make_record(SESSION, directory=tmp_path / 'a' / 'sub')
    make_record(
        OTHER, age=timedelta(hours=2), directory=tmp_path / 'b', shell=Shell(pid=1, started=0.0)
    )
    make_record(THIRD, age=timedelta(hours=3), directory=tmp_path / 'ab')

    def listed(*arguments: str) -> list[str]:
        app(['l', *arguments], result_action='return_value')
        table, _, _ = capsys.readouterr().out.partition('\n\n')  # the legend follows a blank line
        _, *rows = table.splitlines()
        return [row.split()[0] for row in rows]

    assert listed('-d', str(tmp_path / 'a')) == [SESSION[:8]]
    assert listed('--dir', str(tmp_path / 'a'), '--dir', str(tmp_path / 'b')) == [
        SESSION[:8],
        OTHER[:8],
    ]
    monkeypatch.chdir(tmp_path / 'b')
    assert listed('-d', '.') == [OTHER[:8]]


@pytest.mark.usefixtures('claude_env')
def test_list__dirs_none(
    capsys: pytest.CaptureFixture[str], make_record: MakeRecord, tmp_path: Path
) -> None:
    """Test that a --dir that matches no session says so."""
    make_record(SESSION)

    app(['l', '-d', str(tmp_path / 'elsewhere')], result_action='return_value')

    assert capsys.readouterr().out == 'No recorded sessions in these directories.\n'


@pytest.mark.usefixtures('claude_env')
def test_list__dirs_and_all(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """Test that --dir, which lists every shell already, is refused together with --all."""
    with pytest.raises(SystemExit) as exc:
        app(['l', '--all', '-d', str(tmp_path)], result_action='return_value')

    assert exc.value.code != 0
    assert 'Mutually exclusive' in capsys.readouterr().err


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
