# SublimeGit regression tests

Run with:

    .venv/bin/python -m pytest -q

(create the venv first with `make venv` or `python3 -m venv .venv && .venv/bin/pip install pytest`).

`tests/conftest.py` installs stub `sublime` / `sublime_plugin` modules from `tests/stubs/`
into `sys.modules` before `sgit` is imported. Tests that need a real git binary are
skipped when `git` is not on PATH.

## Known-bug markers

Behaviour that is pinned *as currently broken* is marked
`@pytest.mark.xfail(strict=True, ...)`. When the corresponding fix lands the
test turns into an XPASS, which `strict=True` reports as a failure — that is the
signal to delete the marker. There are currently no xfail markers; the ones for
the `os.chdir` cwd leak (`test_cmd.py`), `get_remote_names()` (`test_helpers.py`)
and the `GitBlameCache` cleanup (`test_blame.py`) were removed when those fixes
landed.

The "unpushed" tests in `test_status.py` (`test_ahead_with_identical_tree_is_unpushed`,
`test_behind_upstream_is_not_unpushed`) now assert the ahead-count behaviour of
the porcelain v2 status bar; the earlier tree-diff quirks they documented are gone.

## Status bar state

`sgit/status.py` keeps a module-level cache / token / in-flight table for the
status bar (`_state`). The autouse fixture in `conftest.py` calls
`reset_status_bar_state()` before and after every test so nothing leaks between
tests. `TestStatusBarCacheAndDebounce` patches `GitStatusBarUpdater.start` to a
no-op and calls `updater.run()` by hand to control exactly when a result lands.
Two of those tests (`test_updater_that_raises_releases_the_repo`,
`test_failure_to_start_releases_the_repo`) pin the failure path: an updater that
raises must still release `running[repo]`, otherwise every later request for
that repo parks in `pending` forever and the status bar goes dark permanently.

## Async view refresh state

`git_status_refresh` and `git_diff_refresh` are two-phase: `gather(request)`
runs git in a worker thread, then `apply()` runs on the main thread (via
`sublime.set_timeout`) and invokes the hidden `git_status_write` /
`git_diff_write` TextCommand to write the buffer. The per-view generation /
in-flight / rerun table lives in `sgit/status.py` (`_refresh_state`, shared by
both views); the autouse fixture calls `reset_view_refresh_state()` before and
after every test.

Threads go through one helper, `sgit.status.run_in_thread(fn)`, which
`test_status.py` monkeypatches:

* `inline_threads` fixture -- runs the worker body synchronously, so a test is
  `cmd.run(None, ...)`, `flush()`, then `run_write_commands(view)`
  (the `refresh()` helper does all three).
* `deferred_threads` fixture -- captures the worker bodies in a list so a test
  decides exactly when each gather lands (`TestStatusRefreshCoalescing`, the
  coalescing tests in `TestDiffRefreshCommand`).

The stub's `view.run_command` only records calls, so `run_write_commands(view)`
executes the recorded `git_*_write` commands by hand (and leaves any other
recorded command, e.g. `git_diff_move`, in `view.commands` for assertions).

The failure paths get their own tests, for the same reason as the status bar
ones: `test_gather_that_raises_releases_the_view`,
`test_a_deliver_that_raises_releases_the_view` and
`test_failure_to_start_the_thread_releases_the_view` all assert
`view.id() not in _refresh_state.running` afterwards -- a view left marked as
running is a permanent wedge, every later refresh of it would park in `rerun`
forever. `test_a_view_closed_mid_flight_is_not_written_to` uses the stub's
`view.close()` (which flips `is_valid()`) to pin that a tab closed between the
gather and the apply is not written to.
