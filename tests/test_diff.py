# coding: utf-8
"""Tests for the diff view buffer parser (``sgit.diff.GitDiffTextCmd``).

``parse_diff`` used to walk ``view.lines()`` and call ``view.substr()`` once
per line. It now reads the whole buffer in one ``substr()`` and splits it in
Python. These tests pin the two things that matters: the result is byte for
byte the same as the old implementation, and the number of Sublime API calls no
longer grows with the size of the diff.
"""
import re

import pytest

import sublime
from sgit.diff import GitDiffTextCmd, RE_DIFF_HEAD


# --- the reference (old) implementation ------------------------------------
# Kept verbatim, apart from taking the view as an argument, so the differential
# test below compares against what actually shipped.

def parse_diff_reference(view):
    sections = []
    state = None

    prev_file = None
    current_file = {}
    current_hunks = []

    prev_hunk = None
    current_hunk = None

    for line in view.lines(sublime.Region(0, view.size())):
        linetext = view.substr(line)

        if linetext.startswith('diff --git'):
            state = 'header'
            # new file starts
            if prev_file != line:
                if prev_file is not None:
                    if current_hunk:
                        current_hunks.append(current_hunk)
                    sections.append((current_file, current_hunks))
                prev_file = line
                prev_hunk = None

            current_file = line
            current_hunks = []
        elif state == 'header' and RE_DIFF_HEAD.match(linetext):
            current_file = current_file.cover(line)
        elif linetext.startswith('@@'):
            state = 'hunk'
            # new hunk starts
            if prev_hunk != line:
                if prev_hunk is not None:
                    current_hunks.append(current_hunk)
                prev_hunk = line

            current_hunk = line
        elif state == 'hunk' and linetext[:1] in (' ', '-', '+'):
            # NOTE: the shipped version indexed linetext[0], which raised
            # IndexError on an empty line inside a hunk. The rewrite treats an
            # empty line as "does not match"; the reference does the same so
            # the two can be compared on buffers that contain one.
            current_hunk = current_hunk.cover(line)
        elif state == 'header':
            current_file = current_file.cover(line)

    if current_file and current_hunk:
        current_hunks.append(current_hunk)
        sections.append((current_file, current_hunks))
    return sections


# --- helpers ---------------------------------------------------------------

class _Cmd(GitDiffTextCmd):
    """GitDiffTextCmd needs nothing but a ``view`` for parse_diff."""

    def __init__(self, view):
        self.view = view


def parse(content):
    view = sublime.View(content=content)
    return _Cmd(view).parse_diff(), view


# --- fixtures --------------------------------------------------------------

SIMPLE = """diff --git a/one.py b/one.py
index 1111111..2222222 100644
--- a/one.py
+++ b/one.py
@@ -1,3 +1,4 @@
 import os
+import sys

 def main():
@@ -20,6 +21,6 @@ def main():
-    return 1
+    return 0
"""

MULTI_FILE = SIMPLE + """diff --git a/two.txt b/two.txt
index 3333333..4444444 100644
--- a/two.txt
+++ b/two.txt
@@ -1 +1 @@
-old
+new
diff --git a/three.md b/three.md
index 5555555..6666666 100644
--- a/three.md
+++ b/three.md
@@ -10,2 +10,3 @@ heading
 context
+added
 more
"""

NO_NEWLINE_AT_EOF = """diff --git a/eof.txt b/eof.txt
index aaaaaaa..bbbbbbb 100644
--- a/eof.txt
+++ b/eof.txt
@@ -1,2 +1,2 @@
 keep
-gone
\\ No newline at end of file
+here
\\ No newline at end of file
"""

MODE_CHANGE = """diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
diff --git a/renamed.py b/moved.py
similarity index 92%
rename from renamed.py
rename to moved.py
index ccccccc..ddddddd 100644
--- a/renamed.py
+++ b/moved.py
@@ -4,3 +4,3 @@ class A:
-    x = 1
+    x = 2
     y = 2
"""

NEW_FILE = """diff --git a/brand/new.py b/brand/new.py
new file mode 100644
index 0000000..eeeeeee
--- /dev/null
+++ b/brand/new.py
@@ -0,0 +1,3 @@
+one
+two
+three
diff --git a/deleted.py b/deleted.py
deleted file mode 100644
index fffffff..0000000
--- a/deleted.py
+++ /dev/null
@@ -1,2 +0,0 @@
-bye
-now
"""

BINARY_AND_EMPTY_CONTEXT = """diff --git a/img.png b/img.png
index 1010101..2020202 100644
Binary files a/img.png and b/img.png differ
diff --git a/blank.txt b/blank.txt
index 3030303..4040404 100644
--- a/blank.txt
+++ b/blank.txt
@@ -1,4 +1,4 @@
 first

-second
+2nd
 last
"""

FIXTURES = {
    'simple': SIMPLE,
    'multi_file': MULTI_FILE,
    'no_newline_at_eof': NO_NEWLINE_AT_EOF,
    'mode_change_and_rename': MODE_CHANGE,
    'new_and_deleted_file': NEW_FILE,
    'binary_and_empty_context': BINARY_AND_EMPTY_CONTEXT,
    'empty': '',
    'only_newline': '\n',
    'not_a_diff': 'Nothing to stage (no difference between working tree and index)',
}


def _variants():
    for name, body in FIXTURES.items():
        yield name + ':as-is', body
        if body.endswith('\n'):
            yield name + ':no-trailing-newline', body[:-1]
        else:
            yield name + ':trailing-newline', body + '\n'


VARIANTS = list(_variants())


# --- (a) differential test -------------------------------------------------

@pytest.mark.parametrize('name,content', VARIANTS, ids=[n for n, _ in VARIANTS])
def test_parse_diff_matches_old_implementation(name, content):
    sections, view = parse(content)
    expected = parse_diff_reference(sublime.View(content=content))

    assert len(sections) == len(expected)
    for (header, hunks), (exp_header, exp_hunks) in zip(sections, expected):
        assert header == exp_header
        assert hunks == exp_hunks


@pytest.mark.parametrize('name,content', VARIANTS, ids=[n for n, _ in VARIANTS])
def test_parse_diff_regions_exclude_trailing_newline(name, content):
    """Regions must look exactly like the ones view.lines() produces: they end
    at a line end, never on or past a newline character."""
    sections, view = parse(content)
    for header, hunks in sections:
        for region in [header] + list(hunks):
            assert region.begin() == 0 or view.substr(sublime.Region(region.begin() - 1, region.begin())) == '\n'
            assert region.end() == view.size() or view.substr(sublime.Region(region.end(), region.end() + 1)) == '\n'


def test_parse_diff_finds_the_expected_structure():
    """Sanity check that the fixtures are parsed at all, so the differential
    test above is not comparing two empty lists."""
    sections, view = parse(MULTI_FILE)
    assert [len(hunks) for _, hunks in sections] == [2, 1, 1]
    headers = [view.substr(h).splitlines()[0] for h, _ in sections]
    assert headers == ['diff --git a/one.py b/one.py',
                       'diff --git a/two.txt b/two.txt',
                       'diff --git a/three.md b/three.md']
    first_hunk = view.substr(sections[0][1][0])
    assert first_hunk.startswith('@@ -1,3 +1,4 @@')
    assert first_hunk.endswith('def main():')


def test_parse_diff_does_not_raise_on_empty_line_inside_a_hunk():
    """Regression: linetext[0] used to raise IndexError here."""
    content = SIMPLE + '\n'
    sections, view = parse(content)
    assert sections  # got here without an IndexError


def test_no_newline_marker_is_not_swallowed_into_the_hunk():
    sections, view = parse(NO_NEWLINE_AT_EOF)
    (header, hunks), = sections
    assert len(hunks) == 1
    text = view.substr(hunks[0])
    # The '\ No newline' line matches no branch, but the '+here' line after it
    # is still part of the hunk, so cover() pulls the marker in. That is the
    # pre-existing behaviour and the rewrite keeps it.
    assert text.splitlines()[-1] == '+here'
    assert '\\ No newline at end of file' in text


# --- (b) API call count ----------------------------------------------------

def _big_diff(lines=2000):
    out = ['diff --git a/big.txt b/big.txt',
           'index 1111111..2222222 100644',
           '--- a/big.txt',
           '+++ b/big.txt']
    n = 0
    while n < lines:
        out.append('@@ -%d,50 +%d,50 @@' % (n + 1, n + 1))
        n += 1
        for i in range(49):
            out.append(('+' if i % 3 == 0 else ' ') + 'line %d' % n)
            n += 1
    return '\n'.join(out) + '\n'


def test_parse_diff_makes_a_constant_number_of_api_calls():
    small = sublime.View(content=SIMPLE)
    big_content = _big_diff(2000)
    big = sublime.View(content=big_content)
    assert big_content.count('\n') > 2000

    _Cmd(small).parse_diff()
    _Cmd(big).parse_diff()

    assert small.api_calls.get('substr', 0) == 1
    assert small.api_calls.get('lines', 0) == 0
    assert big.api_calls.get('substr', 0) == 1
    assert big.api_calls.get('lines', 0) == 0

    # and the old implementation really was linear, so the test has teeth
    ref = sublime.View(content=big_content)
    parse_diff_reference(ref)
    assert ref.api_calls['substr'] > 2000


def test_create_patch_does_not_call_lines_per_header_line():
    view = sublime.View(content=MULTI_FILE)
    cmd = _Cmd(view)
    sections = cmd.parse_diff()
    header, hunks = sections[0]
    selected = {(header.begin(), header.end()): hunks}

    view.api_calls.clear()
    patch = cmd.create_patch(selected)

    assert patch.startswith('diff --git a/one.py b/one.py\n')
    assert '--- a/one.py\n' in patch
    assert '@@ -1,3 +1,4 @@\n' in patch
    assert patch.endswith('\n')
    assert view.api_calls.get('lines', 0) == 0
    # one substr for the header + one substr/full_line per selected hunk
    assert view.api_calls.get('substr', 0) == 1 + len(hunks)


def test_create_patch_matches_the_reference_output():
    """The old create_patch, for comparison."""
    view = sublime.View(content=MULTI_FILE)
    cmd = _Cmd(view)
    sections = cmd.parse_diff()

    selected = {}
    for header, hunks in sections:
        selected[(header.begin(), header.end())] = hunks

    expected = []
    for (hstart, hend), hunks in selected.items():
        region = sublime.Region(hstart, hend)
        for head in view.lines(region):
            headline = view.substr(head)
            if headline.startswith('---') or headline.startswith('+++'):
                expected.append('%s\n' % headline.strip())
            else:
                expected.append('%s\n' % headline)
        for h in hunks:
            expected.append(view.substr(view.full_line(h)))

    assert cmd.create_patch(selected) == ''.join(expected)


def test_stub_lines_matches_real_sublime_trailing_empty_line():
    """view.lines(Region(0, size)) on "a\\nb\\n" gives three regions in real
    Sublime: "a", "b", and an empty final one. The parser relies on
    str.split('\\n') reproducing that, so pin both sides."""
    cases = {
        '': [sublime.Region(0, 0)],
        'a': [sublime.Region(0, 1)],
        'a\nb\n': [sublime.Region(0, 1), sublime.Region(2, 3), sublime.Region(4, 4)],
        'a\nb': [sublime.Region(0, 1), sublime.Region(2, 3)],
        '\n': [sublime.Region(0, 0), sublime.Region(1, 1)],
        'a\n\nb\n': [sublime.Region(0, 1), sublime.Region(2, 2),
                     sublime.Region(3, 4), sublime.Region(5, 5)],
    }
    for content, expected in cases.items():
        view = sublime.View(content=content)
        assert view.lines(sublime.Region(0, view.size())) == expected, content
        assert [r for _, r in _Cmd(view).iter_lines()] == expected, content
        assert [t for t, _ in _Cmd(view).iter_lines()] == content.split('\n'), content


def test_re_diff_head_behaviour_is_unchanged():
    """RE_DIFF_HEAD's ``{3}`` repeats the whole alternation, so it needs nine
    dashes and never matches a real '--- a/f' line. Pinned as-is: such lines
    fall through to the generic 'state == header' branch, which covers them
    into the header anyway, so the parse result is the same either way."""
    assert isinstance(RE_DIFF_HEAD, re.Pattern)
    assert RE_DIFF_HEAD.match('--- a/one.py') is None
    assert RE_DIFF_HEAD.match('+++ b/one.py') is None
    assert RE_DIFF_HEAD.match('--------- a/one.py') is not None
