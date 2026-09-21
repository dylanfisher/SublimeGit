# coding: utf-8
from datetime import datetime

import pytest
import sublime

from conftest import requires_git
from sgit.blame import GitBlameCache, GitBlameRefreshCommand, GitBlameEventListener


class TestBlameCache(object):
    # GitBlameCache is reset for every test by the conftest autouse fixture

    def test_entries_are_class_level_and_keyed_by_view_id(self):
        view = sublime.View()
        GitBlameCache.commits[view.id()] = {'abc': {'sha': 'abc'}}
        GitBlameCache.lines[view.id()] = [('abc', 'line')]

        assert GitBlameCache().commits is GitBlameCache.commits
        assert GitBlameCache.commits.get(view.id()) == {'abc': {'sha': 'abc'}}
        assert GitBlameCache.lines.get(view.id()) == [('abc', 'line')]
        assert GitBlameCache.commits.get(sublime.View().id()) is None

    def test_starts_empty(self):
        assert GitBlameCache.commits == {}
        assert GitBlameCache.lines == {}


class TestParseCommitLine(object):

    def test_field_conversions(self):
        p = GitBlameRefreshCommand(sublime.View()).parse_commit_line
        assert p('author Ann Author') == ('author', 'Ann Author')
        assert p('author-mail <ann@example.com>') == ('author-mail', 'ann@example.com')
        assert p('author-time 1700000000') == ('author-time', 1700000000)
        assert p('committer-time 1700000001') == ('committer-time', 1700000001)
        assert p('previous 0123456789abcdef0123456789abcdef01234567 some/file.txt') == (
            'previous', {'commit': '0123456789abcdef0123456789abcdef01234567', 'file': 'some/file.txt'})
        assert p('boundary') == ('boundary', True)
        assert p('summary   trimmed  ') == ('summary', 'trimmed')
        assert p('filename') == ('filename', '')


@requires_git
class TestGetBlame(object):

    def test_get_blame_and_format(self, settings, tmp_repo):
        sha1 = tmp_repo.commit('f.txt', 'one\ntwo\n', message='first')
        sha2 = tmp_repo.commit('f.txt', 'one\ntwo\nthree\n', message='second')

        cmd = GitBlameRefreshCommand(sublime.View())
        commits, lines = cmd.get_blame(tmp_repo.path, 'f.txt')

        assert set(commits) == {sha1, sha2}
        assert lines == [(sha1, 'one'), (sha1, 'two'), (sha2, 'three')]
        assert commits[sha1]['abbrev'] == sha1[:7]
        assert commits[sha1]['author'] == 'Test User'
        assert commits[sha1]['author-mail'] == 'test@example.com'
        assert commits[sha1]['summary'] == 'first'
        assert commits[sha1]['filename'] == 'f.txt'
        assert commits[sha2]['previous'] == {'commit': sha1, 'file': 'f.txt'}
        assert isinstance(commits[sha1]['author-time'], int)

        text = cmd.format_blame(commits, lines)
        d1 = datetime.fromtimestamp(commits[sha1]['author-time']).strftime('%a %b %d %H:%M:%S %Y')
        d2 = datetime.fromtimestamp(commits[sha2]['author-time']).strftime('%a %b %d %H:%M:%S %Y')
        # the root commit is a boundary commit, so it is marked with '^' and
        # every other line gets a space to keep the columns aligned
        assert 'boundary' in commits[sha1]
        assert text.split('\n') == [
            '^%s (Test User  %s) one' % (sha1[:7], d1),
            '^%s (Test User  %s) two' % (sha1[:7], d1),
            ' %s (Test User  %s) three' % (sha2[:7], d2),
        ]

    def test_get_blame_at_revision(self, settings, tmp_repo):
        sha1 = tmp_repo.commit('f.txt', 'one\n', message='first')
        tmp_repo.commit('f.txt', 'one\ntwo\n', message='second')
        commits, lines = GitBlameRefreshCommand(sublime.View()).get_blame(tmp_repo.path, 'f.txt', sha1)
        assert lines == [(sha1, 'one')]
        assert list(commits) == [sha1]


@requires_git
class TestBlameRefreshCommand(object):

    def blame_view(self, repo, filename='f.txt', rev=None):
        return sublime.View(settings={'git_view': 'blame', 'git_repo': repo.path,
                                      'git_blame_file': filename, 'git_blame_rev': rev})

    def test_run_writes_blame_and_fills_the_cache(self, settings, tmp_repo):
        tmp_repo.commit('f.txt', 'one\ntwo\n', message='first')
        view = self.blame_view(tmp_repo)

        GitBlameRefreshCommand(view).run(None)

        cmd = GitBlameRefreshCommand(view)
        commits, lines = cmd.get_blame(tmp_repo.path, 'f.txt')
        assert view.substr(sublime.Region(0, view.size())) == cmd.format_blame(commits, lines)
        assert view.is_read_only()
        assert GitBlameCache.lines[view.id()] == lines
        assert set(GitBlameCache.commits[view.id()]) == set(commits)
        assert list(view.sel()) == [sublime.Region(0, 0)]

    def test_selection_modified_reports_the_commit_summary(self, settings, tmp_repo):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        tmp_repo.commit('f.txt', 'one\ntwo\n', message='second')
        view = self.blame_view(tmp_repo)
        GitBlameRefreshCommand(view).run(None)

        view.sel().clear()
        view.sel().add(sublime.Region(view.text_point(1, 0)))
        GitBlameEventListener().on_selection_modified(view)

        assert sublime.status_messages[-1] == 'second'

    def test_selection_modified_ignores_non_blame_views(self, settings, tmp_repo):
        view = sublime.View(settings={'git_view': 'status'})
        GitBlameEventListener().on_selection_modified(view)
        assert sublime.status_messages == []

    @pytest.mark.xfail(strict=True, raises=AttributeError, reason=(
        'GitBlameEventListener has no on_close hook, so GitBlameCache grows for '
        'the lifetime of the process. Refactor (e) adds the cleanup; when it '
        'lands this test should pass and the xfail marker must be removed.'))
    def test_closing_a_blame_view_drops_its_cache_entries(self, settings, tmp_repo):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        view = self.blame_view(tmp_repo)
        GitBlameRefreshCommand(view).run(None)
        assert view.id() in GitBlameCache.commits

        GitBlameEventListener().on_close(view)

        assert view.id() not in GitBlameCache.commits
        assert view.id() not in GitBlameCache.lines
