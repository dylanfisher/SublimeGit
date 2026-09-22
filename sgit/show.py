from functools import partial
import sublime
from sublime_plugin import WindowCommand, TextCommand

from .util import noop, find_view_by_settings
from .cmd import GitCmd
from .helpers import GitShowHelper
from .status import GitViewRefreshCmd


GIT_SHOW_TITLE_PREFIX = '*git-show*: '
GIT_SHOW_SYNTAX = 'Packages/SublimeGit/syntax/SublimeGit Show.sublime-syntax'


class GitShowCommand(WindowCommand, GitCmd):
    """
    Documentation coming soon.
    """

    def run(self, repo=None, obj=None):
        repo = repo or self.get_repo()
        if not repo:
            return

        if not obj:
            self.window.show_input_panel('Object:', '', partial(self.show, repo), noop, noop)
        else:
            self.show(repo, obj)

    def show(self, repo, obj=None):
        if not obj:
            exit, obj, _ = self.git(['rev-parse', 'HEAD'], cwd=repo)
            if exit != 0:
                return sublime.error_message("Nothing committed (yet)")
            obj = obj.strip()

        title = GIT_SHOW_TITLE_PREFIX + obj[:7] if len(obj) == 40 else obj
        view = find_view_by_settings(self.window, git_view='show', git_repo=repo, git_show_obj=obj)
        if not view:
            view = self.window.new_file()
            view.set_name(title)
            view.set_scratch(True)
            view.set_read_only(True)
            view.assign_syntax(GIT_SHOW_SYNTAX)

            view.settings().set('git_view', 'show')
            view.settings().set('git_repo', repo)
            view.settings().set('git_show_obj', obj)

        view.run_command('git_show_refresh', {'obj': obj})


class GitShowRefreshCommand(TextCommand, GitViewRefreshCmd, GitCmd, GitShowHelper):
    """Fill the show view: ``git show`` runs in a worker thread, the buffer
    is written by ``git_log_write`` on the main thread."""

    def is_visible(self):
        return False

    def run(self, edit, obj=None):
        obj = obj or self.view.settings().get('git_show_obj')
        repo = self.view.settings().get('git_repo')
        if not repo or not obj:
            return
        self.request_refresh({'repo': repo, 'obj': obj})

    def gather(self, request):
        return self.get_show(request['repo'], request['obj'])

    def deliver(self, request, show):
        if show:
            self.view.run_command('git_log_write', {'content': show})
