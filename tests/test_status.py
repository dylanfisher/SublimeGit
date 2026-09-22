# coding: utf-8
"""Behavioural tests for sgit/status.py against a real temporary git repo."""
import json
import os
import time

import pytest
import sublime

from conftest import requires_git, GIT
import sgit.status
from sgit.status import (GitStatusBarUpdater, GitStatusBuilder, GitStatusCommand,
                         GitQuickStatusCommand, GitStatusBarEventListener,
                         GitStatusMoveCmd, GitStatusRefreshCommand, GitStatusWriteCommand,
                         GitStatusEventListener, GitStatusUnstageCommand,
                         GitStatusDiscardCommand, GitStatusStageCommand,
                         UNTRACKED_FILES, UNSTAGED_CHANGES,
                         STAGED_CHANGES,
                         GIT_STATUS_HELP, GIT_STATUS_VIEW_SETTINGS, GIT_STATUS_VIEW_SYNTAX,
                         GIT_STATUS_VIEW_TITLE_PREFIX, GIT_WORKING_DIR_CLEAN,
                         parse_porcelain_v2, format_status_bar_message,
                         request_status_bar_update, invalidate_status_bar_cache,
                         reset_status_bar_state, reset_view_refresh_state)
from sgit.diff import (GitDiffRefreshCommand, GitDiffWriteCommand, GitDiffEventListener,
                       GIT_DIFF_CLEAN, GIT_DIFF_CLEAN_CACHED)
from sgit.cmd import GitCmd
from sgit.helpers import GitStatusHelper, GitStashHelper, GitLogHelper, GitBranchHelper, GitRemoteHelper, GitDiffHelper

pytestmark = requires_git


class RealGit(GitCmd, GitStatusHelper, GitStashHelper, GitLogHelper, GitRemoteHelper, GitDiffHelper):
    pass


def status_bar(repo, view, kind='fancy', flush=sublime.flush_timeouts):
    updater = GitStatusBarUpdater([GIT], 'utf-8', [], repo.path, kind, view)
    updater.run()  # run synchronously instead of via Thread.start()
    flush()
    return view.get_status('git-status')


# The apply phase of the async view refreshes is a hidden TextCommand that the
# stub's ``view.run_command`` only records; this executes what was recorded.
WRITE_COMMANDS = {
    'git_status_write': GitStatusWriteCommand,
    'git_diff_write': GitDiffWriteCommand,
}


def run_write_commands(view):
    """Execute (and drop) the recorded ``git_*_write`` commands of ``view``.
    Other recorded commands are left in place. Returns the number executed."""
    ran = 0
    for name, args in list(view.commands):
        cls = WRITE_COMMANDS.get(name)
        if cls is None:
            continue
        view.commands.remove((name, args))
        cls(view).run(None, **(args or {}))
        ran += 1
    return ran


@pytest.fixture
def inline_threads(monkeypatch):
    """Make ``run_in_thread`` run the worker body synchronously."""
    monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())


@pytest.fixture
def deferred_threads(monkeypatch):
    """Capture worker bodies instead of running them, so a test controls
    exactly when each gather happens. Returns the list of captured bodies."""
    workers = []
    monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: workers.append(fn))
    return workers


def refresh(cmd, flush, **args):
    """Drive a two-phase refresh to completion: request, gather inline,
    flush the main-thread apply, execute the recorded write command."""
    cmd.run(None, **args)
    flush()
    return run_write_commands(cmd.view)


class TestStatusBarUpdater(object):

    def test_clean_repo(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name

    def test_dirty_worktree(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main* in %s' % tmp_repo.name

    def test_staged_change(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('b.txt', 'b\n')
        tmp_repo.git('add', 'b.txt')
        assert status_bar(tmp_repo, sublime.View()) == 'On main* in %s' % tmp_repo.name

    def test_untracked_files_do_not_count_as_dirty(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('untracked.txt', 'x\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name

    def test_detached_head_clears_status(self, settings, tmp_repo):
        """Detached HEAD has no message; any previous text is erased rather
        than left behind (the pre-rewrite updater left it stale)."""
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '--detach')
        view = sublime.View()
        view.set_status('git-status', 'On main in old')
        assert status_bar(tmp_repo, view) == ''
        assert 'git-status' not in view._status

    def test_simple_kind_only_shows_branch(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        assert status_bar(tmp_repo, sublime.View(), kind='simple') == 'On main'

    def test_other_branch_name(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '-b', 'feature/thing')
        assert status_bar(tmp_repo, sublime.View()) == 'On feature/thing in %s' % tmp_repo.name

    def test_unpushed_commits(self, settings, tmp_repo, tmp_path):
        bare = str(tmp_path / 'remote.git')
        tmp_repo.git('init', '-q', '--bare', bare)
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', bare)
        tmp_repo.git('push', '-q', '-u', 'origin', 'main')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name

        tmp_repo.commit('b.txt', 'b\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s with unpushed' % tmp_repo.name

        tmp_repo.write('b.txt', 'bb\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main* in %s with unpushed' % tmp_repo.name

    def _with_upstream(self, tmp_repo, tmp_path):
        bare = str(tmp_path / 'remote.git')
        tmp_repo.git('init', '-q', '--bare', bare)
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', bare)
        tmp_repo.git('push', '-q', '-u', 'origin', 'main')
        return bare

    def test_ahead_with_identical_tree_is_unpushed(self, settings, tmp_repo, tmp_path):
        """Two unpushed commits whose net effect on the tree is nothing.

        "Unpushed" means HEAD has commits the upstream lacks (``# branch.ab
        +N``), regardless of whether the trees differ. The old
        ``git diff @{upstream}..`` check compared trees and missed this.
        """
        self._with_upstream(tmp_repo, tmp_path)
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.git('rm', '-q', 'b.txt')
        tmp_repo.git('commit', '-q', '-m', 'revert b')
        assert tmp_repo.git('rev-list', '--count', '@{upstream}..HEAD') == '2'
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s with unpushed' % tmp_repo.name

    def test_behind_upstream_is_not_unpushed(self, settings, tmp_repo, tmp_path):
        """Purely *behind* the upstream: nothing to push, so no suffix.

        The tree differs from the upstream tree, which fooled the old
        ``git diff @{upstream}..`` check into saying "with unpushed". The
        ahead-count from ``# branch.ab`` is 0 here.
        """
        self._with_upstream(tmp_repo, tmp_path)
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.git('push', '-q', 'origin', 'main')
        tmp_repo.git('reset', '-q', '--hard', 'HEAD~1')
        assert tmp_repo.git('rev-list', '--count', '@{upstream}..HEAD') == '0'
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name

    def test_ahead_and_behind_is_unpushed(self, settings, tmp_repo, tmp_path):
        self._with_upstream(tmp_repo, tmp_path)
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.git('push', '-q', 'origin', 'main')
        tmp_repo.git('reset', '-q', '--hard', 'HEAD~1')
        tmp_repo.commit('c.txt', 'c\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s with unpushed' % tmp_repo.name

    def test_no_upstream_configured_is_never_unpushed(self, settings, tmp_repo):
        """No ``# branch.ab`` line without an upstream, so ahead is 0."""
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', '/nowhere/at/all.git')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name

    def test_unknown_kind_still_builds_fancy_message(self, settings, tmp_repo):
        # GitStatusBarEventListener filters on the setting; the updater itself
        # treats anything that is not 'simple' as fancy.
        tmp_repo.commit('a.txt', 'a\n')
        assert status_bar(tmp_repo, sublime.View(), kind='whatever') == 'On main in %s' % tmp_repo.name

    def test_repo_without_commits(self, settings, tmp_repo):
        """Unborn branch (``# branch.oid (initial)``) reports its real state.

        Previously this always showed ``main*`` because ``diff-index HEAD``
        failed without a HEAD to compare against. Now an empty unborn repo is
        clean, and staging a file makes it dirty.
        """
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name
        tmp_repo.write('a.txt', 'a\n')
        assert status_bar(tmp_repo, sublime.View()) == 'On main in %s' % tmp_repo.name
        tmp_repo.git('add', 'a.txt')
        assert status_bar(tmp_repo, sublime.View()) == 'On main* in %s' % tmp_repo.name

    def test_unmerged_paths_count_as_dirty(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'base\n')
        tmp_repo.git('checkout', '-q', '-b', 'other')
        tmp_repo.commit('a.txt', 'theirs\n')
        tmp_repo.git('checkout', '-q', 'main')
        tmp_repo.commit('a.txt', 'ours\n')
        tmp_repo.git('merge', 'other', check=False)  # conflicts on a.txt
        assert 'UU' in tmp_repo.git('status', '--porcelain')
        assert status_bar(tmp_repo, sublime.View()) == 'On main* in %s' % tmp_repo.name

    def test_git_failure_clears_status(self, settings, tmp_path):
        """A non-zero exit (here: not a repository) has no message, so any
        previous status text is erased instead of lingering."""
        view = sublime.View()
        view.set_status('git-status', 'On main in old')
        updater = GitStatusBarUpdater([GIT], 'utf-8', [], str(tmp_path), 'fancy', view)
        updater.run()
        sublime.flush_timeouts()
        assert 'git-status' not in view._status

    def test_single_git_process(self, settings, tmp_repo, monkeypatch):
        commands = []
        original = GitStatusBarUpdater.git

        def recording_git(self, cmd, *args, **kwargs):
            commands.append(list(cmd))
            return original(self, cmd, *args, **kwargs)

        monkeypatch.setattr(GitStatusBarUpdater, 'git', recording_git)
        tmp_repo.commit('a.txt', 'a\n')
        status_bar(tmp_repo, sublime.View())
        assert commands == [['status', '--porcelain=v2', '--branch', '--untracked-files=no']]

    def test_status_is_delivered_through_set_timeout(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        view = sublime.View()
        updater = GitStatusBarUpdater([GIT], 'utf-8', [], tmp_repo.path, 'fancy', view)
        updater.run()
        assert view.get_status('git-status') == ''
        assert len(sublime.pending_timeouts()) == 1
        sublime.flush_timeouts()
        assert view.get_status('git-status') == 'On main in %s' % tmp_repo.name


PORCELAIN_HEADER = "# branch.oid 1111111111111111111111111111111111111111\n# branch.head main\n"


class TestParsePorcelainV2(object):
    """Table tests for the pure porcelain v2 parser (no git needed)."""

    @pytest.mark.parametrize('text, expected', [
        # clean, no upstream
        (PORCELAIN_HEADER,
         dict(branch='main', oid='1' * 40, upstream=None, ahead=0, behind=0,
              staged=False, unstaged=False, unmerged=False)),
        # staged only
        (PORCELAIN_HEADER + "1 A. N... 000000 100644 100644 0000000 1111111 b.txt\n",
         dict(branch='main', staged=True, unstaged=False, unmerged=False)),
        # unstaged only
        (PORCELAIN_HEADER + "1 .M N... 100644 100644 100644 1111111 1111111 a.txt\n",
         dict(branch='main', staged=False, unstaged=True, unmerged=False)),
        # both in one entry
        (PORCELAIN_HEADER + "1 MM N... 100644 100644 100644 1111111 2222222 a.txt\n",
         dict(staged=True, unstaged=True)),
        # both in separate entries
        (PORCELAIN_HEADER
         + "1 M. N... 100644 100644 100644 1111111 2222222 a.txt\n"
         + "1 .D N... 100644 100644 000000 1111111 1111111 c.txt\n",
         dict(staged=True, unstaged=True)),
        # rename entry (staged) with a tab separated original path
        (PORCELAIN_HEADER + "2 R. N... 100644 100644 100644 1111111 1111111 R100 new.txt\told.txt\n",
         dict(staged=True, unstaged=False)),
        # unmerged entry
        (PORCELAIN_HEADER + "u UU N... 100644 100644 100644 100644 1111111 2222222 3333333 a.txt\n",
         dict(staged=False, unstaged=False, unmerged=True)),
        # untracked and ignored entries never count
        (PORCELAIN_HEADER + "? loose.txt\n! build/\n",
         dict(staged=False, unstaged=False, unmerged=False)),
        # detached head
        ("# branch.oid 1111111111111111111111111111111111111111\n# branch.head (detached)\n",
         dict(branch=None, oid='1' * 40)),
        # unborn branch
        ("# branch.oid (initial)\n# branch.head main\n",
         dict(branch='main', oid=None, staged=False)),
        # ahead / behind
        (PORCELAIN_HEADER + "# branch.upstream origin/main\n# branch.ab +2 -1\n",
         dict(upstream='origin/main', ahead=2, behind=1)),
        # upstream configured but in sync
        (PORCELAIN_HEADER + "# branch.upstream origin/main\n# branch.ab +0 -0\n",
         dict(upstream='origin/main', ahead=0, behind=0)),
        # upstream configured but gone (no branch.ab line)
        (PORCELAIN_HEADER + "# branch.upstream origin/main\n",
         dict(upstream='origin/main', ahead=0, behind=0)),
        # branch name containing spaces-free slashes
        ("# branch.oid (initial)\n# branch.head feature/x-y\n", dict(branch='feature/x-y')),
        # empty output (e.g. git failed silently)
        ("", dict(branch=None, oid=None, ahead=0, staged=False, unstaged=False, unmerged=False)),
    ])
    def test_parse(self, text, expected):
        info = parse_porcelain_v2(text)
        for key, value in expected.items():
            assert info[key] == value, key

    def test_windows_line_endings(self):
        info = parse_porcelain_v2(PORCELAIN_HEADER.replace('\n', '\r\n')
                                  + "1 .M N... 100644 100644 100644 1111111 1111111 a.txt\r\n")
        assert info['branch'] == 'main'
        assert info['unstaged'] is True


class TestFormatStatusBarMessage(object):

    def test_fancy(self):
        info = parse_porcelain_v2(PORCELAIN_HEADER)
        assert format_status_bar_message(info, 'fancy', '/x/repo') == 'On main in repo'

    def test_fancy_dirty_and_unpushed(self):
        info = parse_porcelain_v2(PORCELAIN_HEADER + "# branch.ab +1 -3\n"
                                  + "1 .M N... 100644 100644 100644 1111111 1111111 a.txt\n")
        assert format_status_bar_message(info, 'fancy', '/x/repo') == 'On main* in repo with unpushed'

    def test_unmerged_is_dirty(self):
        info = parse_porcelain_v2(PORCELAIN_HEADER
                                  + "u UU N... 100644 100644 100644 100644 1111111 2222222 3333333 a.txt\n")
        assert format_status_bar_message(info, 'fancy', '/x/repo') == 'On main* in repo'

    def test_behind_only_is_not_unpushed(self):
        info = parse_porcelain_v2(PORCELAIN_HEADER + "# branch.ab +0 -3\n")
        assert format_status_bar_message(info, 'fancy', '/x/repo') == 'On main in repo'

    def test_simple_ignores_everything_but_the_branch(self):
        info = parse_porcelain_v2(PORCELAIN_HEADER + "# branch.ab +1 -0\n"
                                  + "1 MM N... 100644 100644 100644 1111111 2222222 a.txt\n")
        assert format_status_bar_message(info, 'simple', '/x/repo') == 'On main'

    def test_detached_is_none(self):
        info = parse_porcelain_v2("# branch.oid 1111111\n# branch.head (detached)\n")
        assert format_status_bar_message(info, 'fancy', '/x/repo') is None
        assert format_status_bar_message(info, 'simple', '/x/repo') is None


class TestStatusBarCacheAndDebounce(object):
    """Cache hits, coalescing of concurrent requests and stale-result discard.

    ``GitStatusBarUpdater.start`` is patched to a no-op so threads never
    actually run; tests drive ``updater.run()`` by hand at the moment they
    want the result to land, then ``flush()`` the main-thread callbacks.
    """

    @pytest.fixture
    def spawned(self, monkeypatch):
        updaters = []
        monkeypatch.setattr(GitStatusBarUpdater, 'start', lambda self: updaters.append(self))
        return updaters

    def request(self, tmp_repo, view, kind='fancy'):
        return request_status_bar_update([GIT], 'utf-8', [], tmp_repo.path, kind, view)

    def test_cache_hit_within_ttl_applies_without_spawning(self, settings, tmp_repo, spawned, flush):
        tmp_repo.commit('a.txt', 'a\n')
        first = sublime.View()
        updater = self.request(tmp_repo, first)
        assert spawned == [updater]
        updater.run()
        flush()
        assert first.get_status('git-status') == 'On main in %s' % tmp_repo.name

        second = sublime.View()
        assert self.request(tmp_repo, second) is None
        assert spawned == [updater]  # nothing new
        assert second.get_status('git-status') == ''  # still goes through set_timeout
        flush()
        assert second.get_status('git-status') == 'On main in %s' % tmp_repo.name

    def test_cache_expires_after_ttl(self, settings, tmp_repo, spawned, flush, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        updater = self.request(tmp_repo, sublime.View())
        updater.run()
        flush()

        monkeypatch.setattr(sgit.status, 'STATUS_BAR_CACHE_TTL', 0.0)
        view = sublime.View()
        second = self.request(tmp_repo, view)
        assert second is not None and spawned == [updater, second]

    def test_cache_is_per_kind(self, settings, tmp_repo, spawned, flush):
        tmp_repo.commit('a.txt', 'a\n')
        updater = self.request(tmp_repo, sublime.View(), kind='fancy')
        updater.run()
        flush()
        view = sublime.View()
        second = self.request(tmp_repo, view, kind='simple')
        assert second is not None
        second.run()
        flush()
        assert view.get_status('git-status') == 'On main'

    def test_cached_no_status_result_does_not_respawn(self, settings, tmp_repo, spawned, flush):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '--detach')
        updater = self.request(tmp_repo, sublime.View())
        updater.run()
        flush()
        view = sublime.View()
        assert self.request(tmp_repo, view) is None
        assert len(spawned) == 1
        flush()
        assert 'git-status' not in view._status

    def test_concurrent_request_for_same_repo_joins_the_running_updater(self, settings, tmp_repo, spawned, flush):
        tmp_repo.commit('a.txt', 'a\n')
        first, second = sublime.View(), sublime.View()
        updater = self.request(tmp_repo, first)
        assert self.request(tmp_repo, second) is None
        assert self.request(tmp_repo, second) is None  # same view twice is fine
        assert spawned == [updater]

        updater.run()
        flush()
        expected = 'On main in %s' % tmp_repo.name
        assert first.get_status('git-status') == expected
        assert second.get_status('git-status') == expected

    def test_different_repos_run_independently(self, settings, tmp_repo, tmp_path, spawned):
        tmp_repo.commit('a.txt', 'a\n')
        other_path = os.path.realpath(str(tmp_path / 'other'))
        os.makedirs(other_path)
        other = type(tmp_repo)(other_path)
        other.git('init', '-q', '-b', 'main')
        a = self.request(tmp_repo, sublime.View())
        b = self.request(other, sublime.View())
        assert a is not None and b is not None and a is not b

    def test_invalidation_bumps_token_and_drops_cache(self, settings, tmp_repo, spawned, flush):
        tmp_repo.commit('a.txt', 'a\n')
        updater = self.request(tmp_repo, sublime.View())
        updater.run()
        flush()
        invalidate_status_bar_cache(tmp_repo.path)
        second = self.request(tmp_repo, sublime.View())
        assert second is not None
        assert second.token == updater.token + 1

    def test_stale_result_is_discarded_and_rerun(self, settings, tmp_repo, spawned, flush, monkeypatch):
        """A result computed before an invalidation must never be shown."""
        tmp_repo.commit('a.txt', 'a\n')
        view = sublime.View()
        updater = self.request(tmp_repo, view)
        monkeypatch.setattr(updater, 'compute', lambda: 'STALE')

        invalidate_status_bar_cache(tmp_repo.path)  # e.g. a save mid-run
        waiting = sublime.View()
        assert self.request(tmp_repo, waiting) is None  # coalesced onto the running one

        updater.run()
        flush()
        assert view.get_status('git-status') == ''
        assert waiting.get_status('git-status') == ''

        # ... and a fresh updater was spawned for everyone who was waiting
        assert len(spawned) == 2
        fresh = spawned[-1]
        assert fresh is not updater
        fresh.run()
        flush()
        expected = 'On main in %s' % tmp_repo.name
        assert view.get_status('git-status') == expected
        assert waiting.get_status('git-status') == expected
        assert len(spawned) == 2

    def test_updater_that_raises_releases_the_repo(self, settings, tmp_repo, spawned, flush, monkeypatch):
        """An exception in compute() must not wedge the repo forever.

        If ``running[repo]`` were left behind, every later request would be
        parked in ``pending`` and never applied: a permanent outage.
        """
        tmp_repo.commit('a.txt', 'a\n')
        view = sublime.View()
        updater = self.request(tmp_repo, view)

        def boom():
            raise RuntimeError('git blew up')

        monkeypatch.setattr(updater, 'compute', boom)

        waiting = sublime.View()
        assert self.request(tmp_repo, waiting) is None  # coalesced onto it

        updater.run()  # must not raise
        flush()
        assert 'git-status' not in view._status
        assert 'git-status' not in waiting._status
        assert tmp_repo.path not in sgit.status._state.running
        assert tmp_repo.path not in sgit.status._state.pending
        assert tmp_repo.path not in sgit.status._state.cache

        # a later request for the same repo still spawns and works
        fresh = self.request(tmp_repo, view)
        assert fresh is not None and fresh is not updater
        fresh.run()
        flush()
        assert view.get_status('git-status') == 'On main in %s' % tmp_repo.name

    def test_failure_to_start_releases_the_repo(self, settings, tmp_repo, monkeypatch, flush):
        """Same guarantee when the thread cannot even be started."""
        tmp_repo.commit('a.txt', 'a\n')

        def no_start(self):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(GitStatusBarUpdater, 'start', no_start)
        assert self.request(tmp_repo, sublime.View()) is None
        assert tmp_repo.path not in sgit.status._state.running

        monkeypatch.undo()
        view = sublime.View()
        updater = self.request(tmp_repo, view)
        assert updater is not None
        updater.join(5)
        flush()
        assert view.get_status('git-status') == 'On main in %s' % tmp_repo.name

    def test_reset_forgets_everything(self, settings, tmp_repo, spawned, flush):
        tmp_repo.commit('a.txt', 'a\n')
        updater = self.request(tmp_repo, sublime.View())
        updater.run()
        flush()
        reset_status_bar_state()
        assert self.request(tmp_repo, sublime.View()) is not None

    def test_run_actually_uses_a_thread(self, settings, tmp_repo, flush, monkeypatch):
        """The unpatched path: git runs on the updater thread, set_status via set_timeout."""
        import threading
        tmp_repo.commit('a.txt', 'a\n')
        view = sublime.View()
        seen = {}
        original = GitStatusBarUpdater.compute

        def compute(self):
            seen['thread'] = threading.current_thread()
            return original(self)

        monkeypatch.setattr(GitStatusBarUpdater, 'compute', compute)
        updater = self.request(tmp_repo, view)
        updater.join(5)
        assert not updater.is_alive()
        assert seen['thread'] is updater
        assert view.get_status('git-status') == ''
        flush()
        assert view.get_status('git-status') == 'On main in %s' % tmp_repo.name


class TestStatusBuilder(object):

    @pytest.fixture(autouse=True)
    def _no_help(self, settings):
        settings.set('git_show_status_help', False)

    def test_clean_repo_with_commit(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', message='first commit')
        short = tmp_repo.git('rev-parse', '--short', 'HEAD')
        status = GitStatusBuilder().build_status(tmp_repo.path)
        assert status == (
            'Local:    main %s\n' % tmp_repo.path +
            # NOTE: the `git log` stdout is not stripped, so the Head line carries
            # its own newline and is followed by an extra blank line.
            'Head:     %s first commit\n' % short +
            '\n'
            '\n' +
            GIT_WORKING_DIR_CLEAN + '\n'
        )

    def test_nothing_committed_yet(self, tmp_repo):
        tmp_repo.write('new.txt', 'x\n')
        status = GitStatusBuilder().build_status(tmp_repo.path)
        assert status == (
            'Local:    main %s\n' % tmp_repo.path +
            'Head:     nothing committed (yet)\n'
            '\n'
            'Untracked files:\n'
            '\tnew.txt\n'
            '\n'
        )

    def test_detached_head_shows_no_branch(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '--detach')
        status = GitStatusBuilder().build_status(tmp_repo.path)
        assert status.startswith('Local:    (no branch) %s\n' % tmp_repo.path)

    def test_remote_line_when_branch_has_upstream(self, tmp_repo, tmp_path):
        bare = str(tmp_path / 'remote.git')
        tmp_repo.git('init', '-q', '--bare', bare)
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', bare)
        tmp_repo.git('push', '-q', '-u', 'origin', 'main')
        status = GitStatusBuilder().build_status(tmp_repo.path)
        assert status.startswith('Remote:   origin @ %s\nLocal:    main %s\n' % (bare, tmp_repo.path))

    def test_help_appended_when_enabled(self, settings, tmp_repo):
        settings.set('git_show_status_help', True)
        tmp_repo.commit('a.txt', 'a\n')
        assert GitStatusBuilder().build_status(tmp_repo.path).endswith(GIT_STATUS_HELP)

    def test_files_status_sections(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.commit('c.txt', 'c\n')
        tmp_repo.write('a.txt', 'changed\n')            # unstaged modified
        tmp_repo.write('b.txt', 'changed\n')            # staged modified
        tmp_repo.git('add', 'b.txt')
        tmp_repo.git('rm', '-q', 'c.txt')               # staged delete
        tmp_repo.write('d.txt', 'd\n')                   # staged add
        tmp_repo.git('add', 'd.txt')
        tmp_repo.write('u.txt', 'u\n')                   # untracked

        assert GitStatusBuilder().build_files_status(tmp_repo.path) == (
            'Untracked files:\n'
            '\tu.txt\n'
            '\n'
            'Unstaged changes:\n'
            '\tModified   a.txt\n'
            '\n'
            'Staged changes:\n'
            '\tModified   b.txt\n'
            '\tDeleted    c.txt\n'
            '\tAdded      d.txt\n'
            '\n'
        )

    def test_unstaged_only_uses_changes_header(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        assert GitStatusBuilder().build_files_status(tmp_repo.path) == (
            'Changes:\n'
            '\tModified   a.txt\n'
            '\n'
        )

    def test_rename_shown_in_staged(self, tmp_repo):
        tmp_repo.commit('old.txt', 'some content that is long enough\n')
        tmp_repo.git('mv', 'old.txt', 'new.txt')
        assert GitStatusBuilder().build_files_status(tmp_repo.path) == (
            'Staged changes:\n'
            '\tRenamed    old.txt -> new.txt\n'
            '\n'
        )

    def test_stashes_section(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', message='base')
        tmp_repo.write('a.txt', 'changed\n')
        tmp_repo.git('stash', 'push', '-q', '-m', 'my stash')
        short = tmp_repo.git('rev-parse', '--short', 'HEAD')
        assert GitStatusBuilder().build_stashes(tmp_repo.path) == (
            'Stashes:\n'
            '\t0: On main: my stash\n'
            '\n'
        )
        full = GitStatusBuilder().build_status(tmp_repo.path)
        assert full == (
            'Local:    main %s\n' % tmp_repo.path +
            'Head:     %s base\n' % short +
            '\n'
            '\n'
            'Stashes:\n'
            '\t0: On main: my stash\n'
            '\n' +
            GIT_WORKING_DIR_CLEAN + '\n'
        )

    def test_build_status_spawns_four_git_processes(self, tmp_repo, tmp_path, monkeypatch):
        """One status, one config, one log, one stash list -- nothing else."""
        bare = str(tmp_path / 'remote.git')
        tmp_repo.git('init', '-q', '--bare', bare)
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', bare)
        tmp_repo.git('push', '-q', '-u', 'origin', 'main')
        tmp_repo.write('a.txt', 'changed\n')
        tmp_repo.write('u.txt', 'u\n')

        commands = []
        original = sgit.cmd.subprocess.Popen

        def recording_popen(args, *a, **kwargs):
            commands.append(list(args))
            return original(args, *a, **kwargs)

        monkeypatch.setattr(sgit.cmd.subprocess, 'Popen', recording_popen)
        status = GitStatusBuilder().build_status(tmp_repo.path)

        subcommands = [c[1 + len(GitCmd.opts)] for c in commands]
        assert subcommands == ['status', 'config', 'log', 'stash']
        assert len(commands) == 4
        assert status.startswith('Remote:   origin @ %s\nLocal:    main %s\n' % (bare, tmp_repo.path))

    def test_branch_and_status_from_one_call(self, tmp_repo, tmp_path):
        bare = str(tmp_path / 'remote.git')
        tmp_repo.git('init', '-q', '--bare', bare)
        tmp_repo.commit('old.txt', 'some content that is long enough\n')
        tmp_repo.git('remote', 'add', 'origin', bare)
        tmp_repo.git('push', '-q', '-u', 'origin', 'main')
        tmp_repo.git('mv', 'old.txt', 'new.txt')
        assert RealGit().get_branch_and_status(tmp_repo.path) == (
            'main', 'origin/main', ['R  old.txt -> new.txt'])

    def test_branch_and_status_detached_head(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '--detach')
        tmp_repo.write('a.txt', 'changed\n')
        assert RealGit().get_branch_and_status(tmp_repo.path) == (None, None, [' M a.txt'])

    def test_branch_and_status_unborn_branch(self, tmp_repo):
        tmp_repo.write('new.txt', 'x\n')
        assert RealGit().get_branch_and_status(tmp_repo.path) == (
            'main', None, ['?? new.txt'])

    def test_no_stashes_is_empty_string(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        assert GitStatusBuilder().build_stashes(tmp_repo.path) == ''


class TestHelpersAgainstRealGit(object):

    def test_porcelain_status_and_files_status(self, settings, tmp_repo):
        tmp_repo.commit('old.txt', 'some content that is long enough\n')
        tmp_repo.commit('mod.txt', 'm\n')
        tmp_repo.git('mv', 'old.txt', 'new.txt')
        tmp_repo.write('mod.txt', 'mm\n')
        tmp_repo.write('sub/untracked.txt', 'x\n')
        g = RealGit()
        assert g.get_porcelain_status(tmp_repo.path) == [
            ' M mod.txt', 'R  old.txt -> new.txt', '?? sub/untracked.txt']
        assert g.get_files_status(tmp_repo.path) == (
            [('?', 'sub/untracked.txt')],
            [('M', 'mod.txt')],
            [('R', 'old.txt -> new.txt')])

    def test_untracked_mode_none_hides_untracked(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('u.txt', 'x\n')
        settings.set('git_status_untracked_files', 'none')
        assert RealGit().get_porcelain_status(tmp_repo.path) == []

    def test_diff_of_untracked_file_shows_it_as_new(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('sub/new.txt', 'x\ny\n')
        g = RealGit()
        diff = g.get_diff(tmp_repo.path, 'sub/new.txt')
        assert 'diff --git a/sub/new.txt b/sub/new.txt' in diff
        assert 'new file mode' in diff
        assert '+x\n+y\n' in diff
        # the patch stages cleanly, as hunks from the diff view would
        g.git(['apply', '--cached', '-'], stdin=diff, cwd=tmp_repo.path)
        assert g.get_porcelain_status(tmp_repo.path) == ['A  sub/new.txt']
        # the whole-repo diff and the cached diff still leave untracked files out
        tmp_repo.write('other.txt', 'o\n')
        assert g.get_diff(tmp_repo.path) == ''
        assert 'other.txt' not in g.get_diff(tmp_repo.path, 'sub/new.txt', cached=True)

    def test_quick_log(self, settings, tmp_repo):
        sha1 = tmp_repo.commit('a.txt', 'a\n', message='first')
        sha2 = tmp_repo.commit('b.txt', 'b\n', message='second')
        g = RealGit()
        log = g.get_quick_log(tmp_repo.path)
        assert [(l[0], l[1], l[2], l[3]) for l in log] == [
            ('second', sha2, 'Test User', 'test@example.com'),
            ('first', sha1, 'Test User', 'test@example.com')]
        hashes, choices = g.format_quick_log(log)
        assert hashes == [sha2, sha1]
        assert isinstance(choices[0], sublime.QuickPanelItem)
        assert choices[0].trigger == 'second'
        assert choices[0].details[0] == '%s by Test User &lt;test@example.com&gt;' % sha2[:8]
        assert choices[0].details[1].endswith('(%s)' % log[0][4])
        assert g.get_quick_log(tmp_repo.path, path='a.txt') == [log[1]]

    def test_branches_and_stashes(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('branch', 'other')
        tmp_repo.write('a.txt', 'changed\n')
        tmp_repo.git('stash', '-q')
        g = RealGit()
        assert g.get_current_branch(tmp_repo.path) == 'main'
        assert g.get_branches(tmp_repo.path) == [(True, 'main'), (False, 'other')]
        stashes = g.get_stashes(tmp_repo.path)
        assert len(stashes) == 1
        assert stashes[0][0] == '0'
        assert stashes[0][1].startswith('WIP on main: ')

    def _assert_changes(self, repo, expected):
        """get_changes must agree with the two diff --quiet calls it replaces,
        and must do it in a single git process."""
        g = RealGit()
        old = (g.has_staged_changes(repo.path), g.has_unstaged_changes(repo.path))
        assert old == expected

        commands = []
        original = sgit.cmd.subprocess.Popen

        def recording_popen(args, *a, **kwargs):
            commands.append(list(args))
            return original(args, *a, **kwargs)

        try:
            sgit.cmd.subprocess.Popen = recording_popen
            assert g.get_changes(repo.path) == expected
        finally:
            sgit.cmd.subprocess.Popen = original
        assert len(commands) == 1
        assert commands[0][1 + len(GitCmd.opts)] == 'status'
        assert g.has_changes(repo.path) is any(expected)

    def test_get_changes_clean(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('untracked.txt', 'u\n')  # untracked is not a change
        self._assert_changes(tmp_repo, (False, False))

    def test_get_changes_staged_only(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        tmp_repo.git('add', 'a.txt')
        self._assert_changes(tmp_repo, (True, False))

    def test_get_changes_unstaged_only(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        self._assert_changes(tmp_repo, (False, True))

    def test_get_changes_both(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', 'a.txt')
        tmp_repo.write('a.txt', 'and then some more\n')
        self._assert_changes(tmp_repo, (True, True))

    def test_get_changes_staged_rename(self, settings, tmp_repo):
        """The original path is a separate -z record and must not be parsed
        as a status entry of its own."""
        tmp_repo.commit('old.txt', 'some content that is long enough\n')
        tmp_repo.git('mv', 'old.txt', 'new.txt')
        self._assert_changes(tmp_repo, (True, False))

    def test_get_changes_unmerged_conflict(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'base\n')
        tmp_repo.git('checkout', '-q', '-b', 'other')
        tmp_repo.commit('a.txt', 'theirs\n', message='theirs')
        tmp_repo.git('checkout', '-q', 'main')
        tmp_repo.commit('a.txt', 'ours\n', message='ours')
        tmp_repo.git('merge', 'other', check=False)
        assert tmp_repo.git('status', '--porcelain').startswith('UU ')
        # a conflict is reported by both diff --quiet and diff --quiet --cached
        self._assert_changes(tmp_repo, (True, True))

    def test_git_lines_and_exit_code(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        g = RealGit()
        assert g.git_lines(['ls-files'], cwd=tmp_repo.path) == ['a.txt']
        assert g.git_exit_code(['ls-files', 'nope', '--error-unmatch'], cwd=tmp_repo.path) != 0
        assert g.file_in_git(tmp_repo.path, 'a.txt') is True
        assert g.file_in_git(tmp_repo.path, 'nope') is False


class TestParseGoto(object):

    @pytest.mark.parametrize('goto, expected', [
        ('file:1', ('file', 1, None)),
        ('file', ('file', None, None)),
        ('file:foo.txt:unstaged_changes', ('file', 'foo.txt', 'unstaged_changes')),
        ('section:3', ('section', 3, None)),
        ('section:next', ('section', 'next', None)),
        ('item:prev', ('item', 'prev', None)),
        ('stash:0:stashes', ('stash', 0, 'stashes')),
        ('point:42', ('point', 42, None)),
        ('file:x:7', ('file', 'x', 7)),
    ])
    def test_parse_goto(self, goto, expected):
        assert GitStatusMoveCmd().parse_goto(goto) == expected


class TestStatusRefreshCommand(object):
    """Pins what ``git_status_refresh`` puts in the view.

    The git work runs in a worker thread (``inline_threads`` runs it
    synchronously here); the buffer is written by the hidden
    ``git_status_write`` command on the main thread. The resulting buffer
    contents, read-only flag and caret placement are the same as when the
    refresh was synchronous.
    """

    @pytest.fixture(autouse=True)
    def _no_help(self, settings):
        settings.set('git_show_status_help', False)

    def status_view(self, repo, content='stale contents'):
        return sublime.View(settings={'git_view': 'status', 'git_repo': repo.path},
                            content=content)

    def test_replaces_buffer_with_built_status(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n', message='first')
        tmp_repo.write('a.txt', 'changed\n')
        view = self.status_view(tmp_repo)

        refresh(GitStatusRefreshCommand(view), flush, goto='point:0')

        assert view.substr(sublime.Region(0, view.size())) == \
            GitStatusBuilder().build_status(tmp_repo.path)
        assert view.is_read_only()
        assert list(view.sel()) == [sublime.Region(0, 0)]

    def test_default_goto_lands_on_the_clean_line(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)

        refresh(GitStatusRefreshCommand(view), flush)

        text = view.substr(sublime.Region(0, view.size()))
        assert GIT_WORKING_DIR_CLEAN in text
        point = view.sel()[0].begin()
        assert view.substr(view.line(point)) == GIT_WORKING_DIR_CLEAN

    def test_buffer_is_untouched_until_the_apply_phase_runs(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)

        GitStatusRefreshCommand(view).run(None)
        # the gather ran inline, the apply is parked in set_timeout
        assert view.substr(sublime.Region(0, view.size())) == 'stale contents'
        assert view.commands == []

        flush()
        assert [name for name, _ in view.commands] == ['git_status_write']
        assert view.substr(sublime.Region(0, view.size())) == 'stale contents'

        run_write_commands(view)
        assert GIT_WORKING_DIR_CLEAN in view.substr(sublime.Region(0, view.size()))

    def test_does_nothing_for_a_non_status_view(self, settings, tmp_repo, inline_threads, flush):
        view = sublime.View(settings={'git_view': 'diff', 'git_repo': tmp_repo.path},
                            content='untouched')
        assert refresh(GitStatusRefreshCommand(view), flush) == 0
        assert view.substr(sublime.Region(0, view.size())) == 'untouched'

    def test_does_nothing_without_a_repo(self, settings, tmp_path, inline_threads, flush):
        view = sublime.View(settings={'git_view': 'status'}, content='untouched')
        assert refresh(GitStatusRefreshCommand(view), flush) == 0
        assert view.substr(sublime.Region(0, view.size())) == 'untouched'

    def test_viewport_is_restored_for_a_point_goto(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        view.set_viewport_position((0.0, 123.0), False)

        refresh(GitStatusRefreshCommand(view), flush, goto='point:0')

        # move_to_point() alone would have scrolled a row < 10 to the top
        assert view.viewport_position() == (0.0, 123.0)

    def test_viewport_is_not_restored_for_other_gotos(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        view.set_viewport_position((0.0, 123.0), False)

        refresh(GitStatusRefreshCommand(view), flush, goto='file:1')

        assert view.viewport_position() == (0.0, 0.0)

    def test_on_activated_refreshes_at_the_caret(self, settings, tmp_repo):
        view = self.status_view(tmp_repo)
        view.sel().add(sublime.Region(7, 7))
        GitStatusEventListener().on_activated(view)
        assert view.commands == [('git_status_refresh', {'goto': 'point:7'})]

    def test_run_actually_uses_a_thread(self, settings, tmp_repo, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)

        cmd = GitStatusRefreshCommand(view)
        cmd.run(None)
        deadline = time.time() + 10
        while not sublime.pending_timeouts() and time.time() < deadline:
            time.sleep(0.01)
        assert sublime.pending_timeouts(), 'the worker never scheduled the apply phase'

        flush()
        run_write_commands(view)
        assert GIT_WORKING_DIR_CLEAN in view.substr(sublime.Region(0, view.size()))
        assert view.id() not in sgit.status._refresh_state.running


class TestStatusRefreshCoalescing(object):
    """Rapid refreshes of one view must not apply out of order.

    ``deferred_threads`` captures worker bodies; a test runs them by hand at
    the moment it wants the gather to happen, then ``flush()`` runs the
    main-thread apply.
    """

    @pytest.fixture(autouse=True)
    def _no_help(self, settings):
        settings.set('git_show_status_help', False)

    @pytest.fixture
    def gathers(self, monkeypatch):
        calls = []
        original = GitStatusRefreshCommand.gather

        def gather(self, request):
            calls.append(request)
            return original(self, request)
        monkeypatch.setattr(GitStatusRefreshCommand, 'gather', gather)
        return calls

    def status_view(self, repo):
        return sublime.View(settings={'git_view': 'status', 'git_repo': repo.path},
                            content='stale contents')

    def test_second_request_while_running_queues_exactly_one_rerun(self, settings, tmp_repo, deferred_threads, gathers, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        cmd.run(None, goto='file:1')
        cmd.run(None, goto='point:0')
        cmd.run(None, goto='point:1')
        assert len(deferred_threads) == 1  # the others are queued, not spawned

        # the first worker lands: its result is stale, so nothing is written,
        # and one rerun is spawned for the *latest* request
        deferred_threads[0]()
        flush()
        assert view.commands == []
        assert len(deferred_threads) == 2

        deferred_threads[1]()
        flush()
        assert len(deferred_threads) == 2
        assert len(gathers) == 2
        assert [name for name, _ in view.commands] == ['git_status_write']
        assert view.commands[0][1]['goto'] == 'point:1'

        run_write_commands(view)
        assert GIT_WORKING_DIR_CLEAN in view.substr(sublime.Region(0, view.size()))
        assert view.id() not in sgit.status._refresh_state.running

    def test_result_of_a_stale_generation_is_discarded(self, settings, tmp_repo, deferred_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        cmd.run(None)
        generation = sgit.status._refresh_state.generation[view.id()]
        # something moved the generation on while the worker was busy
        cmd.apply(generation - 1, {'repo': tmp_repo.path, 'goto': None, 'viewport': [0.0, 0.0]},
                  'would be stale', True)
        assert view.commands == []

        cmd.apply(generation, {'repo': tmp_repo.path, 'goto': None, 'viewport': [0.0, 0.0]},
                  'fresh', True)
        assert [name for name, _ in view.commands] == ['git_status_write']

    def test_sequential_requests_each_spawn_a_worker(self, settings, tmp_repo, inline_threads, gathers, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        assert refresh(cmd, flush) == 1
        assert refresh(cmd, flush) == 1
        assert len(gathers) == 2

    def test_pre_close_forgets_the_view_and_drops_the_in_flight_result(self, settings, tmp_repo, deferred_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        cmd.run(None)
        cmd.run(None)  # queued rerun
        state = sgit.status._refresh_state
        assert view.id() in state.running and view.id() in state.rerun

        GitStatusEventListener().on_pre_close(view)
        assert view.id() not in state.generation
        assert view.id() not in state.running
        assert view.id() not in state.rerun

        deferred_threads[0]()
        flush()
        assert view.commands == []
        assert len(deferred_threads) == 1  # no rerun for a closed view

    def test_gather_that_raises_releases_the_view(self, settings, tmp_repo, inline_threads, monkeypatch, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        def boom(self, request):
            raise RuntimeError('git exploded')
        monkeypatch.setattr(GitStatusRefreshCommand, 'gather', boom)
        cmd.run(None)
        flush()
        assert view.commands == []
        assert view.id() not in sgit.status._refresh_state.running

        monkeypatch.undo()
        monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())
        assert refresh(cmd, flush) == 1

    def test_queued_rerun_survives_a_failed_gather(self, settings, tmp_repo, inline_threads, monkeypatch, flush):
        """A rerun queued behind a failing worker is a newer, distinct request
        and is still run once the failure lands (it cannot loop: a rerun only
        exists because a new request arrived)."""
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)
        calls = []
        real_gather = GitStatusRefreshCommand.gather

        def flaky(self, request):
            calls.append(request)
            if len(calls) == 1:
                raise RuntimeError('git exploded once')
            return real_gather(self, request)

        pending = []
        monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: pending.append(fn))
        monkeypatch.setattr(GitStatusRefreshCommand, 'gather', flaky)
        cmd.run(None)          # A: in flight
        cmd.run(None)          # B: queued behind A
        pending.pop(0)()       # A gathers and fails
        flush()                # A lands: discarded, B must spawn
        assert len(pending) == 1
        pending.pop(0)()       # B gathers successfully
        flush()
        assert len(calls) == 2
        assert [c[0] for c in view.commands] == ['git_status_write']
        assert view.id() not in sgit.status._refresh_state.running

    def test_failure_to_start_the_thread_releases_the_view(self, settings, tmp_repo, monkeypatch, flush):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        def cannot_start(fn):
            raise RuntimeError("can't start new thread")
        monkeypatch.setattr(sgit.status, 'run_in_thread', cannot_start)
        cmd.run(None)
        assert view.id() not in sgit.status._refresh_state.running

        monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())
        assert refresh(cmd, flush) == 1

    def test_reset_forgets_everything(self, settings, tmp_repo, deferred_threads):
        view = self.status_view(tmp_repo)
        GitStatusRefreshCommand(view).run(None)
        reset_view_refresh_state()
        state = sgit.status._refresh_state
        assert state.generation == {} and state.running == set() and state.rerun == {}

    def test_a_view_closed_mid_flight_is_not_written_to(self, settings, tmp_repo, deferred_threads, flush):
        """The tab can go away between the gather and the apply. Sublime makes
        every call on a closed view a no-op, but do not even try."""
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        cmd.run(None)
        view.close()  # no on_pre_close: the bookkeeping is still there

        deferred_threads[0]()
        flush()
        assert view.commands == []
        assert view.substr(sublime.Region(0, view.size())) == 'stale contents'
        # and the view is released, not wedged as running forever
        assert view.id() not in sgit.status._refresh_state.running

    def test_a_deliver_that_raises_releases_the_view(self, settings, tmp_repo, inline_threads, monkeypatch, flush):
        """An exception in the apply phase must not leave the view marked as
        running, or every later refresh of it would queue behind a worker that
        has already died."""
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        cmd = GitStatusRefreshCommand(view)

        def boom(self, request, result):
            raise RuntimeError('run_command exploded')
        monkeypatch.setattr(GitStatusRefreshCommand, 'deliver', boom)
        cmd.run(None)
        flush()
        assert view.id() not in sgit.status._refresh_state.running

        monkeypatch.undo()
        monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())
        assert refresh(cmd, flush) == 1

    def test_write_command_args_are_json_serializable(self, settings, tmp_repo, inline_threads, flush):
        """Sublime only passes str/int/float/bool/list/dict/None through
        run_command -- a tuple viewport or a Region would not survive."""
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)
        view.set_viewport_position((0.0, 42.0), False)

        GitStatusRefreshCommand(view).run(None, goto='point:0')
        flush()

        name, args = view.commands[0]
        assert name == 'git_status_write'
        assert json.loads(json.dumps(args)) == args
        assert args['viewport'] == [0.0, 42.0]


class TestStatusViewCreation(object):
    """Pins the view the ``git_status`` command opens.

    The view gets the .sublime-syntax via ``assign_syntax``; the *settings* it
    applies and the refresh it triggers are pinned here too.
    """

    def test_creates_configured_scratch_view_and_refreshes(self, settings, tmp_repo):
        window = sublime.Window(folders=[tmp_repo.path], active_view=sublime.View())
        GitStatusCommand(window).run()

        view = window.active_view()
        assert view.name() == GIT_STATUS_VIEW_TITLE_PREFIX + tmp_repo.name
        assert view._scratch is True
        assert view.is_read_only() is True
        assert view.settings().get('git_view') == 'status'
        assert view.settings().get('git_repo') == tmp_repo.path
        for key, val in GIT_STATUS_VIEW_SETTINGS.items():
            assert view.settings().get(key) == val
        assert view.syntax() == GIT_STATUS_VIEW_SYNTAX
        assert view.commands == [('git_status_refresh', None)]

    def test_reuses_an_existing_status_view(self, settings, tmp_repo):
        existing = sublime.View(settings={'git_view': 'status', 'git_repo': tmp_repo.path})
        window = sublime.Window(folders=[tmp_repo.path], views=[existing],
                                active_view=sublime.View())
        GitStatusCommand(window).run()

        assert window.active_view() is existing
        assert len(window.views()) == 1
        assert existing.commands == [('git_status_refresh', None)]

    def test_refresh_only_does_not_open_a_view(self, settings, tmp_repo):
        window = sublime.Window(folders=[tmp_repo.path], active_view=sublime.View())
        GitStatusCommand(window).run(refresh_only=True)
        assert window.views() == []


class TestDiffRefreshCommand(object):
    """Pins what ``git_diff_refresh`` puts in the view.

    Same two-phase shape as the status refresh: ``git diff`` runs in a worker
    (inline here), ``git_diff_write`` writes the buffer on the main thread.
    """

    def diff_view(self, repo, cached=False, content='stale'):
        return sublime.View(settings={'git_view': 'diff-cached' if cached else 'diff',
                                      'git_repo': repo.path,
                                      'git_diff_path': repo.path,
                                      'git_diff_cached': cached},
                            content=content)

    def test_writes_worktree_diff(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        tmp_repo.write('a.txt', 'two\n')
        view = self.diff_view(tmp_repo)

        refresh(GitDiffRefreshCommand(view), flush)

        text = view.substr(sublime.Region(0, view.size()))
        assert text.startswith('diff --git a/a.txt b/a.txt')
        assert '-one' in text and '+two' in text
        assert view.settings().get('git_diff_clean') is False
        assert view.is_read_only()

    def test_clean_worktree_writes_placeholder(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo)

        refresh(GitDiffRefreshCommand(view), flush)

        assert view.substr(sublime.Region(0, view.size())) == GIT_DIFF_CLEAN
        assert view.settings().get('git_diff_clean') is True

    def test_clean_index_writes_cached_placeholder(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo, cached=True)

        refresh(GitDiffRefreshCommand(view), flush, cached=True)

        assert view.substr(sublime.Region(0, view.size())) == GIT_DIFF_CLEAN_CACHED
        assert view.settings().get('git_diff_clean') is True

    def test_unified_setting_is_honoured(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', ''.join('%s\n' % i for i in range(20)))
        tmp_repo.write('a.txt', ''.join('%s\n' % (i if i != 10 else 'x') for i in range(20)))
        view = self.diff_view(tmp_repo)
        view.settings().set('git_diff_unified', 1)

        refresh(GitDiffRefreshCommand(view), flush)

        text = view.substr(sublime.Region(0, view.size()))
        assert '@@ -10,3 +10,3 @@' in text

    def test_buffer_is_untouched_until_the_apply_phase_runs(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo)

        GitDiffRefreshCommand(view).run(None)
        assert view.substr(sublime.Region(0, view.size())) == 'stale'
        flush()
        assert [name for name, _ in view.commands] == ['git_diff_write']
        run_write_commands(view)
        assert view.substr(sublime.Region(0, view.size())) == GIT_DIFF_CLEAN

    def test_run_move_is_forwarded_to_the_write(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        tmp_repo.write('a.txt', 'two\n')
        view = self.diff_view(tmp_repo)

        refresh(GitDiffRefreshCommand(view), flush, run_move=True)

        assert view.commands == [('git_diff_move', None)]

    def test_caret_row_and_column_survive_the_refresh(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        tmp_repo.write('a.txt', 'two\n')
        view = self.diff_view(tmp_repo, content='line0\nline1\nline2\n')
        view.sel().add(sublime.Region(8, 8))  # row 1, col 2

        refresh(GitDiffRefreshCommand(view), flush)

        assert view.rowcol(view.sel()[0].begin()) == (1, 2)

    def test_does_nothing_without_a_diff_path(self, settings, tmp_repo, inline_threads, flush):
        view = sublime.View(settings={'git_view': 'diff', 'git_repo': tmp_repo.path},
                            content='untouched')
        assert refresh(GitDiffRefreshCommand(view), flush) == 0
        assert view.substr(sublime.Region(0, view.size())) == 'untouched'

    def test_second_request_while_running_queues_exactly_one_rerun(self, settings, tmp_repo, deferred_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo)
        cmd = GitDiffRefreshCommand(view)

        cmd.run(None)
        cmd.run(None)
        cmd.run(None)
        assert len(deferred_threads) == 1

        deferred_threads[0]()
        flush()
        assert view.commands == []  # stale, discarded
        assert len(deferred_threads) == 2

        deferred_threads[1]()
        flush()
        assert len(deferred_threads) == 2
        assert [name for name, _ in view.commands] == ['git_diff_write']

    def test_pre_close_forgets_the_view(self, settings, tmp_repo, deferred_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo)
        GitDiffRefreshCommand(view).run(None)
        assert view.id() in sgit.status._refresh_state.running

        GitDiffEventListener().on_pre_close(view)
        assert view.id() not in sgit.status._refresh_state.running

        deferred_threads[0]()
        flush()
        assert view.commands == []

    def test_write_command_args_are_json_serializable(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        tmp_repo.write('a.txt', 'two\n')
        view = self.diff_view(tmp_repo)

        GitDiffRefreshCommand(view).run(None, run_move=True)
        flush()

        name, args = view.commands[0]
        assert name == 'git_diff_write'
        assert json.loads(json.dumps(args)) == args

    def test_a_view_closed_mid_flight_is_not_written_to(self, settings, tmp_repo, deferred_threads, flush):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo)
        GitDiffRefreshCommand(view).run(None)
        view.close()

        deferred_threads[0]()
        flush()
        assert view.commands == []
        assert view.substr(sublime.Region(0, view.size())) == 'stale'
        assert view.id() not in sgit.status._refresh_state.running


class TestQuickStatusCommand(object):
    """Pins the *behaviour* of the quick status panel.

    The rows are plain ``git status --porcelain`` strings (not
    ``QuickPanelItem``s, which are only used for multi-column rows); the
    selected index must keep mapping to the same file and the same follow-up
    commands.
    """

    def panel(self, tmp_repo):
        window = sublime.Window(folders=[tmp_repo.path], active_view=sublime.View())
        GitQuickStatusCommand(window).run()
        return window, window.quick_panel[1]

    def test_clean_repo_shows_the_clean_line_and_selecting_it_is_a_noop(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        window, on_done = self.panel(tmp_repo)
        assert window.quick_panel[0] == [GIT_WORKING_DIR_CLEAN]
        on_done(0)
        on_done(-1)
        assert window.commands == []

    def test_unstaged_file_opens_a_worktree_diff(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        window, on_done = self.panel(tmp_repo)
        on_done(0)
        assert window.commands == [('git_diff', {'repo': tmp_repo.path, 'path': 'a.txt'})]

    def test_partially_staged_file_opens_both_diffs(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', 'a.txt')
        tmp_repo.write('a.txt', 'and unstaged\n')
        window, on_done = self.panel(tmp_repo)
        on_done(0)
        assert window.commands == [
            ('git_diff', {'repo': tmp_repo.path, 'path': 'a.txt'}),
            ('git_diff', {'repo': tmp_repo.path, 'path': 'a.txt', 'cached': True}),
        ]

    def test_untracked_file_opens_a_worktree_diff(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('u.txt', 'u\n')
        window, on_done = self.panel(tmp_repo)
        on_done(0)
        assert window.commands == [('git_diff', {'repo': tmp_repo.path, 'path': 'u.txt'})]
        assert sublime.error_messages == []


class TestStatusBarEventListener(object):
    """Which events spawn an updater, and for which settings.

    Only the ``_async`` variants exist; the ST2-only sync shims
    (``on_activated``/``on_load``/``on_post_save``) were removed.
    """

    @pytest.fixture
    def spawned(self, monkeypatch):
        calls = []

        class FakeUpdater(object):
            def __init__(self, bin, encoding, fallback, repo, kind, view):
                calls.append({'bin': bin, 'encoding': encoding, 'fallback': fallback,
                              'repo': repo, 'kind': kind, 'view': view})

            def start(self):
                calls[-1]['started'] = True

        monkeypatch.setattr('sgit.status.GitStatusBarUpdater', FakeUpdater)
        return calls

    def view_in_repo(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        return sublime.View(file_name=os.path.join(tmp_repo.path, 'a.txt'))

    def test_view_outside_any_repo_clears_status(self, settings, tmp_path, spawned):
        """Switching to a file outside a repo erases the old repo's message."""
        view = sublime.View(file_name=str(tmp_path / 'loose.txt'))
        view.set_status('git-status', 'On main in old')
        GitStatusBarEventListener().on_activated_async(view)
        sublime.flush_timeouts()
        assert spawned == []
        assert 'git-status' not in view._status

    def test_async_events_spawn_an_updater(self, settings, tmp_repo, spawned):
        view = self.view_in_repo(tmp_repo)
        listener = GitStatusBarEventListener()
        for event in (listener.on_activated_async, listener.on_load_async, listener.on_post_save_async):
            reset_status_bar_state()  # nothing running, nothing cached
            event(view)

        assert len(spawned) == 3
        assert all(c['started'] for c in spawned)
        assert spawned[0]['repo'] == tmp_repo.path
        assert spawned[0]['kind'] == 'fancy'
        assert spawned[0]['view'] is view

    def test_burst_of_events_spawns_one_updater(self, settings, tmp_repo, spawned):
        """activate + load for the same repo coalesce while an updater runs."""
        view = self.view_in_repo(tmp_repo)
        listener = GitStatusBarEventListener()
        listener.on_activated_async(view)
        listener.on_load_async(view)
        listener.on_activated_async(sublime.View(file_name=os.path.join(tmp_repo.path, 'a.txt')))
        assert len(spawned) == 1
        assert sgit.status._state.running[tmp_repo.path] is not None
        assert len(sgit.status._state.pending[tmp_repo.path]) == 2

    def test_post_save_invalidates_the_cache(self, settings, tmp_repo, spawned, monkeypatch):
        view = self.view_in_repo(tmp_repo)
        listener = GitStatusBarEventListener()
        listener.on_activated_async(view)
        assert len(spawned) == 1

        # pretend the running updater finished and populated the cache
        reset_status_bar_state()
        sgit.status._state.cache[tmp_repo.path] = (time.monotonic(), 'fancy', 'On main in %s' % tmp_repo.name)
        token_before = sgit.status._state.token(tmp_repo.path)
        listener.on_activated_async(view)
        assert len(spawned) == 1  # served from cache

        listener.on_post_save_async(view)
        assert len(spawned) == 2  # cache dropped, fresh updater
        assert tmp_repo.path not in sgit.status._state.cache
        assert sgit.status._state.token(tmp_repo.path) == token_before + 1

    def test_sync_events_are_gone(self):
        listener = GitStatusBarEventListener()
        for name in ('on_activated', 'on_load', 'on_post_save'):
            assert not hasattr(listener, name)

    def test_simple_setting_is_passed_through(self, settings, tmp_repo, spawned):
        settings.set('git_status_bar', 'simple')
        GitStatusBarEventListener().on_activated_async(self.view_in_repo(tmp_repo))
        assert [c['kind'] for c in spawned] == ['simple']

    def test_disabled_setting_spawns_nothing(self, settings, tmp_repo, spawned):
        settings.set('git_status_bar', 'none')
        GitStatusBarEventListener().on_activated_async(self.view_in_repo(tmp_repo))
        assert spawned == []

    def test_view_outside_a_repo_spawns_nothing(self, settings, tmp_path, spawned):
        view = sublime.View(file_name=os.path.join(str(tmp_path), 'loose.txt'))
        GitStatusBarEventListener().on_activated_async(view)
        assert spawned == []

    def test_encoding_settings_are_forwarded(self, settings, tmp_repo, spawned):
        settings.set('encoding', 'latin-1')
        settings.set('fallback_encodings', ['cp1252'])
        GitStatusBarEventListener().on_activated_async(self.view_in_repo(tmp_repo))
        assert spawned[0]['encoding'] == 'latin-1'
        assert spawned[0]['fallback'] == ['cp1252']


class TestMoveToFile(object):
    """``move_to_file(1)`` falls back to the 'working directory clean' line."""

    def move_cmd(self, content):
        cmd = GitStatusMoveCmd()
        cmd.view = sublime.View(settings={'git_view': 'status'}, content=content)
        return cmd

    def test_moves_to_the_clean_line_when_there_are_no_files(self):
        cmd = self.move_cmd('On branch main\n\n' + GIT_WORKING_DIR_CLEAN + '\n')
        cmd.move_to_file(1)
        point = cmd.view.sel()[0].begin()
        assert cmd.view.substr(cmd.view.line(point)) == GIT_WORKING_DIR_CLEAN

    def test_does_not_move_when_the_clean_line_is_missing(self):
        # view.find() returns Region(-1, -1) when the pattern is not found;
        # moving to it would put the cursor at a negative point.
        cmd = self.move_cmd('On branch main\n\nnothing to see here\n')
        cmd.view.sel().clear()
        cmd.view.sel().add(sublime.Region(3, 3))
        cmd.move_to_file(1)
        assert list(cmd.view.sel()) == [sublime.Region(3, 3)]


class TestUnstageNoCommits(object):
    """``no_commits()`` must ask *the repo*, not whatever the process cwd is."""

    def unstage_cmd(self):
        return GitStatusUnstageCommand(sublime.View())

    def test_unstage_all_in_a_repo_without_commits(self, settings, tmp_repo):
        tmp_repo.write('a.txt', 'a\n')
        tmp_repo.git('add', '--', 'a.txt')

        self.unstage_cmd().unstage_all(tmp_repo.path)

        assert tmp_repo.git('status', '--porcelain') == '?? a.txt'

    def test_unstage_file_in_a_repo_without_commits(self, settings, tmp_repo):
        tmp_repo.write('a.txt', 'a\n')
        tmp_repo.write('b.txt', 'b\n')
        tmp_repo.git('add', '-A')

        self.unstage_cmd().unstage(tmp_repo.path, ['a.txt'])

        assert sorted(tmp_repo.git('status', '--porcelain').split('\n')) == ['?? a.txt', 'A  b.txt']

    def test_unstage_file_in_a_repo_with_commits(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', message='first')
        tmp_repo.write('a.txt', 'changed\n')
        tmp_repo.git('add', '--', 'a.txt')

        self.unstage_cmd().unstage(tmp_repo.path, ['a.txt'])

        # conftest's git() strips, so the leading ' ' of ' M' is gone
        assert tmp_repo.git('status', '--porcelain') == 'M a.txt'


class TestStatusDiscardFiles(object):
    """``GitStatusDiscardCommand.discard_files`` gathers state in bulk."""

    def discard_cmd(self):
        return GitStatusDiscardCommand(sublime.View())

    def record_git(self, monkeypatch):
        commands = []
        original = sgit.cmd.subprocess.Popen

        def recording_popen(args, *a, **kwargs):
            commands.append(list(args))
            return original(args, *a, **kwargs)

        monkeypatch.setattr(sgit.cmd.subprocess, 'Popen', recording_popen)
        return commands

    def subcommands(self, commands):
        """The git arguments of each spawned process, with the global opts stripped."""
        return [c[1 + len(GitCmd.opts):] for c in commands]

    def test_bounded_number_of_diff_processes(self, settings, tmp_repo, monkeypatch):
        files = []
        for i in range(6):
            tmp_repo.commit('mod%d.txt' % i, 'm\n')
            tmp_repo.commit('stg%d.txt' % i, 's\n')
        for i in range(6):
            tmp_repo.write('mod%d.txt' % i, 'changed\n')
            tmp_repo.write('stg%d.txt' % i, 'staged\n')
            tmp_repo.git('add', '--', 'stg%d.txt' % i)
            tmp_repo.write('unt%d.txt' % i, 'u\n')
            files.append((UNSTAGED_CHANGES, 'mod%d.txt' % i))
            files.append((STAGED_CHANGES, 'stg%d.txt' % i))
            files.append((UNTRACKED_FILES, 'unt%d.txt' % i))

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, files)

        diffs = [c for c in self.subcommands(commands) if c[0] == 'diff']
        assert len(diffs) == 2
        assert diffs[0] == ['diff', '--name-status', '--cached', '-z', '--'] + \
            ['stg%d.txt' % i for i in range(6)]
        assert diffs[1] == ['diff', '--name-status', '-z', '--'] + \
            [f for _, f in files if f.startswith(('mod', 'stg'))]
        assert tmp_repo.git('status', '--porcelain') == ''

    def test_modified_file_is_discarded(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [(UNSTAGED_CHANGES, 'a.txt')])

        assert sublime.ok_cancel_dialogs == [
            ("Are you sure you want to perform the following actions?\n\n"
             "  Discard:  a.txt", 'Continue')]
        assert self.subcommands(commands)[1:] == [['checkout', '--', 'a.txt']]
        assert open(os.path.join(tmp_repo.path, 'a.txt')).read() == 'a\n'

    def test_deleted_file_is_resurrected(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        os.unlink(os.path.join(tmp_repo.path, 'a.txt'))

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [(UNSTAGED_CHANGES, 'a.txt')])

        assert sublime.ok_cancel_dialogs == [
            ("Are you sure you want to perform the following actions?\n\n"
             "  Resurrect:  a.txt", 'Continue')]
        assert self.subcommands(commands)[1:] == [
            ['reset', '-q', '--', 'a.txt'],
            ['checkout', '--', 'a.txt']]
        assert open(os.path.join(tmp_repo.path, 'a.txt')).read() == 'a\n'

    def test_staged_and_up_to_date_file_is_discarded(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        tmp_repo.git('add', '--', 'a.txt')

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [(STAGED_CHANGES, 'a.txt')])

        assert sublime.ok_cancel_dialogs == [
            ("Are you sure you want to perform the following actions?\n\n"
             "  Discard:  a.txt", 'Continue')]
        assert self.subcommands(commands)[2:] == [['checkout', 'HEAD', '--', 'a.txt']]
        assert tmp_repo.git('status', '--porcelain') == ''

    def test_staged_but_not_up_to_date_is_an_error(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', '--', 'a.txt')
        tmp_repo.write('a.txt', 'and then modified\n')

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [(STAGED_CHANGES, 'a.txt')])

        assert sublime.error_messages == [
            "You can't discard staged changes to the following files. "
            "Please unstage them first:\n\n  a.txt"]
        assert sublime.ok_cancel_dialogs == []
        # only the two bulk diffs, no actions
        assert len(commands) == 2
        assert open(os.path.join(tmp_repo.path, 'a.txt')).read() == 'and then modified\n'

    def test_untracked_file_is_deleted(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('u.txt', 'u\n')

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [(UNTRACKED_FILES, 'u.txt')])

        assert sublime.ok_cancel_dialogs == [
            ("Are you sure you want to perform the following actions?\n\n"
             "  Delete:  u.txt", 'Continue')]
        # untracked files need neither diff
        assert self.subcommands(commands) == [['clean', '-d', '--force', '--', 'u.txt']]
        assert not os.path.exists(os.path.join(tmp_repo.path, 'u.txt'))

    def test_path_with_a_space(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a file.txt', 'a\n')
        tmp_repo.write('a file.txt', 'changed\n')
        tmp_repo.write('an untracked file.txt', 'u\n')

        self.discard_cmd().discard_files(tmp_repo.path, [
            (UNSTAGED_CHANGES, 'a file.txt'),
            (UNTRACKED_FILES, 'an untracked file.txt')])

        assert sublime.ok_cancel_dialogs == [
            ("Are you sure you want to perform the following actions?\n\n"
             "  Discard:  a file.txt\n"
             "  Delete:  an untracked file.txt", 'Continue')]
        assert tmp_repo.git('status', '--porcelain') == ''
        assert open(os.path.join(tmp_repo.path, 'a file.txt')).read() == 'a\n'

    def test_cancelling_the_confirmation_does_nothing(self, settings, tmp_repo, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        sublime.ok_cancel_answers.append(False)

        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [(UNSTAGED_CHANGES, 'a.txt')])

        assert len(commands) == 1
        assert open(os.path.join(tmp_repo.path, 'a.txt')).read() == 'changed\n'

    def test_empty_file_list_runs_no_git_at_all(self, settings, tmp_repo, monkeypatch):
        commands = self.record_git(monkeypatch)
        self.discard_cmd().discard_files(tmp_repo.path, [])
        assert commands == []
        assert sublime.ok_cancel_dialogs == []


# --- status view scopes ---------------------------------------------------
#
# The stub view has no syntax, so these tests hand it the scope spans that
# ``syntax/SublimeGit Status.sublime-syntax`` would produce: a section block
# (``meta.git-status.<section>``) runs from its header line through the blank
# line that terminates it, and inside it every item line is
# ``meta.git-status.line`` with a ``meta.git-status.file`` (or
# ``meta.git-status.stash.name``) capture.

SECTION_BY_HEADER = {
    'Stashes:': sgit.status.STASHES,
    'Untracked files:': UNTRACKED_FILES,
    # "Changes:" replaces "Unstaged changes:" when nothing is staged; the
    # syntax scopes both as unstaged_changes.
    'Changes:': UNSTAGED_CHANGES,
    'Unstaged changes:': UNSTAGED_CHANGES,
    'Staged changes:': STAGED_CHANGES,
}


def status_scopes(content):
    """Scope spans for a status buffer, mirroring the .sublime-syntax."""
    import re
    scopes = []
    section, section_start, pos = None, 0, 0

    for line in content.splitlines(True):
        text = line.rstrip('\n')
        end = pos + len(line)

        if section is None:
            if text == '# Movement:':
                scopes.append((pos, len(content), 'comment.git-status.help'))
                break
            if text in SECTION_BY_HEADER:
                section = SECTION_BY_HEADER[text]
                section_start = pos
                scopes.append((pos, end, 'constant.other.git-status.header'))
        elif text == '':
            # the blank line terminates the section and belongs to it
            scopes.append((section_start, end, 'meta.git-status.' + section))
            section = None
        elif line.startswith('\t'):
            scopes.append((pos, end, 'meta.git-status.line'))
            if section == sgit.status.STASHES:
                m = re.match(r'\t(.+?): (?:WIP )?[oO]n (.+): (.+)$', text)
                if m:
                    scopes.append((pos + m.start(1), pos + m.end(1),
                                   'meta.git-status.stash.name'))
            elif section == UNTRACKED_FILES:
                scopes.append((pos + 1, pos + len(text), 'meta.git-status.file'))
            else:
                m = re.match(r'\t(\w+) *(.+)$', text)
                if m:
                    scopes.append((pos + m.start(2), pos + m.end(2),
                                   'meta.git-status.file'))
        pos = end

    if section is not None:
        scopes.append((section_start, len(content), 'meta.git-status.' + section))
    return scopes


def scoped_status_view(content):
    view = sublime.View(settings={'git_view': 'status'}, content=content)
    view.set_scopes(status_scopes(content))
    return view


def move_cmd(content):
    cmd = GitStatusMoveCmd()
    cmd.view = scoped_status_view(content)
    return cmd


def caret_line(cmd):
    point = cmd.view.sel()[0].begin()
    return cmd.view.substr(cmd.view.line(point))


FULL_STATUS = (
    "Local:    main ~/repo\n"
    "Head:     abc1234 initial\n"
    "\n"
    "Stashes:\n"
    "\t0: WIP on main: abc1234 initial\n"
    "\n"
    "Untracked files:\n"
    "\tbeta.txt\n"
    "\tdelta.txt\n"
    "\tzeta.txt\n"
    "\n"
    "Unstaged changes:\n"
    "\tModified   a.txt\n"
    "\tDeleted    b.txt\n"
    "\n"
    "Staged changes:\n"
    "\tAdded      c.txt\n"
    "\n"
) + GIT_STATUS_HELP


def brute_force_section_at_point(view, point):
    """The pre-bisect implementation: one score_selector per section."""
    for s in sgit.status.SECTIONS:
        if view.score_selector(point, sgit.status.SECTION_SELECTOR_PREFIX + s) > 0:
            return s
    return None


class TestSectionAtPoint(object):

    def test_agrees_with_scoring_every_point(self):
        cmd = move_cmd(FULL_STATUS)
        mismatches = [p for p in range(cmd.view.size() + 1)
                      if cmd.section_at_point(p) != brute_force_section_at_point(cmd.view, p)]
        assert mismatches == []

    def test_sections_are_found_at_all(self):
        cmd = move_cmd(FULL_STATUS)
        found = set(s for _, _, s in cmd.get_section_regions())
        assert found == set([sgit.status.STASHES, UNTRACKED_FILES,
                             UNSTAGED_CHANGES, STAGED_CHANGES])

    def test_header_and_help_lines_are_outside_any_section(self):
        cmd = move_cmd(FULL_STATUS)
        assert cmd.section_at_point(0) is None
        assert cmd.section_at_point(cmd.view.size() - 1) is None

    def test_changes_pseudo_section_is_reported_as_unstaged(self):
        cmd = move_cmd("Changes:\n\tModified   a.txt\n\n")
        point = cmd.view.text_point(1, 2)
        assert cmd.section_at_point(point) == UNSTAGED_CHANGES
        assert cmd.get_all_files() == [(UNSTAGED_CHANGES, 'a.txt')]

    def test_empty_view_has_no_sections(self):
        cmd = move_cmd('')
        assert cmd.get_section_regions() == []
        assert cmd.section_at_point(0) is None

    def test_cache_is_invalidated_when_the_buffer_changes(self):
        cmd = move_cmd(FULL_STATUS)
        assert cmd.section_at_point(cmd.view.text_point(7, 2)) == UNTRACKED_FILES

        content = "Staged changes:\n\tAdded      c.txt\n\n"
        cmd.view.replace(None, sublime.Region(0, cmd.view.size()), content)
        cmd.view.set_scopes(status_scopes(content))

        assert cmd.section_at_point(cmd.view.text_point(1, 2)) == STAGED_CHANGES


class TestMoveToFileInSection(object):

    def test_lands_on_the_next_file_by_name(self):
        cmd = move_cmd(FULL_STATUS)
        cmd.move_to_file('delta.txt', UNTRACKED_FILES)
        assert caret_line(cmd) == '\tdelta.txt'

    def test_lands_on_the_next_name_when_the_file_is_gone(self):
        cmd = move_cmd(FULL_STATUS)
        cmd.move_to_file('cee.txt', UNTRACKED_FILES)
        assert caret_line(cmd) == '\tdelta.txt'

    def test_lands_on_the_last_file_when_past_the_end(self):
        cmd = move_cmd(FULL_STATUS)
        cmd.move_to_file('zzz.txt', UNTRACKED_FILES)
        assert caret_line(cmd) == '\tzeta.txt'

    def test_only_looks_in_the_given_section(self):
        cmd = move_cmd(FULL_STATUS)
        cmd.move_to_file('a.txt', STAGED_CHANGES)
        assert caret_line(cmd) == '\tAdded      c.txt'

    def test_falls_back_to_the_previous_section_when_the_section_is_gone(self):
        # the untracked section was emptied by staging its last file
        content = FULL_STATUS.replace("Untracked files:\n\tbeta.txt\n\tdelta.txt\n\tzeta.txt\n\n", "")
        cmd = move_cmd(content)
        cmd.move_to_file('beta.txt', UNTRACKED_FILES)
        # nothing before untracked_files holds files, so: first file
        assert caret_line(cmd) == '\tModified   a.txt'

    def test_falls_back_to_the_last_file_of_an_earlier_section(self):
        content = FULL_STATUS.replace("Staged changes:\n\tAdded      c.txt\n\n", "")
        cmd = move_cmd(content)
        cmd.move_to_file('c.txt', STAGED_CHANGES)
        assert caret_line(cmd) == '\tDeleted    b.txt'

    def test_renamed_file_selection_reports_both_names(self):
        content = ("Staged changes:\n"
                   "\tRenamed    old.txt -> new.txt\n"
                   "\n")
        cmd = move_cmd(content)
        cmd.view.sel().clear()
        cmd.view.sel().add(sublime.Region(cmd.view.text_point(1, 2)))
        assert cmd.get_selected_files() == [(STAGED_CHANGES, 'old.txt'),
                                            (STAGED_CHANGES, 'new.txt')]

    def test_move_to_section_by_name(self):
        cmd = move_cmd(FULL_STATUS)
        cmd.move_to_section(UNSTAGED_CHANGES)
        assert caret_line(cmd) == 'Unstaged changes:'


class TestStatusNavigationApiCalls(object):
    """Navigation must not scale its Sublime API calls with the file count."""

    def big_status(self, count=500):
        files = ''.join('\t%04d.txt\n' % i for i in range(count))
        return ("Local:    main ~/repo\n"
                "\n"
                "Untracked files:\n" + files + "\n") + GIT_STATUS_HELP

    def test_move_to_file_is_bounded(self):
        cmd = move_cmd(self.big_status())
        cmd.view.api_calls.clear()

        cmd.move_to_file('0250.txt', UNTRACKED_FILES)

        assert caret_line(cmd) == '\t0250.txt'
        assert sum(cmd.view.api_calls.values()) < 20

    def test_logical_goto_next_file_is_bounded(self):
        cmd = GitStatusStageCommand(scoped_status_view(self.big_status()))
        cmd.view.sel().clear()
        cmd.view.sel().add(sublime.Region(cmd.view.text_point(5, 2)))
        cmd.view.api_calls.clear()

        goto = cmd.logical_goto_next_file()

        assert goto == 'file:0002.txt:%s' % UNTRACKED_FILES
        assert sum(cmd.view.api_calls.values()) < 20

    def test_get_all_files_is_bounded(self):
        cmd = move_cmd(self.big_status())
        cmd.view.api_calls.clear()

        files = cmd.get_all_files()

        assert len(files) == 500
        assert files[0] == (UNTRACKED_FILES, '0000.txt')
        assert files[-1] == (UNTRACKED_FILES, '0499.txt')
        assert sum(cmd.view.api_calls.values()) < 20
