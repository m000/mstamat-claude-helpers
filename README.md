# mstamat-claude-helpers

Command-line helpers around [Claude Code](https://claude.com/claude-code). Each
helper is a command of its own. The package has one helper so far:
`claude-session-purge`.

## Install

The helpers install from a clone of this repository, as uv tools. Every helper
then has its command on your `PATH`.

1.  Clone the repository.

2.  Install the helpers from the clone:

    ``` bash
    make tool-install
    ```

The install is editable, so a change to the code takes effect on the next run.
After a change to `pyproject.toml`, for example a new version or a new helper,
run `make tool-install` again.

uv installs the helpers into a private environment of their own. That
environment takes its dependencies from the version ranges in `pyproject.toml`
and ignores `uv.lock`, so the installed helpers can run newer versions than the
tests did. The dependencies stay at the versions of the first install until you
move them to the newest versions that the ranges allow:

``` bash
make tool-upgrade
```

`make tool-upgrade` changes only the installed helpers. To upgrade the
dependencies that the tests use, update `uv.lock` with the `uv-upgrade` targets
(see `make help`).

## claude-session-purge

`claude-session-purge` removes what Claude Code keeps on your disk of a session:

- the transcript and its directory of tool results and subagent transcripts
- the file edit history and the session environment
- the process records of exited Claude Code processes, with their key files
- the scratchpad and task output in the temporary directory
- the prompts of the session in `~/.claude/history.jsonl`, and the pasted text
  that no other prompt refers to

A copy of the session that Claude Code synced to claude.ai stays. The helper
prints its URL, so that you can delete it from the session menu on claude.ai.

### Record the sessions that end

A SessionEnd hook records every session that ends, with its title and the shell
that started it. Install the hook into your user settings of Claude Code:

``` bash
claude-session-purge hook --install
```

The hook entry carries a marker. If you move the helper, run `hook --install`
again from the new location, and the entry follows. `hook --print` shows the
entry without a change, and `hook --uninstall` removes it.

### Purge a session

1.  List the sessions that ended in the current shell, with the directory, the
    title and the number of memory notes of each:

    ``` bash
    claude-session-purge list
    ```

    Use `--all` to list the sessions of every shell.

2.  See what a purge of the latest session would remove:

    ``` bash
    claude-session-purge purge --auto --dry-run
    ```

    Use `--id` with a session id, or with the start of one, to pick a
    different session.

3.  Purge the session:

    ``` bash
    claude-session-purge purge --auto
    ```

A purge refuses a session that is still running. After a purge, the helper
deletes the records of the purged session and every record older than 14 days.

A purge keeps the memory notes that Claude Code saved during the session,
because later sessions rely on them. The purge lists the notes that the session
created, so that you can delete any of them by hand. Claude Code records the
creating session for some notes only, so the list can miss a note.

### Limitations

- Claude Code does not document where it keeps session data. The helper follows
  the layout of Claude Code 2.1.322. After an upgrade of Claude Code, check a
  purge with `--dry-run` first.
- The tests run on Linux only. The helper reads processes through psutil, so it
  should work on macOS too, but no test ran there yet.

## Development

The development tools run through uv, so `make` needs only uv. `make help` lists the
targets. Run the tests with:

``` bash
uv run --group dev pytest
```

Each helper is a subpackage of `mstamat_claude_helpers` with a command of its
own. The one exception is `common`, which holds what the helpers share:

- `common/claude_paths.py` knows where Claude Code keeps its data. It honors
  `CLAUDE_CONFIG_DIR`, and the tests point it at a temporary directory.
- `common/files.py` holds the file operations: sizes, removal, and a write
  that replaces a file in one rename.

`docs/session-purge.md` describes the design of `claude-session-purge`.
