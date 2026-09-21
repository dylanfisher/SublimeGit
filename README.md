SublimeGit (custom fork)
========================

This is a personal fork of [SublimeGit](https://github.com/SublimeGit/SublimeGit),
based on release 1.0.37, maintained to keep the plugin working on current
Sublime Text builds. It is installed as a manual package (a symlink in the
`Packages` folder), not through Package Control.

Changes from upstream
---------------------

- Renamed the `async` parameter of `git_custom` to `run_async`. `async` has been
  a reserved keyword since Python 3.7, and Sublime Text 4213+ loads plugins under
  Python 3.14, which made the plugin fail to load. The `async` key is still
  accepted in user-defined commands for backward compatibility.
- Removed `package-metadata.json` so Package Control treats this as a manual
  package and does not try to update or replace it.
- Git subprocesses are started with `Popen(cwd=...)` instead of `os.chdir()`,
  so the plugin no longer changes the working directory of the whole Sublime
  process. `cmd(..., ignore_errors=True)` now returns `(0, '', '')`.
- Added `.python-version` (`3.14`) so Sublime Text always loads the plugin
  under the 3.14 plugin host.
- Blame views drop their cached commit/line data when closed instead of
  keeping it for the lifetime of the process.
- Fixed `get_remote_names()` (it crashed calling `.append` on a set), so
  commands that list remote names work again.
- The status bar message is computed with a single `git status --porcelain=v2
  --branch` call instead of five git processes, is cached per repo for one
  second, coalesces bursts of view events into one running update (results
  that arrive after a save are discarded and re-run), and "with unpushed" now
  means the branch is ahead of its upstream rather than "worktree differs from
  upstream" (so a branch that is only behind is no longer flagged). A repo
  without commits is no longer reported as dirty unless something is staged.
  The status entry is cleared when a view leaves a branch (detached HEAD,
  non-repo file) instead of showing stale text.
- The status view and diff view refreshes run their git commands in a worker
  thread instead of blocking the UI: `git_status_refresh` / `git_diff_refresh`
  gather the text off the main thread and hand it to the hidden
  `git_status_write` / `git_diff_write` commands to write the buffer and place
  the caret. Refreshes are coalesced per view (a request that arrives while one
  is running makes that result stale and queues exactly one more run), and a
  refresh on focus restores the scroll position so the view does not jump.
- Removed the Python 2 / Sublime Text 2 compatibility code (`text_type`,
  `string_types`, the `sublime.version() < '3000'` sync event hooks, the
  module `reload` block and `basicConfig` root-logger setup in
  `SublimeGit.py`). Logging is now configured only on the `SublimeGit` logger
  (one handler, no propagation), so it no longer reconfigures every other
  plugin's logging.
- Multi-column quick panels (log, remotes, remote branches, tags, stashes,
  repositories, remote/tag actions, legit branches) use
  `sublime.QuickPanelItem` with details/annotations instead of list-of-lists
  rows. The `format_quick_*` helpers return `QuickPanelItem`s.
- The `git_flow` and `legit` extensions are disabled by default
  (`git_extensions` setting); enabling one requires a restart.
- `SublimeGit: Documentation` opens
  `https://sublimegit.readthedocs.io/en/latest/` (the old docs domain is
  gone). `SublimeGit: Version` reports `1.0.37-fork`.
- Removed the Package Control `messages/` release notes and `messages.json`.

- Fixed `Git: Switch Repo` always selecting the last repository in the list
  regardless of the choice made.

The original documentation is at
[sublimegit.readthedocs.io](http://sublimegit.readthedocs.io/en/latest/) and
still applies. The upstream README follows below for reference.

---

Upstream README
===============

SublimeGit is going open source. This is a short TODO list of what needs to happen:
 - [x] Split plugin code from website code
 - [x] Remove licensing code and commands
 - [x] Release plugin code in SublimeGit repo
 - [x] Add license 
 - [x] Remove code for dealing with bytecode distribution
 - [x] Move documentation to readthedocs, or a github pages site or something
 - [x] Redirect SublimeGit domains to this repository
 - [x] Change SublimeGit repo in Package Control to install from Github


Old Roadmap
-----------

**1.0.X (Minor releases)**

Prioritized features (These are what's being worked on, in this order):
 - Interactive rebase. (Issue #54, #9)
 - Pushing and pulling of tags. (Issue #68)
 - Add unmerged paths to status view.

Various features (In no particular order):
 - Difftool command. (Issue #43)
 - Open status view after select/init repo when running `Git: Status`.
 - Force reindex of project-wide tags on checkout.
 - Open file at correct point with <enter> in diff view.
 - `Git: Commit & Push` command.
 - `Git-flow: Feature Publish` command. (Issue #28)
 - `Git-flow: Feature Pull` command.
 - Open `Git: Remote > Show`
 - Browse forwards and backwards in `Git: Show`. (Related to issue #72)
 - `Git: Show` syntax highlighting (Related to issue #72)
 - Cherry-picking.
 - Improved syntax highlighting for `Git: Blame`.
 - Alternative short syntax for `Git: Blame`.
 - Allow for `--word-diff` option in diff view.

Bugs (In no particular order):
 - Handle username/password/passphrase freezes better.
 - Fix some whitespace problems (It seems like the whitespace = cr-at-eol setting is getting ignored, and probably other whitespace settings too. Running git diff in Sublime Text shows ^M at end of line, doing the same in a terminal window omits the ^M as it should).
 - Improve reload logic to get rid of need to restart Sublime Text after some updates.
 - Improve status view cursor location logic. (Issue #10)
 - Commit message is empty on merge commits.
 - Git-flow issues. (Issue #63, #51)
 - Binary files and diffs can cause issues. (Specifically, some fonts)
 - File encoding issues in `Git: Blame` and `Git: Diff`. (Windows 1252 or ISO-8859-1 with characters like å)

**1.1.0**

Planned Features:
 - `Git: Log` view. (See below)
 - `Git: Branch` view. (See below)
 - Add unpushed commits to status view.
 - Hub integration. (Issue #30)

**1.2.0 and later**
 - `Git: Annotate`.
 - git-svn integration. (Issue #37)
 - Squashing.
 - Bisecting.


Feature Descriptions
--------------------

**Git: Log View** (Issue #70)

Show a pretty tree (ala glog alias). Make sure the branches are colored in the correct way, if possible (use the ascii color codes and make a custom colorscheme for the view).

It should be possible to walk through the graph using n and p, and maybe jumping to merges and branches in an intelligent way. Pressing enter should open a commit in the commit view, and show a color-coded diff. Pressing space should append the commit to an already existing commit view and scroll the view.

It should also be possible to refresh the log by pressing r. In the bottom of the view there should be a text which allows for loading X more lines (if necessary).


**Git: Branch View**

List of local and remote branches, with meta-information for each one, as well as easy navigation.

should allow
 - pushing
 - pulling
 - fetching
 - rebasing
 - publishing
 - unpublishing
 - checkout
 - delete
 - merge
 - rebase
 - rename
 - etc.
