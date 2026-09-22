# coding: utf-8
"""Regression tests for a batch of bug fixes and performance changes:
discarding new files, snapshots and stashes, error reporting in the status
view, log limits, the repo lock policy and off-UI-thread git calls."""
import os
import stat
import subprocess
import threading

import pytest
import sublime

from conftest import requires_git, GIT
import sgit.cmd
import sgit.status
from sgit.cmd import Cmd, GitCmd, repo_lock, is_network_command
from sgit.checkout import GitCheckoutCurrentFileCommand, GitCheckoutCommitCommand
from sgit.commit import (GitCommitAmendCommand, GitCommitEventListener, GitCommitPerformCommand,
                         GitQuickCommitCommand, commit_output)
from sgit.custom import GitCustomCommand
from sgit.helpers import GitDiffHelper, GitLogHelper, LOAD_MORE_COMMITS, new_file_diff
from sgit.log import (GitLogGraphRefreshCommand, GitLogGraphWriteCommand, GitLogGraphShowCommand,
                      GitLogGraphLoadMoreCommand, GitQuickLogCurrentFileCommand, GitLogWriteCommand,
                      GIT_LOG_LOAD_MORE_RE)
from sgit.merge import GitMergeCommand
from sgit.remote import GitFetchCommand
from sgit.show import GitShowRefreshCommand
from sgit.stash import GitStashCommand, GitSnapshotCommand, NO_LOCAL_CHANGES
from sgit.status import (GitStatusDiscardCommand, GitStatusIgnoreCommand, GitStatusStageCommand,
                         GitStatusRefreshCommand, GitStatusWriteCommand, GitStatusBarUpdater,
                         STAGED_CHANGES, UNSTAGED_CHANGES)
from sgit.diff import GitDiffWriteCommand

pytestmark = requires_git


def window_for(repo, filename=None):
    view = sublime.View(file_name=filename)
    return sublime.Window(folders=[repo.path], views=[view], active_view=view)


@pytest.fixture
def popens(monkeypatch):
    """Record ``(argv, env)`` of every process spawned."""
    spawned = []
    original = sgit.cmd.subprocess.Popen

    def recording_popen(args, *a, **kwargs):
        spawned.append((list(args), kwargs.get('env') or {}))
        return original(args, *a, **kwargs)

    monkeypatch.setattr(sgit.cmd.subprocess, 'Popen', recording_popen)
    return spawned


def git_args(spawned):
    return [argv[1 + len(GitCmd.opts):] for argv, _ in spawned]


# Bugs -----------------------------------------------------------------------

class TestDiscardStagedNewFile(object):

    def test_is_deleted_and_the_dialog_says_delete(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('new.txt', 'new\n')
        tmp_repo.git('add', 'new.txt')

        GitStatusDiscardCommand(sublime.View()).discard_files(tmp_repo.path, [(STAGED_CHANGES, 'new.txt')])

        assert sublime.ok_cancel_dialogs == [
            ("Are you sure you want to perform the following actions?\n\n"
             "  Delete:  new.txt", 'Continue')]
        assert not os.path.exists(os.path.join(tmp_repo.path, 'new.txt'))
        assert tmp_repo.git('status', '--porcelain') == ''
        assert sublime.error_messages == []

    def test_in_a_repo_without_commits(self, settings, tmp_repo):
        tmp_repo.write('new.txt', 'new\n')
        tmp_repo.git('add', 'new.txt')

        GitStatusDiscardCommand(sublime.View()).discard_files(tmp_repo.path, [(STAGED_CHANGES, 'new.txt')])

        assert not os.path.exists(os.path.join(tmp_repo.path, 'new.txt'))
        assert sublime.error_messages == []


class TestSnapshot(object):

    def test_clean_tree_does_not_reapply_an_old_stash(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'stashed\n')
        tmp_repo.git('stash', 'push', '-m', 'old')

        GitSnapshotCommand(window_for(tmp_repo)).run()

        assert open(os.path.join(tmp_repo.path, 'a.txt')).read() == 'a\n'
        assert tmp_repo.git('stash', 'list').count('\n') == 0  # still only "old"
        assert sublime.error_messages == [NO_LOCAL_CHANGES]

    def test_saves_a_stash_and_leaves_the_tree_alone(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.write('a.txt', 'unstaged\n')
        tmp_repo.write('b.txt', 'staged\n')
        tmp_repo.git('add', 'b.txt')
        before = tmp_repo.git('status', '--porcelain')

        GitSnapshotCommand(window_for(tmp_repo)).run()

        assert tmp_repo.git('status', '--porcelain') == before
        assert tmp_repo.git('diff', '--cached', '--name-only') == 'b.txt'
        assert 'Snapshot at' in tmp_repo.git('stash', 'list')
        assert tmp_repo.git('stash', 'show', '--name-only', 'stash@{0}').split() == ['a.txt', 'b.txt']
        assert sublime.error_messages == []


class TestStash(object):

    def test_only_staged_changes_can_be_stashed(self, settings, tmp_repo, popens):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', 'a.txt')
        window = window_for(tmp_repo)

        GitStashCommand(window).run()
        assert sublime.error_messages == []
        caption, _, on_done, _, _ = window.input_panel
        on_done('  my title  ')

        assert ['stash', 'push', '-m', 'my title'] in git_args(popens)
        assert tmp_repo.git('stash', 'list') == 'stash@{0}: On main: my title'
        assert tmp_repo.git('status', '--porcelain') == ''

    def test_untracked_and_untitled(self, settings, tmp_repo, popens):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('new.txt', 'n\n')
        window = window_for(tmp_repo)

        GitStashCommand(window).run(untracked=True)
        window.input_panel[2]('')

        assert ['stash', 'push', '--include-untracked'] in git_args(popens)
        assert tmp_repo.git('status', '--porcelain') == ''

    def test_nothing_to_stash(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        window = window_for(tmp_repo)
        GitStashCommand(window).run()
        assert sublime.error_messages == [NO_LOCAL_CHANGES]


def test_ignore_confirmation_with_more_than_ten_files(settings):
    sublime.ok_cancel_answers.append(False)
    patterns = ['f%d' % i for i in range(13)]
    assert GitStatusIgnoreCommand(sublime.View()).confirm('msg', patterns, 'OK') is False
    assert sublime.ok_cancel_dialogs[0][0].endswith('f9\n(3 more...)')


def test_pedantic_marks_every_long_line(settings):
    long = 'x' * 80
    view = sublime.View(settings={'git_view': 'commit'},
                        content='subject\n\n%s\nshort\n%s\n# comment %s\n' % (long, long, long))
    GitCommitEventListener().mark_pedantic(view)
    regions = view.get_regions('git-commit.others')
    assert [view.substr(r) for r in regions] == ['x' * 8, 'x' * 8]


class TestCommitOutput(object):

    def test_combines_stdout_and_stderr(self):
        assert commit_output(0, '[main abc] msg\n', '') == '[main abc] msg'
        assert commit_output(1, '', 'hook said no\n') == 'hook said no'
        assert commit_output(1, 'nothing to commit\n', '') == 'nothing to commit'
        assert commit_output(1, 'out\n', 'err\n') == 'err\nout'

    def test_quick_commit_rejected_by_a_hook_shows_the_hook_output(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        hook = os.path.join(tmp_repo.path, '.git', 'hooks', 'pre-commit')
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\necho "lint failed" >&2\nexit 1\n')
        os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR)
        tmp_repo.write('a.txt', 'changed\n')
        window = window_for(tmp_repo)
        panel = sublime.View(window=window)
        window.get_output_panel = lambda name: panel

        GitQuickCommitCommand(window).on_commit_message(tmp_repo.path, 'msg')
        flush()

        assert panel.commands == [('git_panel_write', {'content': 'lint failed'})]
        assert ('show_panel', {'panel': 'output.git-commit'}) in window.commands


class TestCommitOffTheUiThread(object):

    def test_perform_runs_git_commit_in_the_worker(self, settings, tmp_repo, monkeypatch, flush):
        workers = []
        monkeypatch.setattr(sgit.status, 'run_in_thread', workers.append)
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'staged\n')
        tmp_repo.git('add', 'a.txt')
        window = window_for(tmp_repo)
        head = tmp_repo.git('rev-parse', 'HEAD')

        GitCommitPerformCommand(window).run(tmp_repo.path, 'the message\n')
        assert tmp_repo.git('rev-parse', 'HEAD') == head  # nothing ran yet

        [worker] = workers
        worker()
        flush()
        assert tmp_repo.git('log', '-1', '--format=%s') == 'the message'
        assert ('git_status', {'refresh_only': True}) in window.commands

    def test_merge_runs_in_the_worker(self, settings, tmp_repo, monkeypatch, flush):
        workers = []
        monkeypatch.setattr(sgit.status, 'run_in_thread', workers.append)
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('checkout', '-q', '-b', 'topic')
        topic = tmp_repo.commit('b.txt', 'b\n')
        tmp_repo.git('checkout', '-q', 'main')
        window = window_for(tmp_repo)

        GitMergeCommand(window).run()
        window.quick_panel[1](0)
        assert tmp_repo.git('rev-parse', 'HEAD') != topic

        workers.pop()()
        flush()
        assert tmp_repo.git('rev-parse', 'HEAD') == topic
        assert sublime.error_messages == []

    def test_a_worker_that_raises_reports_the_error(self, settings, flush, inline_threads):
        done = []

        def boom():
            raise RuntimeError('broken')

        sgit.status.run_async(boom, done.append)
        flush()
        assert done == []
        assert sublime.error_messages == ['broken']


def test_quick_log_current_file_on_an_unsaved_view(settings, tmp_repo):
    view = sublime.View()
    window = sublime.Window(folders=[tmp_repo.path], views=[view], active_view=view)
    GitQuickLogCurrentFileCommand(view).run(None)
    assert window.quick_panel[0] == ['No log for file']


class TestPanelShownOnce(object):

    def test_fetch_output(self, settings):
        window = sublime.Window()
        cmd = GitFetchCommand(window)
        cmd.panel = window.get_output_panel('git-fetch')
        cmd.panel_shown = False
        cmd.on_data('one\n')
        cmd.on_data('two\n')
        assert window.commands == [('show_panel', {'panel': 'output.git-fetch'})]

    def test_custom_command_output(self, settings):
        window = sublime.Window()
        cmd = GitCustomCommand(window)
        cmd.output = 'panel'
        cmd.init_output('/repo', ['status'])
        cmd.on_output('one\n')
        cmd.on_output('two\n')
        assert window.commands == [('show_panel', {'panel': 'output.git-custom'})]


def test_checkout_untracked_file_names_the_file(settings, tmp_repo):
    tmp_repo.commit('a.txt', 'a\n')
    path = tmp_repo.write('new.txt', 'n\n')
    view = sublime.View(file_name=path)
    sublime.Window(folders=[tmp_repo.path], views=[view], active_view=view)
    GitCheckoutCurrentFileCommand(view).run(None)
    assert sublime.error_messages == ['The file %s is not tracked by git.' % path]


class TestStatusViewReportsGitFailures(object):

    def test_failed_stage_shows_the_error(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        # a stale index.lock makes every index write fail
        open(os.path.join(tmp_repo.path, '.git', 'index.lock'), 'w').close()
        view = sublime.View(settings={'git_repo': tmp_repo.path})
        cmd = GitStatusStageCommand(view)
        cmd.get_selected_files = lambda: [(UNSTAGED_CHANGES, 'a.txt')]

        cmd.run(None)

        assert len(sublime.error_messages) == 1
        assert 'index.lock' in sublime.error_messages[0]
        assert view.commands[-1][0] == 'git_status_refresh'

    def test_successful_stage_shows_nothing(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'changed\n')
        view = sublime.View(settings={'git_repo': tmp_repo.path})
        cmd = GitStatusStageCommand(view)
        cmd.get_selected_files = lambda: [(UNSTAGED_CHANGES, 'a.txt')]

        cmd.run(None)

        assert sublime.error_messages == []
        assert tmp_repo.git('diff', '--cached', '--name-only') == 'a.txt'


# Small ----------------------------------------------------------------------

class TestAmendPushedCheck(object):

    def test_unpushed_commit_does_not_ask(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        tmp_repo.git('remote', 'add', 'origin', 'https://example.com/repo.git')
        tmp_repo.git('config', 'branch.main.remote', 'origin')
        tmp_repo.git('config', 'branch.main.merge', 'refs/heads/main')
        assert tmp_repo.git('rev-parse', '--abbrev-ref', '@{u}') == 'origin/main'
        # a new commit whose tree equals upstream's used to count as pushed
        tmp_repo.git('commit', '-q', '--allow-empty', '-m', 'empty')

        GitCommitAmendCommand(window_for(tmp_repo)).run()
        assert sublime.ok_cancel_dialogs == []

    def test_pushed_commit_asks_and_names_the_remote(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        sublime.ok_cancel_answers.append(False)

        window = window_for(tmp_repo)
        GitCommitAmendCommand(window).run()
        assert len(sublime.ok_cancel_dialogs) == 1
        assert 'origin/main' in sublime.ok_cancel_dialogs[0][0]


def test_executable_error_splits_path_on_pathsep(monkeypatch):
    monkeypatch.setenv('PATH', os.pathsep.join(['/a/bin', '/b/bin']))
    assert '/a/bin\n/b/bin' in GitCmd().get_executable_error()


# Performance ----------------------------------------------------------------

class TestRepoLockPolicy(object):

    @pytest.mark.parametrize('cmd, network', [
        (['fetch', '-v'], True),
        (['push'], True),
        (['pull', '-v', None, 'origin'], True),
        (['ls-remote'], True),
        (['remote', 'show', 'origin'], True),
        (['remote', 'prune', 'origin'], True),
        (['remote', 'add', 'x', 'url'], False),
        (['status'], False),
        (['commit', '-m', 'push'], False),
        ([], False),
    ])
    def test_is_network_command(self, cmd, network):
        assert is_network_command(cmd) is network

    def test_network_command_does_not_wait_for_the_repo_lock(self, settings, tmp_repo):
        result = []
        with repo_lock(tmp_repo.path):
            t = threading.Thread(target=lambda: result.append(
                GitCmd().git(['fetch', '--all'], cwd=tmp_repo.path)))
            t.start()
            t.join(timeout=20)
            assert result, 'fetch waited for the repo lock'

    def test_read_only_call_does_not_wait_for_the_repo_lock(self, settings, tmp_repo):
        result = []

        def worker():
            with sgit.cmd.read_only_git():
                result.append(GitCmd().git(['status'], cwd=tmp_repo.path))

        with repo_lock(tmp_repo.path):
            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=20)
            assert result, 'read-only status waited for the repo lock'

    def test_other_commands_still_wait(self, settings, tmp_repo):
        result = []
        with repo_lock(tmp_repo.path):
            t = threading.Thread(target=lambda: result.append(
                GitCmd().git(['status'], cwd=tmp_repo.path)))
            t.start()
            t.join(timeout=0.5)
            assert result == []
        t.join(timeout=20)
        assert result

    def test_read_only_flag_is_per_thread(self):
        seen = []
        with sgit.cmd.read_only_git():
            t = threading.Thread(target=lambda: seen.append(sgit.cmd.is_read_only()))
            t.start()
            t.join()
            assert sgit.cmd.is_read_only()
        assert seen == [False]
        assert not sgit.cmd.is_read_only()


class TestEnvironment(object):

    def test_terminal_prompt_is_disabled(self, settings, monkeypatch):
        monkeypatch.delenv('GIT_TERMINAL_PROMPT', raising=False)
        env = Cmd().env()
        assert env['GIT_TERMINAL_PROMPT'] == '0'
        assert 'GIT_OPTIONAL_LOCKS' not in env

    def test_read_only_disables_optional_locks(self, settings):
        assert Cmd().env(read_only=True)['GIT_OPTIONAL_LOCKS'] == '0'

    def test_status_refresh_and_status_bar_run_read_only(self, settings, tmp_repo, popens, inline_threads, flush):
        tmp_repo.commit('a.txt', 'a\n')
        del popens[:]
        view = sublime.View(settings={'git_view': 'status', 'git_repo': tmp_repo.path})
        GitStatusRefreshCommand(view).run(None)
        GitStatusBarUpdater([GIT], 'utf-8', [], tmp_repo.path, 'fancy', view).run()
        flush()
        assert popens
        assert [a for a, env in popens if env.get("GIT_OPTIONAL_LOCKS") != "0"] == []

    def test_ui_thread_calls_take_optional_locks(self, settings, tmp_repo, popens):
        GitCmd().git(['status'], cwd=tmp_repo.path)
        assert 'GIT_OPTIONAL_LOCKS' not in popens[0][1]


class TestGraphLogLimit(object):

    def make_history(self, repo, n):
        for i in range(n):
            repo.git('commit', '-q', '--allow-empty', '-m', 'c%d' % i)

    def refresh(self, view, flush):
        GitLogGraphRefreshCommand(view).run(None)
        flush()
        for name, args in list(view.commands):
            if name == 'git_log_graph_write':
                view.commands.remove((name, args))
                GitLogGraphWriteCommand(view).run(None, **args)
        return view.substr(sublime.Region(0, view.size()))

    def test_cut_off_with_a_load_more_line(self, settings, tmp_repo, inline_threads, flush):
        settings.set('git_log_max_count', 3)
        self.make_history(tmp_repo, 5)
        view = sublime.View(settings={'git_view': 'log-graph', 'git_repo': tmp_repo.path})

        lines = self.refresh(view, flush).rstrip('\n').split('\n')
        assert len(lines) == 4
        assert [l.split(' - ')[1].split(') ')[1] for l in lines[:3]] == ['c4', 'c3', 'c2']
        assert lines[-1] == '-- Showing the latest 3 commits. Press enter on this line to load 3 more --'
        assert GIT_LOG_LOAD_MORE_RE.match(lines[-1])

        # enter on the last line loads more
        view.sel().clear()
        view.sel().add(sublime.Region(view.size() - 2))
        GitLogGraphShowCommand(view).run(None)
        assert view.commands[-1] == ('git_log_graph_load_more', None)
        GitLogGraphLoadMoreCommand(view).run(None)
        assert view.settings().get('git_log_max_count') == 6

        lines = self.refresh(view, flush).rstrip('\n').split('\n')
        assert len(lines) == 5
        assert not GIT_LOG_LOAD_MORE_RE.match(lines[-1])

    def test_exactly_max_count_commits_has_no_load_more_line(self, settings, tmp_repo, inline_threads, flush):
        settings.set('git_log_max_count', 3)
        self.make_history(tmp_repo, 3)
        view = sublime.View(settings={'git_view': 'log-graph', 'git_repo': tmp_repo.path})
        assert 'Showing the latest' not in self.refresh(view, flush)

    def test_zero_means_no_limit(self, settings, tmp_repo, inline_threads, flush):
        settings.set('git_log_max_count', 0)
        self.make_history(tmp_repo, 5)
        view = sublime.View(settings={'git_view': 'log-graph', 'git_repo': tmp_repo.path})
        assert self.refresh(view, flush).count('\n') == 5


class TestQuickLogLimit(object):

    class Log(GitCmd, GitLogHelper):
        pass

    def test_load_more_item_reopens_with_more(self, settings, tmp_repo, flush):
        settings.set('git_log_max_count', 2)
        for i in range(3):
            tmp_repo.git('commit', '-q', '--allow-empty', '-m', 'c%d' % i)
        window = window_for(tmp_repo)
        picked = []

        self.Log().show_quick_log_panel(window, tmp_repo.path, picked.append)
        items, on_done = window.quick_panel
        assert [i.trigger for i in items] == ['c2', 'c1', LOAD_MORE_COMMITS]

        on_done(2)
        flush()
        items, on_done = window.quick_panel
        assert [i.trigger for i in items] == ['c2', 'c1', 'c0']
        on_done(2)
        assert picked == [tmp_repo.git('rev-parse', 'HEAD~2')]

    def test_checkout_commit_uses_the_limited_panel(self, settings, tmp_repo):
        settings.set('git_log_max_count', 1)
        tmp_repo.git('commit', '-q', '--allow-empty', '-m', 'c0')
        tmp_repo.git('commit', '-q', '--allow-empty', '-m', 'c1')
        window = window_for(tmp_repo)
        GitCheckoutCommitCommand(window).run()
        items, on_done = window.quick_panel
        assert [i.trigger for i in items] == ['c1', LOAD_MORE_COMMITS]
        on_done(0)
        assert tmp_repo.git('rev-parse', 'HEAD') == tmp_repo.git('rev-parse', 'main')
        assert tmp_repo.git('status', '--porcelain', '--branch').startswith('## HEAD (no branch)')


class TestUnchangedRefreshDoesNotRewrite(object):

    def test_status_write(self, settings):
        view = sublime.View(content='same\n')
        view.sel().add(sublime.Region(2))
        count = view.change_count()
        GitStatusWriteCommand(view).run(None, content='same\n', goto='point:2', viewport=[0.0, 0.0])
        assert view.change_count() == count
        assert list(view.sel()) == [sublime.Region(2)]

    def test_status_write_with_changed_content(self, settings):
        view = sublime.View(content='same\n')
        GitStatusWriteCommand(view).run(None, content='other\n', goto='point:0')
        assert view.substr(sublime.Region(0, view.size())) == 'other\n'

    def test_diff_write(self, settings):
        view = sublime.View(content='diff\n')
        view.sel().add(sublime.Region(3))
        count = view.change_count()
        GitDiffWriteCommand(view).run(None, content='diff\n', row=0, col=0)
        assert view.change_count() == count
        assert list(view.sel()) == [sublime.Region(3)]

    def test_diff_write_with_run_move_still_moves(self, settings):
        view = sublime.View(content='diff\n')
        GitDiffWriteCommand(view).run(None, content='diff\n', run_move=True)
        assert view.commands == [('git_diff_move', None)]

    def test_log_write(self, settings):
        view = sublime.View(content='log\n')
        count = view.change_count()
        GitLogGraphWriteCommand(view).run(None, content='log\n', point=2)
        GitLogWriteCommand(view).run(None, content='log\n')
        assert view.change_count() == count


class TestShowOffTheUiThread(object):

    def test_show_is_gathered_in_the_worker(self, settings, tmp_repo, inline_threads, flush):
        sha = tmp_repo.commit('a.txt', 'a\n', 'the subject')
        view = sublime.View(settings={'git_repo': tmp_repo.path, 'git_show_obj': sha})
        GitShowRefreshCommand(view).run(None)
        assert view.size() == 0
        flush()
        [(name, args)] = view.commands
        assert name == 'git_log_write'
        GitLogWriteCommand(view).run(None, **args)
        assert 'the subject' in view.substr(sublime.Region(0, view.size()))


class TestUntrackedDiff(object):

    class Diff(GitCmd, GitDiffHelper):
        pass

    def test_no_git_process_per_file(self, settings, tmp_repo, popens):
        tmp_repo.commit('a.txt', 'a\n')
        for i in range(20):
            tmp_repo.write('new/f%d.txt' % i, 'line %d\n' % i)
        del popens[:]

        diff = self.Diff().get_diff(tmp_repo.path, 'new')

        assert [a[0] for a in git_args(popens)] == ['diff', 'ls-files']
        assert diff.count('diff --git') == 20

    def test_matches_git_byte_for_byte(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        files = {
            'plain.txt': b'one\ntwo\n',
            'single.txt': b'x\n',
            'no-newline.txt': b'last',
            'empty.txt': b'',
            'crlf.txt': b'a\r\nb\r\n',
            'utf8.txt': u'caf\xe9\n'.encode('utf-8'),
            'dir with space/f.txt': b'spaced\n',
        }
        for name, data in files.items():
            full = os.path.join(tmp_repo.path, name)
            if not os.path.isdir(os.path.dirname(full)):
                os.makedirs(os.path.dirname(full))
            with open(full, 'wb') as f:
                f.write(data)
        script = tmp_repo.write('run.sh', '#!/bin/sh\n')
        os.chmod(script, 0o755)

        for name in list(files) + ['run.sh']:
            # raw bytes: the conftest helper's text mode would turn \r\n into \n
            expected = subprocess.run([GIT, 'diff', '--no-index', '--', os.devnull, name],
                                      cwd=tmp_repo.path, capture_output=True).stdout.decode('utf-8')
            assert new_file_diff(tmp_repo.path, name, 'utf-8') == expected, name

    def test_files_git_must_handle_fall_back_to_git(self, settings, tmp_repo):
        with open(os.path.join(tmp_repo.path, 'bin.dat'), 'wb') as f:
            f.write(b'\x00\x01\x02')
        tmp_repo.write(u'caf\xe9.txt', 'x\n')
        os.symlink('bin.dat', os.path.join(tmp_repo.path, 'link'))
        assert new_file_diff(tmp_repo.path, 'bin.dat', 'utf-8') is None
        assert new_file_diff(tmp_repo.path, u'caf\xe9.txt', 'utf-8') is None
        assert new_file_diff(tmp_repo.path, 'link', 'utf-8') is None

        diff = self.Diff().get_diff(tmp_repo.path, 'bin.dat')
        assert 'Binary files /dev/null and b/bin.dat differ' in diff
