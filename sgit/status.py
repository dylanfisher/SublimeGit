import os
import bisect
import logging
import threading
import time
from functools import partial

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .util import abbreviate_dir, find_view_by_settings, noop, get_setting, get_executable
from .cmd import GitCmd
from .helpers import GitStatusHelper, GitRemoteHelper, GitStashHelper, GitErrorHelper


logger = logging.getLogger('SublimeGit.status')

GOTO_DEFAULT = 'file:1'

GIT_STATUS_VIEW_TITLE_PREFIX = '*git-status*: '
GIT_STATUS_VIEW_SYNTAX = 'Packages/SublimeGit/syntax/SublimeGit Status.sublime-syntax'
GIT_STATUS_VIEW_SETTINGS = {
    'translate_tabs_to_spaces': False,
    'draw_white_space': 'none',
    'word_wrap': False,
    'git_status': True,
}

STASHES = "stashes"
UNTRACKED_FILES = "untracked_files"
UNSTAGED_CHANGES = "unstaged_changes"
STAGED_CHANGES = "staged_changes"
CHANGES = "changes"  # pseudo-section to ignore staging area

SECTIONS = {
    STASHES: 'Stashes:\n',
    UNTRACKED_FILES: 'Untracked files:\n',
    UNSTAGED_CHANGES: 'Unstaged changes:\n',
    STAGED_CHANGES: 'Staged changes:\n',
    CHANGES: 'Changes:\n',
}

SECTION_ORDER = (
    STASHES,
    UNTRACKED_FILES,
    UNSTAGED_CHANGES,
    STAGED_CHANGES,
    CHANGES,
)


SECTION_SELECTOR_PREFIX = 'meta.git-status.'

STATUS_LABELS = {
    ' ': 'Unmodified',
    'M': 'Modified  ',
    'A': 'Added     ',
    'D': 'Deleted   ',
    'R': 'Renamed   ',
    'C': 'Copied    ',
    'U': 'Unmerged  ',
    '?': 'Untracked ',
    '!': 'Ignored   ',
    'T': 'Typechange'
}

GIT_WORKING_DIR_CLEAN = "Nothing to commit (working directory clean)"

GIT_STATUS_HELP = """
# Movement:
#    r = refresh status
#    1-5 = jump to section
#    n = next item, N = next section
#    p = previous item, P = previous section
#
# Staging:
#    s = stage file/section, S = stage all unstaged files
#    ctrl+shift+s = stage all unstaged and untracked files
#    u = unstage file/section, U = unstage all files
#    backspace = discard file/section, shift+backspace = discard everything
#
# Commit:
#    c = commit, C = commit -a (add unstaged)
#    ctrl+shift+c = commit --amend (amend previous commit)
#
# Other:
#    i = ignore file, I = ignore pattern
#    enter = open file
#    d = view diff
#
# Stashes:
#    a = apply stash, A = pop stash
#    z = create stash, Z = create stash including untracked files
#    backspace = discard stash"""


class GitStatusBuilder(GitCmd, GitStatusHelper, GitRemoteHelper, GitStashHelper):

    def build_status(self, repo):
        # One `git status --porcelain=v2 --branch` gives the branch, its
        # upstream and the file status; the remote name and url come from a
        # single `git config --get-regexp`.
        branch, upstream, status_lines = self.get_branch_and_status(repo)
        remote, remote_url = self.get_remote_and_url(repo, branch)

        abbrev_dir = abbreviate_dir(repo)

        head_rc, head, _ = self.git(['log', '--max-count=1', '--abbrev-commit', '--pretty=oneline'], cwd=repo)

        status = ""
        if remote:
            status += "Remote:   %s @ %s\n" % (remote, remote_url)
        status += "Local:    %s %s\n" % (branch if branch else '(no branch)', abbrev_dir)
        status += "Head:     %s\n" % ("nothing committed (yet)" if head_rc != 0 else head)
        status += "\n"

        # no `update-index --refresh` here: the status call above already
        # refreshed the index and wrote it back

        status += self.build_stashes(repo)
        status += self.build_files_status(repo, status_lines)

        if get_setting('git_show_status_help', True):
            status += GIT_STATUS_HELP

        return status

    def build_stashes(self, repo):
        status = ""

        stashes = self.get_stashes(repo)
        if stashes:
            status += SECTIONS[STASHES]
            for name, title in stashes:
                status += "\t%s: %s\n" % (name, title)
            status += "\n"

        return status

    def build_files_status(self, repo, status_lines=None):
        # get status (``status_lines`` reuses an already fetched status)
        status = ""
        untracked, unstaged, staged = self.get_files_status(repo, status_lines)

        if not untracked and not unstaged and not staged:
            status += GIT_WORKING_DIR_CLEAN + "\n"

        # untracked files
        if untracked:
            status += SECTIONS[UNTRACKED_FILES]
            for s, f in untracked:
                status += "\t%s\n" % f.strip()
            status += "\n"

        # unstaged changes
        if unstaged:
            status += SECTIONS[UNSTAGED_CHANGES] if staged else SECTIONS[CHANGES]
            for s, f in unstaged:
                status += "\t%s %s\n" % (STATUS_LABELS[s], f)
            status += "\n"

        # staged changes
        if staged:
            status += SECTIONS[STAGED_CHANGES]
            for s, f in staged:
                status += "\t%s %s\n" % (STATUS_LABELS[s], f)
            status += "\n"

        return status


class GitStatusTextCmd(GitCmd):

    def run(self, edit, *args):
        sublime.error_message("Unimplemented!")

    # status update
    def update_status(self, goto=None):
        self.view.run_command('git_status_refresh', {'goto': goto})

    # selection commands
    def get_first_point(self):
        sels = self.view.sel()
        if sels:
            return sels[0].begin()

    def get_all_points(self):
        sels = self.view.sel()
        return [s.begin() for s in sels]

    # line helpers
    def get_selected_lines(self):
        sels = self.view.sel()
        selected_lines = []
        for selection in sels:
            lines = self.view.lines(selection)
            for line in lines:
                if self.view.score_selector(line.begin(), 'meta.git-status.line') > 0:
                    selected_lines.append(line)
        return selected_lines

    # stash helpers
    def get_all_stash_regions(self):
        return self.view.find_by_selector('meta.git-status.stash.name')

    def get_all_stashes(self):
        stashes = self.get_all_stash_regions()
        return [(self.view.substr(s), self.view.substr(self.view.line(s)).strip()) for s in stashes]

    def get_selected_stashes(self):
        stashes = []
        lines = self.get_selected_lines()

        if lines:
            for s in self.get_all_stash_regions():
                for l in lines:
                    if l.contains(s):
                        name = self.view.substr(s)
                        title = self.view.substr(self.view.line(s)).strip()
                        stashes.append((name, title))
        return stashes

    # file helpers
    def get_all_file_regions(self):
        return self.view.find_by_selector('meta.git-status.file')

    def get_all_files(self):
        files = self.get_all_file_regions()
        # find_by_selector() returns sorted, non-overlapping regions, so one
        # substr() covers them all instead of one API call per file.
        return list(zip((self.section_at_region(f) for f in files),
                        self.regions_text(files)))

    def get_selected_file_regions(self):
        files = []
        lines = self.get_selected_lines()

        if not lines:
            return files

        for f in self.get_all_file_regions():
            for l in lines:
                if l.contains(f):
                    # check for renamed
                    linestr = self.view.substr(l).strip()
                    if linestr.startswith(STATUS_LABELS['R']) and ' -> ' in linestr:
                        names = self.view.substr(f)
                        # find position of divider
                        e = names.find(' -> ')
                        s = e + 4
                        # add both files
                        f1 = sublime.Region(f.begin(), f.begin() + e)
                        f2 = sublime.Region(f.begin() + s, f.end())
                        files.append((self.section_at_region(f), f1))
                        files.append((self.section_at_region(f), f2))
                    else:
                        files.append((self.section_at_region(f), f))

        return files

    def get_selected_files(self):
        return [(s, self.view.substr(f)) for s, f in self.get_selected_file_regions()]

    def get_status_lines(self):
        lines = []
        chunks = self.view.find_by_selector('meta.git-status.line')
        for c in chunks:
            lines.extend(self.view.lines(c))
        return lines

    # section helpers
    def get_sections(self):
        sections = self.view.find_by_selector('constant.other.git-status.header')
        return sections

    def get_section_regions(self):
        """``[(begin, end, section)]`` for every section block, sorted by begin.

        ``meta.git-status.<section>`` is the meta_scope of a whole section
        block, so one ``find_by_selector`` per section gives every block in a
        single native call instead of scoring each point separately.

        The result is cached on the command instance (TextCommand instances
        are reused across runs) and keyed by ``view.change_count()``, which
        only moves when ``git_status_write`` rewrites the buffer.
        """
        view = self.view
        change_count = getattr(view, 'change_count', None)
        key = (view.id(), change_count() if change_count is not None else None)

        cached = getattr(self, '_section_regions_cache', None)
        if cached is not None and cached[0] == key and key[1] is not None:
            return cached[1]

        regions = []
        for section in SECTION_ORDER:
            # No `meta.git-status.changes` scope exists: the CHANGES
            # pseudo-section header is scoped as unstaged_changes.
            for r in view.find_by_selector(SECTION_SELECTOR_PREFIX + section):
                regions.append((r.begin(), r.end(), section))
        regions.sort()

        self._section_regions_cache = (key, regions)
        return regions

    def section_at_point(self, point):
        regions = self.get_section_regions()
        if not regions:
            return None
        idx = bisect.bisect_right(regions, (point, float('inf'))) - 1
        if idx < 0:
            return None
        begin, end, section = regions[idx]
        if begin <= point < end:
            return section
        return None

    def section_at_region(self, region):
        return self.section_at_point(region.begin())

    def regions_text(self, regions):
        """The text of ``regions`` (sorted, non-overlapping) in one substr."""
        if not regions:
            return []
        start = regions[0].begin()
        covering = self.view.substr(sublime.Region(start, regions[-1].end()))
        return [covering[r.begin() - start:r.end() - start] for r in regions]

    # goto helpers
    def logical_goto_next_file(self):
        goto = "file:1"
        files = self.get_selected_files()
        if files:
            section, filename = files[-1]
            goto = "file:%s:%s" % (filename, section)
        return goto

    def logical_goto_next_stash(self):
        goto = "stash:1"
        stashes = self.get_selected_stashes()
        if stashes:
            goto = "stash:%s:stashes" % (stashes[-1][0])
        return goto


class GitStatusMoveCmd(GitStatusTextCmd):

    def goto(self, goto):
        what, which, where = self.parse_goto(goto)
        if what == "section":
            self.move_to_section(which, where)
        elif what == "item":
            self.move_to_item(which, where)
        elif what == "file":
            self.move_to_file(which, where)
        elif what == "stash":
            self.move_to_stash(which, where)
        elif what == "point":
            try:
                point = int(which)
                self.move_to_point(point)
            except ValueError:
                pass

    def parse_goto(self, goto):
        what, which, where = None, None, None
        parts = goto.split(':')
        what = parts[0]
        if len(parts) > 1:
            try:
                which = int(parts[1])
            except ValueError:
                which = parts[1]
        if len(parts) > 2:
            try:
                where = int(parts[2])
            except ValueError:
                where = parts[2]
        return (what, which, where)

    def move_to_point(self, point):
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(point))

        #if not self.view.visible_region().contains(point):
        pointrow, _ = self.view.rowcol(point)
        pointstart = self.view.text_point(max(pointrow - 3, 0), 0)
        pointend = self.view.text_point(pointrow + 3, 0)

        pointregion = sublime.Region(pointstart, pointend)

        if pointrow < 10:
            self.view.set_viewport_position((0.0, 0.0), False)
        elif not self.view.visible_region().contains(pointregion):
            self.view.show(pointregion, False)

        #sublime.set_timeout(partial(self.adjust_viewport, point), 0)

    # def adjust_viewport(self, point):
    #     _, view_begin = self.view.viewport_position()
    #     _, view_height = self.view.viewport_extent()
    #     view_end = view_begin + view_height

    #     _, point_begin = self.view.text_to_layout(point)
    #     point_end = point_begin + self.view.line_height()

    #     underflow = max(view_begin - point_begin, 0)
    #     overflow = max(point_end - view_end, 0)

    #     if overflow > 0:
    #         self.view.set_viewport_position((0.0, view_begin + overflow + 5), False)
    #     elif underflow > 0:
    #         self.view.set_viewport_position((0.0, view_begin - underflow - 5), False)

    def move_to_region(self, region):
        self.move_to_point(self.view.line(region).begin())

    def prev_region(self, regions, point):
        before = [r for r in regions if self.view.line(r).end() < point]
        return before[-1] if before else regions[-1]

    def next_region(self, regions, point):
        after = [r for r in regions if self.view.line(r).begin() > point]
        return after[0] if after else regions[0]

    def next_or_prev_region(self, direction, regions, point):
        if direction == "next":
            return self.next_region(regions, point)
        else:
            return self.prev_region(regions, point)

    def move_to_section(self, which, where=None):
        if which in range(1, 5):
            sections = self.get_sections()
            if sections and len(sections) >= which:
                section = sections[which - 1]
                self.move_to_region(section)
        elif which in SECTIONS:
            sections = self.get_sections()
            for section in sections:
                if self.section_at_region(section) == which:
                    self.move_to_region(section)
                    return
        elif which in ('next', 'prev'):
            point = self.get_first_point()
            sections = self.get_sections()
            if point and sections:
                next = self.next_or_prev_region(which, sections, point)
                self.move_to_region(next)

    def move_to_item(self, which=1, where=None):
        if which in ('next', 'prev'):
            point = self.get_first_point()
            regions = self.get_status_lines()
            if point and regions:
                next = self.next_or_prev_region(which, regions, point)
                self.move_to_region(next)

    def move_to_file(self, which=1, where=None):
        if isinstance(which, int):
            files = self.get_all_file_regions()
            if files:
                if len(files) >= which:
                    self.move_to_region(self.view.line(files[which - 1]))
                else:
                    self.move_to_region(self.view.line(files[-1]))
            elif self.get_all_stash_regions():
                self.move_to_stash(1)
            else:
                region = self.view.find(GIT_WORKING_DIR_CLEAN, 0, sublime.LITERAL)
                if region.begin() != -1:
                    self.move_to_region(region)
        elif which in ('next', 'prev'):
            point = self.get_first_point()
            regions = self.get_all_file_regions()
            if point and regions:
                next = self.next_or_prev_region(which, regions, point)
                self.move_to_region(next)
        elif which and where:
            regions = self.get_all_file_regions()
            by_section = {}
            for r in regions:
                by_section.setdefault(self.section_at_region(r), []).append(r)

            section_regions = by_section.get(where)
            if section_regions:
                # One substr for the whole section instead of two per file.
                names = self.regions_text(section_regions)
                next = section_regions[-1]
                for region, name in zip(section_regions, names):
                    if name >= which:
                        next = region
                        break
                self.move_to_region(next)
            else:
                idx = SECTION_ORDER.index(where)
                while idx > 0:
                    idx -= 1
                    section = SECTION_ORDER[idx]
                    if section in by_section:
                        self.move_to_region(by_section[section][-1])
                        return
                self.move_to_file(1)

    def move_to_stash(self, which, where=None):
        if which is not None and where:
            which = str(which)
            stash_regions = self.get_all_stash_regions()
            if stash_regions:
                prev_regions = [r for r in stash_regions if self.view.substr(r) < which]
                next_regions = [r for r in stash_regions if self.view.substr(r) >= which]
                if next_regions:
                    next = next_regions[0]
                else:
                    next = prev_regions[-1]
                self.move_to_region(next)
            else:
                self.move_to_file(1)
        elif isinstance(which, int):
            stashes = self.get_all_stash_regions()
            if stashes:
                if len(stashes) >= which:
                    self.move_to_region(self.view.line(stashes[which - 1]))
                else:
                    self.move_to_region(self.view.line(stashes[-1]))


# Async view refresh ------------------------------------------------------
#
# The status and diff views are refreshed in two phases: a *gather* phase
# that runs git in a worker thread and returns plain text, and an *apply*
# phase on the main thread that writes the buffer and places the caret. The
# apply phase needs an ``edit`` object, so it is a hidden TextCommand
# (``git_status_write`` / ``git_diff_write``) invoked via ``view.run_command``.


def run_in_thread(fn):
    """Run ``fn`` in a daemon thread. Tests monkeypatch this to run inline."""
    thread = threading.Thread(target=fn)
    thread.daemon = True
    thread.start()
    return thread


class _ViewRefreshState(object):
    """Process-wide bookkeeping for in-flight view refreshes, guarded by ``lock``.

    * ``generation`` -- ``{view_id: int}``; bumped by every request. A worker
                        captures the generation when it starts and its result
                        is dropped if the generation moved on before it lands.
    * ``running``    -- ``{view_id}``: views with a worker in flight.
    * ``rerun``      -- ``{view_id: (generation, request)}``: the latest request
                        that arrived while a worker was running. Exactly one
                        more worker runs for it once the current one lands.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.generation = {}
        self.running = set()
        self.rerun = {}

    def begin(self, view_id, request):
        """Register a request. Returns the generation to run with, or ``None``
        when a worker is already running (the request is queued as a rerun
        and the running worker's result becomes stale)."""
        with self.lock:
            generation = self.generation.get(view_id, 0) + 1
            self.generation[view_id] = generation
            if view_id in self.running:
                self.rerun[view_id] = (generation, request)
                return None
            self.running.add(view_id)
            return generation

    def is_current(self, view_id, generation):
        with self.lock:
            return self.generation.get(view_id) == generation

    def finish(self, view_id, ok=True):
        """Mark the running worker as landed.

        Returns ``(generation, request)`` of a queued rerun, in which case the
        view stays marked as running and the caller must spawn it; otherwise
        ``None`` and the view is released. A queued rerun is honoured even
        when this worker failed (``ok=False``): it is a distinct, newer request
        (so it cannot loop), and the failure may have been transient.
        """
        with self.lock:
            queued = self.rerun.pop(view_id, None)
            if view_id not in self.running:
                return None
            if queued is not None:
                return queued
            self.running.discard(view_id)
            return None

    def forget(self, view_id):
        with self.lock:
            self.generation.pop(view_id, None)
            self.running.discard(view_id)
            self.rerun.pop(view_id, None)


_refresh_state = _ViewRefreshState()


def reset_view_refresh_state():
    """Forget all view refresh generations, in-flight workers and reruns (tests)."""
    with _refresh_state.lock:
        _refresh_state.reset()


def forget_view_refresh(view_id):
    """Drop the refresh bookkeeping of a view that is being closed."""
    _refresh_state.forget(view_id)


class GitViewRefreshCmd(object):
    """Mixin for a TextCommand that refreshes its view in two phases.

    Subclasses implement ``gather(request) -> result`` (worker thread; git
    only, no view mutation) and ``deliver(request, result)`` (main thread;
    runs the hidden write command). ``request`` is a plain dict captured on
    the main thread when the refresh was asked for.
    """

    def request_refresh(self, request):
        """Start a refresh, or queue it behind the one already running.
        Returns ``True`` when a worker was spawned."""
        generation = _refresh_state.begin(self.view.id(), request)
        if generation is None:
            return False
        self.spawn(generation, request)
        return True

    def spawn(self, generation, request):
        try:
            run_in_thread(partial(self.work, generation, request))
        except Exception:
            # Thread creation failed; release the view or every later request
            # for it would be queued behind a worker that never runs.
            _refresh_state.finish(self.view.id(), ok=False)
            logger.warning('could not start refresh for view %s', self.view.id(), exc_info=True)

    def work(self, generation, request):
        """Worker body: gather, then hand the result to the main thread."""
        result, ok = None, True
        try:
            result = self.gather(request)
        except Exception:
            # Must not escape: a worker that dies without calling finish()
            # leaves its view marked as running forever.
            ok = False
            logger.warning('refresh failed for view %s', self.view.id(), exc_info=True)
        sublime.set_timeout(partial(self.apply, generation, request, result, ok), 0)

    def apply(self, generation, request, result, ok):
        """Main thread: write the result unless it is stale, then run the
        rerun that was queued while the worker was busy, if any."""
        view_id = self.view.id()
        try:
            if ok and self.view_is_valid() and _refresh_state.is_current(view_id, generation):
                self.deliver(request, result)
        except Exception:
            # finish() below must run whatever happens: an apply that escapes
            # would leave the view marked as running forever.
            ok = False
            logger.warning('could not apply refresh for view %s', view_id, exc_info=True)
        rerun = _refresh_state.finish(view_id, ok=ok)
        if rerun is not None:
            self.spawn(*rerun)

    def view_is_valid(self):
        """False when the view was closed between the request and the apply.
        ``is_valid`` is ST3+; treat its absence as valid."""
        is_valid = getattr(self.view, 'is_valid', None)
        return True if is_valid is None else bool(is_valid())

    def gather(self, request):
        raise NotImplementedError

    def deliver(self, request, result):
        raise NotImplementedError


class GitStatusCommand(WindowCommand, GitStatusBuilder):
    """
    Documentation coming soon.
    """

    def run(self, refresh_only=False):
        repo = self.get_repo(silent=True if refresh_only else False)
        if not repo:
            return

        title = GIT_STATUS_VIEW_TITLE_PREFIX + os.path.basename(repo)

        view = find_view_by_settings(self.window, git_view='status', git_repo=repo)
        if not view and not refresh_only:
            view = self.window.new_file()

            view.set_name(title)
            view.assign_syntax(GIT_STATUS_VIEW_SYNTAX)
            view.set_scratch(True)
            view.set_read_only(True)

            view.settings().set('git_view', 'status')
            view.settings().set('git_repo', repo)
            view.settings().set('__vi_external_disable', get_setting('git_status_disable_vintageous') is True)

            for key, val in GIT_STATUS_VIEW_SETTINGS.items():
                view.settings().set(key, val)

        if view is not None:
            self.window.focus_view(view)
            view.run_command('git_status_refresh')


class GitStatusRefreshCommand(TextCommand, GitViewRefreshCmd, GitStatusBuilder):
    """Refresh the status view: ``build_status`` runs in a worker thread, the
    buffer is written by ``git_status_write`` on the main thread."""
    _lpop = False

    def is_visible(self):
        return False

    def run(self, edit, goto=None):
        if not self.view.settings().get('git_view') == 'status':
            return

        repo = self.get_repo()
        if not repo:
            return

        self.request_refresh({
            'repo': repo,
            'goto': goto,
            'viewport': list(self.view.viewport_position()),
        })

    def gather(self, request):
        return self.build_status(request['repo'])

    def deliver(self, request, status):
        if not status:
            return
        self.view.run_command('git_status_write', {
            'content': status,
            'goto': request['goto'],
            'viewport': request['viewport'],
        })


class GitStatusWriteCommand(TextCommand, GitStatusMoveCmd):
    """Apply phase of ``git_status_refresh``: replace the buffer and place the
    caret. Hidden; only invoked from the main thread by the refresh."""

    def is_visible(self):
        return False

    def run(self, edit, content='', goto=None, viewport=None):
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(True)

        if goto:
            self.goto(goto)
        else:
            self.goto(GOTO_DEFAULT)

        # A refresh on focus keeps the caret where it was; also keep the
        # scroll position so the view does not jump. move_to_point() has
        # already scrolled (it snaps anything in the first 10 rows to the
        # top), so undo that -- but the new buffer may be shorter or shaped
        # differently, so make sure the caret did not end up off-screen:
        # show() is a no-op when the point is already visible.
        if viewport is not None and goto and goto.startswith('point:'):
            self.view.set_viewport_position(tuple(viewport), False)
            if self.view.sel():
                self.view.show(self.view.sel()[0].begin(), False)


class GitStatusEventListener(EventListener):

    def on_activated(self, view):
        if view.settings().get('git_view') == 'status' and get_setting('git_update_status_on_focus', True):
            goto = None
            if view.sel():
                goto = "point:%s" % view.sel()[0].begin()
            view.run_command('git_status_refresh', {'goto': goto})

    def on_pre_close(self, view):
        forget_view_refresh(view.id())


# Status bar ---------------------------------------------------------------

# How long (seconds) a computed status bar message stays valid for a repo.
# Bursts of view events (activate + load, tab switching) within this window
# reuse the cached message instead of spawning another git process.
STATUS_BAR_CACHE_TTL = 1.0

STATUS_BAR_KEY = 'git-status'


def _apply_status_bar_message(view, msg):
    """Show ``msg`` in ``view``'s status bar, or clear the entry when there is
    nothing to show (detached HEAD, not a repository), so a stale "On main"
    never lingers after the view moves out of a branch. Always goes through
    ``sublime.set_timeout`` so it is safe to call from a worker thread."""
    if msg is None:
        sublime.set_timeout(partial(view.erase_status, STATUS_BAR_KEY), 0)
    else:
        sublime.set_timeout(partial(view.set_status, STATUS_BAR_KEY, msg), 0)


def parse_porcelain_v2(text):
    """Parse the output of ``git status --porcelain=v2 --branch``.

    Pure function. Returns a dict with:

    * ``branch``   -- branch name, or ``None`` when HEAD is detached
    * ``oid``      -- HEAD commit, or ``None`` for an unborn branch (``(initial)``)
    * ``upstream`` -- upstream ref name, or ``None`` when none is configured
    * ``ahead`` / ``behind`` -- ints; 0 when there is no upstream
    * ``staged`` / ``unstaged`` / ``unmerged`` -- bools

    Untracked (``?``) and ignored (``!``) entries are ignored.
    """
    info = {
        'branch': None,
        'oid': None,
        'upstream': None,
        'ahead': 0,
        'behind': 0,
        'staged': False,
        'unstaged': False,
        'unmerged': False,
    }

    for line in text.splitlines():
        if not line:
            continue

        if line.startswith('# '):
            key, _, value = line[2:].partition(' ')
            if key == 'branch.head':
                info['branch'] = None if value == '(detached)' else value
            elif key == 'branch.oid':
                info['oid'] = None if value == '(initial)' else value
            elif key == 'branch.upstream':
                info['upstream'] = value or None
            elif key == 'branch.ab':
                for token in value.split():
                    try:
                        if token.startswith('+'):
                            info['ahead'] = int(token[1:])
                        elif token.startswith('-'):
                            info['behind'] = int(token[1:])
                    except ValueError:
                        pass
            continue

        kind = line[0]
        if kind in ('1', '2') and len(line) >= 4:
            x, y = line[2], line[3]
            if x != '.':
                info['staged'] = True
            if y != '.':
                info['unstaged'] = True
        elif kind == 'u':
            info['unmerged'] = True
        # '?' (untracked) and '!' (ignored) do not affect the status bar

    return info


def format_status_bar_message(info, kind, repo):
    """Build the status bar text from a ``parse_porcelain_v2`` result.

    Returns ``None`` when there is nothing to show (detached HEAD).
    """
    branch = info.get('branch')
    if not branch:
        return None

    if kind == 'simple':
        return 'On {branch}'.format(branch=branch)

    dirty = info.get('staged') or info.get('unstaged') or info.get('unmerged')
    return 'On {branch}{dirty} in {repo}{unpushed}'.format(
        branch=branch,
        dirty='*' if dirty else '',
        repo=os.path.basename(repo),
        unpushed=' with unpushed' if info.get('ahead', 0) > 0 else '',
    )


class _StatusBarState(object):
    """Process-wide bookkeeping for status bar updates, guarded by ``lock``.

    * ``cache``   -- ``{repo: (timestamp, kind, msg)}``; ``msg`` may be ``None``
    * ``tokens``  -- ``{repo: int}``; bumped by ``invalidate()``. An updater
                     captures the token when it is created and its result is
                     discarded if the token has moved on by the time it lands.
    * ``running`` -- ``{repo: updater}`` for the one in-flight updater per repo
    * ``pending`` -- ``{repo: {view_id: view}}``: views that asked for a status
                     while an updater was already running and will receive its
                     result when it lands.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.cache = {}
        self.tokens = {}
        self.running = {}
        self.pending = {}

    def token(self, repo):
        return self.tokens.get(repo, 0)

    def invalidate(self, repo):
        with self.lock:
            self.tokens[repo] = self.tokens.get(repo, 0) + 1
            self.cache.pop(repo, None)

    def cached(self, repo, kind, now):
        """Return ``(hit, msg)`` for a cache entry younger than the TTL."""
        entry = self.cache.get(repo)
        if entry is None:
            return False, None
        timestamp, cached_kind, msg = entry
        if cached_kind != kind or now - timestamp >= STATUS_BAR_CACHE_TTL:
            return False, None
        return True, msg

    def complete(self, updater, msg, timestamp, ok=True):
        """Record an updater's result and release its repo slot.

        Returns ``(apply_to, retry_for)``: the views to apply ``msg`` to, and
        the views whose request must be re-run because the result is stale.

        ``ok=False`` means the updater raised: the repo is released so later
        requests can spawn again, but nothing is cached, applied or retried
        (retrying would most likely fail the same way, in a loop).
        """
        with self.lock:
            views = {updater.view.id(): updater.view}
            if self.running.get(updater.repo) is updater:
                del self.running[updater.repo]
                views.update(self.pending.pop(updater.repo, {}))

            if not ok:
                return [], []

            if self.tokens.get(updater.repo, 0) != updater.token:
                return [], list(views.values())

            self.cache[updater.repo] = (timestamp, updater.kind, msg)
            return list(views.values()), []

    def release(self, updater):
        """Free the repo slot of an updater that never got to run."""
        with self.lock:
            if self.running.get(updater.repo) is updater:
                del self.running[updater.repo]
                self.pending.pop(updater.repo, None)


_state = _StatusBarState()


def reset_status_bar_state():
    """Forget all cached messages, tokens and in-flight updaters (tests)."""
    with _state.lock:
        _state.reset()


def invalidate_status_bar_cache(repo):
    """Drop the cached message for ``repo`` and mark in-flight results stale."""
    _state.invalidate(repo)


def request_status_bar_update(bin, encoding, fallback, repo, kind, view):
    """Ask for ``view``'s status bar to show the state of ``repo``.

    Applies a fresh cached message immediately (via ``sublime.set_timeout``),
    joins an updater that is already running for ``repo``, or spawns a new
    one. Returns the spawned ``GitStatusBarUpdater`` or ``None``.
    """
    with _state.lock:
        hit, msg = _state.cached(repo, kind, time.monotonic())
        if hit:
            updater = None
        elif repo in _state.running:
            _state.pending.setdefault(repo, {})[view.id()] = view
            return None
        else:
            updater = GitStatusBarUpdater(bin, encoding, fallback, repo, kind, view)
            _state.running[repo] = updater

    if updater is None:
        _apply_status_bar_message(view, msg)
        return None

    try:
        updater.start()
    except Exception:
        # Thread creation failed; release the repo or every later request for
        # it would be parked in ``pending`` behind an updater that never runs.
        _state.release(updater)
        logger.warning('could not start status bar updater for %s', repo, exc_info=True)
        return None
    return updater


class GitStatusBarUpdater(threading.Thread, GitCmd):
    """Run one ``git status`` off the UI thread and deliver the message.

    The message is applied with ``view.set_status`` on the main thread via
    ``sublime.set_timeout``. Nothing is scheduled when there is no message
    (detached HEAD, not a repo, git failure).
    """
    _lpop = False

    STATUS_COMMAND = ['status', '--porcelain=v2', '--branch', '--untracked-files=no']

    def __init__(self, bin, encoding, fallback, repo, kind, view, token=None, *args, **kwargs):
        super(GitStatusBarUpdater, self).__init__(*args, **kwargs)
        self.daemon = True
        self.bin = bin
        self.encoding = encoding
        self.fallback = fallback
        self.repo = repo
        self.kind = kind
        self.view = view
        self.token = _state.token(repo) if token is None else token

    def build_command(self, cmd):
        return self.bin + self.opts + [c for c in cmd if c]

    def compute(self):
        """Return the status bar message, or ``None`` if nothing should be shown."""
        exit_code, stdout, _ = self.git(self.STATUS_COMMAND, cwd=self.repo, ignore_errors=True,
                                        encoding=self.encoding, fallback=self.fallback)
        if exit_code != 0:
            return None
        return format_status_bar_message(parse_porcelain_v2(stdout), self.kind, self.repo)

    def run(self):
        timestamp = time.monotonic()
        msg, ok = None, True
        try:
            msg = self.compute()
        except Exception:
            # Must not escape: an updater that dies without calling complete()
            # leaves its repo marked as running forever, so every later request
            # would be parked in ``pending`` and never applied -- a permanent
            # status bar outage for that repo.
            ok = False
            logger.warning('status bar update failed for %s', self.repo, exc_info=True)

        apply_to, retry_for = _state.complete(self, msg, timestamp, ok=ok)

        for view in apply_to:
            _apply_status_bar_message(view, msg)

        # The repo changed while we were running (a save came in); re-run for
        # everybody who was waiting so they do not end up with a stale message.
        for view in retry_for:
            request_status_bar_update(self.bin, self.encoding, self.fallback, self.repo, self.kind, view)


class GitStatusBarEventListener(EventListener, GitCmd):
    _lpop = False

    def on_activated_async(self, view):
        self.set_status(view)

    def on_load_async(self, view):
        self.set_status(view)

    def on_post_save_async(self, view):
        self.set_status(view, invalidate=True)

    def set_status(self, view, invalidate=False):
        kind = get_setting('git_status_bar', 'fancy')
        if kind not in ('fancy', 'simple'):
            return

        repo = self.get_repo_from_view(view)
        if not repo:
            # e.g. switched from a repo file to one outside any repo
            _apply_status_bar_message(view, None)
            return

        if invalidate:
            # a save changes dirtiness; never serve the pre-save message
            invalidate_status_bar_cache(repo)

        bin = get_executable('git', self.bin)
        encoding = get_setting('encoding', 'utf-8')
        fallback = get_setting('fallback_encodings', [])

        request_status_bar_update(bin, encoding, fallback, repo, kind, view)


class GitQuickStatusCommand(WindowCommand, GitCmd, GitStatusHelper):
    """
    Show an abbreviated status in the quick bar.

    As an alternative to the full status window, a list of changed files is presented
    the quick bar. Next to each filename there is an abbreviation, denoting the files
    status.

    This status contains 2 characters, X and Y. For paths with merge conflicts, X and Y show the
    modification states of each side of the merge. For paths that do not have merge conflicts,
    X shows the status of the index, and Y shows the status of the work tree.

    The statuses are as follows:

    * **' '** = unmodified
    * **M** = modified
    * **A** = added
    * **D** = deleted
    * **R** = renamed
    * **C** = copied
    * **U** = updated but unmerged
    * **?** = untracked

    Selecting an entry in the list will bring up a diff view of the file.
    """

    def run(self):
        repo = self.get_repo()
        if not repo:
            return

        status = self.get_porcelain_status(repo)
        if not status:
            status = [GIT_WORKING_DIR_CLEAN]

        def on_done(idx):
            if idx == -1 or status[idx] == GIT_WORKING_DIR_CLEAN:
                return
            state, filename = status[idx][0:2], status[idx][3:]
            index, worktree = state
            if state == '??':
                return sublime.error_message("Cannot show diff for untracked files.")

            window = self.window
            if worktree != ' ':
                window.run_command('git_diff', {'repo': repo, 'path': filename})
            if index != ' ':
                window.run_command('git_diff', {'repo': repo, 'path': filename, 'cached': True})

        self.window.show_quick_panel(status, on_done, sublime.MONOSPACE_FONT)


class GitStatusMoveCommand(TextCommand, GitStatusMoveCmd):

    def is_visible(self):
        return False

    def run(self, edit, goto="file:1"):
        self.goto(goto)


class GitStatusStageCommand(TextCommand, GitStatusTextCmd):

    def run(self, edit, stage="file"):
        repo = self.get_repo()
        if not repo:
            return

        goto = None
        if stage == "all":
            self.add_all(repo)
        elif stage == "unstaged":
            self.add_all_unstaged(repo)
        elif stage == "section":
            points = self.get_all_points()
            sections = set([self.section_at_point(p) for p in points])
            if UNTRACKED_FILES in sections and UNSTAGED_CHANGES in sections:
                self.add_all(repo)
            elif UNSTAGED_CHANGES in sections:
                self.add_all_unstaged(repo)
            elif UNTRACKED_FILES in sections:
                self.add_all_untracked(repo)
        elif stage == "file":
            files = self.get_selected_files()
            untracked = [f for s, f in files if s in (UNTRACKED_FILES,)]
            unstaged = [f for s, f in files if s in (UNSTAGED_CHANGES,)]
            if untracked:
                self.add(repo, untracked)
            if unstaged:
                self.add_update(repo, unstaged)
            goto = self.logical_goto_next_file()

        self.update_status(goto)

    def add(self, repo, files):
        return self.git(['add', '--'] + files, cwd=repo)

    def add_update(self, repo, files):
        return self.git(['add', '--update', '--'] + files, cwd=repo)

    def add_all(self, repo):
        return self.git(['add', '--all'], cwd=repo)

    def add_all_unstaged(self, repo):
        return self.git(['add', '--update', '.'], cwd=repo)

    def add_all_untracked(self, repo):
        untracked = self.git_lines(['ls-files', '--other', '--exclude-standard'], cwd=repo)
        return self.git(['add', '--'] + untracked, cwd=repo)


class GitStatusUnstageCommand(TextCommand, GitStatusTextCmd):

    def run(self, edit, unstage="file"):
        repo = self.get_repo()
        if not repo:
            return

        goto = None
        if unstage == "all":
            self.unstage_all(repo)
        elif unstage == "file":
            files = self.get_selected_files()
            staged = [f for s, f in files if s == STAGED_CHANGES]
            if staged:
                self.unstage(repo, staged)
                goto = self.logical_goto_next_file()

        self.update_status(goto)

    def no_commits(self, repo):
        return 0 != self.git_exit_code(['rev-list', 'HEAD', '--max-count=1'], cwd=repo)

    def unstage(self, repo, files):
        if self.no_commits(repo):
            return self.git(['rm', '--cached', '--'] + files, cwd=repo)
        return self.git(['reset', '-q', 'HEAD', '--'] + files, cwd=repo)

    def unstage_all(self, repo):
        if self.no_commits(repo):
            return self.git(['rm', '-r', '--cached', '.'], cwd=repo)
        return self.git(['reset', '-q', 'HEAD'], cwd=repo)


class GitStatusOpenFileCommand(TextCommand, GitStatusTextCmd):

    def run(self, edit):
        repo = self.view.settings().get('git_repo')
        if not repo:
            return

        transient = get_setting('git_status_open_files_transient', True) is True
        files = self.get_selected_files()
        window = self.view.window()

        for s, f in files:
            filename = os.path.join(repo, f)
            if transient:
                window.open_file(filename, sublime.TRANSIENT)
            else:
                window.open_file(filename)


class GitStatusIgnoreCommand(TextCommand, GitStatusTextCmd):

    IGNORE_TRACKED = ("The following files have already been added to git. "
                      "Adding them to .gitignore will not exclude them from being tracked by git. "
                      "Are you sure you want to continue?")
    IGNORE_CONFIRMATION = "Are you sure you want add the following patterns to .gitignore?"
    IGNORE_BUTTON = "Add to .gitignore"
    IGNORE_NO_FILES = "No files selected for ignore."
    IGNORE_LABEL = "Ignore pattern:"

    def run(self, edit, ask=True, edit_pattern=False):
        window = self.view.window()
        repo = self.get_repo()
        if not repo:
            return

        files = self.get_selected_files()
        to_ignore = [f for _, f in files]

        tracked = [f for s, f in files if s != UNTRACKED_FILES]
        if tracked and not self.confirm_tracked(tracked):
            return

        if not to_ignore:
            sublime.error_message(self.IGNORE_NO_FILES)
            return

        if edit_pattern:
            patterns = []
            to_ignore.reverse()

            def on_done(pattern=None):
                if pattern:
                    patterns.append(pattern)
                if to_ignore:
                    filename = to_ignore.pop()
                    window.show_input_panel(self.IGNORE_LABEL, filename, on_done, noop, on_done)
                elif patterns:
                    if ask:
                        if not self.confirm_ignore(patterns):
                            return

                    self.add_to_gitignore(repo, patterns)
                    goto = self.logical_goto_next_file()
                    self.update_status(goto)

            filename = to_ignore.pop()
            window.show_input_panel(self.IGNORE_LABEL, filename, on_done, noop, on_done)
        else:
            if ask:
                if not self.confirm_ignore(to_ignore):
                    return
            self.add_to_gitignore(repo, to_ignore)
            goto = self.logical_goto_next_file()
            self.update_status(goto)

    def confirm_ignore(self, patterns):
        return self.confirm(self.IGNORE_CONFIRMATION, patterns, self.IGNORE_BUTTON)

    def confirm_tracked(self, patterns):
        return self.confirm(self.IGNORE_TRACKED, patterns, "Continue")

    def confirm(self, message, patterns, button):
        msg = message
        msg += "\n\n"
        msg += "\n".join(patterns[:10])
        if len(patterns) > 10:
            msg += "\n"
            msg += "(%s more...)" % len(patterns) - 10
        return sublime.ok_cancel_dialog(msg, button)

    def add_to_gitignore(self, repo, patterns):
        gitignore = os.path.join(repo, '.gitignore')
        contents = []

        # read existing gitignore
        if os.path.exists(gitignore):
            with open(gitignore, 'r+') as f:
                contents = [l.strip() for l in f]
        logger.debug('Initial .gitignore: %s', contents)

        # add new patterns to the end
        for p in patterns:
            if p not in contents:
                logger.debug('Adding to .gitignore: %s', p)
                contents.append(p)

        # always add a newline
        contents.append('')

        # write gitignore
        with open(gitignore, 'w+') as f:
            f.write("\n".join(contents))
        logger.debug('Final .gitignore: %s', contents)

        return contents


class GitStatusDiscardCommand(TextCommand, GitStatusTextCmd):

    DELETE_UNTRACKED_CONFIRMATION = "Delete all untracked files and directories?"

    def run(self, edit, discard="item"):
        repo = self.get_repo()
        if not repo:
            return

        goto = None
        if discard == "section":
            points = self.get_all_points()
            sections = set([self.section_at_point(p) for p in points])
            all_files = self.get_all_files()

            if STASHES in sections:
                self.discard_all_stashes(repo)

            if UNTRACKED_FILES in sections:
                self.discard_all_untracked(repo)

            files = [i for i in all_files if i[0] in (UNSTAGED_CHANGES, STAGED_CHANGES)]
            if files:
                self.discard_files(repo, files)

        elif discard == "item":
            files = self.get_selected_files()
            stashes = self.get_selected_stashes()
            if files:
                self.discard_files(repo, files)
            if stashes:
                self.discard_stashes(repo, stashes)
            goto = self.logical_goto_next_file()

        elif discard == "all":
            self.discard_all(repo)

        self.update_status(goto)

    # global discards

    def discard_all_stashes(self, repo):
        if sublime.ok_cancel_dialog('Discard all stashes?', 'Discard'):
            self.git(['stash', 'clear'], cwd=repo)

    def discard_all_untracked(self, repo):
        if sublime.ok_cancel_dialog(self.DELETE_UNTRACKED_CONFIRMATION, 'Delete'):
            self.git(['clean', '-d', '--force'], cwd=repo)

    def discard_all(self, repo):
        if sublime.ok_cancel_dialog("Discard all staged and unstaged changes?", "Discard"):
            if sublime.ok_cancel_dialog("Are you absolutely sure?", "Discard"):
                self.git(['reset', '--hard'], cwd=repo)

    # individual discards

    def discard_stashes(self, repo, stashes):
        # build message
        stashlist = "\n  ".join([t for _, t in stashes])
        msgtemplate = "Are you sure you want to discard the following stashes?\n\n  {stashes}"

        # ask for confirmation
        if sublime.ok_cancel_dialog(msgtemplate.format(stashes=stashlist), 'Discard'):
            nums = reversed(sorted(int(n) for n, _ in stashes))
            for n in nums:
                self.git(['stash', 'drop', '--quiet', 'stash@{%s}' % n], cwd=repo)

    def discard_files(self, repo, files):
        # Gather the state of every selected file up front, with a constant
        # number of git calls, and reuse it for both the confirmation dialog
        # and the actions below.
        staged_paths = [f for s, f in files if s == STAGED_CHANGES]
        worktree_paths = [f for s, f in files if s in (STAGED_CHANGES, UNSTAGED_CHANGES)]

        staging_statuses = self.get_staging_statuses(repo, staged_paths)
        worktree_statuses = self.get_worktree_statuses(repo, worktree_paths)

        # See if any of the files cannot be discarded
        error = "You can't discard staged changes to the following files. Please unstage them first:\n\n  {errfiles}"
        errlist = []
        for s, f in files:
            if s == STAGED_CHANGES and f in worktree_statuses:
                errlist.append(f)

        if errlist:
            errfiles = "\n  ".join(errlist)
            sublime.error_message(error.format(errfiles=errfiles))
            return

        # Confirm before unstaging any files
        confirm = "Are you sure you want to perform the following actions?\n\n  {actions}"
        actionlist = []
        for s, f in files:
            staged = s == STAGED_CHANGES
            status = staging_statuses.get(f) if staged else worktree_statuses.get(f)

            if staged and f in errlist:
                continue

            if s == UNTRACKED_FILES or status == 'N':
                action = 'Delete: '
            elif status == 'D':
                action = 'Resurrect: '
            else:
                action = 'Discard: '

            actionlist.append("{action} {file}".format(action=action, file=f))

        if not actionlist:
            return

        actions = "\n  ".join(actionlist)
        if not sublime.ok_cancel_dialog(confirm.format(actions=actions), 'Continue'):
            return

        # perform various unstaging/deleting/resurrection actions
        for s, f in files:
            staged = s == STAGED_CHANGES
            status = staging_statuses.get(f) if staged else worktree_statuses.get(f)

            if staged and f in worktree_statuses:
                continue

            if s == UNTRACKED_FILES:
                self.git(['clean', '-d', '--force', '--', f], cwd=repo)
            elif status == 'D':
                self.git(['reset', '-q', '--', f], cwd=repo)
                self.git(['checkout', '--', f], cwd=repo)
            elif status == 'N':
                self.git(['rm', '-f', '--', f], cwd=repo)
            else:
                if staged:
                    self.git(['checkout', 'HEAD', '--', f], cwd=repo)
                else:
                    self.git(['checkout', '--', f], cwd=repo)

    # bulk status helpers

    def parse_name_status_z(self, output):
        """Parse ``git diff --name-status -z`` output into {path: status letter}.

        The output is a flat NUL-separated list of ``STATUS\0path\0``, except
        for renames/copies which emit ``R100\0old\0new\0``.
        """
        statuses = {}
        fields = output.split('\0') if output else []
        i = 0
        while i < len(fields):
            status = fields[i]
            if not status:
                i += 1
                continue
            letter = status[0]
            if letter in ('R', 'C'):
                if i + 2 >= len(fields):
                    break
                statuses[fields[i + 1]] = letter
                statuses[fields[i + 2]] = letter
                i += 3
            else:
                if i + 1 >= len(fields):
                    break
                statuses[fields[i + 1]] = letter
                i += 2
        return statuses

    def get_worktree_statuses(self, repo, paths):
        """Worktree (vs. index) status of each of ``paths``, in one git call.

        Paths missing from the result are unchanged in the worktree, i.e. they
        are "up to date".
        """
        if not paths:
            return {}
        output = self.git_string(['diff', '--name-status', '-z', '--'] + list(paths),
                                 cwd=repo, strip=False)
        return self.parse_name_status_z(output)

    def get_staging_statuses(self, repo, paths):
        """Staged (index vs. HEAD) status of each of ``paths``, in one git call."""
        if not paths:
            return {}
        output = self.git_string(['diff', '--name-status', '--cached', '-z', '--'] + list(paths),
                                 cwd=repo, strip=False)
        return self.parse_name_status_z(output)

    # status helpers

    def is_up_to_date(self, repo, filename):
        return self.git_exit_code(['diff', '--quiet', '--', filename], cwd=repo) == 0

    def get_worktree_status(self, repo, filename):
        output = self.git_string(['diff', '--name-status', '--', filename], cwd=repo)
        if output:
            status, _ = output.split('\t')
            return status

    def get_staging_status(self, repo, filename):
        output = self.git_string(['diff', '--name-status', '--cached', '--', filename], cwd=repo)
        if output:
            status, _ = output.split('\t')
            return status


class GitStatusStashCmd(GitStatusTextCmd, GitStashHelper, GitErrorHelper):

    def pop_or_apply_selected_stashes(self, cmd):
        repo = self.get_repo()
        if not repo:
            return

        goto = None
        stashes = self.get_selected_stashes()
        if stashes:
            for name, title in stashes:
                if sublime.ok_cancel_dialog('Are you sure you want to %s %s?' % (cmd, title), "%s stash" % cmd.capitalize()):
                    exit, stdout, stderr = self.git(['stash', cmd, '-q', 'stash@{%s}' % name], cwd=repo)
                    if exit != 0:
                        sublime.error_message(self.format_error_message(stderr))
            if cmd == "apply":
                region = self.view.line(self.get_first_point())
                goto = "point:%s" % region.begin()
            else:
                goto = self.logical_goto_next_stash()

        self.update_status(goto)


class GitStatusStashApplyCommand(TextCommand, GitStatusStashCmd):

    def run(self, edit):
        self.pop_or_apply_selected_stashes('apply')


class GitStatusStashPopCommand(TextCommand, GitStatusStashCmd):

    def run(self, edit):
        self.pop_or_apply_selected_stashes('pop')


class GitStatusDiffCommand(TextCommand, GitStatusTextCmd):

    def run(self, edit):
        repo = self.get_repo()
        if not repo:
            return

        files = self.get_selected_files()
        window = self.view.window()

        for s, f in files:
            if s != UNTRACKED_FILES:
                cached = (s == STAGED_CHANGES)
                window.run_command('git_diff', {'repo': repo, 'path': f, 'cached': cached})
