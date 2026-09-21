from functools import partial

import sublime
from sublime_plugin import WindowCommand

from .cmd import GitCmd
from .helpers import GitErrorHelper, GitRemoteHelper


GIT_NOT_FULLY_MERGED = ("The branch '{branch}' is not fully merged into '{current}'. "
                        "Its commits will be lost if it is deleted. Delete it anyway?")
GIT_DELETE_MERGED = ("Delete the following branches? They are all fully merged into '{target}'.\n\n{branches}")
GIT_NO_MERGED = "No branches are fully merged into '{target}' (other than the current branch)."
GIT_ONLY_CURRENT = "There are no local branches to delete other than the current one."


class GitBranchWindowCmd(GitCmd, GitRemoteHelper, GitErrorHelper):

    def report(self, exit, stdout, stderr, panel_name='git-branch'):
        if exit == 0:
            panel = self.window.get_output_panel(panel_name)
            panel.run_command('git_panel_write', {'content': stdout or stderr})
            self.window.run_command('show_panel', {'panel': 'output.%s' % panel_name})
        else:
            sublime.error_message(self.format_error_message(stderr))
        self.window.run_command('git_status', {'refresh_only': True})


class GitDeleteBranchCommand(WindowCommand, GitBranchWindowCmd):
    """
    Delete a local branch.

    A list of local branches other than the current one is shown.
    Selecting one runs ``git branch -d <branch>``; if git refuses because
    the branch is not fully merged, you are asked whether to force the
    deletion with ``git branch -D``.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        local = [b for b in self.get_branch_details(repo) if not b[1]]
        if not local:
            return sublime.error_message(GIT_ONLY_CURRENT)

        names = [b[0] for b in local]
        self.window.show_quick_panel(self.format_quick_branch_details(local), partial(self.on_done, repo, names))

    def on_done(self, repo, names, idx):
        if idx == -1:
            return
        branch = names[idx]

        exit, stdout, stderr = self.git(['branch', '-d', branch], cwd=repo)
        if exit != 0 and 'not fully merged' in stderr:
            current = self.get_current_branch(repo) or 'HEAD'
            msg = GIT_NOT_FULLY_MERGED.format(branch=branch, current=current)
            if not sublime.ok_cancel_dialog(msg, 'Delete'):
                return
            exit, stdout, stderr = self.git(['branch', '-D', branch], cwd=repo)
        self.report(exit, stdout, stderr)


class GitDeleteMergedBranchesCommand(WindowCommand, GitBranchWindowCmd):
    """
    Delete every local branch that is fully merged into a chosen branch.

    First select the target branch (the current branch is listed first,
    then the other local branches; remote branches are not offered). All
    local branches whose commits are contained in that target, except the
    current branch and the target itself, are listed in a confirmation
    dialog and deleted with ``git branch -d``.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        local = self.get_branch_details(repo)
        current = [b for b in local if b[1]]
        others = [b for b in local if not b[1]]

        choices = (self.format_quick_branch_details(current, annotation='current')
                   + self.format_quick_branch_details(others))
        names = [b[0] for b in current + others]

        self.window.show_quick_panel(choices, partial(self.on_target, repo, names))

    def on_target(self, repo, names, idx):
        if idx == -1:
            return
        target = names[idx]
        current = self.get_current_branch(repo)

        merged = self.git_lines(['branch', '--list', '--merged', target, '--format=%(refname:short)'], cwd=repo)
        merged = [b for b in merged if b and b not in (target, current)]
        if not merged:
            return sublime.error_message(GIT_NO_MERGED.format(target=target))

        msg = GIT_DELETE_MERGED.format(target=target, branches="\n".join(merged))
        if not sublime.ok_cancel_dialog(msg, 'Delete %d branches' % len(merged) if len(merged) > 1 else 'Delete branch'):
            return

        exit, stdout, stderr = self.git(['branch', '-d'] + merged, cwd=repo)
        self.report(exit, stdout, stderr)
