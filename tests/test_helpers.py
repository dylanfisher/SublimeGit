# coding: utf-8
import os

import pytest
import sublime

from sgit.helpers import (GitRepoHelper, GitBranchHelper, GitRemoteHelper, GitStashHelper,
                          GitErrorHelper, GitStatusHelper, GitLogHelper, GitTagHelper, GIT_INIT_DIALOG)


# ---------------------------------------------------------------------------
# fake git layer so helper parsing can be tested without a git binary
# ---------------------------------------------------------------------------

class FakeGit(object):
    def __init__(self, string='', lines=None, exit_code=0):
        self._string = string
        self._lines = lines or []
        self._exit_code = exit_code
        self.calls = []

    def git_string(self, cmd, *args, **kwargs):
        self.calls.append(('string', cmd, kwargs))
        return self._string

    def git_lines(self, cmd, *args, **kwargs):
        self.calls.append(('lines', cmd, kwargs))
        return self._lines

    def git_exit_code(self, cmd, *args, **kwargs):
        self.calls.append(('exit', cmd, kwargs))
        return self._exit_code


class Branches(FakeGit, GitBranchHelper):
    pass


class Remotes(FakeGit, GitRemoteHelper):
    pass


class Stashes(FakeGit, GitStashHelper):
    pass


class Status(FakeGit, GitStatusHelper):
    pass


class Log(FakeGit, GitLogHelper):
    pass


class Tags(FakeGit, GitTagHelper):
    pass


def mkrepo(path):
    """Create a fake repo dir with a .git directory and return its realpath."""
    os.makedirs(os.path.join(str(path), '.git'))
    return os.path.realpath(str(path))


def mkdir(path):
    os.makedirs(str(path))
    return os.path.realpath(str(path))


# ---------------------------------------------------------------------------
# GitRepoHelper
# ---------------------------------------------------------------------------

class TestRepoDiscovery(object):

    def test_is_git_repo_checks_for_dot_git(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        plain = mkdir(tmp_path / 'plain')
        h = GitRepoHelper()
        assert h.is_git_repo(repo)
        assert not h.is_git_repo(plain)

    def test_first_git_repo_returns_dir_itself(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        assert GitRepoHelper().first_git_repo(repo) == repo

    def test_first_git_repo_walks_up(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        sub = mkdir(tmp_path / 'repo' / 'a' / 'b')
        assert GitRepoHelper().first_git_repo(sub) == repo

    def test_first_git_repo_prefers_closest_nested_repo(self, tmp_path):
        mkrepo(tmp_path / 'outer')
        inner = mkrepo(tmp_path / 'outer' / 'inner')
        deep = mkdir(tmp_path / 'outer' / 'inner' / 'x')
        assert GitRepoHelper().first_git_repo(deep) == inner

    def test_first_git_repo_none_when_nothing_found(self, tmp_path):
        plain = mkdir(tmp_path / 'plain' / 'deeper')
        assert GitRepoHelper().first_git_repo(plain) is None

    def test_all_dirnames_walks_to_root(self):
        assert GitRepoHelper().all_dirnames('/a/b/c') == ['/a/b/c', '/a/b', '/a', '/']

    def test_find_git_repos_collects_every_repo_up_each_tree(self, tmp_path):
        outer = mkrepo(tmp_path / 'outer')
        inner = mkrepo(tmp_path / 'outer' / 'inner')
        deep = mkdir(tmp_path / 'outer' / 'inner' / 'x')
        other = mkrepo(tmp_path / 'other')
        plain = mkdir(tmp_path / 'plain')

        repos = GitRepoHelper().find_git_repos([deep, other, plain])
        assert repos == {outer, inner, other}


class TestDirsFromWindow(object):

    def test_get_dir_from_view_is_realpath_of_file_dir(self, tmp_path):
        d = mkdir(tmp_path / 'd')
        view = sublime.View(file_name=os.path.join(str(tmp_path), 'd', 'f.txt'))
        assert GitRepoHelper().get_dir_from_view(view) == d

    def test_get_dir_from_view_none_for_unsaved_or_missing_view(self):
        h = GitRepoHelper()
        assert h.get_dir_from_view(None) is None
        assert h.get_dir_from_view(sublime.View(file_name=None)) is None

    def test_get_dirs_merges_folders_and_view_dirs(self, tmp_path):
        d = mkdir(tmp_path / 'd')
        view = sublime.View(file_name=os.path.join(d, 'f.txt'))
        window = sublime.Window(folders=['/folder/one'], views=[view, sublime.View()])
        assert GitRepoHelper().get_dirs(window) == {'/folder/one', d}
        assert GitRepoHelper().get_dirs(None) == set()

    def test_get_dirs_prioritized_active_view_first_then_longest(self, tmp_path):
        a = mkdir(tmp_path / 'a')
        long_dir = mkdir(tmp_path / 'a' / 'much' / 'longer' / 'path')
        mid = mkdir(tmp_path / 'a' / 'mid')
        active = sublime.View(file_name=os.path.join(a, 'f.txt'))
        v2 = sublime.View(file_name=os.path.join(long_dir, 'g.txt'))
        window = sublime.Window(folders=[mid, a], views=[active, v2], active_view=active)

        assert GitRepoHelper().get_dirs_prioritized(window) == [a, long_dir, mid]

    def test_get_dirs_prioritized_without_active_file(self, tmp_path):
        a = mkdir(tmp_path / 'a')
        window = sublime.Window(folders=[a], active_view=sublime.View())
        assert GitRepoHelper().get_dirs_prioritized(window) == [a]
        assert GitRepoHelper().get_dirs_prioritized(None) == []


class TestGetRepoFromView(object):

    def test_none_view(self):
        assert GitRepoHelper().get_repo_from_view(None) is None

    def test_view_settings_take_precedence(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        view = sublime.View(file_name=os.path.join(repo, 'f.txt'), settings={'git_repo': '/from/settings'})
        assert GitRepoHelper().get_repo_from_view(view) == '/from/settings'

    def test_falls_back_to_file_location(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        sub = mkdir(tmp_path / 'repo' / 'sub')
        view = sublime.View(file_name=os.path.join(sub, 'f.txt'))
        assert GitRepoHelper().get_repo_from_view(view) == repo

    def test_none_when_file_not_in_repo(self, tmp_path):
        plain = mkdir(tmp_path / 'plain')
        view = sublime.View(file_name=os.path.join(plain, 'f.txt'))
        assert GitRepoHelper().get_repo_from_view(view) is None


class TestGetRepoFromWindow(object):

    # GitRepoHelper.windows is reset for every test by the conftest autouse fixture

    def test_no_window(self):
        assert GitRepoHelper().get_repo_from_window(None) is None

    def test_active_view_setting_wins(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        active = sublime.View(settings={'git_repo': '/from/settings'})
        window = sublime.Window(folders=[repo], active_view=active)
        assert GitRepoHelper().get_repo_from_window(window) == '/from/settings'

    def test_active_view_file_wins_over_folders(self, tmp_path):
        repo_a = mkrepo(tmp_path / 'a')
        repo_b = mkrepo(tmp_path / 'b')
        active = sublime.View(file_name=os.path.join(repo_b, 'f.txt'))
        window = sublime.Window(folders=[repo_a], views=[active], active_view=active)
        assert GitRepoHelper().get_repo_from_window(window) == repo_b

    def test_single_repo_from_folders(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        sub = mkdir(tmp_path / 'repo' / 'sub')
        window = sublime.Window(folders=[sub], active_view=sublime.View())
        assert GitRepoHelper().get_repo_from_window(window) == repo

    def test_multiple_repos_uses_window_repository(self, tmp_path):
        repo_a = mkrepo(tmp_path / 'a')
        repo_b = mkrepo(tmp_path / 'b')
        window = sublime.Window(folders=[repo_a, repo_b])
        h = GitRepoHelper()
        h.set_window_repository(window, repo_b)
        assert h.get_window_repository(window) == repo_b
        assert h.get_repo_from_window(window) == repo_b

    def test_multiple_repos_silent_returns_none(self, tmp_path):
        repo_a = mkrepo(tmp_path / 'a')
        repo_b = mkrepo(tmp_path / 'b')
        window = sublime.Window(folders=[repo_a, repo_b])
        assert GitRepoHelper().get_repo_from_window(window, silent=True) is None
        assert window.commands == []

    def test_multiple_repos_not_silent_runs_switch_repo(self, tmp_path):
        repo_a = mkrepo(tmp_path / 'a')
        repo_b = mkrepo(tmp_path / 'b')
        window = sublime.Window(folders=[repo_a, repo_b])
        assert GitRepoHelper().get_repo_from_window(window, silent=False) is None
        assert window.commands == [('git_switch_repo', None)]

    def test_no_repos_not_silent_offers_init(self, tmp_path):
        plain = mkdir(tmp_path / 'plain')
        window = sublime.Window(folders=[plain])
        sublime.ok_cancel_answers.append(True)
        assert GitRepoHelper().get_repo_from_window(window, silent=False) is None
        assert sublime.ok_cancel_dialogs == [(GIT_INIT_DIALOG, 'Initialize repository')]
        assert window.commands == [('git_init', None)]

    def test_no_repos_not_silent_init_declined(self, tmp_path):
        plain = mkdir(tmp_path / 'plain')
        window = sublime.Window(folders=[plain])
        sublime.ok_cancel_answers.append(False)
        assert GitRepoHelper().get_repo_from_window(window, silent=False) is None
        assert window.commands == []

    def test_get_repo_uses_view_then_window(self, tmp_path):
        repo = mkrepo(tmp_path / 'repo')
        window = sublime.Window(folders=[repo])

        class TextCmd(GitRepoHelper):
            def __init__(self, view):
                self.view = view

        class WindowCmd(GitRepoHelper):
            def __init__(self, window):
                self.window = window

        assert TextCmd(sublime.View(window=window)).get_repo() == repo
        assert TextCmd(sublime.View(settings={'git_repo': '/v'}, window=window)).get_repo() == '/v'
        assert WindowCmd(window).get_repo() == repo


# ---------------------------------------------------------------------------
# GitBranchHelper
# ---------------------------------------------------------------------------

class TestBranchHelper(object):

    def test_get_current_branch_strips_refs_heads(self):
        b = Branches(string='refs/heads/feature/x')
        assert b.get_current_branch('/repo') == 'feature/x'
        assert b.calls == [('string', ['symbolic-ref', '-q', 'HEAD'], {'cwd': '/repo'})]

    def test_get_current_branch_passes_other_values_through(self):
        assert Branches(string='').get_current_branch('/repo') == ''
        assert Branches(string='refs/tags/v1').get_current_branch('/repo') == 'refs/tags/v1'

    def test_get_branches_parses_current_marker_and_symrefs(self):
        b = Branches(lines=['* main', '  develop', '  origin/HEAD -> origin/main', '  origin/main'])
        assert b.get_branches('/repo') == [(True, 'main'), (False, 'develop'),
                                           (False, 'origin/main'), (False, 'origin/main')]
        assert b.calls[0][1] == ['branch', '--list', '--no-color', None]

    def test_get_branches_remotes_flag(self):
        b = Branches(lines=[])
        assert b.get_branches('/repo', remotes=True) == []
        assert b.calls[0][1] == ['branch', '--list', '--no-color', '--remotes']


# ---------------------------------------------------------------------------
# GitRemoteHelper
# ---------------------------------------------------------------------------

REMOTE_LINES = [
    'origin\thttps://example.com/a.git (fetch)',
    'origin\thttps://example.com/a.git (push)',
    'upstream\tgit@example.com:b.git (fetch)',
    'upstream\tgit@example.com:b-push.git (push)',
    'fetchonly\thttps://example.com/c.git (fetch)',
]


class TestRemoteHelper(object):

    def test_get_remotes_runs_remote_v(self):
        r = Remotes(lines=REMOTE_LINES)
        assert r.get_remotes('/repo') == REMOTE_LINES
        assert r.calls == [('lines', ['remote', '-v'], {'cwd': '/repo'})]

    @pytest.mark.xfail(strict=True, raises=AttributeError,
                       reason='get_remote_names calls .append on a set (bug); intended: sorted unique names')
    def test_get_remote_names_sorted_unique(self):
        assert Remotes().get_remote_names(REMOTE_LINES) == ['fetchonly', 'origin', 'upstream']

    def test_format_quick_remotes(self):
        assert Remotes().format_quick_remotes(REMOTE_LINES) == [
            ['origin', 'https://example.com/a.git (fetch)', 'https://example.com/a.git (push)'],
            ['upstream', 'git@example.com:b.git (fetch)', 'git@example.com:b-push.git (push)'],
            ['fetchonly', 'https://example.com/c.git (fetch)', None],
        ]

    def test_format_quick_remotes_empty(self):
        assert Remotes().format_quick_remotes([]) == []

    def test_get_remote_branches_filters_by_remote_prefix(self):
        r = Remotes(lines=['  origin/HEAD -> origin/main', '  origin/main', '  origin/feat/x', '  upstream/main'])
        assert r.get_remote_branches('/repo', 'origin') == ['origin/main', 'origin/main', 'origin/feat/x']
        assert r.get_remote_branches('/repo', 'upstream') == ['upstream/main']
        assert r.calls[0][1] == ['branch', '--list', '--no-color', '--remotes']

    def test_format_quick_branches_strips_remote(self):
        assert Remotes().format_quick_branches(['origin/main', 'origin/feat/x']) == [
            ['main', 'origin/main'], ['feat/x', 'origin/feat/x']]

    def test_config_lookups(self):
        r = Remotes(string='origin')
        assert r.get_remote_url('/repo', 'origin') == 'origin'
        assert r.get_branch_upstream('/repo', 'main') == ('origin', 'origin')
        assert [c[1] for c in r.calls] == [['config', 'remote.origin.url'],
                                           ['config', 'branch.main.remote'],
                                           ['config', 'branch.main.merge']]


# ---------------------------------------------------------------------------
# GitStashHelper / GitErrorHelper
# ---------------------------------------------------------------------------

class TestStashHelper(object):

    def test_get_stashes_parses_name_and_title(self):
        s = Stashes(lines=['stash@{0}: WIP on main: 1234567 message',
                           'stash@{1}: On feature: named stash',
                           'garbage line'])
        assert s.get_stashes('/repo') == [('0', 'WIP on main: 1234567 message'),
                                          ('1', 'On feature: named stash')]
        assert s.calls == [('lines', ['stash', 'list'], {'cwd': '/repo'})]

    def test_get_stashes_empty(self):
        assert Stashes(lines=[]).get_stashes('/repo') == []


class TestErrorHelper(object):

    def test_strips_error_prefix(self):
        assert GitErrorHelper().format_error_message('error: something bad\n') == 'something bad\n'

    def test_strips_trailing_aborting(self):
        msg = 'error: Your local changes would be overwritten\nAborting\n'
        assert GitErrorHelper().format_error_message(msg) == 'Your local changes would be overwritten\n'

    def test_note_prefix_is_never_stripped(self):
        # `msg.lower().startswith('Note: ')` can never match a lower-cased string
        assert GitErrorHelper().format_error_message('Note: keep me') == 'Note: keep me'
        assert GitErrorHelper().format_error_message('note: keep me') == 'note: keep me'

    def test_other_messages_untouched(self):
        assert GitErrorHelper().format_error_message('fatal: nope') == 'fatal: nope'


# ---------------------------------------------------------------------------
# GitStatusHelper
# ---------------------------------------------------------------------------

class TestStatusHelper(object):

    def test_untracked_mode(self, settings):
        s = Status()
        assert s.get_untracked_mode() == 'all'
        settings.set('git_status_untracked_files', 'none')
        assert s.get_untracked_mode() == 'no'
        settings.set('git_status_untracked_files', 'auto')
        assert s.get_untracked_mode() is None
        settings.set('git_status_untracked_files', 'normal')
        assert s.get_untracked_mode() == 'all'

    def test_get_porcelain_status_parses_nul_separated_output(self, settings):
        raw = '\x00'.join([' M modified.txt', 'R  new name.txt', 'old name.txt',
                           'A  added.txt', '?? untracked.txt', 'MM both.txt', '']) 
        s = Status(string=raw)
        assert s.get_porcelain_status('/repo') == [
            ' M modified.txt',
            'R  old name.txt -> new name.txt',
            'A  added.txt',
            '?? untracked.txt',
            'MM both.txt',
        ]
        assert s.calls == [('string', ['status', '-z', '--untracked-files=all'],
                            {'cwd': '/repo', 'strip': False})]

    def test_get_porcelain_status_untracked_mode_auto_omits_flag(self, settings):
        settings.set('git_status_untracked_files', 'auto')
        s = Status(string='')
        assert s.get_porcelain_status('/repo') == []
        assert s.calls[0][1] == ['status', '-z', None]

    def test_get_porcelain_status_skips_comment_rows(self, settings):
        s = Status(string='# branch.head main\x00 M a.txt\x00')
        assert s.get_porcelain_status('/repo') == [' M a.txt']

    def test_get_files_status_classification(self, settings):
        class S(GitStatusHelper):
            def get_porcelain_status(self, repo):
                return [' M unstaged.txt', 'M  staged.txt', 'MM both.txt', 'A  added.txt', ' D deleted.txt',
                        'D  staged-del.txt', 'R  old.txt -> new.txt', '?? untracked.txt', '!! ignored.txt',
                        'UU conflict.txt', 'AA both-added.txt', 'T  typechange.txt']

        untracked, unstaged, staged = S().get_files_status('/repo')
        assert untracked == [('?', 'untracked.txt')]
        assert unstaged == [('M', 'unstaged.txt'), ('M', 'both.txt'), ('D', 'deleted.txt')]
        assert staged == [('M', 'staged.txt'), ('M', 'both.txt'), ('A', 'added.txt'), ('D', 'staged-del.txt'),
                          ('R', 'old.txt -> new.txt'), ('T', 'typechange.txt')]

    def test_change_predicates_use_exit_codes(self, settings):
        s = Status(exit_code=1)
        assert s.has_staged_changes('/repo') is True
        assert s.has_unstaged_changes('/repo') is True
        assert s.has_changes('/repo') is True
        assert s.file_in_git('/repo', 'f') is False
        assert [c[1] for c in s.calls[:2]] == [['diff', '--exit-code', '--quiet', '--cached'],
                                               ['diff', '--exit-code', '--quiet']]
        assert s.calls[-1][1] == ['ls-files', 'f', '--error-unmatch']
        clean = Status(exit_code=0)
        assert clean.has_changes('/repo') is False
        assert clean.file_in_git('/repo', 'f') is True


# ---------------------------------------------------------------------------
# GitLogHelper / GitTagHelper
# ---------------------------------------------------------------------------

class TestLogHelper(object):

    def test_get_quick_log_splits_records(self):
        rec1 = '\x03'.join(['Subject one', 'a' * 40, 'Ann', 'ann@example.com', 'Mon Jan 1 10:00:00 2024', '2 days ago'])
        rec2 = '\x03'.join(['Subject two', 'b' * 40, 'Bob', 'bob@example.com', 'Sun Dec 31 09:00:00 2023', '3 days ago'])
        log = Log(string=rec1 + '\x04\n' + rec2 + '\x04\n')
        assert log.get_quick_log('/repo') == [rec1.split('\x03'), rec2.split('\x03')]
        cmd = log.calls[0][1]
        assert cmd[:3] == ['log', '--no-color', '--date=local']
        assert cmd[3] == '--format=' + GitLogHelper.GIT_QUICK_LOG_FORMAT
        assert log.calls[0][2] == {'cwd': '/repo', 'strip': False}

    def test_get_quick_log_path_and_follow(self):
        log = Log(string='')
        assert log.get_quick_log('/repo', path='f.txt', follow=True) == []
        assert log.calls[0][1][-3:] == ['--follow', '--', 'f.txt']

    def test_get_quick_log_raises_on_malformed_record(self):
        with pytest.raises(Exception):
            Log(string='only\x03two\x04').get_quick_log('/repo')

    def test_format_quick_log(self):
        log = [['Subject one', 'abcdef0123456789' + 'a' * 24, 'Ann', 'ann@example.com', 'Mon Jan 1', '2 days ago']]
        hashes, choices = GitLogHelper().format_quick_log(log)
        assert hashes == [log[0][1]]
        assert choices == [['Subject one', 'abcdef01 by Ann <ann@example.com>', '2 days ago (Mon Jan 1)']]


class TestTagHelper(object):

    def test_get_tags_commands(self):
        t = Tags(lines=['v1.0            first'])
        assert t.get_tags('/repo') == ['v1.0            first']
        assert t.get_tags('/repo', annotate=False) == ['v1.0            first']
        assert [c[1] for c in t.calls] == [['tag', '--list', '-n1'], ['tag', '--list', '-n0', '--no-column']]

    def test_format_quick_tags_reverses_and_strips_annotation(self):
        assert GitTagHelper().format_quick_tags(['v1.0            first', 'v2.0            second release']) == [
            ['v2.0', 'second release'], ['v1.0', 'first']]
