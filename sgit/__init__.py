
__version__ = '1.0.37-fork'


# Import all the commands

from .util import GitPanelWriteCommand, GitPanelAppendCommand

from .repo import GitInitCommand, GitSwitchRepoCommand

from .custom import GitCustomCommand, GitCustomOutputCommand

from .diff import (GitDiffCommand, GitDiffCachedCommand, GitDiffRefreshCommand, GitDiffMoveCommand,
                   GitDiffChangeHunkSizeCommand, GitDiffStageUnstageHunkCommand, GitDiffCurrentFileCommand,
                   GitDiffCachedCurrentFileCommand, GitDiffWriteCommand, GitDiffEventListener)

from .show import GitShowCommand, GitShowRefreshCommand

from .help import GitHelpCommand, GitVersionCommand

from .gc import GitGarbageCollectCommand

from .log import (GitLogCommand, GitLogGraphRefreshCommand, GitLogGraphWriteCommand,
                  GitLogGraphShowCommand, GitLogGraphEventListener,
                  GitQuickLogCommand, GitQuickLogCurrentFileCommand,
                  GitLogCurrentFileCommand, GitLogRefreshCommand, GitLogWriteCommand,
                  GitLogGraphLoadMoreCommand)

from .blame import (GitBlameCommand, GitBlameRefreshCommand, GitBlameWriteCommand,
                    GitBlameShowCommand, GitBlameBlameCommand)
from .blame import GitBlameEventListener

from .remote import (GitPushCurrentBranchCommand, GitPullCurrentBranchCommand,
                     GitFetchCommand, GitPullCommand, GitPushCommand,
                     GitRemoteCommand, GitRemoteAddCommand)

from .status import (GitStatusCommand, GitStatusRefreshCommand, GitStatusWriteCommand, GitQuickStatusCommand,
                     GitStatusMoveCommand, GitStatusStageCommand,
                     GitStatusUnstageCommand, GitStatusDiscardCommand,
                     GitStatusOpenFileCommand, GitStatusDiffCommand,
                     GitStatusIgnoreCommand, GitStatusStashCmd, GitStatusStashApplyCommand,
                     GitStatusStashPopCommand)
from .status import GitStatusBarEventListener, GitStatusEventListener

from .add import GitQuickAddCommand, GitAddCurrentFileCommand

from .commit import (GitCommitCommand, GitCommitAmendCommand, GitCommitTemplateCommand,
                     GitCommitPerformCommand, GitQuickCommitCommand, GitQuickCommitCurrentFileCommand,
                     GitCommitSaveCommand, GitUndoCommitCommand)
from .commit import GitCommitEventListener

from .stash import (GitStashCommand, GitSnapshotCommand,
                    GitStashApplyCommand, GitStashPopCommand)

from .tag import GitTagCommand, GitAddTagCommand

from .checkout import (GitCheckoutBranchCommand, GitCheckoutCommitCommand,
                       GitCheckoutNewBranchCommand, GitCheckoutCurrentFileCommand,
                       GitCheckoutTagCommand, GitCheckoutRemoteBranchCommand)

from .merge import GitMergeCommand, GitMergeAbortCommand, GitRebaseCommand, GitRebaseAbortCommand

from .branch import GitDeleteBranchCommand, GitDeleteMergedBranchesCommand

from .gitk import GitGitkCommand

from .sublimegit import (SublimeGitDocumentationCommand, SublimeGitVersionCommand)


# import plugins

from . import git_extensions
