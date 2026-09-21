# coding: utf-8
"""Tests for the graph log view (``Git: Log``) in ``sgit/log.py``."""
import pytest

import sublime
import sgit.status

from conftest import requires_git
from sgit.log import (GitLogCommand, GitLogGraphRefreshCommand, GitLogGraphWriteCommand,
                      GitLogGraphShowCommand, GitLogGraphEventListener)


@pytest.fixture
def inline_threads(monkeypatch):
    """Make ``run_in_thread`` run the worker body synchronously."""
    monkeypatch.setattr(sgit.status, 'run_in_thread', lambda fn: fn())


def run_write_commands(view):
    ran = 0
    for name, args in list(view.commands):
        if name != 'git_log_graph_write':
            continue
        view.commands.remove((name, args))
        GitLogGraphWriteCommand(view).run(None, **(args or {}))
        ran += 1
    return ran


def refresh(cmd, flush):
    cmd.run(None)
    flush()
    return run_write_commands(cmd.view)


def log_view(repo, content=''):
    return sublime.View(settings={'git_view': 'log-graph', 'git_repo': repo.path}, content=content)


class TestParseLine(object):
    parse = staticmethod(GitLogGraphShowCommand.parse_line)

    def test_plain_and_graph_prefixed_lines(self):
        assert self.parse('* 32f3e53 - (2 minutes ago) subject - author (HEAD -> main)') == '32f3e53'
        assert self.parse('| * 2f14206 - (17 minutes ago) subject - author') == '2f14206'
        assert self.parse('| | * deadbee - (1 year ago) subject - author') == 'deadbee'
        assert self.parse('*   a1b2c3d - (3 hours ago) Merge branch - author') == 'a1b2c3d'

    def test_full_hash(self):
        sha = '0123456789abcdef0123456789abcdef01234567'
        assert self.parse('* %s - (now) s - a' % sha) == sha

    def test_graph_only_and_junk_lines(self):
        assert self.parse('|/  ') is None
        assert self.parse('|\\  ') is None
        assert self.parse('| |') is None
        assert self.parse('') is None
        assert self.parse('No commits yet') is None
        assert self.parse('fatal: not a git repository') is None

    def test_hex_words_in_subject_do_not_confuse(self):
        # the hash is the first token after the graph, followed by " - ("
        line = '* 1234567 - (now) fix deadbeef - cafe - a'
        assert self.parse(line) == '1234567'


class TestShowCommand(object):

    def select_rows(self, view, rows):
        view.sel().clear()
        for row in rows:
            view.sel().add(sublime.Region(view.text_point(row, 0)))

    def make_view(self):
        window = sublime.Window()
        content = ('* aaaaaaa - (now) one - me (HEAD -> main)\n'
                   '* bbbbbbb - (now) two - me\n'
                   '|\\  \n'
                   '| * ccccccc - (now) three - me\n'
                   '|/  \n'
                   '* ddddddd - (now) four - me\n')
        view = sublime.View(window=window, settings={'git_view': 'log-graph', 'git_repo': '/repo'},
                            content=content)
        return window, view

    def test_enter_on_a_commit_line_runs_git_show(self):
        window, view = self.make_view()
        self.select_rows(view, [1])
        GitLogGraphShowCommand(view).run(None)
        assert window.commands == [('git_show', {'repo': '/repo', 'obj': 'bbbbbbb'})]

    def test_graph_only_line_is_ignored(self):
        window, view = self.make_view()
        self.select_rows(view, [2])
        GitLogGraphShowCommand(view).run(None)
        assert window.commands == []
        assert sublime.error_messages == ['No commits selected.']

    def test_multi_line_selection_opens_each_commit_once(self):
        window, view = self.make_view()
        view.sel().clear()
        view.sel().add(sublime.Region(view.text_point(0, 0), view.text_point(3, 5)))
        view.sel().add(sublime.Region(view.text_point(3, 0)))  # ccccccc again
        GitLogGraphShowCommand(view).run(None)
        assert [a['obj'] for _, a in window.commands] == ['aaaaaaa', 'bbbbbbb', 'ccccccc']

    def test_more_than_five_commits_asks_first(self, settings):
        window = sublime.Window()
        content = ''.join('* %s - (now) s - a\n' % (str(i) * 7) for i in range(6))
        view = sublime.View(window=window, settings={'git_view': 'log-graph', 'git_repo': '/repo'},
                            content=content)
        view.sel().clear()
        view.sel().add(sublime.Region(0, view.size()))

        sublime.ok_cancel_answers.append(False)
        GitLogGraphShowCommand(view).run(None)
        assert window.commands == []
        assert len(sublime.ok_cancel_dialogs) == 1

        sublime.ok_cancel_answers.append(True)
        GitLogGraphShowCommand(view).run(None)
        assert len(window.commands) == 6
        assert len(sublime.ok_cancel_dialogs) == 2

        settings.set('git_blame_warn_multiple_tabs', False)
        window.commands = []
        GitLogGraphShowCommand(view).run(None)
        assert len(window.commands) == 6
        assert len(sublime.ok_cancel_dialogs) == 2


@requires_git
class TestLogGraphRefresh(object):

    def test_writes_the_graph_log_and_stays_read_only(self, tmp_repo, inline_threads, flush):
        sha1 = tmp_repo.commit('f.txt', 'one\n', message='first - with dash')
        sha2 = tmp_repo.commit('f.txt', 'two\n', message='second')
        view = log_view(tmp_repo)

        assert refresh(GitLogGraphRefreshCommand(view), flush) == 1

        lines = view.substr(sublime.Region(0, view.size())).splitlines()
        assert lines[0].startswith('* %s - (' % sha2[:7])
        assert lines[0].endswith(') second - Test User (HEAD -> main)')
        assert lines[1].startswith('* %s - (' % sha1[:7])
        assert lines[1].endswith(') first - with dash - Test User')
        assert view.is_read_only()
        assert [r.begin() for r in view.sel()] == [0]

    def test_refresh_keeps_the_caret(self, tmp_repo, inline_threads, flush):
        tmp_repo.commit('f.txt', 'one\n', message='first')
        tmp_repo.commit('f.txt', 'two\n', message='second')
        view = log_view(tmp_repo)
        refresh(GitLogGraphRefreshCommand(view), flush)

        second_line = view.text_point(1, 0)
        view.sel().clear()
        view.sel().add(sublime.Region(second_line))
        refresh(GitLogGraphRefreshCommand(view), flush)
        assert [r.begin() for r in view.sel()] == [second_line]

    def test_empty_repo(self, tmp_repo, inline_threads, flush):
        view = log_view(tmp_repo)
        refresh(GitLogGraphRefreshCommand(view), flush)
        assert view.substr(sublime.Region(0, view.size())) == 'No commits yet\n'

    def test_ignores_views_of_other_kinds(self, tmp_repo, inline_threads, flush):
        view = sublime.View(settings={'git_view': 'status', 'git_repo': tmp_repo.path})
        assert refresh(GitLogGraphRefreshCommand(view), flush) == 0

    def test_show_command_uses_the_repo_of_the_view(self, tmp_repo, inline_threads, flush):
        sha = tmp_repo.commit('f.txt', 'one\n', message='first')
        window = sublime.Window()
        view = sublime.View(window=window, settings={'git_view': 'log-graph', 'git_repo': tmp_repo.path})
        refresh(GitLogGraphRefreshCommand(view), flush)
        view.sel().clear()
        view.sel().add(sublime.Region(0))
        GitLogGraphShowCommand(view).run(None)
        assert window.commands == [('git_show', {'repo': tmp_repo.path, 'obj': sha[:7]})]


@requires_git
class TestLogCommand(object):

    def test_creates_a_view_once_and_refreshes_it(self, tmp_repo):
        window = sublime.Window(folders=[tmp_repo.path])
        GitLogCommand(window).run()
        views = window.views()
        assert len(views) == 1
        view = views[0]
        assert view.name() == '*git-log*: ' + tmp_repo.name
        assert view.settings().get('git_view') == 'log-graph'
        assert view.settings().get('git_repo') == tmp_repo.path
        assert view.settings().get('word_wrap') is False
        assert view.is_read_only()
        assert view.commands == [('git_log_graph_refresh', None)]

        GitLogCommand(window).run()
        assert len(window.views()) == 1
        assert view.commands == [('git_log_graph_refresh', None)] * 2


class TestEventListener(object):

    def test_pre_close_forgets_refresh_state(self, monkeypatch):
        forgotten = []
        monkeypatch.setattr('sgit.log.forget_view_refresh', forgotten.append)
        view = sublime.View(settings={'git_view': 'log-graph'})
        GitLogGraphEventListener().on_pre_close(view)
        assert forgotten == [view.id()]
        GitLogGraphEventListener().on_pre_close(sublime.View(settings={'git_view': 'status'}))
        assert forgotten == [view.id()]
