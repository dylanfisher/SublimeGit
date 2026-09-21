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
