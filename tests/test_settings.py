import json
from pathlib import Path

import pytest

from mstamat_claude_helpers.session_purge.settings import HookSettings, hook_entry
from mstamat_claude_helpers.session_purge.types import HookChange

OTHER_HOOK = {'type': 'command', 'command': 'echo other'}


# HookSettings.install() ###########################################################################
def test_hook_settings_install__new_file(tmp_path: Path) -> None:
    """Test that an install creates the settings file with the entry, and a second install of
    the same entry changes nothing."""
    settings = HookSettings(tmp_path / 'settings.json')
    entry = hook_entry('/bin/purge')

    assert settings.install(entry) == HookChange.INSTALLED
    assert settings.install(entry) == HookChange.UNCHANGED
    assert json.loads(settings.path.read_text()) == {'hooks': {'SessionEnd': [{'hooks': [entry]}]}}
    assert settings.path.read_text().startswith('{\n  "hooks"')


def test_hook_settings_install__moved(tmp_path: Path) -> None:
    """Test that an install from a new location updates the entry in place and drops its
    duplicates. Other hooks and other settings keep their place."""
    settings = HookSettings(tmp_path / 'settings.json')
    old, new = hook_entry('/old/purge'), hook_entry('/new/purge')
    current = {
        'model': 'opus',
        'hooks': {
            'SessionEnd': [
                {'matcher': 'clear', 'hooks': [OTHER_HOOK, old]},
                {'hooks': [old]},
            ]
        },
    }
    settings.path.write_text(json.dumps(current))

    assert settings.install(new) == HookChange.UPDATED
    assert json.loads(settings.path.read_text()) == {
        'model': 'opus',
        'hooks': {'SessionEnd': [{'matcher': 'clear', 'hooks': [OTHER_HOOK, new]}]},
    }


def test_hook_settings_install__invalid_json(tmp_path: Path) -> None:
    """Test that a settings file that is not valid JSON raises and stays as it is."""
    settings = HookSettings(tmp_path / 'settings.json')
    settings.path.write_text('{"a": 1,}')

    with pytest.raises(json.JSONDecodeError):
        settings.install(hook_entry('/bin/purge'))
    assert settings.path.read_text() == '{"a": 1,}'


def test_hook_settings_install__symlink(tmp_path: Path) -> None:
    """Test that a symlinked settings file stays a symlink, and its target gets the entry."""
    target = tmp_path / 'dotfiles' / 'settings.json'
    target.parent.mkdir()
    target.write_text('{}')
    settings = HookSettings(tmp_path / 'settings.json')
    settings.path.symlink_to(target)

    settings.install(hook_entry('/bin/purge'))

    assert settings.path.is_symlink()
    assert 'SessionEnd' in json.loads(target.read_text())['hooks']


# HookSettings.uninstall() #########################################################################
@pytest.mark.parametrize(
    ('others', 'remaining'),
    (
        ([OTHER_HOOK], {'model': 'opus', 'hooks': {'SessionEnd': [{'hooks': [OTHER_HOOK]}]}}),
        ([], {'model': 'opus'}),
    ),
    ids=('other_hooks', 'only_ours'),
)
def test_hook_settings_uninstall__containers(
    tmp_path: Path, others: list[dict[str, str]], remaining: dict[str, object]
) -> None:
    """Test that an uninstall removes this helper's entry, and the containers it leaves empty."""
    settings = HookSettings(tmp_path / 'settings.json')
    current = {
        'model': 'opus',
        'hooks': {'SessionEnd': [{'hooks': [*others, hook_entry('/bin/purge')]}]},
    }
    settings.path.write_text(json.dumps(current))

    assert settings.uninstall()
    assert json.loads(settings.path.read_text()) == remaining
    assert not settings.uninstall()
