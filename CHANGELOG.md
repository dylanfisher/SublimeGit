# Changelog

All notable changes to this fork since it was taken over from upstream
SublimeGit 1.0.37. Entries are grouped by the commit that introduced them,
newest first. Upstream's own release notes (the old Package Control
`messages/` files) were removed; the original documentation at
<https://sublimegit.readthedocs.io/en/latest/> still applies.

## Distinguish staged, unstaged and untracked files in the status and commit views

### Changed

- Status view: staged file lines are scoped `markup.inserted.git-status.staged`
  so colour schemes highlight them; untracked file lines and stash lines are
  scoped `comment.git-status.untracked` and `comment.git-status.stash` so they
  are muted. Unstaged lines keep the default foreground.
- Commit message view: the "Changes to be committed", "Changes not staged for
  commit" and "Untracked files" sections of the status block are now separate
  contexts (`meta.git-commit.staged`, `.unstaged`, `.untracked`). Staged file
  lines render in the default foreground; unstaged and untracked file lines
  carry `comment.git-commit.unstaged` / `comment.git-commit.untracked` so they
  stay muted with the rest of the comment block. The `support.other.git-commit.status`
  and `support.other.git-commit.file` scopes are replaced by
  `meta.git-commit.status` and `meta.git-commit.file`.

## Add rebase, abort, undo commit, branch deletion and file log commands

### Added

- `Git: Rebase` rebases the current branch onto a chosen local or remote
  branch. Extra flags come from the new `git_rebase_flags` setting (for
  example `["--autostash"]`). The command refuses to start while a merge or
  rebase is already in progress.
- `Git: Abort Merge` and `Git: Abort Rebase` back out of an in-progress merge
  or rebase after a confirmation. The state is detected through
  `git rev-parse --git-path` (`MERGE_HEAD`, `rebase-merge`, `rebase-apply`).
- `Git: Undo Last Commit` runs `git reset --soft HEAD~1`, keeping the
  changes staged. It shows the commit subject in the confirmation, refuses on
  the root commit, and warns first when a remote-tracking branch already
  contains the commit.
- `Git: Delete Branch` lists local branches (with last subject and upstream)
  other than the current one. They are deleted with `-d`, and you are asked
  before forcing `-D` when git reports the branch is not fully merged.
- `Git: Delete Merged Branches` picks a target branch (the current branch is
  listed first), shows every local branch fully merged into it, and deletes
  them with `git branch -d`. The current branch and the target are never
  deleted.
- `Git: Log Current File` opens a read-only `*git-log*: <path>` view with
  `git log --follow --patch` for the file, in the same layout and syntax
  highlighting as `Git: Show`. `r` refreshes the view.
- `git_fetch_prune` setting (default `true`).
- `git_rebase_flags` setting (default `[]`).
- `CHANGELOG.md`, with a per-commit history of the fork; the README links to it.
- Helpers: `sort_remote_names`, `GitBranchHelper.get_branch_details`,
  `format_quick_branch_details`, `get_git_paths`, `get_in_progress`, and
  `sgit.cmd.repo_lock`.

### Changed

- `Git: Fetch`, `Git: Pull` and `Git: Pull Current Branch` pass `--prune`
  unless `git_fetch_prune` is `false`, so remote-tracking branches deleted
  upstream disappear locally.
- Remote names and remote pickers list `origin` first, then `upstream`, then
  the rest alphabetically.
- Git subprocesses are serialized per repository. Both the synchronous
  `cmd()` path and the streaming `cmd_async()` path hold a lock keyed by the
  working directory for the lifetime of the process, so a worker-thread
  status refresh and a UI-thread stage or commit can no longer race for
  `.git/index.lock`. While a long fetch or push holds the lock, a synchronous
  git call for the same repository waits for it.
- `Git: Merge` refuses to start while a merge or rebase is in progress.

### Fixed

- `Git: Fetch`, `Git: Push Current Branch` and `Git: Pull Current Branch`
  counted `git remote -v` lines instead of remote names, so a repository with
  a single remote still showed the remote picker and, when the picker was
  skipped, passed a raw `name<TAB>url (fetch)` line to git. They now use the
  unique remote names.

### Tests

- `tests/test_branching.py` covers rebase, abort merge/rebase, undo commit,
  branch deletion, prune flags, remote ordering, the log view and the
  per-repo command lock (389 tests total).
- The `sublime` stub gained `Window.show_input_panel`,
  `Window.get_output_panel` and `Window.create_output_panel`.

## 7b3a97a Cut git process and view API round-trips in status, blame, diff and commit

### Changed

- Status view: one `git status --porcelain=v2 --branch -z` yields the
  branch, upstream and file status (`parse_porcelain_v2_z`), and one
  `git config --get-regexp` yields the remote name and url. The explicit
  `update-index --refresh` calls in status and stash were dropped since
  `git status` refreshes the index itself.
- `GitStatusHelper.get_changes()` reports staged/unstaged from a single
  porcelain call; commit and quick-commit use it instead of two diffs.
- Discard fetches the worktree and staging status of the selected files in
  bulk with `git diff --name-status -z` instead of two or three git calls per
  file.
- Status view navigation uses cached `find_by_selector` regions keyed by
  `change_count()` plus bisect instead of `score_selector` per point, and
  reads file names with one `substr` per section.
- Diff view: `parse_diff` and the hunk patch builder read the buffer once and
  split in Python instead of calling `substr` on every line.
- Blame: `git blame` and its formatting run on the worker thread via
  `GitViewRefreshCmd`; the hidden `git_blame_write` command applies the
  result on the main thread. Parse errors are reported from the main thread.
  The selection listener moved to `on_selection_modified_async`.

### Tests

- The stub gained scopes (`find_by_selector`, `score_selector`),
  `change_count`, `Region.cover/intersects/intersection` and API call
  counters. New tests for commit and diff, more for blame, helpers and
  status (355 tests).

## cda0afd Convert the six syntax grammars to .sublime-syntax

### Changed

- The `syntax/*.tmLanguage` and `.JSON-tmLanguage` grammars were
  hand-converted to `.sublime-syntax` with identical regexes and scope names,
  so the status view navigation, blame, diff and keymap selectors are
  unchanged. Views use `assign_syntax()`.

### Fixed

- The Diff, Show and Commit grammars include `scope:source.diff#diffs`
  instead of `source.diff`. Sublime Text 4's own Diff syntax pushes a
  never-popping context on the first line it sees, which under the old
  include left the commit template and every `git show` header unscoped.

### Tests

- `syntax/tests/syntax_test_*.txt` pin the scopes (242 assertions); run them
  with Sublime's "Syntax Tests" build.

## 7c358cc Remove Python 2 / ST2 compatibility code and modernize the Sublime API usage

### Changed

- Dropped the `PY2`/`text_type`/`unichr` shims, every
  `sublime.version() < '3000'` branch, the dead module `reload` block, the
  Python 2 import branch in `SublimeGit.py`, coding headers, `u''` prefixes
  and the `cElementTree` fallback.
- Logging is configured only on the `SublimeGit` logger (one guarded stream
  handler, no propagation) instead of `logging.basicConfig` on the root
  logger, so the plugin no longer reconfigures every other plugin's logging.
  The handler is removed on `plugin_unloaded`.
- Multi-column quick panels (log, branches, remotes, tags, stashes,
  switch-repo, remote/tag actions, legit) use `sublime.QuickPanelItem` with
  details and annotations. Details are minihtml, so dynamic parts are
  HTML-escaped (author emails were otherwise swallowed as unknown tags).
- The `git_flow` and `legit` extensions are disabled by default via the
  `git_extensions` setting; enabling one requires a restart.
- `SublimeGit: Documentation` opens
  `https://sublimegit.readthedocs.io/en/latest/`. `SublimeGit: Version`
  reports `1.0.37-fork`.

### Fixed

- `Git: Switch Repo` always switched to the last listed repository
  regardless of the choice (the callback used the leaked loop variable).

### Removed

- Package Control `messages/` release notes and `messages.json`.

## 53c42a7 Run status and diff view refreshes off the UI thread

### Changed

- `git_status_refresh` and `git_diff_refresh` gather their git output in a
  worker thread and apply it on the main thread through the hidden
  `git_status_write` / `git_diff_write` commands, which carry over the exact
  buffer write, goto, caret and `git_diff_clean` handling. Previously the
  status view ran six git commands synchronously inside `TextCommand.run`,
  freezing the editor on every keystroke and focus change.
- Refreshes are coalesced per view: a request arriving while one is in
  flight queues a single rerun with the latest arguments, the in-flight
  result is discarded, and the final buffer always reflects the last
  request. A worker or deliver step that raises releases the view, a queued
  rerun still runs after a failure, closed views are skipped, and
  `on_pre_close` clears per-view state.
- On-focus refreshes restore the scroll position so the view no longer jumps
  to the top.

## 06e61db Rewrite the status bar updater around one git call with cache and debounce

### Changed

- The status bar message is computed with a single
  `git status --porcelain=v2 --branch --untracked-files=no` call parsed by a
  pure `parse_porcelain_v2()`, instead of five git processes per view
  activation, load and save.
- Results are cached per repo for one second (monotonic clock), concurrent
  requests for a repo are coalesced onto the running updater, and a token
  invalidated on save makes stale results get discarded and re-run. An
  updater that raises releases its repo slot so the status bar cannot wedge.
- "with unpushed" now means the branch is ahead of its upstream. The old
  diff-based check flagged branches that were only behind and missed ahead
  commits with an identical tree.
- A repo with no commits is only reported dirty when something is actually
  staged or modified.
- The status entry is erased when a view leaves a branch or repo (detached
  HEAD, non-repo file) instead of showing stale text.

## 4f90850 Fix process-cwd mutation and several small correctness bugs

### Fixed

- `Cmd.cmd()`, `cmd_async()` and the gitk command pass `cwd=` to `Popen`
  instead of calling `os.chdir`, so concurrent commands can no longer run in
  the wrong repository and the Sublime process cwd is left alone.
  `no_commits()` in `status.py` relied on the old side effect and now passes
  `cwd=repo` explicitly.
- `cmd(ignore_errors=True)` returns a 3-tuple `(0, '', '')` like the normal
  path.
- `get_remote_names()` crashed calling `.append` on a set; it returns the
  unique names.
- Blame cache entries are dropped when the view closes (`on_pre_close`)
  instead of living for the lifetime of the process.
- The `Note: ` prefix stripping in `format_error_message` could never match.
- `move_to_file` guards against `view.find()` returning `Region(-1, -1)`.

### Added

- `.python-version` (`3.14`) pins the plugin host to the version Sublime's
  own Default package uses on build 4213.
- `git_update_diff_on_focus` is documented in the settings file.

## 181520d Add regression test suite pinning current plugin behavior

### Added

- pytest suite with stub `sublime` / `sublime_plugin` modules and real-git
  fixtures (169 tests, 4 strict xfails documenting known bugs at the time).
  Covers the `Cmd` subprocess wrapper, repo discovery and parsing helpers,
  status bar and status/diff view rendering, the blame cache and util
  helpers. Run with `make venv` once, then `make test`.

## 302d5f6 Drop package-metadata.json

### Removed

- `package-metadata.json`, so Package Control treats this as a manual
  package and does not try to update or replace it.

## 1e95419 Import SublimeGit 1.0.37 and fix Python 3.14 syntax error

### Fixed

- Renamed the `async` parameter of `git_custom` to `run_async`. `async` has
  been a reserved keyword since Python 3.7, and Sublime Text 4213+ loads
  plugins under Python 3.14, which made the plugin fail to load. The `async`
  key is still accepted in user-defined commands for backward compatibility.
