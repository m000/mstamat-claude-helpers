"""What Claude Code keeps on disk of a session, and how each kind of it is found and removed."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Self

import psutil

from mstamat_claude_helpers.common.claude_paths import ClaudePaths
from mstamat_claude_helpers.common.files import atomic_write, remove_path, size_of
from mstamat_claude_helpers.session_purge.records import Record
from mstamat_claude_helpers.session_purge.types import Finding, SessionPurge


class SessionRunningError(RuntimeError):
    """The session to purge still runs, so it keeps writing the files a purge would remove."""

    def __init__(self, session_id: str, pids: list[int]) -> None:
        self.session_id = session_id
        self.pids = pids
        pid_list = ', '.join(map(str, pids))
        super().__init__(f'session {session_id} is still running (pid {pid_list}), exit it first')


@dataclass(frozen=True)
class ProcessRecord:
    """A Claude Code process record, and whether its process still runs."""

    path: Path
    pid: int
    bridge_session_id: str | None
    alive: bool

    @classmethod
    def load(cls, path: Path, data: dict[str, Any]) -> Self:
        try:
            started = psutil.Process(data['pid']).create_time()
            # NB: Without startedAt a reused pid reads as running, which errs on the safe side.
            alive = 'startedAt' not in data or abs(started - data['startedAt'] / 1000) < 5
        except psutil.NoSuchProcess:
            alive = False
        return cls(path, data['pid'], data.get('bridgeSessionId'), alive)


@dataclass
class Session:
    """A session to purge, with its records and the paths of the Claude Code data it lives in."""

    id: str
    records: list[Record]
    claude_paths: ClaudePaths

    @property
    def process_records(self) -> list[ProcessRecord]:
        return [
            ProcessRecord.load(path, data)
            for path in self.claude_paths.sessions.glob('*.json')
            if (data := json.loads(path.read_text(encoding='utf-8'))).get('sessionId') == self.id
        ]

    @property
    def running_pids(self) -> list[int]:
        return [p.pid for p in self.process_records if p.alive]

    @property
    def transcripts(self) -> list[Path]:
        recorded = {Path(r.hook_input['transcript_path']) for r in self.records}
        found = {*self.claude_paths.projects.glob(f'*/{self.id}.jsonl'), *recorded}
        return sorted(p for p in found if p.is_file())

    @property
    def history_pattern(self) -> str:
        # NB: Matching the raw text is exact: inside a prompt's text the quotes would be escaped,
        #     so only the sessionId field itself can match.
        return f'"sessionId":"{self.id}"'

    def synced_url(self) -> str | None:
        """The claude.ai URL of the session's synced copy, from the transcript or a process
        record."""
        urls = [
            attachment['url']
            for transcript in self.transcripts
            for line in transcript.open(encoding='utf-8')
            if '"remote_session_change"' in line
            if (attachment := json.loads(line).get('attachment'))
            and attachment.get('type') == 'remote_session_change'
        ]
        if urls:
            return urls[-1]
        bridges = [p.bridge_session_id for p in self.process_records if p.bridge_session_id]
        return f'https://claude.ai/code/{bridges[0]}' if bridges else None


class Artifact:
    """One kind of session artifact: finds a session's files, and removes what it found.

    Supporting a new location after a Claude Code upgrade means one more subclass in ARTIFACTS.
    """

    kind: ClassVar[str]

    def paths(self, session: Session) -> list[Path]:
        raise NotImplementedError

    def find(self, session: Session) -> list[Finding]:
        return [Finding(self.kind, p, size_of(p)) for p in self.paths(session)]

    def remove(self, session: Session, findings: list[Finding]) -> None:
        for finding in findings:
            remove_path(finding.path)


class Transcripts(Artifact):
    kind = 'transcript'

    def paths(self, session: Session) -> list[Path]:
        return session.transcripts


class Sidecars(Artifact):
    """The transcript's directory of tool results and subagent transcripts."""

    kind = 'sidecar'

    def paths(self, session: Session) -> list[Path]:
        return sorted(
            p for p in session.claude_paths.projects.glob(f'*/{session.id}') if p.is_dir()
        )


class FileHistory(Artifact):
    kind = 'file history'

    def paths(self, session: Session) -> list[Path]:
        return [p for p in [session.claude_paths.file_history / session.id] if p.is_dir()]


class SessionEnv(Artifact):
    kind = 'session env'

    def paths(self, session: Session) -> list[Path]:
        return [p for p in [session.claude_paths.session_env / session.id] if p.is_dir()]


class ProcessRecords(Artifact):
    """Process records whose process has exited, each with the key file of the same pid."""

    kind = 'process record'

    def paths(self, session: Session) -> list[Path]:
        stale = [p for p in session.process_records if not p.alive]
        keys = session.claude_paths.sessions.glob
        return [path for p in stale for path in [p.path, *keys(f'{p.pid}.*.key')]]


class Scratch(Artifact):
    """The scratchpad and task output in the temporary directory."""

    kind = 'scratch'

    def paths(self, session: Session) -> list[Path]:
        recorded = {r.scratch_dir for r in session.records if r.scratch_dir}
        found = {*session.claude_paths.tmp_root.glob(f'*/{session.id}'), *recorded}
        return sorted(p for p in found if p.name == session.id and p.is_dir())


class HistoryPrompts(Artifact):
    """The session's prompts, as lines of the history file. The file itself stays."""

    kind = 'history'

    def find(self, session: Session) -> list[Finding]:
        history = session.claude_paths.history
        if not history.is_file():
            return []
        lines = [line for line in history.open(encoding='utf-8') if session.history_pattern in line]
        if not lines:
            return []
        size = sum(len(line.encode()) for line in lines)
        return [Finding(self.kind, history, size, f'{len(lines)} prompt(s)')]

    def remove(self, session: Session, findings: list[Finding]) -> None:
        if not findings:
            return
        history = session.claude_paths.history
        # NB: A prompt that another running session appends between the read and the rename is lost.
        kept = [
            line for line in history.open(encoding='utf-8') if session.history_pattern not in line
        ]
        atomic_write(history, ''.join(kept))


class Pastes(Artifact):
    """Paste-cache entries that the session's prompts reference and no other prompt does."""

    kind = 'paste'

    def paths(self, session: Session) -> list[Path]:
        history = session.claude_paths.history
        if not history.is_file():
            return []
        mine: set[str] = set()
        others: set[str] = set()
        for line in history.open(encoding='utf-8'):
            pasted = (json.loads(line).get('pastedContents') or {}).values()
            hashes = {p['contentHash'] for p in pasted if 'contentHash' in p}
            (mine if session.history_pattern in line else others).update(hashes)
        cache = session.claude_paths.paste_cache
        return sorted(p for h in mine - others if (p := cache / f'{h}.txt').is_file())


ARTIFACTS: list[Artifact] = [
    Transcripts(),
    Sidecars(),
    FileHistory(),
    SessionEnv(),
    ProcessRecords(),
    Scratch(),
    HistoryPrompts(),
    Pastes(),
]


def purge_session(session: Session, *, dry_run: bool) -> SessionPurge:
    """Remove the local artifacts of a session, and report what was found.

    Everything is collected before anything is removed, the URL of the synced copy included.
    A running session raises SessionRunningError; a dry run reports its pids instead.
    """
    running = session.running_pids
    if running and not dry_run:
        raise SessionRunningError(session.id, running)
    found = [(artifact, artifact.find(session)) for artifact in ARTIFACTS]
    url = session.synced_url()
    if not dry_run:
        for artifact, findings in found:
            artifact.remove(session, findings)
    return SessionPurge([f for _, findings in found for f in findings], url, running)
