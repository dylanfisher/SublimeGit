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
signal to delete the marker. Currently:

* `test_cmd.py` – `Cmd.cmd()` / `Cmd.cmd_async()` `os.chdir()` into `cwd`,
  changing the cwd of the whole process (→ `Popen(cwd=...)`).
* `test_helpers.py` – `get_remote_names()` calls `.append()` on a `set`.
* `test_blame.py` – `GitBlameEventListener` has no `on_close`, so
  `GitBlameCache` never shrinks.

The "unpushed" tests in `test_status.py` are *not* xfailed, because the current
`git diff @{upstream}..` check does produce a message; the two tests
`test_ahead_but_identical_tree_is_not_reported_as_unpushed` and
`test_behind_upstream_is_reported_as_unpushed` document the wrong message and
their docstrings say what it should become once the ahead-count check lands.
