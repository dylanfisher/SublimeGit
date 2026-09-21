"""Quick panels that show multi-column rows as ``sublime.QuickPanelItem``.

The ``on_done(idx)`` contract is unchanged: the index into the item list must
keep mapping to the same underlying object (stash, tag, remote, branch, repo).
"""
import os

import sublime

from sgit.checkout import GitCheckoutRemoteBranchCommand
from sgit.helpers import KIND_STASH, KIND_TAG, KIND_BRANCH
from sgit.remote import GitFetchCommand, GitRemoteCommand
from sgit.repo import GitSwitchRepoCommand
from sgit.stash import GitStashPopCommand
from sgit.tag import GitTagCommand
from sgit.util import abbreviate_dir


def window_for(repo):
    return sublime.Window(folders=[repo.path], active_view=sublime.View())


def all_items(items):
    return all(isinstance(i, sublime.QuickPanelItem) for i in items)


class TestStashPanel(object):

    def test_rows_are_items_and_index_maps_to_the_stash(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.write('a.txt', 'first\n')
        tmp_repo.git('stash', 'push', '-q', '-m', 'first change')
        tmp_repo.write('a.txt', 'second\n')
        tmp_repo.git('stash', 'push', '-q', '-m', 'second change')

        window = window_for(tmp_repo)
        GitStashPopCommand(window).run()
        items, on_done = window.quick_panel

        assert all_items(items)
        assert [(i.trigger, i.annotation, i.kind) for i in items] == [
            ('On main: second change', 'stash@{0}', KIND_STASH),
            ('On main: first change', 'stash@{1}', KIND_STASH),
        ]

        on_done(1)  # pop the *older* stash
        assert tmp_repo.git('stash', 'list').splitlines() == ['stash@{0}: On main: second change']
        with open(os.path.join(tmp_repo.path, 'a.txt')) as f:
            assert f.read() == 'first\n'


class TestTagPanel(object):

    def test_tags_newest_first_then_add_tag_row(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('tag', '-a', 'v1.0', '-m', 'first release')
        tmp_repo.git('tag', '-a', 'v2.0', '-m', 'second release')

        window = window_for(tmp_repo)
        GitTagCommand(window).run(repo=tmp_repo.path)
        items, on_done = window.quick_panel

        assert all_items(items)
        assert [(i.trigger, i.details) for i in items] == [
            ('v2.0', ['second release']),
            ('v1.0', ['first release']),
            (GitTagCommand.ADD_TAG, ['Add a tag referencing the current commit.']),
        ]
        assert items[0].kind == KIND_TAG

        on_done(2)
        assert window.commands == [('git_add_tag', None)]

    def test_selecting_a_tag_shows_the_action_panel_for_that_tag(self, settings, tmp_repo, flush):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('tag', 'v1.0')

        window = window_for(tmp_repo)
        GitTagCommand(window).run(repo=tmp_repo.path)
        items, on_done = window.quick_panel
        assert items[0].trigger == 'v1.0'

        on_done(0)
        flush()
        actions, _ = window.quick_panel
        assert all_items(actions)
        assert [(i.trigger, i.details) for i in actions] == [
            (a, [t.format(tag='v1.0')]) for a, t in GitTagCommand.TAG_ACTIONS]


class TestRemotePanels(object):

    def add_remotes(self, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        tmp_repo.git('remote', 'add', 'origin', 'https://example.com/a.git')
        tmp_repo.git('remote', 'add', 'upstream', 'https://example.com/b.git')

    def test_fetch_appends_an_all_row_after_the_remotes(self, settings, tmp_repo, monkeypatch):
        self.add_remotes(tmp_repo)
        window = window_for(tmp_repo)
        cmd = GitFetchCommand(window)
        fetched = []
        monkeypatch.setattr(cmd, 'on_remote', lambda repo, remote=None: fetched.append(remote))

        cmd.run()
        items, on_done = window.quick_panel

        assert all_items(items)
        assert [i.trigger for i in items] == ['origin', 'upstream', '+ All']
        assert items[0].details == ['https://example.com/a.git (fetch)', 'https://example.com/a.git (push)']
        assert items[-1].details == ['Fetch from all configured remotes', 'git fetch --all']

        on_done(1)
        on_done(2)
        assert fetched == ['upstream', None]

    def test_remote_command_shows_actions_for_the_selected_remote(self, settings, tmp_repo, flush):
        self.add_remotes(tmp_repo)
        window = window_for(tmp_repo)
        GitRemoteCommand(window).run()
        items, on_done = window.quick_panel
        assert [i.trigger for i in items] == ['origin', 'upstream']

        on_done(1)
        flush()
        actions, _ = window.quick_panel
        assert all_items(actions)
        assert [(i.trigger, i.details) for i in actions] == [
            (a, [d]) for a, d in GitRemoteCommand.REMOTE_ACTIONS]


class TestCheckoutRemoteBranchPanel(object):

    def test_only_branches_missing_locally_are_offered(self, settings, tmp_repo, flush, monkeypatch):
        tmp_repo.commit('a.txt', 'a\n')
        window = window_for(tmp_repo)
        cmd = GitCheckoutRemoteBranchCommand(window)
        monkeypatch.setattr(cmd, 'get_remote_branches',
                            lambda repo, remote: ['origin/main', 'origin/feature', 'origin/hotfix'])
        monkeypatch.setattr(cmd, 'get_branches', lambda repo: [(True, 'main'), (False, 'hotfix')])
        checked_out = []

        def fake_git(args, cwd=None):
            checked_out.append(args)
            return 1, '', 'error: pathspec did not match'  # the stub Window has no output panel

        monkeypatch.setattr(cmd, 'git', fake_git)

        remotes = cmd.format_quick_remotes(['origin\thttps://example.com/a.git (fetch)'])
        cmd.remote_panel_done(tmp_repo.path, remotes, 0)
        flush()
        items, on_done = window.quick_panel

        assert all_items(items)
        assert [(i.trigger, i.details, i.kind) for i in items] == [('feature', ['origin/feature'], KIND_BRANCH)]

        on_done(0)
        assert checked_out == [['checkout', 'feature']]
        assert sublime.error_messages and 'pathspec' in sublime.error_messages[0]


class TestSwitchRepoPanel(object):

    def test_selection_sets_the_chosen_repo(self, settings, tmp_path):
        """Regression: on_done used the leaked loop variable, so any choice
        switched to the *last* repository in the list."""
        repos = []
        for name in ('one', 'two', 'three'):
            d = tmp_path / name
            d.mkdir()
            (d / '.git').mkdir()
            repos.append(str(d))
        window = sublime.Window(folders=repos, active_view=sublime.View())
        cmd = GitSwitchRepoCommand(window)
        cmd.run()
        items, on_done = window.quick_panel
        listed = [i.trigger for i in items]
        assert sorted(listed) == ['one', 'three', 'two']

        for idx, trigger in enumerate(listed):
            on_done(idx)
            assert os.path.basename(cmd.get_window_repository(window)) == trigger

    def test_rows_show_basename_and_abbreviated_path(self, settings, tmp_repo):
        tmp_repo.commit('a.txt', 'a\n')
        window = window_for(tmp_repo)
        GitSwitchRepoCommand(window).run()
        items, _ = window.quick_panel

        assert all_items(items)
        assert [(i.trigger, i.details) for i in items] == [
            (os.path.basename(tmp_repo.path), [abbreviate_dir(tmp_repo.path)])]
