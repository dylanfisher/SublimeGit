from functools import partial

import sublime
from sublime_plugin import WindowCommand

from .util import get_setting
from .cmd import GitCmd
from .helpers import GitBranchHelper, GitErrorHelper


GIT_MERGE_IN_PROGRESS = ("A merge is already in progress. Finish it with Git: Commit, "
                         "or back out with Git: Abort Merge.")
GIT_REBASE_IN_PROGRESS = ("A rebase is already in progress. Resolve the conflicts and run "
                          "'git rebase --continue', or back out with Git: Abort Rebase.")
GIT_NOT_MERGING = "No merge is in progress, nothing to abort."
GIT_NOT_REBASING = "No rebase is in progress, nothing to abort."

GIT_ABORT_MERGE = "Abort the merge in progress? The working tree is restored to the state before the merge."
GIT_ABORT_REBASE = "Abort the rebase in progress? The branch is restored to where it was before the rebase."


class GitMergeWindowCmd(GitCmd, GitBranchHelper, GitErrorHelper):

    def show_result(self, panel_name, exit, stdout, stderr):
        if exit == 0:
            panel = self.window.get_output_panel(panel_name)
            panel.run_command('git_panel_write', {'content': stdout})
            self.window.run_command('show_panel', {'panel': 'output.%s' % panel_name})
        else:
            sublime.error_message(self.format_error_message((stdout + stderr).strip() or stderr))
        self.window.run_command('git_status', {'refresh_only': True})

    def branch_choices(self, repo):
        """Local branches other than the current one, followed by the remote
        tracking branches. Returns (names, quick panel items)."""
        local = [b for b in self.get_branch_details(repo) if not b[1]]
        remote = self.get_branch_details(repo, remotes=True)
        choices = self.format_quick_branch_details(local) + self.format_quick_branch_details(remote, annotation='remote')
        names = [b[0] for b in local] + [b[0] for b in remote]
        return names, choices


class GitMergeCommand(WindowCommand, GitMergeWindowCmd):
    """
    Merge a branch into the current branch.

    A list of local branches is shown; select one to run
    ``git merge --no-progress <branch>`` (plus any ``git_merge_flags``
    from the settings). The output is shown in a panel, or as an error
    dialog if the merge failed. If the merge stops on conflicts, resolve
    them and commit, or use Git: Abort Merge.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        merging, rebasing = self.get_in_progress(repo)
        if merging:
            return sublime.error_message(GIT_MERGE_IN_PROGRESS)
        if rebasing:
            return sublime.error_message(GIT_REBASE_IN_PROGRESS)

        branches = self.get_branches(repo)
        choices = [name for c, name in branches if not c]

        self.window.show_quick_panel(choices, partial(self.on_done, repo, choices), sublime.MONOSPACE_FONT)

    def on_done(self, repo, choices, idx):
        if idx == -1:
            return

        cmd = ['merge', '--no-progress']

        extra_flags = get_setting('git_merge_flags')
        if isinstance(extra_flags, list):
            cmd.extend(extra_flags)

        branch = choices[idx]
        cmd.append(branch)

        exit, stdout, stderr = self.git(cmd, cwd=repo)
        self.show_result('git-merge', exit, stdout, stderr)


class GitMergeAbortCommand(WindowCommand, GitMergeWindowCmd):
    """
    Abort a merge in progress (``git merge --abort``).

    Restores the working tree and index to the state before the merge
    started. Only available while a merge is in progress; you will be
    asked to confirm.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        merging, _ = self.get_in_progress(repo)
        if not merging:
            return sublime.error_message(GIT_NOT_MERGING)

        if not sublime.ok_cancel_dialog(GIT_ABORT_MERGE, 'Abort Merge'):
            return

        exit, stdout, stderr = self.git(['merge', '--abort'], cwd=repo)
        if exit == 0:
            sublime.status_message('Merge aborted')
        else:
            sublime.error_message(self.format_error_message(stderr))
        self.window.run_command('git_status', {'refresh_only': True})


class GitRebaseCommand(WindowCommand, GitMergeWindowCmd):
    """
    Rebase the current branch onto another branch.

    A list of local branches (other than the current one) and remote
    tracking branches is shown; select one to run ``git rebase <branch>``
    (plus any ``git_rebase_flags`` from the settings, for example
    ``["--autostash"]``).

    If the rebase stops on conflicts, resolve them, stage the files and
    run ``git rebase --continue`` from a terminal (or Git: Custom Command),
    or back out with Git: Abort Rebase.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        merging, rebasing = self.get_in_progress(repo)
        if merging:
            return sublime.error_message(GIT_MERGE_IN_PROGRESS)
        if rebasing:
            return sublime.error_message(GIT_REBASE_IN_PROGRESS)

        if not self.get_current_branch(repo):
            return sublime.error_message("Cannot rebase a detached HEAD. Check out a branch first.")

        names, choices = self.branch_choices(repo)
        if not names:
            return sublime.error_message("There are no other branches to rebase onto.")

        self.window.show_quick_panel(choices, partial(self.on_done, repo, names))

    def on_done(self, repo, names, idx):
        if idx == -1:
            return

        cmd = ['rebase']

        extra_flags = get_setting('git_rebase_flags')
        if isinstance(extra_flags, list):
            cmd.extend(extra_flags)

        cmd.append(names[idx])

        exit, stdout, stderr = self.git(cmd, cwd=repo)
        # git rebase reports "Successfully rebased ..." on stderr
        self.show_result('git-rebase', exit, stdout + stderr, '')


class GitRebaseAbortCommand(WindowCommand, GitMergeWindowCmd):
    """
    Abort a rebase in progress (``git rebase --abort``).

    Restores the branch to where it was before the rebase started. Only
    available while a rebase is in progress; you will be asked to confirm.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        _, rebasing = self.get_in_progress(repo)
        if not rebasing:
            return sublime.error_message(GIT_NOT_REBASING)

        if not sublime.ok_cancel_dialog(GIT_ABORT_REBASE, 'Abort Rebase'):
            return

        exit, stdout, stderr = self.git(['rebase', '--abort'], cwd=repo)
        if exit == 0:
            sublime.status_message('Rebase aborted')
        else:
            sublime.error_message(self.format_error_message(stderr))
        self.window.run_command('git_status', {'refresh_only': True})
