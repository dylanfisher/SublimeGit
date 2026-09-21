# coding: utf-8
import os
import sys

import pytest
import sublime

from sgit.cmd import Cmd, GitCmd, SublimeGitException


class PyCmd(Cmd):
    """A Cmd whose binary is the local python interpreter, so the
    subprocess layer can be exercised without git."""
    executable = 'py'
    bin = [sys.executable]
    opts = ['-c']


def realpath(p):
    return os.path.realpath(str(p))


class TestBuildCommand(object):

    def test_prepends_bin_and_opts_and_drops_falsy_args(self, settings):
        cmd = GitCmd()
        command = cmd.build_command(['status', None, '', '--porcelain', 0])
        assert command == ['git'] + GitCmd.opts + ['status', '--porcelain']

    def test_git_opts_are_the_known_set(self):
        assert GitCmd.opts == ['--no-pager',
                               '-c', 'color.diff=false',
                               '-c', 'color.status=false',
                               '-c', 'color.branch=false',
                               '-c', 'status.displayCommentPrefix=true',
                               '-c', 'core.commentchar=#']

    def test_uses_git_executables_setting_when_set(self, settings):
        settings.set('git_executables', {'git': ['/opt/git/bin/git']})
        assert GitCmd().build_command(['log']) == ['/opt/git/bin/git'] + GitCmd.opts + ['log']


class TestEnv(object):

    def test_default_env_is_copy_of_environ(self, settings, monkeypatch):
        monkeypatch.setenv('PATH', '/x/bin')
        env = Cmd().env()
        assert env['PATH'] == '/x/bin'
        assert env is not os.environ

    def test_git_force_path_list_joined_with_pathsep(self, settings):
        settings.set('git_force_path', ['/a/bin', '/b/bin'])
        assert Cmd().env()['PATH'] == os.pathsep.join(['/a/bin', '/b/bin'])

    def test_git_force_path_string_used_verbatim(self, settings):
        settings.set('git_force_path', '/only/bin')
        assert Cmd().env()['PATH'] == '/only/bin'

    def test_git_force_path_empty_is_ignored(self, settings, monkeypatch):
        monkeypatch.setenv('PATH', '/orig')
        settings.set('git_force_path', [])
        assert Cmd().env()['PATH'] == '/orig'
        settings.set('git_force_path', '')
        assert Cmd().env()['PATH'] == '/orig'


class TestDecode(object):

    def test_decodes_with_primary_encoding(self):
        assert Cmd().decode(b'caf\xc3\xa9', 'utf-8') == u'caf\xe9'

    def test_falls_back_to_next_encoding(self):
        assert Cmd().decode(b'caf\xe9', 'utf-8', ['ascii', 'latin-1']) == u'caf\xe9'

    def test_raises_when_all_encodings_fail(self):
        with pytest.raises(UnicodeDecodeError):
            Cmd().decode(b'caf\xe9', 'utf-8', ['ascii'])
        with pytest.raises(UnicodeDecodeError):
            Cmd().decode(b'caf\xe9', 'utf-8')

    def test_non_bytes_passthrough(self):
        assert Cmd().decode(u'already text', 'utf-8') == u'already text'


class TestCmdSync(object):

    def test_returns_tuple_of_returncode_stdout_stderr(self, settings):
        result = PyCmd().cmd(['import sys; sys.stdout.write("out"); sys.stderr.write("err"); sys.exit(3)'])
        assert result == (3, 'out', 'err')
        assert isinstance(result[1], str)

    def test_child_runs_in_requested_cwd(self, settings, tmp_path):
        rc, out, _ = PyCmd().cmd(['import os; print(os.getcwd())'], cwd=str(tmp_path))
        assert rc == 0
        assert realpath(out.strip()) == realpath(tmp_path)

    @pytest.mark.xfail(strict=True, reason=(
        'cmd() currently does os.chdir(cwd), which mutates the cwd of the whole '
        'Sublime process. Refactor (a) replaces it with Popen(cwd=...); when that '
        'lands this test should start passing and the xfail marker must be removed.'))
    def test_cmd_does_not_change_the_parent_process_cwd(self, settings, tmp_path):
        before = os.getcwd()
        PyCmd().cmd(['import os; print(os.getcwd())'], cwd=str(tmp_path))
        assert os.getcwd() == before

    @pytest.mark.xfail(strict=True, reason=(
        'cmd_async() currently does os.chdir(cwd) from a worker thread. '
        'Refactor (a) should give it Popen(cwd=...) too.'))
    def test_cmd_async_does_not_change_the_parent_process_cwd(self, settings, tmp_path):
        before = os.getcwd()
        thread = PyCmd().cmd_async(['import os; print(os.getcwd())'], cwd=str(tmp_path))
        thread.start()
        thread.join(timeout=30)
        assert os.getcwd() == before

    def test_child_runs_in_current_dir_when_cwd_not_given(self, settings, tmp_path, monkeypatch):
        monkeypatch.chdir(str(tmp_path))
        rc, out, _ = PyCmd().cmd(['import os; print(os.getcwd())'])
        assert realpath(out.strip()) == realpath(tmp_path)

    def test_stdin_text_is_encoded_and_delivered(self, settings, monkeypatch):
        monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')
        rc, out, _ = PyCmd().cmd(['import sys; sys.stdout.write(sys.stdin.read())'], stdin=u'h\xe9llo')
        assert (rc, out) == (0, u'h\xe9llo')

    def test_stdin_bytes_passed_verbatim(self, settings):
        rc, out, _ = PyCmd().cmd(['import sys; print(len(sys.stdin.buffer.read()))'], stdin=b'raw\xff')
        assert (rc, out.strip()) == (0, '4')

    def test_explicit_encoding_used_for_stdin(self, settings, monkeypatch):
        monkeypatch.setenv('PYTHONIOENCODING', 'latin-1')
        rc, out, _ = PyCmd().cmd(['import sys; sys.stdout.write(sys.stdin.read())'],
                                 stdin=u'\xe9', encoding='latin-1')
        assert out == u'\xe9'

    def test_falsy_args_dropped_from_command(self, settings):
        rc, out, _ = PyCmd().cmd(['import sys; print(len(sys.argv))', None, '', 'a', 'b'])
        # sys.argv == ['-c', 'a', 'b']
        assert out.strip() == '3'

    def test_decoding_uses_fallback_encodings_setting(self, settings):
        settings.set('fallback_encodings', ['latin-1'])
        rc, out, _ = PyCmd().cmd(['import sys; sys.stdout.buffer.write(b"caf\\xe9")'])
        assert out == u'caf\xe9'

    def test_decoding_error_without_ignore_errors_raises_and_reports(self, settings):
        with pytest.raises(SublimeGitException):
            PyCmd().cmd(['import sys; sys.stdout.buffer.write(b"caf\\xe9")'])
        assert len(sublime.error_messages) == 1
        assert 'Could not decode output from git' in sublime.error_messages[0]
        assert 'utf-8' in sublime.error_messages[0]

    def test_decoding_error_with_ignore_errors_returns_two_tuple(self, settings):
        # NOTE: current behaviour returns a 2-tuple here, not (rc, stdout, stderr)
        result = PyCmd().cmd(['import sys; sys.stdout.buffer.write(b"caf\\xe9")'], ignore_errors=True)
        assert result == (0, '')
        assert sublime.error_messages == []

    def test_missing_binary_without_ignore_errors_raises_and_reports(self, settings):
        class Missing(Cmd):
            executable = 'nope'
            bin = ['/definitely/not/a/binary']

        with pytest.raises(SublimeGitException):
            Missing().cmd(['x'])
        assert len(sublime.error_messages) == 1
        assert "Executable '['/definitely/not/a/binary']' was not found in PATH" in sublime.error_messages[0]
        assert "git_executables['nope']" in sublime.error_messages[0]

    def test_missing_binary_with_ignore_errors_returns_two_tuple(self, settings):
        class Missing(Cmd):
            executable = 'nope'
            bin = ['/definitely/not/a/binary']

        assert Missing().cmd(['x'], ignore_errors=True) == (0, '')
        assert sublime.error_messages == []

    def test_environment_passed_to_child(self, settings):
        settings.set('git_force_path', ['/forced/path'])
        rc, out, _ = PyCmd().cmd(['import os; print(os.environ["PATH"])'])
        assert out.strip() == '/forced/path'


class TestCmdConvenience(object):

    def test_string_strips_by_default(self, settings):
        assert PyCmd()._string(['print("  x  ")']) == 'x'
        assert PyCmd()._string(['print("  x  ")'], strip=False) == '  x  \n'

    def test_lines_splits_and_drops_trailing_newline(self, settings):
        assert PyCmd()._lines(['print("a\\nb\\n")']) == ['a', 'b']

    def test_lines_empty_output_is_empty_list(self, settings):
        assert PyCmd()._lines(['print("")']) == []
        assert PyCmd()._lines(['pass']) == []

    def test_exit_code(self, settings):
        assert PyCmd()._exit_code(['import sys; sys.exit(7)']) == 7
        assert PyCmd()._exit_code(['pass']) == 0


class TestCmdAsync(object):

    def run_async(self, cmd, cwd=None, **callbacks):
        thread = PyCmd().cmd_async(cmd, cwd=cwd, **callbacks)
        assert not thread.is_alive()  # returned unstarted
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive()
        return thread

    def test_callbacks_delivered_via_set_timeout(self, settings, flush):
        events = []
        self.run_async(['print("one"); print("two")'],
                       on_data=lambda line: events.append(('data', line)),
                       on_complete=lambda rc: events.append(('complete', rc)),
                       on_error=lambda rc: events.append(('error', rc)))

        # nothing is delivered until the sublime main loop runs the callbacks
        assert events == []
        assert len(sublime.pending_timeouts()) == 3
        flush()
        assert events == [('data', 'one\n'), ('data', 'two\n'), ('complete', 0)]

    def test_on_error_for_nonzero_exit_and_stderr_merged_into_stdout(self, settings, flush):
        events = []
        self.run_async(['import sys; sys.stderr.write("bad\\n"); sys.exit(2)'],
                       on_data=lambda line: events.append(('data', line)),
                       on_complete=lambda rc: events.append(('complete', rc)),
                       on_error=lambda rc: events.append(('error', rc)))
        flush()
        assert events == [('data', 'bad\n'), ('error', 2)]

    def test_child_runs_in_requested_cwd(self, settings, flush, tmp_path):
        lines = []
        self.run_async(['import os; print(os.getcwd())'], cwd=str(tmp_path), on_data=lines.append)
        flush()
        assert realpath(lines[0].strip()) == realpath(tmp_path)

    def test_on_exception_for_missing_binary(self, settings, flush):
        class Missing(Cmd):
            executable = 'nope'
            bin = ['/definitely/not/a/binary']

        caught = []
        thread = Missing().cmd_async(['x'], on_exception=caught.append, on_complete=lambda rc: caught.append('complete'))
        thread.start()
        thread.join(timeout=30)
        flush()
        assert len(caught) == 1
        assert isinstance(caught[0], OSError)

    def test_missing_callbacks_are_fine(self, settings, flush):
        self.run_async(['print("x")'])
        assert flush() == 0
