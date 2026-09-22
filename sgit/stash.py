import time

import sublime
from sublime_plugin import WindowCommand

from .util import noop
from .cmd import GitCmd
from .helpers import GitStashHelper, GitStatusHelper, GitErrorHelper, KIND_STASH


NO_LOCAL_CHANGES = "No local changes to save"


class GitStashWindowCmd(GitCmd, GitStashHelper, GitErrorHelper):

    def pop_or_apply_from_panel(self, action):
        repo = self.get_repo()
        if not repo:
            return

        stashes = self.get_stashes(repo)

        if not stashes:
            return sublime.error_message('No stashes. Use the Git: Stash command to stash changes')

        callback = self.pop_or_apply_callback(repo, action, stashes)
        panel = []
        for name, title in stashes:
            panel.append(sublime.QuickPanelItem(title, annotation="stash@{%s}" % name, kind=KIND_STASH))

        self.window.show_quick_panel(panel, callback)

    def pop_or_apply_callback(self, repo, action, stashes):
        def inner(choice):
            if choice != -1:
                name, _ = stashes[choice]
                exit_code, stdout, stderr = self.git(['stash', action, '-q', 'stash@{%s}' % name], cwd=repo)
                if exit_code != 0:
                    sublime.error_message(self.format_error_message(stderr))
                window = sublime.active_window()
                if window:
                    window.run_command('git_status', {'refresh_only': True})
        return inner


class GitStashCommand(WindowCommand, GitCmd, GitStatusHelper, GitErrorHelper):
    """
    Documentation coming soon.
    """

    def run(self, untracked=False):
        repo = self.get_repo()
        if not repo:
            return

        def on_done(title):
            title = title.strip()
            cmd = ['stash', 'push', '--include-untracked' if untracked else None]
            if title:
                cmd.extend(['-m', title])
            exit, stdout, stderr = self.git(cmd, cwd=repo)
            if exit != 0:
                sublime.error_message(self.format_error_message(stderr or stdout))
            self.window.run_command('git_status', {'refresh_only': True})

        # get files status (the status call refreshes the index itself)
        untracked_files, unstaged_files, staged_files = self.get_files_status(repo)

        # check for if there's something to stash
        if not unstaged_files and not staged_files:
            if (untracked and not untracked_files) or (not untracked):
                return sublime.error_message(NO_LOCAL_CHANGES)

        self.window.show_input_panel('Stash title:', '', on_done, noop, noop)


class GitSnapshotCommand(WindowCommand, GitStashWindowCmd):
    """
    Save the current changes as a stash without touching the working tree.

    Uses ``git stash create`` + ``git stash store``, so the working tree and
    index are left exactly as they were. Untracked files are not included.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        snapshot = time.strftime("Snapshot at %Y-%m-%d %H:%M:%S")
        exit, sha, stderr = self.git(['stash', 'create', snapshot], cwd=repo)
        sha = sha.strip()
        if exit != 0:
            return sublime.error_message(self.format_error_message(stderr))
        if not sha:
            # nothing to save; stash create makes no commit and prints nothing
            return sublime.error_message(NO_LOCAL_CHANGES)

        exit, _, stderr = self.git(['stash', 'store', '-m', snapshot, sha], cwd=repo)
        if exit != 0:
            sublime.error_message(self.format_error_message(stderr))
        else:
            sublime.status_message(snapshot)
        self.window.run_command('git_status', {'refresh_only': True})


class GitStashPopCommand(WindowCommand, GitStashWindowCmd):
    """
    Documentation coming soon.
    """

    def run(self):
        self.pop_or_apply_from_panel('pop')


class GitStashApplyCommand(WindowCommand, GitStashWindowCmd):
    """
    Documentation coming soon.
    """

    def run(self):
        self.pop_or_apply_from_panel('apply')
