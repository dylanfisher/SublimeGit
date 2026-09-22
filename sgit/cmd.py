import os
import subprocess
import logging
import threading
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from functools import partial

import sublime

from .util import get_executable, get_setting
from .helpers import GitRepoHelper


logger = logging.getLogger('SublimeGit.cmd')


class SublimeGitException(Exception):
    pass


# Git commands are serialized per repository. Two git processes touching the
# same repo at once (a status refresh on a worker thread and a stage from the
# UI thread, say) race for ``.git/index.lock`` and one of them fails with
# "Unable to create '.../index.lock': File exists". Holding a lock keyed by
# the working directory for the lifetime of each process removes that race.
# Commands run without a ``cwd`` share one fallback lock.
_repo_locks = {}
_repo_locks_guard = threading.Lock()


def repo_lock(cwd):
    key = os.path.realpath(cwd) if cwd else None
    with _repo_locks_guard:
        lock = _repo_locks.get(key)
        if lock is None:
            lock = _repo_locks[key] = threading.Lock()
        return lock


def reset_repo_locks():
    """Test hook: drop every per-repo lock."""
    with _repo_locks_guard:
        _repo_locks.clear()


@contextmanager
def _no_lock():
    yield


# Git commands that talk to a remote. They can run for a long time (a slow
# network, a credential helper waiting for input), so they never hold the
# repo lock: a push that hangs must not freeze every UI-thread git call in
# the same repo behind it.
NETWORK_COMMANDS = frozenset(['fetch', 'push', 'pull', 'clone', 'ls-remote'])
NETWORK_REMOTE_SUBCOMMANDS = frozenset(['show', 'prune', 'update'])


def is_network_command(cmd):
    args = [c for c in cmd if c]
    if not args:
        return False
    if args[0] in NETWORK_COMMANDS:
        return True
    return args[0] == 'remote' and len(args) > 1 and args[1] in NETWORK_REMOTE_SUBCOMMANDS


# Read-only calls made from worker threads (status view and status bar
# refreshes, diff views) run with GIT_OPTIONAL_LOCKS=0. Git then never takes
# ``index.lock`` for them (it skips writing back the refreshed index), which
# is the race the repo lock exists for, so they skip the repo lock as well
# and never queue behind a slow command from the UI thread, or the other way
# round. The flag is per thread so UI-thread calls are unaffected.
_read_only = threading.local()


@contextmanager
def read_only_git():
    """Run every git call made by this thread inside the block read-only."""
    previous = getattr(_read_only, 'active', False)
    _read_only.active = True
    try:
        yield
    finally:
        _read_only.active = previous


def is_read_only():
    return getattr(_read_only, 'active', False)


class Cmd(object):
    started_at = datetime.today()
    last_popup_at = None

    executable = None
    bin = []
    opts = []

    # cmd helpers
    def _string(self, cmd, strip=True, *args, **kwargs):
        _, stdout, _ = self.cmd(cmd, *args, **kwargs)
        return stdout.strip() if strip else stdout

    def _lines(self, cmd, *args, **kwargs):
        _, stdout, _ = self.cmd(cmd, *args, **kwargs)
        stdout = stdout.rstrip()
        if not stdout:
            return []
        return stdout.split('\n')

    def _exit_code(self, cmd, *args, **kwargs):
        exit, _, _ = self.cmd(cmd, *args, **kwargs)
        return exit

    def build_command(self, cmd):
        bin = get_executable(self.executable, self.bin)
        return bin + self.opts + [c for c in cmd if c]

    def env(self, read_only=False):
        env = os.environ.copy()
        path = get_setting('git_force_path', [])
        if path:
            if isinstance(path, list):
                env['PATH'] = os.pathsep.join(path)
            elif isinstance(path, str):
                env['PATH'] = path
        # There is no terminal to answer a username/password prompt on, so
        # make git fail right away instead of waiting on one forever.
        env.setdefault('GIT_TERMINAL_PROMPT', '0')
        if read_only:
            env['GIT_OPTIONAL_LOCKS'] = '0'
        return env

    def uses_repo_lock(self, cmd):
        return True

    def lock_for(self, cmd, cwd, read_only=False):
        if read_only or not self.uses_repo_lock(cmd):
            return _no_lock()
        return repo_lock(cwd)

    def startupinfo(self):
        startupinfo = None
        if hasattr(subprocess, 'STARTUPINFO'):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo

    def decode(self, stream, encoding, fallback=None):
        if not hasattr(stream, 'decode'):
            return stream

        try:
            return stream.decode(encoding)
        except UnicodeDecodeError:
            if fallback:
                for enc in fallback:
                    try:
                        return stream.decode(enc)
                    except UnicodeDecodeError:
                        pass
            raise

    # sync commands
    def cmd(self, cmd, stdin=None, cwd=None, ignore_errors=False, encoding=None, fallback=None, read_only=None):
        if read_only is None:
            read_only = is_read_only()
        command = self.build_command(cmd)
        environment = self.env(read_only=read_only)
        encoding = encoding or get_setting('encoding', 'utf-8')
        fallback = fallback or get_setting('fallback_encodings', [])

        try:
            logger.debug("cmd: %s", command)

            if stdin and hasattr(stdin, 'encode'):
                stdin = stdin.encode(encoding)

            with self.lock_for(cmd, cwd, read_only):
                proc = subprocess.Popen(command,
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        startupinfo=self.startupinfo(),
                                        cwd=cwd or None,
                                        env=environment)
                stdout, stderr = proc.communicate(stdin)

            logger.debug("out: (%s) %s", proc.returncode, [stdout[:100]])

            return (proc.returncode, self.decode(stdout, encoding, fallback), self.decode(stderr, encoding, fallback))
        except OSError as e:
            if ignore_errors:
                return (0, '', '')
            sublime.error_message(self.get_executable_error())
            raise SublimeGitException("Could not execute command: %s" % e)
        except UnicodeDecodeError as e:
            if ignore_errors:
                return (0, '', '')
            sublime.error_message(self.get_decoding_error(encoding, fallback))
            raise SublimeGitException("Could not execute command: %s" % command)

    # async commands
    def cmd_async(self, cmd, cwd=None, **callbacks):
        command = self.build_command(cmd)
        environment = self.env()
        encoding = get_setting('encoding', 'utf-8')
        fallback = get_setting('fallback_encodings', [])
        lock = self.lock_for(cmd, cwd)

        def async_inner(cmd, cwd, encoding, on_data=None, on_complete=None, on_error=None, on_exception=None):
            try:
                logger.debug('async-cmd: %s', cmd)

                with lock:
                    proc = subprocess.Popen(cmd,
                                            stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT,
                                            startupinfo=self.startupinfo(),
                                            cwd=cwd or None,
                                            env=environment)

                    for line in iter(proc.stdout.readline, b''):
                        logger.debug('async-out: %s', line.strip())
                        line = self.decode(line, encoding, fallback)
                        if callable(on_data):
                            sublime.set_timeout(partial(on_data, line), 0)

                    proc.wait()
                logger.debug('async-exit: %s', proc.returncode)
                if proc.returncode == 0:
                    if callable(on_complete):
                        sublime.set_timeout(partial(on_complete, proc.returncode), 0)
                else:
                    if callable(on_error):
                        sublime.set_timeout(partial(on_error, proc.returncode), 0)

            except (OSError, UnicodeDecodeError) as e:
                logger.debug('async-exception: %s' % e)
                if callable(on_exception):
                    sublime.set_timeout(partial(on_exception, e), 0)

        thread = threading.Thread(target=partial(async_inner, command, cwd, encoding, **callbacks))
        return thread

    # messages
    EXECUTABLE_ERROR = ("Executable '{bin}' was not found in PATH. Current PATH:\n\n"
                        "{path}\n\n"
                        "Try adjusting the git_executables['{executable}'] setting.")

    def get_executable_error(self):
        path = "\n".join(os.environ.get('PATH', '').split(os.pathsep))
        return self.EXECUTABLE_ERROR.format(executable=self.executable,
                                            path=path,
                                            bin=self.bin)

    DECODING_ERROR = ("Could not decode output from git. This means that you have a commit "
                      "message or some files in an unrecognized encoding. The following encodings "
                      "were tried:\n\n"
                      "{encodings}\n\n"
                      "Try adjusting the fallback_encodings setting.")

    def get_decoding_error(self, encoding, fallback):
        encodings = [encoding]
        if fallback:
            encodings.extend(fallback)
        return self.DECODING_ERROR.format(encodings="\n".join(encodings))


class GitCmd(GitRepoHelper, Cmd):
    executable = 'git'
    bin = ['git']
    opts = [
        '--no-pager',
        '-c', 'color.diff=false',
        '-c', 'color.status=false',
        '-c', 'color.branch=false',
        '-c', 'status.displayCommentPrefix=true',
        '-c', 'core.commentchar=#',
    ]

    def uses_repo_lock(self, cmd):
        return not is_network_command(cmd)

    def git(self, cmd, *args, **kwargs):
        return self.cmd(cmd, *args, **kwargs)

    def git_string(self, cmd, *args, **kwargs):
        return self._string(cmd, *args, **kwargs)

    def git_lines(self, cmd, *args, **kwargs):
        return self._lines(cmd, *args, **kwargs)

    def git_exit_code(self, cmd, *args, **kwargs):
        return self._exit_code(cmd, *args, **kwargs)

    def git_async(self, cmd, *args, **kwargs):
        return self.cmd_async(cmd, *args, **kwargs)


class GitFlowCmd(GitRepoHelper, Cmd):
    executable = 'git_flow'
    bin = ['git-flow']

    def git_flow(self, cmd, *args, **kwargs):
        return self.cmd(cmd, *args, **kwargs)

    def git_flow_string(self, cmd, *args, **kwargs):
        return self._string(cmd, *args, **kwargs)

    def git_flow_lines(self, cmd, *args, **kwargs):
        return self._lines(cmd, *args, **kwargs)

    def git_flow_exit_code(self, cmd, *args, **kwargs):
        return self._exit_code(cmd, *args, **kwargs)

    def git_flow_async(self, cmd, *args, **kwargs):
        return self.cmd_async(cmd, *args, **kwargs)


class LegitCmd(GitRepoHelper, Cmd):
    executable = 'legit'
    bin = ['legit']

    def legit(self, cmd, *args, **kwargs):
        return self.cmd(cmd, *args, **kwargs)

    def legit_string(self, cmd, *args, **kwargs):
        return self._string(cmd, *args, **kwargs)

    def legit_lines(self, cmd, *args, **kwargs):
        return self._lines(cmd, *args, **kwargs)

    def legit_exit_code(self, cmd, *args, **kwargs):
        return self._exit_code(cmd, *args, **kwargs)

    def legit_async(self, cmd, *args, **kwargs):
        return self.cmd_async(cmd, *args, **kwargs)
