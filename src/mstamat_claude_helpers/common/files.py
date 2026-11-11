"""File operations shared by the artifacts, the records and the settings."""

import shutil
import tempfile
from pathlib import Path


def size_of(path: Path) -> int:
    """Bytes of a file, or of all files below a directory. A symlink counts as itself."""
    if path.is_dir() and not path.is_symlink():
        return sum(p.lstat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())
    return path.lstat().st_size


def remove_path(path: Path) -> None:
    """Remove a file, a symlink, or a directory with everything below it."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def atomic_write(path: Path, text: str) -> None:
    """Replace a file's content in one rename, so that no reader ever sees it half written.

    The file keeps its mode. A symlink keeps pointing where it did, and its target is replaced.
    """
    path = path.resolve()
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', dir=path.parent, prefix=f'.{path.name}.', delete=False
    ) as tmp:
        tmp.write(text)
    Path(tmp.name).chmod(mode)
    Path(tmp.name).replace(path)
