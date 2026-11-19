# claude-session-purge

The helper lives in `session_purge/`. Its modules follow the life of a session
after it ends:

1.  **`records.py`** turns the SessionEnd hook input into a `Record` and keeps
    it in a `RecordStore`, one JSON file per ended session under
    `$XDG_STATE_HOME/claude-session-purge/`.
2.  **`artifacts.py`** finds and removes what Claude Code keeps of a session.
    Each kind of file is one `Artifact` subclass in `ARTIFACTS`.
    `purge_session()` collects every finding before it removes anything.
3.  **`memory.py`** finds the memory notes that a session created, from the
    `originSessionId` in their frontmatter. A purge reports those notes and
    keeps them: later sessions may rely on a note or have edited it, and the
    memory directory keeps no versions.
4.  **`settings.py`** adds and removes the hook entry in the settings file of
    Claude Code.
5.  **`cli.py`** only picks the sessions, calls the modules above, and prints
    what they return. A `purge` first shows every picked session and gathers the
    approvals, and removes the approved sessions at the end.

## How a record finds its shell

The SessionEnd hook runs without a terminal. Its first ancestor process with a
terminal is `claude`. The leader of that terminal session is the shell that
started `claude`, and a `purge` without a session id later runs in the same
session.

A record identifies the shell by its process id and its start time. The process
id alone is not enough, because the system gives it to a new process after the
shell exits. The terminal name is not used for this, since terminal names are
reused too. It only makes the file names readable.

The start time must not change while the shell is running, and on Linux the
usual one does. The kernel only stores how long after boot a process started.
Tools such as `ps` and psutil turn this into a date by adding the boot time,
and the kernel computes the boot time from the current system clock. So when
the clock is adjusted, the start date of every running process changes too.
On most systems the change is small. On WSL it can be hours: the virtual
machine is frozen while the host sleeps, and the clock jumps forward when the
host wakes up.

For this reason a record on Linux keeps the start time as the kernel stores it,
counted from boot, together with the boot id. The boot id is needed because the
count restarts from zero on every boot. On other systems the kernel stores the
start date itself, and the record keeps that.

The same problem was reported against psutil. Since then psutil uses the start
time counted from boot for its own checks, but `create_time()` still returns
the date. If psutil ever offers the stable value publicly, the helper can use
it and drop its own reading of `/proc`.

- <https://github.com/giampaolo/psutil/issues/2526>

## Adding a kind of artifact

When Claude Code keeps session data in a new place:

1.  Add an `Artifact` subclass to `artifacts.py`. Give it a `kind` and a
    `paths()` method. Override `find()` and `remove()` only when the artifact
    is a part of a shared file, as `HistoryPrompts` is.
2.  Add an instance to `ARTIFACTS`.
3.  Add the file to the `session_files` fixture in `tests/conftest.py`, and its
    kind to `test_purge_session__dry_run`.
