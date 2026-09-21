# coding: utf-8
"""Tests for the commit commands (sgit/commit.py)."""
import pytest
import sublime

from conftest import requires_git
import sgit.cmd
from sgit.cmd import GitCmd
from sgit.commit import (GitCommit, GitCommitCommand, GitQuickCommitCommand,
                         GIT_NOTHING_STAGED)
from sgit.status import GIT_WORKING_DIR_CLEAN

pytestmark = requires_git


@pytest.fixture
def window(tmp_repo):
    """A window whose active view points at the temp repo."""
    view = sublime.View(file_name=tmp_repo.path + '/a.txt')
    win = sublime.Window(folders=[tmp_repo.path], views=[view], active_view=view)
    yield win
    GitCommit.windows.clear()


@pytest.fixture
def commands(monkeypatch):
    """Record every git process spawned, returning the list of argv lists."""
    spawned = []
    original = sgit.cmd.subprocess.Popen

    def recording_popen(args, *a, **kwargs):
        spawned.append(list(args))
        return original(args, *a, **kwargs)

    monkeypatch.setattr(sgit.cmd.subprocess, 'Popen', recording_popen)
    return spawned


def subcommands(spawned):
    return [c[1 + len(GitCmd.opts)] for c in spawned]


class TestGitCommitCommand(object):

    def test_one_status_process_before_the_dry_run(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', 'a.txt')
        del commands[:]

        GitCommitCommand(window).run()

        # one status for the staged/unstaged check, then the commit --dry-run
        assert subcommands(commands) == ['status', 'commit']
        assert commands[-1][-3:] == ['commit', '--dry-run', '--status']

        view = window.active_view()
        assert view.name() == 'COMMIT_EDITMSG'
        assert view.settings().get('git_view') == 'commit'
        assert view.settings().get('git_repo') == tmp_repo.path
        assert view.id() in GitCommit.windows
        assert [name for name, _ in view.commands] == ['git_commit_template']

    def test_nothing_staged_errors_after_one_process(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        del commands[:]

        GitCommitCommand(window).run()

        assert subcommands(commands) == ['status']
        assert sublime.error_messages == [GIT_NOTHING_STAGED]
        assert GitCommit.windows == {}

    def test_add_on_clean_repo_errors_after_one_process(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        del commands[:]

        GitCommitCommand(window).run(add=True)

        assert subcommands(commands) == ['status']
        assert sublime.error_messages == [GIT_WORKING_DIR_CLEAN]

    def test_add_with_only_unstaged_changes_opens_the_view(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        del commands[:]

        GitCommitCommand(window).run(add=True)

        assert subcommands(commands) == ['status', 'commit']
        assert '--all' in commands[-1]
        assert sublime.error_messages == []


class TestGitQuickCommitCommand(object):

    def test_open_spawns_one_process(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        del commands[:]

        panels = []
        window.show_input_panel = lambda *args: panels.append(args)
        GitQuickCommitCommand(window).run()

        assert subcommands(commands) == ['status']
        assert len(panels) == 1
        assert panels[0][0] == 'Commit message:'

    def test_clean_repo_errors(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        del commands[:]

        panels = []
        window.show_input_panel = lambda *args: panels.append(args)
        GitQuickCommitCommand(window).run()

        assert subcommands(commands) == ['status']
        assert panels == []
        assert sublime.error_messages == [GIT_WORKING_DIR_CLEAN.capitalize()]

    def _panel(self, window):
        panel = sublime.View(window=window)
        window.get_output_panel = lambda name: panel
        return panel

    def test_submit_with_staged_changes_commits_the_index(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', 'a.txt')
        self._panel(window)
        del commands[:]

        GitQuickCommitCommand(window).on_commit_message(tmp_repo.path, 'the message')

        # a single status re-check, then the commit itself
        assert subcommands(commands) == ['status', 'commit']
        assert '-a' not in commands[-1]
        assert tmp_repo.git('log', '-1', '--format=%s') == 'the message'

    def test_submit_without_staged_changes_commits_all(self, settings, tmp_repo, window, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        self._panel(window)
        del commands[:]

        GitQuickCommitCommand(window).on_commit_message(tmp_repo.path, 'commit all')

        assert subcommands(commands) == ['status', 'commit']
        assert '-a' in commands[-1]
        assert tmp_repo.git('log', '-1', '--format=%s') == 'commit all'
        assert tmp_repo.git('status', '--porcelain') == ''

    def test_staging_while_the_panel_is_open_is_picked_up(self, settings, tmp_repo, window, commands):
        """The submit-time re-check is what makes this work."""
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        cmd = GitQuickCommitCommand(window)
        window.show_input_panel = lambda *args: None
        cmd.run()

        tmp_repo.write('b.txt', 'b\n')
        tmp_repo.git('add', 'b.txt')
        self._panel(window)

        cmd.on_commit_message(tmp_repo.path, 'only the index')
        assert tmp_repo.git('log', '-1', '--name-only', '--format=') == 'b.txt'
        # a.txt is still only modified in the worktree
        assert tmp_repo.git('diff', '--name-only') == 'a.txt'
