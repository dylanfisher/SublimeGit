from functools import partial

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .util import find_view_by_settings, noop, get_setting
from .cmd import GitCmd
from .helpers import GitStatusHelper, GitBranchHelper
from .status import GIT_WORKING_DIR_CLEAN, run_async, set_status_busy, clear_status_busy


GIT_COMMIT_VIEW_TITLE = "COMMIT_EDITMSG"
GIT_COMMIT_VIEW_SYNTAX = 'Packages/SublimeGit/syntax/SublimeGit Commit Message.sublime-syntax'

GIT_NOTHING_STAGED = 'No changes added to commit. Use s on files/sections in the status view to stage changes.'
GIT_STATUS_COMMITTING = "Committing...\n"
GIT_COMMIT_EMPTY_MESSAGE = "Commit aborted: empty commit message"

GIT_COMMIT_TEMPLATE = """{old_msg}
# Please enter the commit message for your changes. Lines starting
# with '#' will be ignored, and an empty message aborts the commit.
{status}"""

GIT_AMEND_PUSHED = ("The last commit has already been pushed to {remotes}. It is discouraged to "
                    "rewrite history which has already been pushed. Are you sure you want to amend the commit?")

GIT_UNDO_COMMIT = ("Undo the last commit?\n\n{subject}\n\nThe commit is removed from the branch; "
                   "its changes are kept, staged, in the working tree.")
GIT_UNDO_PUSHED = ("The last commit has already been pushed to {remotes}. Undoing it rewrites "
                   "history that others may have. Undo it anyway?")
GIT_UNDO_ROOT_COMMIT = ("Undo the first commit?\n\n{subject}\n\nThe branch is left with no commits; "
                        "its changes are kept, staged, in the working tree.")
GIT_UNDO_NOTHING = "Nothing committed (yet)"

CUT_LINE = "------------------------ >8 ------------------------\n"
CUT_EXPLANATION = "# Do not touch the line above.\n# Everything below will be removed.\n"


class GitCommit(object):

    windows = {}


def commit_output(exit, stdout, stderr):
    """What to show after ``git commit``: hooks and most errors write to
    stderr, the summary (and "nothing to commit") to stdout, so show both;
    the error part first when the commit failed."""
    parts = [stdout, stderr] if exit == 0 else [stderr, stdout]
    return "\n".join(p.strip('\n') for p in parts if p.strip())


def is_empty_commit_message(message):
    """True when ``git commit --cleanup=strip`` would find nothing left of
    ``message``: only comments, whitespace and a verbose diff."""
    message = message.split("# " + CUT_LINE, 1)[0]
    return not any(l.strip() for l in message.splitlines() if not l.startswith('#'))


class GitCommitWindowCmd(GitCmd, GitStatusHelper):

    @property
    def is_verbose(self):
        return get_setting('git_commit_verbose', False)

    def get_commit_template(self, repo, add=False, amend=False):
        cmd = ['commit', '--dry-run', '--status',
               '--all' if add else None,
               '--amend' if amend else None,
               '--verbose' if self.is_verbose else None]
        exit, stdout, stderr = self.git(cmd, cwd=repo)

        stderr = stderr.strip()
        if stderr:
            for line in stderr.splitlines():
                stdout += "# %s\n" % line

        old_msg = ''
        if amend:
            old_msg = self.git_lines(['rev-list', '--format=%B', '--max-count=1', 'HEAD'], cwd=repo)
            old_msg = "%s\n" % "\n".join(old_msg[1:])

        if self.is_verbose and CUT_LINE not in stdout:
            comments = []
            other = []
            for line in stdout.splitlines():
                if line.startswith('#'):
                    comments.append(line)
                else:
                    other.append(line)
            status = "\n".join(comments)
            status += "\n# %s" % CUT_LINE
            status += CUT_EXPLANATION
            status += "\n".join(other)
        else:
            status = stdout

        return GIT_COMMIT_TEMPLATE.format(status=status, old_msg=old_msg)

    def show_commit_panel(self, content):
        show_commit_panel(self.window, content)

    def run_commit(self, window, repo, cmd, message):
        """Run ``git commit`` off the UI thread (pre-commit hooks can take a
        while), then show its output and refresh the status view. Until
        then the status view shows a placeholder, not the pre-commit files."""
        def work():
            return self.git(cmd, stdin=message, cwd=repo)

        def refresh():
            clear_status_busy(repo)
            window.run_command('git_status', {'refresh_only': True})

        def done(result):
            show_commit_panel(window, commit_output(*result))
            refresh()

        set_status_busy(window, repo, GIT_STATUS_COMMITTING)
        run_async(work, done, 'Committing...', on_error=refresh)


def show_commit_panel(window, content):
    panel = window.get_output_panel('git-commit')
    panel.run_command('git_panel_write', {'content': content})
    window.run_command('show_panel', {'panel': 'output.git-commit'})


class GitCommitCommand(WindowCommand, GitCommitWindowCmd):
    """
    Documentation coming soon.
    """

    def run(self, add=False):
        repo = self.get_repo()
        if not repo:
            return

        staged, dirty = self.get_changes(repo)

        if not add and not staged:
            return sublime.error_message(GIT_NOTHING_STAGED)
        elif add and (not staged and not dirty):
            return sublime.error_message(GIT_WORKING_DIR_CLEAN)

        view = find_view_by_settings(self.window, git_view='commit', git_repo=repo)
        if not view:
            view = self.window.new_file()
            view.set_name(GIT_COMMIT_VIEW_TITLE)
            view.assign_syntax(GIT_COMMIT_VIEW_SYNTAX)
            view.set_scratch(True)

            view.settings().set('git_view', 'commit')
            view.settings().set('git_repo', repo)

        GitCommit.windows[view.id()] = (self.window, add, False)
        self.window.focus_view(view)

        template = self.get_commit_template(repo, add=add)
        view.run_command('git_commit_template', {'template': template})


class GitCommitAmendCommand(GitCommitWindowCmd, GitBranchHelper, WindowCommand):
    """
    Documentation coming soon.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        remotes = self.get_remote_branches_containing(repo, 'HEAD')
        if remotes:
            if not sublime.ok_cancel_dialog(GIT_AMEND_PUSHED.format(remotes=', '.join(remotes)), 'Amend commit'):
                return

        view = find_view_by_settings(self.window, git_view='commit', git_repo=repo)
        if not view:
            view = self.window.new_file()
            view.set_name(GIT_COMMIT_VIEW_TITLE)
            view.assign_syntax(GIT_COMMIT_VIEW_SYNTAX)
            view.set_scratch(True)

            view.settings().set('git_view', 'commit')
            view.settings().set('git_repo', repo)

        GitCommit.windows[view.id()] = (self.window, False, True)
        self.window.focus_view(view)

        template = self.get_commit_template(repo, amend=True)
        view.run_command('git_commit_template', {'template': template})


class GitCommitTemplateCommand(TextCommand):

    def is_visible(self):
        return False

    def run(self, edit, template=''):
        if self.view.size() > 0:
            self.view.erase(edit, sublime.Region(0, self.view.size()))
        self.view.insert(edit, 0, template)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0))


class GitCommitEventListener(EventListener):
    _lpop = False

    def mark_pedantic(self, view):
        if view.settings().get('git_view') == 'commit' or view.file_name() == 'COMMIT_EDITMSG':
            # Header lines should be a max of 50 chars
            view.erase_regions('git-commit.header')
            firstline = view.line(view.text_point(0, 0))
            if firstline.end() > 50 and not view.substr(firstline).startswith('#'):
                view.add_regions('git-commit.header', [sublime.Region(50, firstline.end())], 'invalid', 'dot')

            # The second line should be empty
            view.erase_regions('git-commit.line2')
            secondline = view.line(view.text_point(1, 0))
            if secondline.end() - secondline.begin() > 0 and not view.substr(secondline).startswith('#'):
                view.add_regions('git-commit.line2', [secondline], 'invalid', 'dot')

            # Other lines should be at most 72 chars
            view.erase_regions('git-commit.others')
            too_long = []
            for l in view.lines(sublime.Region(view.text_point(2, 0), view.size())):
                if view.substr(l).startswith('#'):
                    break
                if l.end() - l.begin() > 72:
                    too_long.append(sublime.Region(l.begin() + 72, l.end()))
            if too_long:
                view.add_regions('git-commit.others', too_long, 'invalid', 'dot')

    def on_modified_async(self, view):
        if get_setting('git_commit_pedantic') is True:
            self.mark_pedantic(view)

    def on_activated_async(self, view):
        if get_setting('git_commit_pedantic') is True:
            self.mark_pedantic(view)

    def on_pre_close(self, view):
        # pre_close, not close: the status view behind this one must show
        # the "Committing..." placeholder before it is revealed
        if view.settings().get('git_view') == 'commit' and view.id() in GitCommit.windows:
            message = view.substr(sublime.Region(0, view.size()))
            window, add, amend = GitCommit.windows[view.id()]
            repo = view.settings().get('git_repo')
            window.run_command('git_commit_perform', {'message': message, 'add': add, 'amend': amend, 'repo': repo})


class GitCommitPerformCommand(WindowCommand, GitCommitWindowCmd):

    def run(self, repo, message, add=False, amend=False):
        # git would abort anyway; skip it so the status view is not marked
        # as committing and no output panel pops up
        if is_empty_commit_message(message):
            sublime.status_message(GIT_COMMIT_EMPTY_MESSAGE)
            return

        cmd = ['commit', '--cleanup=strip',
               '--all' if add else None,
               '--amend' if amend else None,
               '--verbose' if self.is_verbose else None, '-F', '-']

        self.run_commit(self.window, repo, cmd, message)

    def is_visible(self):
        return False


class GitUndoCommitCommand(WindowCommand, GitCmd, GitBranchHelper):
    """
    Undo the last commit, keeping its changes staged (``git reset --soft HEAD~1``).

    You are asked to confirm, and warned first if the commit is already
    contained in a remote tracking branch (i.e. it has been pushed). The
    root commit has no parent to reset to, so it is undone by deleting the
    branch ref instead (``git update-ref -d HEAD``), which likewise keeps
    its changes staged.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        exit, subject, _ = self.git(['log', '-1', '--format=%h %s'], cwd=repo)
        if exit != 0:
            return sublime.error_message(GIT_UNDO_NOTHING)

        is_root = self.git_exit_code(['rev-parse', '-q', '--verify', 'HEAD~1'], cwd=repo) != 0

        remotes = self.get_remote_branches_containing(repo, 'HEAD')
        if remotes:
            if not sublime.ok_cancel_dialog(GIT_UNDO_PUSHED.format(remotes=', '.join(remotes)), 'Undo commit'):
                return

        confirm = GIT_UNDO_ROOT_COMMIT if is_root else GIT_UNDO_COMMIT
        if not sublime.ok_cancel_dialog(confirm.format(subject=subject.strip()), 'Undo commit'):
            return

        if is_root:
            exit, stdout, stderr = self.git(['update-ref', '-d', 'HEAD'], cwd=repo)
        else:
            exit, stdout, stderr = self.git(['reset', '--soft', 'HEAD~1'], cwd=repo)
        if exit == 0:
            sublime.status_message('Undid commit %s' % subject.strip())
        else:
            sublime.error_message(stderr)
        self.window.run_command('git_status', {'refresh_only': True})


class GitCommitSaveCommand(TextCommand):

    def is_visible(self):
        return False

    def run(self, edit):
        if self.view.settings().get('git_view') == 'commit' and self.view.id() in GitCommit.windows:
            return
        self.view.run_command('save')


class GitQuickCommitCommand(WindowCommand, GitCommitWindowCmd):
    """
    Quickly commit changes with a one-line commit message.

    If there are any staged changes, only those changes will be added. If there
    are no staged changes, any changed files that git know about will be added
    in the commit.

    If the working directory is clean, an error will be shown indicating it.

    After entering the commit message, press enter to commit, or esc to cancel.
    An empty commit message will also result in the commit being cancelled.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        staged, dirty = self.get_changes(repo)

        if not staged and not dirty:
            sublime.error_message(GIT_WORKING_DIR_CLEAN.capitalize())
            return

        self.window.show_input_panel("Commit message:", '', partial(self.on_commit_message, repo), noop, noop)

    def on_commit_message(self, repo, msg=None):
        if not msg:
            msg = ''
        # re-checked on submit: the user may have staged something while the
        # input panel was open (one process, as before)
        staged, _ = self.get_changes(repo)
        cmd = ['commit', '-F', '-'] if staged else ['commit', '-a', '-F', '-']
        self.run_commit(self.window, repo, cmd, msg)


class GitQuickCommitCurrentFileCommand(TextCommand, GitCommitWindowCmd):
    """
    Documentation coming soon.
    """

    def run(self, edit):
        filename = self.view.file_name()
        if not filename:
            sublime.error_message("Cannot commit a file which has not been saved.")
            return

        repo = self.get_repo()
        if not repo:
            return

        if not self.file_in_git(repo, filename):
            if sublime.ok_cancel_dialog("The file %s is not tracked by git. Do you want to add it?" % filename, "Add file"):
                exit, stdout, stderr = self.git(['add', '--force', '--', filename], cwd=repo)
                if exit == 0:
                    sublime.status_message('Added %s' % filename)
                else:
                    sublime.error_message('git error: %s' % stderr)
            else:
                return

        self.view.window().show_input_panel("Commit message:", '', partial(self.on_commit_message, repo, filename), noop, noop)

    def on_commit_message(self, repo, filename, msg=None):
        if not msg:
            msg = ''

        cmd = ['commit', '-F', '-', '--only', '--', filename]
        self.run_commit(self.view.window(), repo, cmd, msg)
