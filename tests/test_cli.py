import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from mstamat_claude_helpers.session_purge.cli import app, tildify
from mstamat_claude_helpers.session_purge.records import Shell
from tests.conftest import OTHER, SESSION, MakeRecord


# purge() ##########################################################################################
@pytest.mark.usefixtures('claude_env')
def test_purge__auto(capsys: pytest.CaptureFixture[str], make_record: MakeRecord) -> None:
    """Test that --auto purges the session that ended last in this shell, and that the records
    to remove are the session's own, expired or not, and the expired ones of other sessions,
    each counted once."""
    own = [make_record(SESSION, age=timedelta(days=20)), make_record(SESSION)]
    expired = make_record(OTHER, age=timedelta(days=15), shell=Shell(pid=1, started=0.0))
    make_record('22222222-0000-0000-0000-000000000000', age=timedelta(days=2))

    app(['purge', '--auto', '--dry-run', '--json'], result_action='return_value')

    report = json.loads(capsys.readouterr().out)
    assert report['session_id'] == SESSION
    assert sorted(report['records']) == sorted(str(r.path) for r in [*own, expired])


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
