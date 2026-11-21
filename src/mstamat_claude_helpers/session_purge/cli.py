"""Remove the local artifacts of Claude Code sessions.

A SessionEnd hook, installed with `hook --install`, records every session that ends, together with
the shell it was started from. `list` shows the recorded sessions. `purge` removes the sessions
given by id, or else the latest that ended in the current shell, and then the records it no longer
needs. It asks before it removes anything, unless told not to with `-y`.

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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, NoReturn

import psutil
from cyclopts import App, Group, Parameter, validators
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm, Prompt
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
from mstamat_claude_helpers.session_purge.types import HookChange, Outcome, SessionPurge

out = Console(highlight=False)
err = Console(stderr=True, highlight=False)

app = App(name='claude-session-purge', help=__doc__, version=__version__)
mode = Group('Mode', validator=validators.MutuallyExclusive())
action = Group('Action', validator=validators.LimitedChoice(1, 1))
scope = Group('Scope', validator=validators.MutuallyExclusive())


RUNNING_MARK = '[yellow]▶[/]'
HERE_MARK = '[cyan]●[/]'
MARKS = {
    RUNNING_MARK: f'{RUNNING_MARK} running again',
    HERE_MARK: f'{HERE_MARK} ended in this shell',
}


def main() -> None:
    app()


def die(message: str) -> NoReturn:
    err.print(f'[red]error:[/] {escape(message)}')
    raise SystemExit(1)


def tildify(path: Path) -> str:
    """A path with the home directory written as `~`, or the full path when it lies outside."""
    home = Path.home()
    return str(Path('~') / path.relative_to(home)) if path.is_relative_to(home) else str(path)


def in_any(path: Path, dirs: Sequence[Path]) -> bool:
    """Whether a path is one of the directories or lies below one. Both sides are resolved, so
    `~`, `.` and symbolic links match the directories they name."""
    resolved = path.expanduser().resolve()
    return any(resolved.is_relative_to(d.expanduser().resolve()) for d in dirs)


def human(size: float) -> str:
    for unit in ('B', 'KiB', 'MiB'):
        if size < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GiB'


# Commands #########################################################################################
@app.command(name='list', alias='l')
def list_records(
    *,
    every_shell: Annotated[bool, Parameter(name='--all', group=scope, negative=())] = False,
    dirs: Annotated[
        list[Path] | None, Parameter(name=['--dir', '-d'], group=scope, negative=())
    ] = None,
) -> None:
    """List the recorded sessions of this shell, newest first.

    A ▶ after an id marks a session that is running again.

    Parameters
    ----------
    every_shell
        List the records of every shell, including shells that have exited. A ● after an id
        marks a session that ended in this shell: a purge without session ids takes the newest.
    dirs
        List only the sessions in this directory or below it, from every shell, marked as with
        --all. Repeat it for several directories, and give `.` for the current one.
    """
    store = RecordStore.default()
    here = Shell.current()
    every_shell = every_shell or bool(dirs)
    records = store.all() if every_shell else store.of_shell(here)
    if dirs:
        records = [r for r in records if in_any(r.directory, dirs)]
    if not records:
        out.print(
            'No recorded sessions in these directories.'
            if dirs
            else 'No recorded sessions.'
            if every_shell
            else 'No recorded sessions for this shell. Try --all.'
        )
        return

    claude_paths = ClaudePaths.default()
    notes = memory_notes(claude_paths)
    now = datetime.now().astimezone()
    seen: set[str] = set()
    table = Table(box=None, header_style='bold')
    table.add_column('ID', no_wrap=True)
    table.add_column('Ended', no_wrap=True)
    table.add_column('Expires', justify='right', no_wrap=True)
    table.add_column('Notes', justify='right', no_wrap=True)
    table.add_column('Directory', overflow='ellipsis', no_wrap=True, ratio=1)
    table.add_column('Branch', overflow='ellipsis', no_wrap=True, max_width=24)
    table.add_column('Title', overflow='ellipsis', no_wrap=True, ratio=2)
    for r in records:
        running = bool(Session(r.session_id, [r], claude_paths).running_pids)
        marks = {RUNNING_MARK: running, HERE_MARK: every_shell and r.shell == here}
        seen.update(mark for mark, shown in marks.items() if shown)
        # NB: Each mark keeps its own place, so that the marks of the rows line up.
        short_id = f'{r.session_id[:8]} {"".join(m if s else " " for m, s in marks.items())}'
        days = math.ceil((r.expires - now) / timedelta(days=1))
        expires = f'{days} d' if days > 0 else '[red]expired[/]'
        table.add_row(
            short_id.rstrip(),
            f'{r.ended:%Y-%m-%d %H:%M}',
            expires,
            str(len(notes.get(r.session_id, []))),
            escape(tildify(r.directory)),
            escape(r.branch or 'N/A'),
            escape(r.title or '-'),
        )
    out.print(table)
    if legend := [text for mark, text in MARKS.items() if mark in seen]:
        out.print()
        out.print('   '.join(legend), style='dim')


@app.command(alias='p')
def purge(
    *session_ids: str,
    interactive: Annotated[
        bool, Parameter(name='-i', group=mode, negative=(), show_default=False)
    ] = False,
    yes: Annotated[bool, Parameter(name='-y', group=mode, negative=(), show_default=False)] = False,
    no: Annotated[bool, Parameter(name='-n', group=mode, negative=(), show_default=False)] = False,
    as_json: Annotated[bool, Parameter(name='--json', negative=())] = False,
) -> None:
    """Remove the local artifacts of sessions, then the records that are no longer needed.

    Without a session id, the session that ended last in this shell is purged. Every session is
    shown and asked about first, and the approved ones are purged at the end.

    The records removed are those of the purged sessions, from any shell, and those older than
    14 days. A session that is still running is refused, and then nothing is purged.

    Parameters
    ----------
    session_ids
        The sessions to purge: full session ids, or prefixes of recorded ones.
    interactive
        Ask before the purge of each session. This is the default.
    yes
        Purge every session without asking.
    no
        Purge nothing: a dry run, which shows what would be removed.
    as_json
        Print the outcome as JSON. The questions go to the standard error.
    """
    del interactive  # the default: it only names the mode that neither -y nor -n is given
    asking = not (yes or no)
    if asking and not sys.stdin.isatty():
        die('no terminal to ask on: give -y to purge without asking, or -n for a dry run')
    store = RecordStore.default()
    picks = pick_sessions(store, session_ids, dry_run=no)
    gather_approvals(picks, err if as_json else out, asking=asking, quiet=as_json and not asking)

    # Purge.
    chosen = [p for p in picks if p.outcome is not Outcome.DECLINED]
    for pick in chosen:
        if no:
            continue
        try:
            pick.found = purge_session(pick.session, dry_run=False)
        except SessionRunningError as exc:
            die(str(exc))
        store.purge_sibling_records(pick.session.id, dry_run=False)
        pick.outcome = Outcome.PURGED
    own = [r for p in chosen for r in p.session.records]
    stale = (
        store.purge_stale_records(dry_run=no, keep_sessions={p.session.id for p in chosen})
        if chosen
        else []
    )

    if as_json:
        print(json.dumps(json_report(picks, stale, dry_run=no), indent=2))
        return
    if not chosen:
        out.print('Nothing removed.')
        return
    verb = 'would be removed' if no else 'removed'
    count = f'{len(chosen)} session' + ('s' if len(chosen) > 1 else '')
    size = human(sum(p.size for p in chosen))
    out.print(f'{"Dry run" if no else "Purged"}: {count}, {size} {verb}')
    if own or stale:
        out.print(
            f'Records: {len(own) + len(stale)} {verb} '
            f'({len(own)} of the purged sessions, {len(stale)} expired)'
        )
    if urls := [p.found.synced_url for p in chosen if p.found.synced_url]:
        out.print('Delete the synced copies by hand, from the session menu on claude.ai:')
        for url in urls:
            out.print(f'  {url}', soft_wrap=True)


@dataclass
class Pick:
    """A session picked for a purge: what a purge finds of it, and what became of it."""

    session: Session
    found: SessionPurge
    outcome: Outcome = Outcome.DRY_RUN

    @property
    def size(self) -> int:
        return sum(f.size for f in self.found.findings)


def pick_sessions(store: RecordStore, session_ids: Sequence[str], *, dry_run: bool) -> list[Pick]:
    """The sessions to purge, with what a purge finds of each: those given, or else the session
    that ended last in this shell. An id that matches no session exits with an error, and so does
    a running session, of which a dry run only warns."""
    try:
        sids = (
            list(dict.fromkeys(store.resolve(s) for s in session_ids))
            if session_ids
            else [store.latest(Shell.current()).session_id]
        )
    except SelectionError as exc:
        die(str(exc))
    claude_paths = ClaudePaths.default()
    records = store.all()
    picks = []
    for sid in sids:
        session = Session(sid, [r for r in records if r.session_id == sid], claude_paths)
        picks.append(Pick(session, purge_session(session, dry_run=True)))
    for pick in picks:
        if not pick.found.running_pids:
            continue
        if not dry_run:
            die(f'session {pick.session.id} is still running; nothing was removed')
        err.print(
            f'[yellow]warning:[/] session {pick.session.id} is still running, a purge would '
            f'refuse it'
        )
    return picks


def gather_approvals(picks: list[Pick], console: Console, *, asking: bool, quiet: bool) -> None:
    """Show every picked session, and when asking, mark the ones the user declines. Quitting
    exits before anything is removed."""
    for pick in picks:
        if not quiet:
            print_review(console, pick)
        if asking:
            match ask(console, several=len(picks) > 1):
                case 'n':
                    pick.outcome = Outcome.DECLINED
                case 'a':
                    asking = False
                case 'q':
                    console.print('Nothing removed.')
                    raise SystemExit(1)
        if not quiet:
            console.print()


def json_report(picks: list[Pick], stale: list[Record], *, dry_run: bool) -> dict[str, Any]:
    return {
        'dry_run': dry_run,
        'sessions': [
            {
                'session_id': p.session.id,
                'outcome': p.outcome,
                'running_pids': p.found.running_pids,
                'findings': [{**f._asdict(), 'path': str(f.path)} for f in p.found.findings],
                'total_size': p.size,
                'records': [str(r.path) for r in p.session.records],
                'synced_url': p.found.synced_url,
                'memory_notes': [str(n) for n in p.found.memory_notes],
            }
            for p in picks
        ],
        'expired_records': [str(r.path) for r in stale],
    }


def print_review(console: Console, pick: Pick) -> None:
    """Show what a purge of a session removes, and what it leaves for deletion by hand."""
    session, found = pick.session, pick.found
    console.print(f'[bold]Session {session.id}[/]')
    if session.records:
        r = session.records[0]
        console.print(
            f'  {escape(r.title or "-")}, ended {r.ended:%Y-%m-%d %H:%M} '
            f'in {escape(tildify(r.directory))}',
            soft_wrap=True,
        )
    if found.findings:
        table = Table(box=None, show_header=False)
        table.add_column(justify='right', no_wrap=True)
        table.add_column(style='dim', no_wrap=True)
        table.add_column(overflow='fold')
        for f in found.findings:
            where = f'{f.detail} in {tildify(f.path)}' if f.detail else tildify(f.path)
            table.add_row(human(f.size), f.kind, escape(where))
        console.print(table)
        console.print(f'Total: {human(pick.size)}')
    else:
        console.print('No local artifacts found.')
    if found.memory_notes:
        console.print(f'Memory notes created by this session, kept: {len(found.memory_notes)}')
        for note in found.memory_notes:
            console.print(f'  {escape(tildify(note))}', soft_wrap=True)
    else:
        console.print('Memory notes created by this session: none recorded')
    if not found.synced_url:
        console.print(
            'No claude.ai URL recorded: the session was not synced, or its transcript is gone.'
        )


def ask(console: Console, *, several: bool) -> str:
    """Ask whether to purge the session just shown. Returns `y`, `n`, `a` for this session and
    all the rest, or `q` to quit and purge nothing. The last two are offered among several
    sessions only. An end of input quits."""
    try:
        if several:
            return Prompt.ask(
                'Purge this session? (a: this and all the rest, q: quit and purge nothing)',
                choices=['y', 'n', 'a', 'q'],
                default='n',
                console=console,
            )
        return 'y' if Confirm.ask('Purge this session?', default=False, console=console) else 'n'
    except (EOFError, KeyboardInterrupt):
        console.print()
        return 'q'


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
