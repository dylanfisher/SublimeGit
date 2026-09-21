# coding: utf-8
"""Test stub for the ``sublime`` module.

Installed into ``sys.modules`` by ``tests/conftest.py`` before ``sgit`` is
imported. It records side effects (timeouts, status messages, dialogs) so tests
can inspect and flush them synchronously.
"""

# Constants used by the plugin
HIDDEN = 32
LITERAL = 1
MONOSPACE_FONT = 1
TRANSIENT = 4
IGNORECASE = 2

KIND_ID_AMBIGUOUS = 0
KIND_ID_KEYWORD = 1
KIND_ID_TYPE = 2
KIND_ID_FUNCTION = 3
KIND_ID_NAMESPACE = 4
KIND_ID_NAVIGATION = 5
KIND_ID_MARKUP = 6
KIND_ID_VARIABLE = 7
KIND_ID_SNIPPET = 8
KIND_ID_COLOR_REDISH = 9
KIND_ID_COLOR_ORANGISH = 10
KIND_ID_COLOR_YELLOWISH = 11
KIND_ID_COLOR_GREENISH = 12
KIND_ID_COLOR_CYANISH = 13
KIND_ID_COLOR_BLUISH = 14
KIND_ID_COLOR_PURPLISH = 15
KIND_ID_COLOR_PINKISH = 16
KIND_ID_COLOR_DARK = 17
KIND_ID_COLOR_LIGHT = 18
KIND_AMBIGUOUS = (KIND_ID_AMBIGUOUS, '', '')


class QuickPanelItem(object):
    """Stand-in for ``sublime.QuickPanelItem`` (ST 4083+)."""

    def __init__(self, trigger, details='', annotation='', kind=KIND_AMBIGUOUS):
        self.trigger = trigger
        self.details = details
        self.annotation = annotation
        self.kind = kind

    def __eq__(self, other):
        return (isinstance(other, QuickPanelItem)
                and (self.trigger, self.details, self.annotation, self.kind)
                == (other.trigger, other.details, other.annotation, other.kind))

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return 'QuickPanelItem(%r, details=%r, annotation=%r, kind=%r)' % (
            self.trigger, self.details, self.annotation, self.kind)


# --- settings -------------------------------------------------------------

class Settings(object):
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def erase(self, key):
        self._data.pop(key, None)

    def has(self, key):
        return key in self._data

    # test helpers
    def clear(self):
        self._data.clear()

    def update(self, values):
        self._data.update(values)


_settings_files = {}


def load_settings(name):
    return _settings_files.setdefault(name, Settings())


def save_settings(name):
    pass


# --- timeouts -------------------------------------------------------------

_timeouts = []
_timeouts_async = []


def set_timeout(fn, delay=0):
    _timeouts.append((fn, delay))


def set_timeout_async(fn, delay=0):
    _timeouts_async.append((fn, delay))


def flush_timeouts(max_rounds=100):
    """Run every recorded callback (including callbacks that schedule new
    callbacks) synchronously. Returns the number of callbacks executed."""
    ran = 0
    rounds = 0
    while (_timeouts or _timeouts_async) and rounds < max_rounds:
        rounds += 1
        pending = _timeouts[:] + _timeouts_async[:]
        del _timeouts[:]
        del _timeouts_async[:]
        for fn, _ in pending:
            fn()
            ran += 1
    return ran


def pending_timeouts():
    return [fn for fn, _ in _timeouts]


# --- messages / dialogs ---------------------------------------------------

status_messages = []
error_messages = []
message_dialogs = []
ok_cancel_dialogs = []

# answers for ok_cancel_dialog, popped in order; default True when exhausted
ok_cancel_answers = []


def status_message(msg):
    status_messages.append(msg)


def error_message(msg):
    error_messages.append(msg)


def message_dialog(msg):
    message_dialogs.append(msg)


def ok_cancel_dialog(msg, ok_title=''):
    ok_cancel_dialogs.append((msg, ok_title))
    if ok_cancel_answers:
        return ok_cancel_answers.pop(0)
    return True


# --- misc -----------------------------------------------------------------

def version():
    return '4213'


def platform():
    return 'osx'


def arch():
    return 'x64'


def packages_path():
    return '/tmp/Packages'


_windows = []


def windows():
    return list(_windows)


def active_window():
    return _windows[0] if _windows else None


# --- Region ---------------------------------------------------------------

class Region(object):
    def __init__(self, a, b=None):
        self.a = a
        self.b = a if b is None else b

    def begin(self):
        return min(self.a, self.b)

    def end(self):
        return max(self.a, self.b)

    def size(self):
        return self.end() - self.begin()

    def empty(self):
        return self.a == self.b

    def contains(self, x):
        if isinstance(x, Region):
            return self.begin() <= x.begin() and x.end() <= self.end()
        return self.begin() <= x <= self.end()

    def cover(self, other):
        """Smallest region spanning both. Like the real API, the result is
        normalized (a <= b) even when either input is reversed."""
        return Region(min(self.begin(), other.begin()), max(self.end(), other.end()))

    def intersects(self, other):
        return (self.end() > other.begin() and other.end() > self.begin()) or \
            self == other or (self.empty() and other.contains(self.begin())) or \
            (other.empty() and self.contains(other.begin()))

    def intersection(self, other):
        a, b = max(self.begin(), other.begin()), min(self.end(), other.end())
        return Region(a, b) if a < b else Region(0, 0)

    def __eq__(self, other):
        return isinstance(other, Region) and self.a == other.a and self.b == other.b

    def __hash__(self):
        return hash((self.a, self.b))

    def __repr__(self):
        return 'Region(%r, %r)' % (self.a, self.b)


class Selection(list):
    def add(self, region):
        self.append(region)


# --- View / Window --------------------------------------------------------

_next_id = [1000]


def _new_id():
    _next_id[0] += 1
    return _next_id[0]


class View(object):
    def __init__(self, file_name=None, window=None, settings=None, view_id=None, content='',
                 scopes=None):
        self._id = view_id if view_id is not None else _new_id()
        # [(begin, end, 'scope.name')]; may nest/overlap, see set_scopes()
        self._scopes = list(scopes or [])
        self._change_count = 1
        # {'score_selector': n, 'find_by_selector': n, 'substr': n, 'lines': n,
        # 'full_line': n}; tests assert on these
        self.api_calls = {}
        self._file_name = file_name
        self._window = window
        self._settings = Settings(settings)
        self._status = {}
        self._sel = Selection()
        self._regions = {}
        self.commands = []
        self._name = None
        self._read_only = False
        self._scratch = False
        self._syntax = None
        self._buf = content
        self.viewport = (0.0, 0.0)
        self._valid = True

    def id(self):
        return self._id

    def is_valid(self):
        """False once the view is closed. Real Sublime turns every method on a
        closed view into a no-op; the plugin checks this before writing."""
        return self._valid

    # test helper: pretend the user closed the tab
    def close(self):
        self._valid = False

    def file_name(self):
        return self._file_name

    def window(self):
        return self._window

    def settings(self):
        return self._settings

    def set_status(self, key, value):
        self._status[key] = value

    def get_status(self, key):
        return self._status.get(key, '')

    def erase_status(self, key):
        self._status.pop(key, None)

    def run_command(self, name, args=None):
        self.commands.append((name, args))

    def sel(self):
        return self._sel

    def set_name(self, name):
        self._name = name

    def name(self):
        return self._name

    def set_read_only(self, flag):
        self._read_only = flag

    def set_scratch(self, flag):
        self._scratch = flag

    def set_syntax_file(self, syntax):
        self._syntax = syntax

    # ST4 name for the same thing; refactor (d) switches to this
    def assign_syntax(self, syntax):
        self._syntax = syntax

    def syntax(self):
        return self._syntax

    def is_read_only(self):
        return self._read_only

    # --- text buffer ------------------------------------------------------
    # Enough of a buffer that the *_refresh commands can actually run.
    # Scope-based lookups (find_by_selector / score_selector) need a real
    # syntax definition, so they behave as if nothing is scoped.

    def size(self):
        return len(self._buf)

    def substr(self, x):
        self._count_call('substr')
        if isinstance(x, Region):
            return self._buf[x.begin():x.end()]
        return self._buf[x:x + 1]

    def change_count(self):
        """Bumped by every buffer modification, like the real API."""
        return self._change_count

    def insert(self, edit, point, text):
        self._buf = self._buf[:point] + text + self._buf[point:]
        self._change_count += 1
        return len(text)

    def erase(self, edit, region):
        self._buf = self._buf[:region.begin()] + self._buf[region.end():]
        self._change_count += 1

    def replace(self, edit, region, text):
        self._buf = self._buf[:region.begin()] + text + self._buf[region.end():]
        self._change_count += 1

    def _line_bounds(self, point):
        point = max(0, min(point, len(self._buf)))
        start = self._buf.rfind('\n', 0, point) + 1
        end = self._buf.find('\n', point)
        if end == -1:
            end = len(self._buf)
        return start, end

    def line(self, x):
        point = x.begin() if isinstance(x, Region) else x
        start, _ = self._line_bounds(point)
        endpoint = x.end() if isinstance(x, Region) else point
        _, end = self._line_bounds(endpoint)
        return Region(start, end)

    def full_line(self, x):
        self._count_call('full_line')
        line = self.line(x)
        end = min(line.end() + 1, len(self._buf))
        return Region(line.begin(), end)

    def lines(self, region):
        self._count_call('lines')
        out = []
        point = region.begin()
        while True:
            line = self.line(point)
            out.append(line)
            if line.end() >= region.end():
                break
            point = line.end() + 1
        return out

    def rowcol(self, point):
        point = max(0, min(point, len(self._buf)))
        head = self._buf[:point]
        row = head.count('\n')
        col = point - (head.rfind('\n') + 1)
        return (row, col)

    def text_point(self, row, col):
        lines = self._buf.split('\n')
        if row >= len(lines):
            return len(self._buf)
        offset = sum(len(l) + 1 for l in lines[:row])
        return min(offset + col, len(self._buf))

    def find(self, pattern, start_point, flags=0):
        if flags & LITERAL:
            idx = self._buf.find(pattern, start_point)
        else:
            import re as _re
            m = _re.compile(pattern, _re.I if flags & IGNORECASE else 0).search(self._buf, start_point)
            idx = m.start() if m else -1
            if idx != -1:
                return Region(m.start(), m.end())
        if idx == -1:
            return Region(-1, -1)
        return Region(idx, idx + len(pattern))

    # --- scopes -----------------------------------------------------------
    # A view has no syntax here, so scopes are supplied by the test as a list
    # of ``(begin, end, 'scope.name')`` spans (a point's scopes are all the
    # spans that contain it, i.e. the real scope stack). Without them nothing
    # is scoped, as before.

    def set_scopes(self, scopes):
        self._scopes = list(scopes)
        return self

    @staticmethod
    def _selector_matches(scope, selector):
        return scope == selector or scope.startswith(selector + '.')

    def _count_call(self, name):
        self.api_calls[name] = self.api_calls.get(name, 0) + 1

    def find_by_selector(self, selector):
        self._count_call('find_by_selector')
        spans = sorted((b, e) for b, e, scope in self._scopes
                       if self._selector_matches(scope, selector))
        merged = []
        for b, e in spans:
            if merged and b <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([b, e])
        return [Region(b, e) for b, e in merged]

    def score_selector(self, point, selector):
        self._count_call('score_selector')
        for b, e, scope in self._scopes:
            if b <= point < e and self._selector_matches(scope, selector):
                return 1
        return 0

    def visible_region(self):
        return Region(0, len(self._buf))

    # --- regions ----------------------------------------------------------

    def add_regions(self, key, regions, scope='', icon='', flags=0):
        self._regions[key] = list(regions)

    def get_regions(self, key):
        return list(self._regions.get(key, []))

    def erase_regions(self, key):
        self._regions.pop(key, None)

    def show_at_center(self, point):
        pass

    def show(self, x, show_surrounds=True):
        pass

    def set_viewport_position(self, pos, animate=True):
        self.viewport = pos

    def viewport_position(self):
        return self.viewport

    def __repr__(self):
        return 'View(id=%r, file_name=%r)' % (self._id, self._file_name)


class Window(object):
    def __init__(self, folders=None, views=None, active_view=None, window_id=None):
        self._id = window_id if window_id is not None else _new_id()
        self._folders = list(folders or [])
        self._views = list(views or [])
        self._active_view = active_view
        self.commands = []
        self.quick_panel = None
        for v in self._views:
            if v._window is None:
                v._window = self
        if self._active_view is not None and self._active_view._window is None:
            self._active_view._window = self

    def id(self):
        return self._id

    def folders(self):
        return list(self._folders)

    def views(self):
        return list(self._views)

    def active_view(self):
        return self._active_view

    def run_command(self, name, args=None):
        self.commands.append((name, args))

    def new_file(self):
        v = View(window=self)
        self._views.append(v)
        return v

    def focus_view(self, view):
        self._active_view = view

    def show_quick_panel(self, items, on_done, flags=0, selected_index=-1, on_highlighted=None):
        self.quick_panel = (items, on_done)

    def show_input_panel(self, caption, initial_text, on_done, on_change, on_cancel):
        self.input_panel = (caption, initial_text, on_done, on_change, on_cancel)

    def get_output_panel(self, name):
        if not hasattr(self, 'panels'):
            self.panels = {}
        return self.panels.setdefault(name, View(window=self))

    def create_output_panel(self, name, unlisted=False):
        return self.get_output_panel(name)

    def __repr__(self):
        return 'Window(id=%r)' % self._id


# --- reset ----------------------------------------------------------------

def reset():
    """Clear all recorded state. Called by the conftest autouse fixture."""
    del _timeouts[:]
    del _timeouts_async[:]
    del status_messages[:]
    del error_messages[:]
    del message_dialogs[:]
    del ok_cancel_dialogs[:]
    del ok_cancel_answers[:]
    del _windows[:]
    for s in _settings_files.values():
        s.clear()
