import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from .util import noop, find_view_by_settings
from .cmd import GitCmd
from .helpers import GitLogHelper


GIT_LOG_FORMAT = '--format=%s%n%H by %an <%aE>%n%ar (%ad)'

GIT_LOG_VIEW_TITLE_PREFIX = '*git-log*: '
GIT_LOG_VIEW_SYNTAX = 'Packages/SublimeGit/syntax/SublimeGit Show.sublime-syntax'


class GitLogCommand(WindowCommand, GitCmd):
    """
    Documentation coming soon.
    """

    def run(self):
        self.window.run_command('git_quick_log')


class GitQuickLogCommand(WindowCommand, GitCmd, GitLogHelper):
    """
    Documentation coming soon.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        log = self.get_quick_log(repo)
        hashes, choices = self.format_quick_log(log)

        def on_done(idx):
            if idx == -1:
                return
            commit = hashes[idx]
            self.window.run_command('git_show', {'obj': commit, 'repo': repo})

        self.window.show_quick_panel(choices, on_done)


class GitQuickLogCurrentFileCommand(TextCommand, GitCmd, GitLogHelper):
    """
    Documentation coming soon.
    """

    def run(self, edit):
        filename = self.view.file_name()
        if not filename:
            self.window.show_quick_panel(['No log for file'], noop)

        repo = self.get_repo()
        if not repo:
            return

        log = self.get_quick_log(repo, path=filename, follow=True)
        hashes, choices = self.format_quick_log(log)

        def on_done(idx):
            if idx == -1:
                return
            commit = hashes[idx]
            self.view.window().run_command('git_show', {'obj': commit, 'repo': repo})

        self.view.window().show_quick_panel(choices, on_done)


class GitLogCurrentFileCommand(TextCommand, GitCmd):
    """
    Show the full history of the current file in a view.

    Opens a read-only ``*git-log*: <file>`` view with the output of
    ``git log --follow --patch`` for the file: the same commit header and
    diff layout (and syntax highlighting) as Git: Show, for every commit
    that touched the file, following renames. Press ``r`` in the view to
    refresh it.
    """

    def run(self, edit):
        filename = self.view.file_name()
        if not filename:
            return sublime.error_message("Cannot show the log of an unsaved file.")

        repo = self.get_repo()
        if not repo:
            return

        window = self.view.window()
        relpath = os.path.relpath(filename, repo)
        title = GIT_LOG_VIEW_TITLE_PREFIX + relpath

        view = find_view_by_settings(window, git_view='log', git_repo=repo, git_log_path=relpath)
        if not view:
            view = window.new_file()
            view.set_name(title)
            view.set_scratch(True)
            view.set_read_only(True)
            view.assign_syntax(GIT_LOG_VIEW_SYNTAX)

            view.settings().set('git_view', 'log')
            view.settings().set('git_repo', repo)
            view.settings().set('git_log_path', relpath)

        window.focus_view(view)
        view.run_command('git_log_refresh')


class GitLogRefreshCommand(TextCommand, GitCmd):

    def is_visible(self):
        return False

    def run(self, edit):
        repo = self.view.settings().get('git_repo')
        relpath = self.view.settings().get('git_log_path')
        if not repo:
            return

        cmd = ['log', '--no-color', '--format=medium', '--patch', '--follow' if relpath else None]
        if relpath:
            cmd.extend(['--', relpath])
        exit, stdout, stderr = self.git(cmd, cwd=repo)
        content = stdout if exit == 0 else stderr
        if exit == 0 and not stdout.strip():
            content = "No commits for %s\n" % (relpath or 'HEAD')

        self.view.set_read_only(False)
        if self.view.size() > 0:
            self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.insert(edit, 0, content)
        self.view.set_read_only(True)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0))
