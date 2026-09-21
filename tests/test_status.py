# coding: utf-8
"""Behavioural tests for sgit/status.py against a real temporary git repo."""
import os
import time

import pytest
import sublime

from conftest import requires_git, GIT
import sgit.status
from sgit.status import (GitStatusBarUpdater, GitStatusBuilder, GitStatusCommand,
                         GitQuickStatusCommand, GitStatusBarEventListener,
                         GitStatusMoveCmd, GitStatusRefreshCommand, GitStatusUnstageCommand,
                         GIT_STATUS_HELP, GIT_STATUS_VIEW_SETTINGS, GIT_STATUS_VIEW_SYNTAX,
                         GIT_STATUS_VIEW_TITLE_PREFIX, GIT_WORKING_DIR_CLEAN,
                         parse_porcelain_v2, format_status_bar_message,
                         request_status_bar_update, invalidate_status_bar_cache,
                         reset_status_bar_state)
from sgit.diff import GitDiffRefreshCommand, GIT_DIFF_CLEAN, GIT_DIFF_CLEAN_CACHED
from sgit.cmd import GitCmd
from sgit.helpers import GitStatusHelper, GitStashHelper, GitLogHelper, GitBranchHelper, GitRemoteHelper

pytestmark = requires_git


class RealGit(GitCmd, GitStatusHelper, GitStashHelper, GitLogHelper, GitRemoteHelper):
    pass


def status_bar(repo, view, kind='fancy', flush=sublime.flush_timeouts):
    updater = GitStatusBarUpdater([GIT], 'utf-8', [], repo.path, kind, view)
    updater.run()  # run synchronously instead of via Thread.start()
    flush()
    return view.get_status('git-status')


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
        assert choices[0][0] == 'second'
        assert choices[0][1] == '%s by Test User <test@example.com>' % sha2[:8]
        assert choices[0][2].endswith('(%s)' % log[0][4])
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

    Refactor (c) moves the git work off the UI thread; the resulting buffer
    contents and read-only flag must not change.
    """

    @pytest.fixture(autouse=True)
    def _no_help(self, settings):
        settings.set('git_show_status_help', False)

    def status_view(self, repo, content='stale contents'):
        return sublime.View(settings={'git_view': 'status', 'git_repo': repo.path},
                            content=content)

    def test_replaces_buffer_with_built_status(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', message='first')
        tmp_repo.write('a.txt', 'changed\n')
        view = self.status_view(tmp_repo)

        GitStatusRefreshCommand(view).run(None, goto='point:0')

        assert view.substr(sublime.Region(0, view.size())) == \
            GitStatusBuilder().build_status(tmp_repo.path)
        assert view.is_read_only()
        assert list(view.sel()) == [sublime.Region(0, 0)]

    def test_default_goto_lands_on_the_clean_line(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        view = self.status_view(tmp_repo)

        GitStatusRefreshCommand(view).run(None)

        text = view.substr(sublime.Region(0, view.size()))
        assert GIT_WORKING_DIR_CLEAN in text
        point = view.sel()[0].begin()
        assert view.substr(view.line(point)) == GIT_WORKING_DIR_CLEAN

    def test_does_nothing_for_a_non_status_view(self, settings, tmp_repo):
        view = sublime.View(settings={'git_view': 'diff', 'git_repo': tmp_repo.path},
                            content='untouched')
        GitStatusRefreshCommand(view).run(None)
        assert view.substr(sublime.Region(0, view.size())) == 'untouched'

    def test_does_nothing_without_a_repo(self, settings, tmp_path):
        view = sublime.View(settings={'git_view': 'status'}, content='untouched')
        GitStatusRefreshCommand(view).run(None)
        assert view.substr(sublime.Region(0, view.size())) == 'untouched'


class TestStatusViewCreation(object):
    """Pins the view the ``git_status`` command opens.

    Refactor (d) swaps ``set_syntax_file`` for ``assign_syntax`` and the
    .tmLanguage file for a .sublime-syntax; the *settings* it applies and the
    refresh it triggers must survive that.
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
    """Pins what ``git_diff_refresh`` puts in the view (refactor (c))."""

    def diff_view(self, repo, cached=False, content='stale'):
        return sublime.View(settings={'git_view': 'diff-cached' if cached else 'diff',
                                      'git_repo': repo.path,
                                      'git_diff_path': repo.path,
                                      'git_diff_cached': cached},
                            content=content)

    def test_writes_worktree_diff(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'one\n')
        tmp_repo.write('a.txt', 'two\n')
        view = self.diff_view(tmp_repo)

        GitDiffRefreshCommand(view).run(None)

        text = view.substr(sublime.Region(0, view.size()))
        assert text.startswith('diff --git a/a.txt b/a.txt')
        assert '-one' in text and '+two' in text
        assert view.settings().get('git_diff_clean') is False
        assert view.is_read_only()

    def test_clean_worktree_writes_placeholder(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo)

        GitDiffRefreshCommand(view).run(None)

        assert view.substr(sublime.Region(0, view.size())) == GIT_DIFF_CLEAN
        assert view.settings().get('git_diff_clean') is True

    def test_clean_index_writes_cached_placeholder(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'one\n')
        view = self.diff_view(tmp_repo, cached=True)

        GitDiffRefreshCommand(view).run(None, cached=True)

        assert view.substr(sublime.Region(0, view.size())) == GIT_DIFF_CLEAN_CACHED
        assert view.settings().get('git_diff_clean') is True

    def test_unified_setting_is_honoured(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', ''.join('%s\n' % i for i in range(20)))
        tmp_repo.write('a.txt', ''.join('%s\n' % (i if i != 10 else 'x') for i in range(20)))
        view = self.diff_view(tmp_repo)
        view.settings().set('git_diff_unified', 1)

        GitDiffRefreshCommand(view).run(None)

        text = view.substr(sublime.Region(0, view.size()))
        assert '@@ -10,3 +10,3 @@' in text


class TestQuickStatusCommand(object):
    """Pins the *behaviour* of the quick status panel.

    Refactor (d) replaces the plain string items with ``QuickPanelItem``s; the
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

    def test_untracked_file_reports_an_error(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('u.txt', 'u\n')
        window, on_done = self.panel(tmp_repo)
        on_done(0)
        assert window.commands == []
        assert sublime.error_messages == ['Cannot show diff for untracked files.']


class TestStatusBarEventListener(object):
    """Which events spawn an updater, and for which settings.

    ``on_activated``/``on_load``/``on_post_save`` are ST2-only shims guarded by
    ``sublime.version() < '3000'``; on ST4 only the ``_async`` variants do work.
    Refactor (d) removes the shims, which must not change this.
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

    def test_sync_events_do_nothing_on_st3_plus(self, settings, tmp_repo, spawned):
        view = self.view_in_repo(tmp_repo)
        listener = GitStatusBarEventListener()
        listener.on_activated(view)
        listener.on_load(view)
        listener.on_post_save(view)
        assert spawned == []

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
