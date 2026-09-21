# coding: utf-8
"""Shared fixtures.

The stub ``sublime`` / ``sublime_plugin`` modules MUST be in ``sys.modules``
before anything imports ``sgit``, so that is done at module import time here.
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUBS = os.path.join(ROOT, 'tests', 'stubs')

if STUBS not in sys.path:
    sys.path.insert(0, STUBS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sublime  # noqa: E402  (the stub)
import sublime_plugin  # noqa: E402,F401  (the stub)

sys.modules['sublime'] = sublime
sys.modules['sublime_plugin'] = sublime_plugin

import sgit  # noqa: E402,F401  -- imports the whole plugin against the stubs
from sgit.helpers import GitRepoHelper  # noqa: E402
from sgit.blame import GitBlameCache  # noqa: E402
from sgit.status import reset_status_bar_state, reset_view_refresh_state  # noqa: E402
from sgit.cmd import reset_repo_locks  # noqa: E402

GIT = shutil.which('git')
requires_git = pytest.mark.skipif(GIT is None, reason='git not found on PATH')


def _reset_plugin_class_state():
    # Both of these are class attributes, i.e. process-global for the whole run.
    GitRepoHelper.windows.clear()
    GitBlameCache.commits.clear()
    GitBlameCache.lines.clear()
    # Module-level status bar cache / token / in-flight bookkeeping.
    reset_status_bar_state()
    # Module-level per-view generation / in-flight table of the async view refreshes.
    reset_view_refresh_state()
    reset_repo_locks()


@pytest.fixture(autouse=True)
def _reset_sublime_state():
    sublime.reset()
    _reset_plugin_class_state()
    yield
    sublime.reset()
    _reset_plugin_class_state()


@pytest.fixture(autouse=True)
def _preserve_cwd():
    """Safety net: no plugin code should chdir(), but do not let a test leak one."""
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


@pytest.fixture
def settings():
    """The SublimeGit.sublime-settings object backing ``get_setting``."""
    return sublime.load_settings('SublimeGit.sublime-settings')


@pytest.fixture
def flush():
    return sublime.flush_timeouts


def git(cwd, *args, **kwargs):
    """Run git in ``cwd`` and return stripped stdout; raises on failure."""
    check = kwargs.pop('check', True)
    proc = subprocess.run([GIT] + list(args), cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError('git %s failed (%s): %s' % (' '.join(args), proc.returncode, proc.stderr))
    return proc.stdout.strip()


class TmpRepo(object):
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)

    def git(self, *args, **kwargs):
        return git(self.path, *args, **kwargs)

    def write(self, relpath, content=''):
        full = os.path.join(self.path, relpath)
        d = os.path.dirname(full)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def commit(self, relpath, content='', message='commit'):
        self.write(relpath, content)
        self.git('add', '--', relpath)
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def commit_all(self, message='commit'):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')


@pytest.fixture
def tmp_repo(tmp_path):
    if GIT is None:
        pytest.skip('git not found on PATH')
    path = tmp_path / 'repo'
    path.mkdir()
    # realpath so paths match what GitRepoHelper computes (macOS /private/tmp)
    repo = TmpRepo(os.path.realpath(str(path)))
    repo.git('init', '-q', '-b', 'main')
    repo.git('config', 'user.name', 'Test User')
    repo.git('config', 'user.email', 'test@example.com')
    repo.git('config', 'commit.gpgsign', 'false')
    repo.git('config', 'core.autocrlf', 'false')
    return repo
