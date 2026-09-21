# coding: utf-8
from datetime import datetime

import pytest

import sublime
import sgit.status

from conftest import requires_git
from sgit.blame import (GitBlameCache, GitBlameRefreshCommand, GitBlameWriteCommand,
                        GitBlameEventListener)


@pytest.fixture
def inline_threads(monkeypatch):
    """Make ``run_in_thread`` run the worker body synchronously."""
    monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())


def run_write_commands(view):
    """Execute (and drop) the recorded ``git_blame_write`` commands of ``view``.
    Returns the number executed."""
    ran = 0
    for name, args in list(view.commands):
        if name != 'git_blame_write':
            continue
        view.commands.remove((name, args))
        GitBlameWriteCommand(view).run(None, **(args or {}))
        ran += 1
    return ran


def refresh(cmd, flush, **args):
    """Drive the two-phase blame refresh to completion: request, gather
    inline, flush the main-thread apply, run the recorded write command."""
    cmd.run(None, **args)
    flush()
    return run_write_commands(cmd.view)


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
        commits, lines, error = cmd.get_blame(tmp_repo.path, 'f.txt')

        assert error is None
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
        commits, lines, _ = GitBlameRefreshCommand(sublime.View()).get_blame(tmp_repo.path, 'f.txt', sha1)
        assert lines == [(sha1, 'one')]
        assert list(commits) == [sha1]


@requires_git
class TestBlameRefreshCommand(object):
    """Pins what ``git_blame_refresh`` puts in the view.

    The git work runs in a worker thread (``inline_threads`` runs it
    synchronously here); the buffer is written by the hidden
    ``git_blame_write`` command on the main thread.
    """

    def blame_view(self, repo, filename='f.txt', rev=None, content=''):
        return sublime.View(settings={'git_view': 'blame', 'git_repo': repo.path,
                                      'git_blame_file': filename, 'git_blame_rev': rev},
                            content=content)

    def test_run_writes_blame_and_fills_the_cache(self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\ntwo\n', message='first')
        view = self.blame_view(tmp_repo)

        assert refresh(GitBlameRefreshCommand(view), flush) == 1

        cmd = GitBlameRefreshCommand(view)
        commits, lines, _ = cmd.get_blame(tmp_repo.path, 'f.txt')
        assert view.substr(sublime.Region(0, view.size())) == cmd.format_blame(commits, lines)
        assert view.is_read_only()
        assert GitBlameCache.lines[view.id()] == lines
        assert set(GitBlameCache.commits[view.id()]) == set(commits)
        assert list(view.sel()) == [sublime.Region(0, 0)]

    def test_refresh_goes_through_request_refresh_and_writes_via_the_write_command(
            self, settings, tmp_repo, inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\ntwo\n', message='first')
        view = self.blame_view(tmp_repo, content='stale contents')

        GitBlameRefreshCommand(view).run(None)
        # the gather ran inline, the apply is parked in set_timeout
        assert view.substr(sublime.Region(0, view.size())) == 'stale contents'
        assert view.commands == []

        flush()
        assert [name for name, _ in view.commands] == ['git_blame_write']
        assert view.substr(sublime.Region(0, view.size())) == 'stale contents'

        run_write_commands(view)
        assert view.substr(sublime.Region(0, view.size())).endswith(') two')

    def test_selected_rows_are_marked_and_the_caret_is_placed(self, settings, tmp_repo,
                                                              inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\ntwo\nthree\n', message='first')
        view = self.blame_view(tmp_repo)

        refresh(GitBlameRefreshCommand(view), flush, rows=[1, 2])

        assert view.get_regions('git-blame.lines') == [view.line(view.text_point(1, 0)),
                                                       view.line(view.text_point(2, 0))]
        assert list(view.sel()) == [sublime.Region(view.text_point(1, 0))]

    def test_a_parse_error_is_reported_on_the_main_thread(self, settings, tmp_repo,
                                                          inline_threads, flush, monkeypatch):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        view = self.blame_view(tmp_repo, content='untouched')

        def boom(self, commitline):
            raise ValueError('nope')

        monkeypatch.setattr(GitBlameRefreshCommand, 'parse_commit_line', boom)

        assert refresh(GitBlameRefreshCommand(view), flush) == 0

        assert view.substr(sublime.Region(0, view.size())) == 'untouched'
        assert view.id() not in GitBlameCache.commits
        assert sublime.error_messages == ['Error parsing git blame output: nope']

    def test_an_empty_blame_does_not_raise(self, settings, tmp_repo, inline_threads, flush, monkeypatch):
        """No commits means ``format_blame`` would blow up on ``max()``."""
        view = self.blame_view(tmp_repo, content='untouched')
        monkeypatch.setattr(GitBlameRefreshCommand, 'get_blame',
                            lambda self, repo, filename, revision=None: ({}, [], None))

        assert refresh(GitBlameRefreshCommand(view), flush) == 0

        assert view.substr(sublime.Region(0, view.size())) == 'untouched'
        assert sublime.error_messages == []

    def test_a_gather_that_raises_does_not_leak(self, settings, tmp_repo, inline_threads, flush, monkeypatch):
        """``format_blame`` raising in the worker must not escape, and the view
        must stay usable for the next refresh."""
        tmp_repo.commit('f.txt', 'one\n', message='first')
        view = self.blame_view(tmp_repo, content='untouched')

        def boom(self, commits, lines):
            raise ValueError('nope')

        monkeypatch.setattr(GitBlameRefreshCommand, 'format_blame', boom)
        assert refresh(GitBlameRefreshCommand(view), flush) == 0
        assert view.substr(sublime.Region(0, view.size())) == 'untouched'

        monkeypatch.undo()  # also undoes inline_threads, so re-apply it
        monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())
        assert refresh(GitBlameRefreshCommand(view), flush) == 1
        assert view.substr(sublime.Region(0, view.size())).endswith(') one')

    def test_selection_modified_reports_the_commit_summary(self, settings, tmp_repo,
                                                          inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        tmp_repo.commit('f.txt', 'one\ntwo\n', message='second')
        view = self.blame_view(tmp_repo)
        refresh(GitBlameRefreshCommand(view), flush)

        view.sel().clear()
        view.sel().add(sublime.Region(view.text_point(1, 0)))
        GitBlameEventListener().on_selection_modified_async(view)

        assert sublime.status_messages[-1] == 'second'

    def test_selection_modified_ignores_non_blame_views(self, settings, tmp_repo):
        view = sublime.View(settings={'git_view': 'status'})
        GitBlameEventListener().on_selection_modified_async(view)
        assert sublime.status_messages == []

    def test_closing_a_blame_view_drops_its_cache_entries(self, settings, tmp_repo,
                                                          inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        view = self.blame_view(tmp_repo)
        refresh(GitBlameRefreshCommand(view), flush)
        assert view.id() in GitBlameCache.commits

        GitBlameEventListener().on_pre_close(view)

        assert view.id() not in GitBlameCache.commits
        assert view.id() not in GitBlameCache.lines

    def test_closing_a_blame_view_forgets_its_refresh_state(self, settings, tmp_repo,
                                                            inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        view = self.blame_view(tmp_repo)
        refresh(GitBlameRefreshCommand(view), flush)
        assert view.id() in sgit.status._refresh_state.generation

        GitBlameEventListener().on_pre_close(view)

        assert view.id() not in sgit.status._refresh_state.generation
