import os
import re

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .util import noop, find_view_by_settings, get_setting
from .cmd import GitCmd
from .helpers import GitLogHelper
from .status import GitViewRefreshCmd, forget_view_refresh


GIT_LOG_VIEW_TITLE_PREFIX = '*git-log*: '
GIT_LOG_VIEW_SYNTAX = 'Packages/SublimeGit/syntax/SublimeGit Show.sublime-syntax'

# Graph log view (Git: Log) ------------------------------------------------

GIT_LOG_GRAPH_SYNTAX = 'Packages/SublimeGit/syntax/SublimeGit Log.sublime-syntax'
GIT_LOG_GRAPH_FORMAT = '%h - (%ar) %s - %an%d'
GIT_LOG_GRAPH_VIEW_SETTINGS = {
    'word_wrap': False,
    'translate_tabs_to_spaces': False,
    'draw_white_space': 'none',
}

# A commit line is "<graph glyphs><abbrev hash> - (<relative date>) ...".
# Lines that only carry graph glyphs (merge legs such as "|\ " or "|/")
# have no hash and are skipped.
GIT_LOG_GRAPH_LINE_RE = re.compile(r'^[ |/\\_*.\-]*?([0-9a-f]{7,40}) - \(')


class GitLogCommand(WindowCommand, GitCmd):
    """
    Show the history of the repository as a graph in a view.

    Opens a read-only ``*git-log*: <repo>`` view with the output of
    ``git log --graph --decorate`` (one commit per line: abbreviated hash,
    relative date, subject, author and any branch or tag names). The log is
    gathered off the UI thread, so large histories do not block the editor.

    **Keyboard shortcuts:**

    * ``enter``: Open the selected commit(s) in a ``*git-show*`` view. A
      multi-line selection opens one view per commit; you will be asked to
      confirm when this would open more than 5 tabs, unless the
      **git_blame_warn_multiple_tabs** setting is false.
    * ``r``: Refresh the log, keeping the caret and scroll position.

    Use ``Git: Quick Log`` for the quick-panel version of the log.
    """

    def run(self, repo=None):
        repo = repo or self.get_repo()
        if not repo:
            return

        title = GIT_LOG_VIEW_TITLE_PREFIX + os.path.basename(repo)

        view = find_view_by_settings(self.window, git_view='log-graph', git_repo=repo)
        if not view:
            view = self.window.new_file()
            view.set_name(title)
            view.set_scratch(True)
            view.set_read_only(True)
            view.assign_syntax(GIT_LOG_GRAPH_SYNTAX)

            view.settings().set('git_view', 'log-graph')
            view.settings().set('git_repo', repo)
            for key, val in GIT_LOG_GRAPH_VIEW_SETTINGS.items():
                view.settings().set(key, val)

        self.window.focus_view(view)
        view.run_command('git_log_graph_refresh')


class GitLogGraphRefreshCommand(TextCommand, GitViewRefreshCmd, GitCmd):
    """Refresh the graph log view: git runs in a worker thread, the buffer is
    written by ``git_log_graph_write`` on the main thread."""

    def is_visible(self):
        return False

    def run(self, edit):
        if self.view.settings().get('git_view') != 'log-graph':
            return

        repo = self.view.settings().get('git_repo')
        if not repo:
            return

        point = None
        if self.view.size() > 0 and self.view.sel():
            point = self.view.sel()[0].begin()

        self.request_refresh({
            'repo': repo,
            'point': point,
            'viewport': list(self.view.viewport_position()),
        })

    def gather(self, request):
        return self.get_log_graph(request['repo'])

    def get_log_graph(self, repo):
        if self.git_exit_code(['rev-parse', '--verify', '--quiet', 'HEAD'], cwd=repo) != 0:
            return 'No commits yet\n'
        cmd = ['log', '--graph', '--abbrev-commit', '--decorate', '--date=relative',
               '--no-color', '--format=format:%s' % GIT_LOG_GRAPH_FORMAT]
        exit, stdout, stderr = self.git(cmd, cwd=repo)
        if exit != 0:
            return stderr or stdout
        if not stdout.endswith('\n'):
            stdout += '\n'
        return stdout

    def deliver(self, request, content):
        if content is None:
            return
        self.view.run_command('git_log_graph_write', {
            'content': content,
            'point': request['point'],
            'viewport': request['viewport'],
        })


class GitLogGraphWriteCommand(TextCommand):
    """Apply phase of ``git_log_graph_refresh``: replace the buffer and put
    the caret back. Hidden; only invoked from the main thread by the refresh."""

    def is_visible(self):
        return False

    def run(self, edit, content='', point=None, viewport=None):
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(True)

        if point is None:
            point = 0
        point = min(point, self.view.size())
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(point))

        if viewport is not None and point:
            self.view.set_viewport_position(tuple(viewport), False)
            self.view.show(point, False)


class GitLogGraphShowCommand(TextCommand, GitCmd):
    """Open a ``*git-show*`` view for every commit on the selected lines."""

    def is_visible(self):
        return False

    def commits_from_selection(self):
        commits = []
        for sel in self.view.sel():
            for line in self.view.lines(sel):
                sha = self.parse_line(self.view.substr(line))
                if sha and sha not in commits:
                    commits.append(sha)
        return commits

    @staticmethod
    def parse_line(text):
        match = GIT_LOG_GRAPH_LINE_RE.match(text)
        return match.group(1) if match else None

    def run(self, edit):
        commits = self.commits_from_selection()
        if not commits:
            return sublime.error_message('No commits selected.')

        if len(commits) > 5 and get_setting('git_blame_warn_multiple_tabs', True):
            msg = 'This will open %s tabs. Are you sure you want to continue?' % len(commits)
            if not sublime.ok_cancel_dialog(msg, 'Open tabs'):
                return

        repo = self.get_repo()
        if not repo:
            return

        window = self.view.window()
        for sha in commits:
            window.run_command('git_show', {'repo': repo, 'obj': sha})


class GitLogGraphEventListener(EventListener):

    def on_pre_close(self, view):
        if view.settings().get('git_view') == 'log-graph':
            forget_view_refresh(view.id())


# File log view (Git: Log Current File) ------------------------------------


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
