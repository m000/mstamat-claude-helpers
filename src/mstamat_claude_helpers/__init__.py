"""Command-line helpers around Claude Code. Each helper is a subpackage with its own command,
except `common`, which holds what the helpers share."""

from importlib.metadata import version

# NB: pyproject.toml is the only place the version is written; this reads it back from the
#     installed package.
__version__ = version('mstamat-claude-helpers')
