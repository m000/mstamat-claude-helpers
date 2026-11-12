"""Remove the local artifacts of Claude Code sessions.

A SessionEnd hook, installed with `hook --install`, records every session that ends, together with
the shell it was started from. `list` shows the recorded sessions. `purge` removes one session,
given by id or as the latest that ended in the current shell, and then the records it no longer
needs.

For a session, `purge` removes:

- the transcript and its sidecar directory (tool results, subagents)
- the file edit history and the session environment
- stale process records and their key files
- the scratchpad and task output in the temporary directory
- the session's prompts in history.jsonl, and the paste-cache entries no other prompt references

A copy synced to claude.ai cannot be deleted from here, so its URL is printed for deletion by hand.
"""

import json
import math
import shlex
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, NoReturn

import psutil
from cyclopts import App, Group, Parameter, validators
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from mstamat_claude_helpers import __version__
from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.session_purge.artifacts import (
    Session,
    SessionRunningError,
    purge_session,
)
from mstamat_claude_helpers.session_purge.memory import memory_notes
from mstamat_claude_helpers.session_purge.records import Record, RecordStore, SelectionError, Shell
from mstamat_claude_helpers.session_purge.settings import HookSettings, hook_entry
from mstamat_claude_helpers.session_purge.types import HookChange

out = Console(highlight=False)
err = Console(stderr=True, highlight=False)

app = App(name='claude-session-purge', help=__doc__, version=__version__)
target = Group('Session', validator=validators.LimitedChoice(1, 1))
action = Group('Action', validator=validators.LimitedChoice(1, 1))


def main() -> None:
    app()


def die(message: str) -> NoReturn:
    err.print(f'[red]error:[/] {escape(message)}')
    raise SystemExit(1)


def tildify(path: Path) -> str:
    """A path with the home directory written as `~`, or the full path when it lies outside."""
    home = Path.home()
    return str(Path('~') / path.relative_to(home)) if path.is_relative_to(home) else str(path)


def human(size: float) -> str:
    for unit in ('B', 'KiB', 'MiB'):
        if size < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GiB'


# Commands #########################################################################################
@app.command(name='list', alias='l')
def list_records(
    *, every_shell: Annotated[bool, Parameter(name='--all', negative=())] = False
) -> None:
    """List the recorded sessions of this shell, newest first.

    Parameters
    ----------
    every_shell
        List the records of every shell, including shells that have exited.
    """
    store = RecordStore.default()
    records = store.all() if every_shell else store.of_shell(Shell.current())
    if not records:
        out.print(
            'No recorded sessions.'
            if every_shell
            else 'No recorded sessions for this shell. Try --all.'
        )
        return

    claude_paths = ClaudePaths.default()
    notes = memory_notes(claude_paths)
    now = datetime.now().astimezone()
    table = Table(box=None, header_style='bold')
    table.add_column('ID', no_wrap=True)
    table.add_column('Ended', no_wrap=True)
    table.add_column('Expires', justify='right', no_wrap=True)
    if every_shell:
        table.add_column('Terminal', no_wrap=True)
    table.add_column('Notes', justify='right', no_wrap=True)
    table.add_column('Directory', overflow='ellipsis', no_wrap=True, ratio=1)
    table.add_column('Title', overflow='ellipsis', no_wrap=True, ratio=2)
    for r in records:
        running = Session(r.session_id, [r], claude_paths).running_pids
        short_id = r.session_id[:8] + (' [yellow](running)[/]' if running else '')
        days = math.ceil((r.expires - now) / timedelta(days=1))
        expires = f'{days} d' if days > 0 else '[red]expired[/]'
        gone = '' if r.shell and r.shell.alive else ' [dim](gone)[/]'
        terminal = [escape(r.terminal or '-') + gone] if every_shell else []
        table.add_row(
            short_id,
            f'{r.ended:%Y-%m-%d %H:%M}',
            expires,
            *terminal,
            str(len(notes.get(r.session_id, []))),
            escape(tildify(r.directory)),
            escape(r.title or '-'),
        )
    out.print(table)


@app.command(alias='p')
def purge(
    *,
    session_id: Annotated[str | None, Parameter(name='--id', group=target)] = None,
    auto: Annotated[bool, Parameter(group=target, negative=())] = False,
    dry_run: Annotated[bool, Parameter(name=('--dry-run', '-n'), negative=())] = False,
    as_json: Annotated[bool, Parameter(name='--json', negative=())] = False,
) -> None:
    """Remove the local artifacts of one session, then the records that are no longer needed.

    The records removed are those of the purged session, from any shell, and those older than
    14 days. A session that is still running is refused.

    Parameters
    ----------
    session_id
        The session to purge: a full session id, or a prefix of a recorded one.
    auto
        Purge the session that ended last in this shell.
    dry_run
        Show what would be removed, and remove nothing.
    as_json
        Print the findings as JSON instead of a table.
    """
    store = RecordStore.default()
    try:
        match session_id, auto:
            case None, True:
                sid = store.latest(Shell.current()).session_id
            case str(), False:
                sid = store.resolve(session_id)
            case _:  # pragma: no cover
                raise ValueError('exactly one of --id and --auto is required')
        session = Session(
            sid, [r for r in store.all() if r.session_id == sid], ClaudePaths.default()
        )
        result = purge_session(session, dry_run=dry_run)
    except (SelectionError, SessionRunningError) as exc:
        die(str(exc))
    siblings = store.purge_sibling_records(sid, dry_run=dry_run)
    stale = store.purge_stale_records(dry_run=dry_run, keep_session=sid)

    if result.running_pids:
        err.print(f'[yellow]warning:[/] session {sid} is still running, sizes are a snapshot')
    if as_json:
        report = {
            'session_id': sid,
            'dry_run': dry_run,
            'running_pids': result.running_pids,
            'findings': [{**f._asdict(), 'path': str(f.path)} for f in result.findings],
            'total_size': sum(f.size for f in result.findings),
            'records': [str(r.path) for r in siblings + stale],
            'synced_url': result.synced_url,
            'memory_notes': [str(p) for p in result.memory_notes],
        }
        print(json.dumps(report, indent=2))
        return

    verb = 'would be removed' if dry_run else 'removed'
    if result.findings:
        table = Table(box=None, show_header=False)
        table.add_column(justify='right', no_wrap=True)
        table.add_column(style='dim', no_wrap=True)
        table.add_column(overflow='fold')
        for f in result.findings:
            where = f'{f.detail} in {tildify(f.path)}' if f.detail else tildify(f.path)
            table.add_row(human(f.size), f.kind, escape(where))
        out.print(table)
        out.print(f'Total: {human(sum(f.size for f in result.findings))} {verb}')
    else:
        out.print(f'No local artifacts found for session {sid}.')
    if siblings or stale:
        count = len(siblings) + len(stale)
        out.print(
            f'Records: {count} {verb} ({len(siblings)} of this session, {len(stale)} expired)'
        )
    if result.memory_notes:
        out.print(f'Memory notes created by this session, kept: {len(result.memory_notes)}')
        for note in result.memory_notes:
            out.print(f'  {escape(tildify(note))}', soft_wrap=True)
    else:
        out.print('Memory notes created by this session: none recorded')
    out.print()
    if result.synced_url:
        out.print('Delete the synced copy by hand, from the session menu on claude.ai:')
        out.print(f'  {result.synced_url}', soft_wrap=True)
    else:
        out.print(
            'No claude.ai URL recorded: the session was not synced, or its transcript is gone.'
        )


@app.command(alias='r')
def record() -> None:
    """Record the session that just ended. The SessionEnd hook runs this, with the hook input on
    stdin."""
    RecordStore.default().save(Record.from_hook(json.load(sys.stdin), psutil.Process().parents()))


@app.command
def hook(
    *,
    install: Annotated[bool, Parameter(group=action, negative=())] = False,
    uninstall: Annotated[bool, Parameter(group=action, negative=())] = False,
    print_: Annotated[bool, Parameter(name='--print', group=action, negative=())] = False,
) -> None:
    """Manage the SessionEnd hook that records ended sessions, in Claude Code's user settings.

    The hook entry carries a marker, so an install from a new location updates the entry in place.
    The settings are rewritten in one rename, and only when the current file is valid JSON.

    Parameters
    ----------
    install
        Add the hook, or point the existing one at this installation.
    uninstall
        Remove the hook.
    print_
        Print the settings snippet, and change nothing.
    """
    script = Path(sys.argv[0])
    # Run with `python -m`, argv[0] is the module's file, so the hook needs the interpreter.
    command = (
        f'{shlex.quote(sys.executable)} -m mstamat_claude_helpers.session_purge'
        if script.name == '__main__.py'
        else shlex.quote(str(script.absolute()))
    )
    entry = hook_entry(command)
    settings = HookSettings(ClaudePaths.default().settings)
    try:
        match install, uninstall, print_:
            case True, False, False:
                match settings.install(entry):
                    case HookChange.INSTALLED:
                        out.print(f'Installed the SessionEnd hook in {tildify(settings.path)}:')
                    case HookChange.UPDATED:
                        out.print(f'Updated the SessionEnd hook in {tildify(settings.path)}:')
                    case HookChange.UNCHANGED:
                        out.print('The hook is already installed:')
                    case _:  # pragma: no cover
                        raise ValueError('unknown hook change')
                out.print(f'  {escape(entry["command"])}', soft_wrap=True)
            case False, True, False:
                if settings.uninstall():
                    out.print(f'Removed the SessionEnd hook from {tildify(settings.path)}.')
                else:
                    out.print('The hook is not installed.')
            case False, False, True:
                print(json.dumps({'hooks': {'SessionEnd': [{'hooks': [entry]}]}}, indent=2))
            case _:  # pragma: no cover
                raise ValueError('exactly one of --install, --uninstall and --print is required')
    except json.JSONDecodeError as exc:
        die(f'{tildify(settings.path)} is not valid JSON, fix it first: {exc}')
