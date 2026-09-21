# coding: utf-8
import os

import pytest
import sublime

from sgit.util import (find_view_by_settings, abbreviate_dir, get_setting, get_executable,
                       get_user_dir, StatusSpinner, noop)


class TestFindViewBySettings(object):

    def test_returns_first_view_matching_all_settings(self):
        v1 = sublime.View(settings={'git_view': 'status', 'git_repo': '/a'})
        v2 = sublime.View(settings={'git_view': 'status', 'git_repo': '/b'})
        v3 = sublime.View(settings={'git_view': 'status', 'git_repo': '/b'})
        window = sublime.Window(views=[v1, v2, v3])

        assert find_view_by_settings(window, git_view='status', git_repo='/b') is v2

    def test_returns_none_when_no_view_matches(self):
        v1 = sublime.View(settings={'git_view': 'status'})
        window = sublime.Window(views=[v1])

        assert find_view_by_settings(window, git_view='diff') is None

    def test_missing_setting_matches_none_value(self):
        # a view without the key has settings().get(key) == None, so kwargs of None match
        v1 = sublime.View(settings={'git_view': 'blame'})
        window = sublime.Window(views=[v1])

        assert find_view_by_settings(window, git_view='blame', git_blame_rev=None) is v1

    def test_no_kwargs_returns_first_view(self):
        v1 = sublime.View()
        window = sublime.Window(views=[v1, sublime.View()])

        assert find_view_by_settings(window) is v1


class TestAbbreviateDir(object):

    def test_replaces_home_prefix_with_tilde(self):
        home = get_user_dir()
        assert home == os.path.expanduser('~')
        assert abbreviate_dir(os.path.join(home, 'projects', 'x')) == '~' + os.sep + os.path.join('projects', 'x')

    def test_leaves_other_paths_alone(self):
        assert abbreviate_dir('/private/tmp/somewhere') == '/private/tmp/somewhere'

    def test_non_string_is_returned_unchanged(self):
        assert abbreviate_dir(None) is None


class TestSettings(object):

    def test_get_setting_default_when_missing(self, settings):
        assert get_setting('nope') is None
        assert get_setting('nope', 'dflt') == 'dflt'

    def test_get_setting_reads_sublimegit_settings_file(self, settings):
        settings.set('encoding', 'latin-1')
        assert get_setting('encoding', 'utf-8') == 'latin-1'

    def test_get_executable_defaults(self, settings):
        assert get_executable('git', ['git']) == ['git']

    def test_get_executable_from_git_executables_setting(self, settings):
        settings.set('git_executables', {'git': ['/usr/local/bin/git'], 'legit': ['legit']})
        assert get_executable('git', ['git']) == ['/usr/local/bin/git']
        assert get_executable('git_flow', ['git-flow']) == ['git-flow']


class FakeThread(object):
    def __init__(self, alive=True):
        self.alive = alive
        self.started = False

    def is_alive(self):
        return self.alive

    def start(self):
        self.started = True


class TestStatusSpinner(object):

    def test_progress_bounces_between_ends(self):
        spinner = StatusSpinner(FakeThread(alive=True), 'Working')

        seen = []
        for _ in range(20):
            spinner.progress()
            seen.append(sublime.status_messages[-1])

        assert seen[0] == '[=         ] Working'
        assert seen[1] == '[ =        ] Working'
        assert seen[9] == '[         =] Working'
        assert seen[10] == '[        = ] Working'
        assert seen[18] == '[=         ] Working'
        assert seen[19] == '[ =        ] Working'
        # every call re-schedules itself
        assert len(sublime.pending_timeouts()) == 20
        assert all(fn == spinner.progress for fn in sublime.pending_timeouts())

    def test_progress_clears_status_and_stops_when_thread_finished(self):
        spinner = StatusSpinner(FakeThread(alive=False), 'Working')
        spinner.progress()

        assert sublime.status_messages == ['']
        assert sublime.pending_timeouts() == []

    def test_start_starts_thread_and_schedules_progress(self, flush):
        thread = FakeThread(alive=True)
        spinner = StatusSpinner(thread, 'Pushing')
        spinner.start()

        assert thread.started
        assert sublime.pending_timeouts() == [spinner.progress]
        thread.alive = False
        flush()
        assert sublime.status_messages == ['']

    def test_noop_accepts_anything(self):
        assert noop(1, 2, x=3) is None
