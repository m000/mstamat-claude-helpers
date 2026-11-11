# claude-session-purge

The helper lives in `session_purge/`. Its modules follow the life of a session
after it ends:

1.  **`records.py`** turns the SessionEnd hook input into a `Record` and keeps
    it in a `RecordStore`, one JSON file per ended session under
    `$XDG_STATE_HOME/claude-session-purge/`.
2.  **`artifacts.py`** finds and removes what Claude Code keeps of a session.
    Each kind of file is one `Artifact` subclass in `ARTIFACTS`.
    `purge_session()` collects every finding before it removes anything.
3.  **`settings.py`** adds and removes the hook entry in the settings file of
    Claude Code.
4.  **`cli.py`** only picks the session, calls the modules above, and prints
    what they return.

## How a record finds its shell

The SessionEnd hook runs without a terminal. Its first ancestor process with a
terminal is `claude`. The leader of that terminal session is the shell that
started `claude`, and `--auto` later runs in the same session.

A record keys the shell by its process id and its start time. Process ids are
reused, so the start time tells a new shell from an old one with the same id.
The terminal name only makes the file names readable, since terminal names are
reused too.

## Adding a kind of artifact

When Claude Code keeps session data in a new place:

1.  Add an `Artifact` subclass to `artifacts.py`. Give it a `kind` and a
    `paths()` method. Override `find()` and `remove()` only when the artifact
    is a part of a shared file, as `HistoryPrompts` is.
2.  Add an instance to `ARTIFACTS`.
3.  Add the file to the `session_files` fixture in `tests/conftest.py`, and its
    kind to `test_purge_session__dry_run`.
