# coding: utf-8
"""Rebase / abort, branch deletion, undo commit, fetch prune and remote ordering."""
import os
import threading
import time

import pytest
import sublime

from conftest import requires_git
import sgit.cmd
from sgit.cmd import GitCmd, repo_lock
from sgit.branch import GitDeleteBranchCommand, GitDeleteMergedBranchesCommand, GIT_ONLY_CURRENT
from sgit.commit import GitUndoCommitCommand
from sgit.helpers import sort_remote_names, GitRemoteHelper, GitBranchHelper, KIND_BRANCH
from sgit.log import GitLogCurrentFileCommand, GIT_LOG_VIEW_TITLE_PREFIX
from sgit.merge import (GitMergeCommand, GitMergeAbortCommand, GitRebaseCommand, GitRebaseAbortCommand,
                        GIT_NOT_MERGING, GIT_NOT_REBASING, GIT_REBASE_IN_PROGRESS)
from sgit.remote import GitFetchCommand, GitPullCommand

pytestmark = requires_git


def window_for(repo, filename=None):
    view = sublime.View(file_name=filename)
    return sublime.Window(folders=[repo.path], views=[view], active_view=view)


@pytest.fixture
def commands(monkeypatch):
    spawned = []
    original = sgit.cmd.subprocess.Popen

    def recording_popen(args, *a, **kwargs):
        spawned.append(list(args))
        return original(args, *a, **kwargs)

    monkeypatch.setattr(sgit.cmd.subprocess, 'Popen', recording_popen)
    return spawned


def git_args(spawned):
    return [c[1 + len(GitCmd.opts):] for c in spawned]


class Helper(GitCmd, GitBranchHelper):
    pass


def wait_for(spawned, subcommand, timeout=10):
    """Wait for the async git thread (started by StatusSpinner) to spawn
    ``subcommand``."""
    deadline = time.time() + timeout
    while time.time() < deadline and not any(a[:1] == [subcommand] for a in git_args(spawned)):
        time.sleep(0.05)


def in_progress(repo):
    return Helper().get_in_progress(repo.path)


def make_conflict(repo):
    """main and topic both edit a.txt so merging/rebasing conflicts."""
    repo.commit('a.txt', 'base\n', 'base')
    repo.git('checkout', '-q', '-b', 'topic')
    repo.commit('a.txt', 'topic\n', 'topic change')
    repo.git('checkout', '-q', 'main')
    repo.commit('a.txt', 'main\n', 'main change')


class TestRebase(object):

    @pytest.fixture(autouse=True)
    def _inline(self, inline_threads):
        """The rebase runs through run_async; run it inline (then flush)."""

    def test_lists_other_local_and_remote_branches(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('branch', 'feature')
        tmp_repo.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        window = window_for(tmp_repo)

        GitRebaseCommand(window).run()
        items, on_done = window.quick_panel
        assert [(i.trigger, i.annotation, i.kind) for i in items] == [
            ('feature', '', KIND_BRANCH),
            ('origin/main', 'remote', KIND_BRANCH),
        ]

    def test_rebases_onto_selected_branch(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', 'base')
        tmp_repo.git('checkout', '-q', '-b', 'topic')
        topic = tmp_repo.commit('b.txt', 'b\n', 'topic')
        tmp_repo.git('checkout', '-q', 'main')
        main = tmp_repo.commit('c.txt', 'c\n', 'main')
        tmp_repo.git('checkout', '-q', 'topic')
        window = window_for(tmp_repo)

        GitRebaseCommand(window).run()
        items, on_done = window.quick_panel
        on_done([i.trigger for i in items].index('main'))
        sublime.flush_timeouts()

        assert tmp_repo.git('rev-parse', 'HEAD~1') == main
        assert tmp_repo.git('rev-parse', 'HEAD') != topic
        assert tmp_repo.git('log', '-1', '--format=%s') == 'topic'
        assert sublime.error_messages == []
        assert ('git_status', {'refresh_only': True}) in window.commands

    def test_rebase_flags_setting_is_applied(self, settings, tmp_repo, commands):
        settings.set('git_rebase_flags', ['--autostash'])
        tmp_repo.commit('a.txt', 'a\n', 'base')
        tmp_repo.git('branch', 'other')
        window = window_for(tmp_repo)

        GitRebaseCommand(window).run()
        del commands[:]
        window.quick_panel[1](0)
        assert ['rebase', '--autostash', 'other'] in git_args(commands)

    def test_conflict_reports_error_and_leaves_rebase_in_progress(self, settings, tmp_repo):
        make_conflict(tmp_repo)
        tmp_repo.git('checkout', '-q', 'topic')
        window = window_for(tmp_repo)

        GitRebaseCommand(window).run()
        window.quick_panel[1](0)
        sublime.flush_timeouts()

        assert len(sublime.error_messages) == 1
        assert 'CONFLICT' in sublime.error_messages[0]
        assert in_progress(tmp_repo) == (False, True)

        # a second rebase is refused while this one is unresolved
        window2 = window_for(tmp_repo)
        GitRebaseCommand(window2).run()
        assert window2.quick_panel is None
        assert sublime.error_messages[-1] == GIT_REBASE_IN_PROGRESS

    def test_abort_rebase_restores_branch(self, settings, tmp_repo):
        make_conflict(tmp_repo)
        tmp_repo.git('checkout', '-q', 'topic')
        before = tmp_repo.git('rev-parse', 'HEAD')
        tmp_repo.git('rebase', 'main', check=False)
        assert in_progress(tmp_repo) == (False, True)
        window = window_for(tmp_repo)

        GitRebaseAbortCommand(window).run()

        assert sublime.ok_cancel_dialogs[0][1] == 'Abort Rebase'
        assert in_progress(tmp_repo) == (False, False)
        assert tmp_repo.git('rev-parse', 'HEAD') == before
        assert tmp_repo.git('rev-parse', '--abbrev-ref', 'HEAD') == 'topic'

    def test_abort_rebase_declined_does_nothing(self, settings, tmp_repo):
        make_conflict(tmp_repo)
        tmp_repo.git('checkout', '-q', 'topic')
        tmp_repo.git('rebase', 'main', check=False)
        sublime.ok_cancel_answers.append(False)

        GitRebaseAbortCommand(window_for(tmp_repo)).run()
        assert in_progress(tmp_repo) == (False, True)

    def test_abort_rebase_without_rebase_is_an_error(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        GitRebaseAbortCommand(window_for(tmp_repo)).run()
        assert sublime.error_messages == [GIT_NOT_REBASING]
        assert sublime.ok_cancel_dialogs == []


class TestMergeAbort(object):

    def test_abort_merge_restores_worktree(self, settings, tmp_repo):
        make_conflict(tmp_repo)
        tmp_repo.git('merge', 'topic', check=False)
        assert in_progress(tmp_repo) == (True, False)
        window = window_for(tmp_repo)

        GitMergeAbortCommand(window).run()

        assert in_progress(tmp_repo) == (False, False)
        with open(os.path.join(tmp_repo.path, 'a.txt')) as f:
            assert f.read() == 'main\n'
        assert tmp_repo.git('status', '--porcelain') == ''

    def test_abort_merge_without_merge_is_an_error(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        GitMergeAbortCommand(window_for(tmp_repo)).run()
        assert sublime.error_messages == [GIT_NOT_MERGING]

    def test_merge_refused_while_merge_in_progress(self, settings, tmp_repo):
        make_conflict(tmp_repo)
        tmp_repo.git('merge', 'topic', check=False)
        window = window_for(tmp_repo)
        GitMergeCommand(window).run()
        assert window.quick_panel is None
        assert len(sublime.error_messages) == 1


class TestUndoCommit(object):

    def test_soft_resets_and_keeps_changes_staged(self, settings, tmp_repo):
        first = tmp_repo.commit('a.txt', 'a\n', 'first')
        tmp_repo.commit('b.txt', 'b\n', 'second')
        window = window_for(tmp_repo)

        GitUndoCommitCommand(window).run()

        assert len(sublime.ok_cancel_dialogs) == 1
        assert 'second' in sublime.ok_cancel_dialogs[0][0]
        assert tmp_repo.git('rev-parse', 'HEAD') == first
        assert tmp_repo.git('status', '--porcelain') == 'A  b.txt'
        assert ('git_status', {'refresh_only': True}) in window.commands

    def test_declined_leaves_commit(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', 'first')
        second = tmp_repo.commit('b.txt', 'b\n', 'second')
        sublime.ok_cancel_answers.append(False)

        GitUndoCommitCommand(window_for(tmp_repo)).run()
        assert tmp_repo.git('rev-parse', 'HEAD') == second

    def test_root_commit_deletes_branch_and_keeps_changes_staged(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', 'first')
        branch = tmp_repo.git('symbolic-ref', '--short', 'HEAD')

        GitUndoCommitCommand(window_for(tmp_repo)).run()

        assert len(sublime.ok_cancel_dialogs) == 1
        assert 'first commit' in sublime.ok_cancel_dialogs[0][0]
        assert tmp_repo.git('rev-parse', '-q', '--verify', 'HEAD', check=False) == ''
        assert tmp_repo.git('symbolic-ref', '--short', 'HEAD') == branch
        assert tmp_repo.git('status', '--porcelain') == 'A  a.txt'

    def test_declined_root_commit_is_kept(self, settings, tmp_repo):
        root = tmp_repo.commit('a.txt', 'a\n', 'first')
        sublime.ok_cancel_answers.append(False)

        GitUndoCommitCommand(window_for(tmp_repo)).run()
        assert tmp_repo.git('rev-parse', 'HEAD') == root

    def test_pushed_commit_warns_first(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', 'first')
        tmp_repo.commit('b.txt', 'b\n', 'second')
        tmp_repo.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        sublime.ok_cancel_answers.append(False)  # decline the "already pushed" warning

        GitUndoCommitCommand(window_for(tmp_repo)).run()

        assert len(sublime.ok_cancel_dialogs) == 1
        assert 'origin/main' in sublime.ok_cancel_dialogs[0][0]
        assert tmp_repo.git('log', '-1', '--format=%s') == 'second'


class TestDeleteBranch(object):

    def test_lists_only_other_local_branches(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n', 'base')
        tmp_repo.git('branch', 'feature')
        tmp_repo.git('update-ref', 'refs/remotes/origin/feature', 'HEAD')
        tmp_repo.git('update-ref', 'refs/remotes/origin/other', 'HEAD')
        window = window_for(tmp_repo)

        GitDeleteBranchCommand(window).run()
        items, _ = window.quick_panel
        assert [(i.trigger, i.annotation) for i in items] == [('feature', '')]
        assert items[0].details == ['base']

    def test_deletes_merged_branch_without_asking(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('branch', 'feature')
        window = window_for(tmp_repo)

        GitDeleteBranchCommand(window).run()
        window.quick_panel[1](0)

        assert sublime.ok_cancel_dialogs == []
        assert tmp_repo.git('branch', '--list', 'feature') == ''
        assert sublime.error_messages == []

    def test_unmerged_branch_asks_then_forces(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '-b', 'feature')
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.git('checkout', '-q', 'main')
        window = window_for(tmp_repo)

        GitDeleteBranchCommand(window).run()
        window.quick_panel[1](0)

        assert len(sublime.ok_cancel_dialogs) == 1
        assert 'not fully merged' in sublime.ok_cancel_dialogs[0][0]
        assert tmp_repo.git('branch', '--list', 'feature') == ''

    def test_unmerged_branch_kept_when_declined(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '-b', 'feature')
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.git('checkout', '-q', 'main')
        sublime.ok_cancel_answers.append(False)
        window = window_for(tmp_repo)

        GitDeleteBranchCommand(window).run()
        window.quick_panel[1](0)
        assert tmp_repo.git('branch', '--list', 'feature') != ''

    def test_only_current_branch_is_an_error(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('update-ref', 'refs/remotes/origin/feature', 'HEAD')
        window = window_for(tmp_repo)
        GitDeleteBranchCommand(window).run()
        assert window.quick_panel is None
        assert sublime.error_messages == [GIT_ONLY_CURRENT]


class TestDeleteMergedBranches(object):

    def setup_branches(self, repo):
        repo.commit('a.txt', 'a\n', 'base')
        repo.git('branch', 'merged-1')
        repo.git('branch', 'merged-2')
        repo.git('checkout', '-q', '-b', 'unmerged')
        repo.commit('b.txt', 'b\n', 'extra')
        repo.git('checkout', '-q', 'main')

    def test_current_branch_listed_first_and_no_remotes(self, settings, tmp_repo):
        self.setup_branches(tmp_repo)
        tmp_repo.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        window = window_for(tmp_repo)
        GitDeleteMergedBranchesCommand(window).run()
        items, _ = window.quick_panel
        assert [(i.trigger, i.annotation) for i in items] == [
            ('main', 'current'), ('merged-1', ''), ('merged-2', ''), ('unmerged', '')]

    def test_deletes_only_fully_merged_branches(self, settings, tmp_repo):
        self.setup_branches(tmp_repo)
        window = window_for(tmp_repo)

        GitDeleteMergedBranchesCommand(window).run()
        window.quick_panel[1](0)  # target: main

        msg, title = sublime.ok_cancel_dialogs[0]
        assert 'merged-1' in msg and 'merged-2' in msg and 'unmerged' not in msg
        assert title == 'Delete 2 branches'
        assert sorted(tmp_repo.git('branch', '--format=%(refname:short)').splitlines()) == ['main', 'unmerged']

    def test_target_and_current_are_never_deleted(self, settings, tmp_repo):
        self.setup_branches(tmp_repo)
        window = window_for(tmp_repo)

        GitDeleteMergedBranchesCommand(window).run()
        items, on_done = window.quick_panel
        on_done([i.trigger for i in items].index('merged-1'))  # target: merged-1

        msg, _ = sublime.ok_cancel_dialogs[0]
        assert msg.split('\n\n', 1)[1] == 'merged-2'
        assert sorted(tmp_repo.git('branch', '--format=%(refname:short)').splitlines()) == ['main', 'merged-1', 'unmerged']

    def test_nothing_merged_is_an_error(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        window = window_for(tmp_repo)
        GitDeleteMergedBranchesCommand(window).run()
        window.quick_panel[1](0)
        assert sublime.ok_cancel_dialogs == []
        assert len(sublime.error_messages) == 1


class TestPrune(object):

    def test_single_remote_fetches_without_a_picker(self, settings, tmp_repo, commands):
        tmp_repo.git('remote', 'add', 'origin', tmp_repo.path)
        window = window_for(tmp_repo)
        GitFetchCommand(window).run()
        assert window.quick_panel is None
        wait_for(commands, 'fetch')
        assert [a for a in git_args(commands) if a[:1] == ['fetch']][0][-1] == 'origin'

    def test_fetch_passes_prune_by_default(self, settings, tmp_repo, commands):
        tmp_repo.git('remote', 'add', 'origin', tmp_repo.path)
        window = window_for(tmp_repo)
        GitFetchCommand(window).run()
        wait_for(commands, 'fetch')
        fetch = [a for a in git_args(commands) if a[:1] == ['fetch']]
        assert fetch and '--prune' in fetch[0]

    def test_prune_can_be_disabled(self, settings, tmp_repo, commands):
        settings.set('git_fetch_prune', False)
        tmp_repo.git('remote', 'add', 'origin', tmp_repo.path)
        window = window_for(tmp_repo)
        GitFetchCommand(window).run()
        wait_for(commands, 'fetch')
        fetch = [a for a in git_args(commands) if a[:1] == ['fetch']]
        assert fetch and '--prune' not in fetch[0]

    def test_pull_passes_prune(self, settings, tmp_repo, commands):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', tmp_repo.path)
        tmp_repo.git('config', 'branch.main.remote', 'origin')
        tmp_repo.git('config', 'branch.main.merge', 'refs/heads/main')
        window = window_for(tmp_repo)
        GitPullCommand(window).run()
        wait_for(commands, 'pull')
        pull = [a for a in git_args(commands) if a[:1] == ['pull']]
        assert pull and '--prune' in pull[0]


class TestRemoteOrdering(object):

    def test_origin_then_upstream_then_alphabetical(self):
        assert sort_remote_names(['zeta', 'upstream', 'alpha', 'origin', 'beta']) == \
            ['origin', 'upstream', 'alpha', 'beta', 'zeta']

    def test_quick_remotes_follow_the_same_order(self, settings, tmp_repo):
        for name in ('alpha', 'upstream', 'origin'):
            tmp_repo.git('remote', 'add', name, 'https://example.com/%s.git' % name)
        helper = type('H', (GitCmd, GitRemoteHelper), {})()
        remotes = helper.get_remotes(tmp_repo.path)
        assert helper.get_remote_names(remotes) == ['origin', 'upstream', 'alpha']
        assert [i.trigger for i in helper.format_quick_remotes(remotes)] == ['origin', 'upstream', 'alpha']


class TestLogCurrentFile(object):

    def test_opens_log_view_following_renames(self, settings, tmp_repo):
        tmp_repo.commit('old.txt', 'a\n', 'add old')
        tmp_repo.git('mv', 'old.txt', 'new.txt')
        tmp_repo.commit_all('rename to new')
        path = os.path.join(tmp_repo.path, 'new.txt')
        view = sublime.View(file_name=path)
        window = sublime.Window(folders=[tmp_repo.path], views=[view], active_view=view)

        GitLogCurrentFileCommand(view).run(None)

        log_view = window.active_view()
        assert log_view is not view
        assert log_view.name() == GIT_LOG_VIEW_TITLE_PREFIX + 'new.txt'
        assert log_view.settings().get('git_view') == 'log'
        assert log_view.settings().get('git_log_path') == 'new.txt'
        assert log_view.commands == [('git_log_refresh', None)]

    def test_refresh_writes_patch_log(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('old.txt', 'a\n', 'add old')
        tmp_repo.git('mv', 'old.txt', 'new.txt')
        tmp_repo.commit_all('rename to new')
        from sgit.log import GitLogRefreshCommand, GitLogWriteCommand
        view = sublime.View()
        view.settings().set('git_repo', tmp_repo.path)
        view.settings().set('git_log_path', 'new.txt')

        GitLogRefreshCommand(view).run(None)
        flush()
        # git ran in the worker; the buffer is only written by git_log_write
        assert view.size() == 0
        [(name, args)] = view.commands
        assert name == 'git_log_write'
        GitLogWriteCommand(view).run(None, **args)

        text = view.substr(sublime.Region(0, view.size()))
        assert 'rename to new' in text and 'add old' in text
        # same layout as git show: commit header + patch
        assert text.startswith('commit ')
        assert 'rename from old.txt' in text and 'rename to new.txt' in text
        assert '+a\n' in text
        assert view._read_only is True

    def test_unsaved_file_is_an_error(self, settings, tmp_repo):
        view = sublime.View()
        sublime.Window(folders=[tmp_repo.path], views=[view], active_view=view)
        GitLogCurrentFileCommand(view).run(None)
        assert len(sublime.error_messages) == 1


class TestSerializedCommands(object):

    def test_same_repo_shares_a_lock_and_others_do_not(self, tmp_path):
        a = str(tmp_path / 'a')
        b = str(tmp_path / 'b')
        os.makedirs(a)
        os.makedirs(b)
        assert repo_lock(a) is repo_lock(a)
        assert repo_lock(a) is not repo_lock(b)
        assert repo_lock(None) is repo_lock(None)

    def test_concurrent_git_processes_in_one_repo_never_overlap(self, settings, tmp_repo, monkeypatch):
        """Popen is wrapped to count how many git processes are alive at once
        in the repo; with the lock it must never exceed one."""
        original = sgit.cmd.subprocess.Popen
        state = {'active': 0, 'peak': 0}
        guard = threading.Lock()

        class CountingPopen(object):
            def __init__(self, *args, **kwargs):
                with guard:
                    state['active'] += 1
                    state['peak'] = max(state['peak'], state['active'])
                self._proc = original(*args, **kwargs)

            def communicate(self, *a, **kw):
                try:
                    time.sleep(0.02)
                    return self._proc.communicate(*a, **kw)
                finally:
                    with guard:
                        state['active'] -= 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self._proc.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._proc, name)

        monkeypatch.setattr(sgit.cmd.subprocess, 'Popen', CountingPopen)
        tmp_repo.commit('a.txt', 'a\n')

        def worker():
            for _ in range(5):
                GitCmd().git(['status', '--porcelain'], cwd=tmp_repo.path)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert state['peak'] == 1
