"""The records of ended sessions, which the SessionEnd hook writes, and the shells they ran from."""

import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, Self

import psutil

from mstamat_claude_helpers.common.files import atomic_write

RECORD_VERSION = 1
RECORD_TTL = timedelta(days=14)
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


class SelectionError(LookupError):
    """No recorded session matches what was asked for, or more than one does."""


class Process(Protocol):
    """The part of psutil.Process that finding a session's shell reads."""

    @property
    def pid(self) -> int: ...

    def terminal(self) -> str | None: ...


@dataclass(frozen=True)
class Shell:
    """The shell a session was started from: the leader of the terminal session `claude` ran in.

    A pid is reused once its process exits, so the start time completes the key.
    """

    pid: int
    started: float

    @classmethod
    def of_session(cls, sid: int) -> Self:
        return cls(sid, round(psutil.Process(sid).create_time(), 2))

    @classmethod
    def current(cls) -> Self:
        return cls.of_session(os.getsid(0))

    @property
    def alive(self) -> bool:
        try:
            return round(psutil.Process(self.pid).create_time(), 2) == self.started
        except psutil.NoSuchProcess:
            return False


@dataclass
class Record:
    """One ended session, as the SessionEnd hook saw it. A session that ended twice, for example
    after a /resume, has two records."""

    session_id: str
    reason: str
    ended: datetime
    title: str | None
    directory: Path  # the working directory of the session, as the hook input gave it
    terminal: str | None
    shell: Shell | None
    claude_pid: int | None
    hook_input: dict[str, Any]
    path: Path | None = None  # where the store keeps the record; not part of it

    @classmethod
    def from_hook(cls, hook_input: dict[str, Any], ancestors: Iterable[Process]) -> Self:
        """The record of the session that just ended, from the SessionEnd hook's input.

        The hook has no terminal of its own. Its first ancestor with one is `claude`, and the
        leader of that terminal session is the shell `claude` was started from.
        """
        claude = next((p for p in ancestors if p.terminal()), None)
        transcript = Path(hook_input['transcript_path'])
        return cls(
            session_id=hook_input['session_id'],
            reason=hook_input['reason'],
            ended=datetime.now().astimezone(),
            title=title_of(transcript) if transcript.is_file() else None,
            directory=Path(hook_input['cwd']),
            terminal=claude.terminal() if claude else None,
            shell=Shell.of_session(os.getsid(claude.pid)) if claude else None,
            claude_pid=claude.pid if claude else None,
            hook_input=hook_input,
        )

    @classmethod
    def load(cls, path: Path) -> Self:
        data = json.loads(path.read_text(encoding='utf-8'))
        match data['version']:
            case 1:
                return cls(
                    session_id=data['session_id'],
                    reason=data['reason'],
                    ended=datetime.fromisoformat(data['ended']),
                    title=data['title'],
                    directory=Path(data['directory']),
                    terminal=data['terminal'],
                    shell=Shell(**data['shell']) if data['shell'] else None,
                    claude_pid=data['claude_pid'],
                    hook_input=data['hook_input'],
                    path=path,
                )
            case _:  # pragma: no cover
                raise ValueError(f'{path}: unknown record version {data["version"]!r}')

    @property
    def expires(self) -> datetime:
        return self.ended + RECORD_TTL

    @property
    def scratch_dir(self) -> Path | None:
        """The session's directory in the temporary directory, as the hook input named it."""
        scratchpad = self.hook_input.get('scratchpad_dir')
        return Path(scratchpad).parent if scratchpad else None

    def to_json(self) -> str:
        data = {
            'version': RECORD_VERSION,
            'session_id': self.session_id,
            'reason': self.reason,
            'ended': self.ended.isoformat(),
            'title': self.title,
            'directory': str(self.directory),
            'terminal': self.terminal,
            'shell': asdict(self.shell) if self.shell else None,
            'claude_pid': self.claude_pid,
            'hook_input': self.hook_input,
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + '\n'

    def delete(self) -> None:
        if self.path is None:  # pragma: no cover
            raise ValueError(f'the record of {self.session_id} was never saved')
        self.path.unlink()


def title_of(transcript: Path) -> str | None:
    """The latest AI-generated title of a transcript, or else the first prompt of the session."""
    ai_title = first_prompt = None
    with transcript.open(encoding='utf-8') as lines:
        for line in lines:
            if '"ai-title"' not in line and '"last-prompt"' not in line:
                continue  # spares parsing the bulk of the transcript
            entry = json.loads(line)
            if entry.get('type') == 'ai-title':
                ai_title = entry['aiTitle']
            elif entry.get('type') == 'last-prompt' and first_prompt is None:
                first_prompt = ' '.join(entry['lastPrompt'].split())[:200]
    return ai_title or first_prompt


@dataclass(frozen=True)
class RecordStore:
    """The records on disk, one JSON file per ended session."""

    directory: Path

    @classmethod
    def default(cls) -> Self:
        """The store under XDG_STATE_HOME, or ~/.local/state when it is unset."""
        state = os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state'
        return cls(Path(state) / 'claude-session-purge')

    def all(self) -> list[Record]:
        """Every record, newest first."""
        records = (Record.load(p) for p in self.directory.glob('*.json'))
        return sorted(records, key=lambda r: r.ended, reverse=True)

    def save(self, record: Record) -> None:
        terminal = record.terminal or 'noterminal'
        name = f'{terminal.removeprefix("/dev/").replace("/", "-")}.{record.ended:%Y%m%dT%H%M%S}'
        record.path = self.directory / f'{name}.{record.session_id[:8]}.json'
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write(record.path, record.to_json())

    def of_shell(self, shell: Shell) -> list[Record]:
        """The records of the sessions started from a shell, newest first."""
        return [r for r in self.all() if r.shell == shell]

    def latest(self, shell: Shell) -> Record:
        """The record of the session that ended last in a shell."""
        if not (records := self.of_shell(shell)):
            raise SelectionError('no recorded session for this shell; see `list --all`')
        return records[0]

    def resolve(self, session_id: str) -> str:
        """A full session id as given, or the recorded session whose id starts with a prefix."""
        if UUID_RE.fullmatch(session_id := session_id.lower()):
            return session_id
        matches = sorted({r.session_id for r in self.all() if r.session_id.startswith(session_id)})
        if len(matches) != 1:
            raise SelectionError(
                f'{len(matches)} recorded sessions start with {session_id!r}, give more of the id'
            )
        return matches[0]

    def purge_sibling_records(self, session_id: str, *, dry_run: bool) -> list[Record]:
        """Remove every record of a session, from any shell. Returns the records."""
        siblings = [r for r in self.all() if r.session_id == session_id]
        if not dry_run:
            for r in siblings:
                r.delete()
        return siblings

    def purge_stale_records(
        self, *, dry_run: bool, keep_session: str | None = None
    ) -> list[Record]:
        """Remove the records that expired, RECORD_TTL after their session ended. Returns them.

        The records of `keep_session` stay, for purge_sibling_records to count as its own.
        """
        now = datetime.now().astimezone()
        stale = [r for r in self.all() if r.expires <= now and r.session_id != keep_session]
        if not dry_run:
            for r in stale:
                r.delete()
        return stale
