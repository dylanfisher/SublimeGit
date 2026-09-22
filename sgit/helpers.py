import re
import os
import html
import logging
import sublime

from .util import get_setting


logger = logging.getLogger('SublimeGit.helpers')

# Quick panel item kinds (icon letter + tooltip shown next to each row)
KIND_REMOTE = (sublime.KIND_ID_NAMESPACE, 'r', 'Remote')
KIND_BRANCH = (sublime.KIND_ID_COLOR_ORANGISH, 'b', 'Branch')
KIND_COMMIT = (sublime.KIND_ID_COLOR_BLUISH, 'c', 'Commit')
KIND_TAG = (sublime.KIND_ID_COLOR_GREENISH, 't', 'Tag')
KIND_STASH = (sublime.KIND_ID_COLOR_PURPLISH, 's', 'Stash')


def format_details(*parts):
    """Build a ``QuickPanelItem.details`` list.

    ``details`` is rendered as minihtml, so anything coming from git (author
    emails in angle brackets, urls containing ``&``, ...) has to be escaped or
    it is silently swallowed by the markup parser. Empty parts are dropped.
    """
    return [html.escape(p, quote=False) for p in parts if p]


ERE_SPECIAL_RE = re.compile(r'([.^$*+?()\[\]{}|\\])')


def ere_escape(string):
    """Escape ``string`` for use as a literal inside a POSIX extended regexp
    (what ``git config --get-regexp`` matches with)."""
    return ERE_SPECIAL_RE.sub(r'\\\1', string)


GIT_INIT_DIALOG = ("Could not find any git repositories based on the open files and folders. "
                   "Do you want to initialize a repository?")


class GitRepoHelper(object):
    # fallback repos for windows, indexed by id
    windows = {}

    # working dir remake
    def get_dir_from_view(self, view=None):
        d = None
        if view is not None and view.file_name():
            d = os.path.realpath(os.path.dirname(view.file_name()))
            logger.info('get_dir_from_view(view=%s): %s', view.id(), d)
        return d

    def get_dirs_from_window_folders(self, window=None):
        dirs = set()
        if window is not None:
            dirs = set(f for f in window.folders())
            logger.info('get_dirs_from_window_folders(window=%s): %s', window.id(), dirs)
        return dirs

    def get_dirs_from_window_views(self, window=None):
        dirs = set()
        if window is not None:
            view_dirs = [self.get_dir_from_view(v) for v in window.views()]
            dirs = set(d for d in view_dirs if d)
            logger.info('get_dirs_from_window_views(window=%s): %s', window.id(), dirs)
        return dirs

    def get_dirs(self, window=None):
        dirs = set()
        if window is not None:
            dirs |= self.get_dirs_from_window_folders(window)
            dirs |= self.get_dirs_from_window_views(window)
            logger.info('get_dirs(window=%s): %s', window.id(), dirs)
        return dirs

    def get_dirs_prioritized(self, window=None):
        dirs = list()
        if window is not None:
            all_dirs = self.get_dirs(window)
            active_view_dir = self.get_dir_from_view(window.active_view())
            if active_view_dir:
                dirs.append(active_view_dir)
                all_dirs.discard(active_view_dir)
            for d in sorted(list(all_dirs), key=lambda x: len(x), reverse=True):
                dirs.append(d)
            logger.info('get_dirs_prioritized(window=%s): %s', window.id(), dirs)
        return dirs

    # path walking
    def all_dirnames(self, directory):
        dirnames = [directory]
        while directory and directory != os.path.dirname(directory):
            directory = os.path.dirname(directory)
            dirnames.append(directory)

        logger.info('all_dirs(directory=%s): %s', directory, dirnames)
        return dirnames

    # git repos
    def is_git_repo(self, directory):
        git_dir = os.path.join(directory, '.git')
        return os.path.exists(git_dir)

    def first_git_repo(self, directory):
        # check the first directory and exit fast
        if self.is_git_repo(directory):
            return directory

        # check up the tree
        while directory and directory != os.path.dirname(directory):
            directory = os.path.dirname(directory)
            if self.is_git_repo(directory):
                return directory

        # No repos
        return None

    def find_git_repos(self, directories):
        repos = set()
        for directory in directories:
            for dirname in self.all_dirnames(directory):
                if self.is_git_repo(dirname):
                    repos.add(dirname)
        return repos

    def git_repos_from_window(self, window=None):
        repos = set()
        if window is not None:
            dirs = self.get_dirs_prioritized(window)
            for repo in self.find_git_repos(dirs):
                repos.add(repo)
        return repos

    def git_repo_from_view(self, view=None):
        repo = None
        if view is not None:
            view_dir = self.get_dir_from_view(view)
            if view_dir:
                repo = self.first_git_repo(view_dir)
        return repo

    def get_repo(self, silent=False):
        repo = None

        if hasattr(self, 'view'):
            repo = self.get_repo_from_view(self.view, silent=silent)
            if self.view.window() and not repo:
                repo = self.get_repo_from_window(self.view.window(), silent=silent)
        elif hasattr(self, 'window'):
            repo = self.get_repo_from_window(self.window, silent=silent)

        return repo

    def get_repo_from_view(self, view=None, silent=True):
        if view is None:
            return

        # first try the view settings (for things like status, diff, etc)
        view_repo = view.settings().get('git_repo')
        if view_repo:
            logger.info('get_repo_from_view(view=%s, silent=%s): %s (view settings)', view.id(), silent, view_repo)
            return view_repo

        # else try the given view file
        file_repo = self.git_repo_from_view(view)
        if file_repo:
            logger.info('get_repo(window=%s, silent=%s): %s (file)', view.id(), silent, file_repo)
            return file_repo

    def get_repo_from_window(self, window=None, silent=True):
        if not window:
            logger.info('get_repo_from_window(window=%s, silent=%s): None (no window)', None, silent)
            return

        active_view = window.active_view()
        if active_view is not None:
            # if the active view has a setting, use that
            active_view_repo = active_view.settings().get('git_repo')
            if active_view_repo:
                logger.info('get_repo_from_window(window=%s, silent=%s): %s (view settings)', window.id(), silent, active_view_repo)
                return active_view_repo

            # if the active view has a filename, use that
            active_file_repo = self.git_repo_from_view(active_view)
            if active_file_repo:
                logger.info('get_repo_from_window(window=%s, silent=%s): %s (active file)', window.id(), silent, active_file_repo)
                return active_file_repo

        # find all possible repos
        any_repos = self.git_repos_from_window(window)
        window_repo = self.get_window_repository(window)

        # if there is only one repository, use that
        if len(any_repos) == 1:
            only_repo = any_repos.pop()
            logger.info('get_repo_from_window(window=%s, silent=%s): %s (only repo)', window.id(), silent, only_repo)
            return only_repo
        elif len(any_repos) > 1 and window_repo:
            logger.info('get_repo_from_window(window=%s, silent=%s): %s (window repo)', window.id(), silent, window_repo)
            return window_repo

        if silent:
            logger.info('get_repo_from_window(window=%s, silent=%s): None (silent)', window.id(), silent)
            return

        if any_repos:
            window.run_command('git_switch_repo')
        else:
            if sublime.ok_cancel_dialog(GIT_INIT_DIALOG, 'Initialize repository'):
                window.run_command('git_init')

    def set_window_repository(self, window, repo):
        GitRepoHelper.windows[window.id()] = repo

    def get_window_repository(self, window):
        return GitRepoHelper.windows.get(window.id())


# Remotes that are listed ahead of the rest, in this order. Everything else
# follows alphabetically.
PREFERRED_REMOTES = ('origin', 'upstream')


def remote_sort_key(name):
    try:
        return (PREFERRED_REMOTES.index(name), '')
    except ValueError:
        return (len(PREFERRED_REMOTES), name)


def sort_remote_names(names):
    """Sort remote names so ``origin`` and ``upstream`` come first."""
    return sorted(set(names), key=remote_sort_key)


class GitBranchHelper(object):

    def get_current_branch(self, repo):
        branch = self.git_string(['symbolic-ref', '-q', 'HEAD'], cwd=repo)
        return branch[11:] if branch.startswith('refs/heads/') else branch

    BRANCH_DETAILS_FORMAT = '%(refname)%09%(HEAD)%09%(upstream:short)%09%(subject)'

    def get_branch_details(self, repo, remotes=False):
        """Return ``[(name, current, upstream, subject), ...]`` for every local
        (or, with ``remotes=True``, remote-tracking) branch from one git call.
        Symbolic refs such as ``origin/HEAD`` are skipped."""
        prefix = 'refs/remotes/' if remotes else 'refs/heads/'
        lines = self.git_lines(['for-each-ref', '--format=%s' % self.BRANCH_DETAILS_FORMAT, prefix], cwd=repo)
        branches = []
        for line in lines:
            parts = line.split('\t', 3)
            if len(parts) != 4:
                continue
            refname, head, upstream, subject = parts
            name = refname[len(prefix):]
            if remotes and name.endswith('/HEAD'):
                continue
            branches.append((name, head == '*', upstream, subject))
        return branches

    def format_quick_branch_details(self, branches, annotation=''):
        choices = []
        for name, current, upstream, subject in branches:
            details = format_details(subject, ('tracks %s' % upstream) if upstream else '')
            choices.append(sublime.QuickPanelItem(name, details=details, annotation=annotation, kind=KIND_BRANCH))
        return choices

    def get_git_paths(self, repo, *names):
        """Resolve ``names`` inside the git dir (``git rev-parse --git-path``)
        to absolute paths with one call."""
        cmd = ['rev-parse']
        for name in names:
            cmd.extend(['--git-path', name])
        paths = self.git_lines(cmd, cwd=repo)
        return [p if os.path.isabs(p) else os.path.join(repo, p) for p in paths]

    def get_in_progress(self, repo):
        """Return ``(merging, rebasing)`` for the repo, from one git call."""
        merge_head, rebase_merge, rebase_apply = self.get_git_paths(repo, 'MERGE_HEAD', 'rebase-merge', 'rebase-apply')
        return (os.path.exists(merge_head),
                os.path.isdir(rebase_merge) or os.path.isdir(rebase_apply))

    def get_branches(self, repo, remotes=False):
        lines = self.git_lines(['branch', '--list', '--no-color', '--remotes' if remotes else None], cwd=repo)

        branches = []
        for line in lines:
            current = line.startswith('*')
            nameparts = line[2:].split(' -> ')
            name = nameparts[1] if len(nameparts) == 2 else nameparts[0]
            branches.append((current, name))

        return branches


class GitRemoteHelper(GitBranchHelper):

    def get_remotes(self, repo):
        return self.git_lines(['remote', '-v'], cwd=repo)

    def get_remote_names(self, remotes):
        names = set()
        for r in remotes:
            name, right = r.split('\t', 1)
            url, action = right.rsplit(' ', 1)
            names.add(name)
        return sort_remote_names(names)

    def format_quick_remotes(self, remotes):
        data = {}
        for r in remotes:
            name, right = r.split('\t', 1)
            url, action = right.rsplit(' ', 1)
            data.setdefault(name, {})[action] = "%s %s" % (url, action)
        choices = []
        for remote in sort_remote_names(data):
            urls = data[remote]
            choices.append(sublime.QuickPanelItem(
                remote,
                details=format_details(urls.get('(fetch)'), urls.get('(push)')),
                kind=KIND_REMOTE))
        return choices

    def get_remote_url(self, repo, remote):
        return self.git_string(['config', 'remote.%s.url' % remote], cwd=repo)

    def get_remote_and_url(self, repo, branch):
        """Return ``(remote, url)`` for ``branch`` using a single git process.

        ``branch.<branch>.remote`` stays authoritative for the remote name (it
        is not derived from the upstream ref), and the url is picked from the
        ``remote.<name>.url`` entries returned by the same call. Returns
        ``(None, None)`` when the branch has no configured remote.
        """
        if not branch:
            return (None, None)

        pattern = r'^(branch\.%s\.remote|remote\..+\.url)$' % ere_escape(branch)
        remote, urls = None, {}
        for line in self.git_lines(['config', '--get-regexp', pattern], cwd=repo):
            key, _, value = line.partition(' ')
            if key.startswith('branch.') and key.endswith('.remote'):
                remote = value
            elif key.startswith('remote.') and key.endswith('.url'):
                urls[key[len('remote.'):-len('.url')]] = value

        if not remote:
            return (None, None)
        return (remote, urls.get(remote, ''))

    def get_branch_upstream(self, repo, branch):
        return (self.get_branch_remote(repo, branch), self.get_branch_merge(repo, branch))

    def get_branch_remote(self, repo, branch):
        return self.git_string(['config', 'branch.%s.remote' % branch], cwd=repo)

    def get_branch_merge(self, repo, branch):
        return self.git_string(['config', 'branch.%s.merge' % branch], cwd=repo)

    def get_remote_branches(self, repo, remote):
        branches = [b for _, b in self.get_branches(repo, remotes=True)]
        return [b for b in branches if b.startswith(remote + '/')]

    def format_quick_branches(self, branches):
        choices = []
        for b in branches:
            branch = b.split('/', 1)[1]
            choices.append(sublime.QuickPanelItem(branch, details=format_details(b), kind=KIND_BRANCH))
        return choices


class GitStashHelper(object):

    STASH_RE = re.compile(r'^stash@\{(.*)\}:\s*(.*)')

    def get_stashes(self, repo):
        stashes = []
        output = self.git_lines(['stash', 'list'], cwd=repo)
        for line in output:
            match = self.STASH_RE.match(line)
            if match:
                stashes.append((match.group(1), match.group(2)))
        return stashes


class GitErrorHelper(object):

    def format_error_message(self, msg):
        if msg.startswith('error: '):
            msg = msg[7:]
        elif msg.startswith('Note: '):
            msg = msg[6:]
        if msg.endswith('Aborting\n'):
            msg = msg.rstrip()[:-8]
        return msg


# The index of the path field in each porcelain v2 entry kind, i.e. the number
# of space-separated fields that precede it (paths may contain spaces, so the
# split is bounded by this).
PORCELAIN_V2_PATH_FIELD = {
    '1': 8,   # 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
    '2': 9,   # 2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\0<origPath>
    'u': 10,  # u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
}


def parse_porcelain_v2_z(output):
    """Parse ``git status --porcelain=v2 --branch -z`` output.

    Returns ``(branch, upstream, lines)``, where ``lines`` are v1-style
    porcelain strings: ``"XY path"``, or ``"XY old -> new"`` for renames and
    copies. ``branch`` is ``None`` for a detached HEAD (an unborn branch still
    reports its name). Unmodified states are reported as ``.`` in v2 and are
    translated back to a space.
    """
    branch, upstream, lines = None, None, []

    rows = output.split('\x00')
    idx = 0
    while idx < len(rows):
        row = rows[idx]
        idx += 1
        if not row:
            continue

        kind = row[0]
        if kind == '#':
            key, _, value = row[2:].partition(' ')
            if key == 'branch.head':
                branch = None if value == '(detached)' else value
            elif key == 'branch.upstream':
                upstream = value or None
            continue

        if kind in ('1', '2', 'u'):
            status = row[2:4].replace('.', ' ')
            path = row.split(' ', PORCELAIN_V2_PATH_FIELD[kind])[PORCELAIN_V2_PATH_FIELD[kind]]
            if kind == '2':
                # with -z the original path is a separate NUL-terminated field
                orig = rows[idx] if idx < len(rows) else ''
                idx += 1
                lines.append("%s %s -> %s" % (status, orig, path))
            else:
                lines.append("%s %s" % (status, path))
        elif kind in ('?', '!'):
            lines.append("%s%s %s" % (kind, kind, row[2:]))

    return branch, upstream, lines


class GitStatusHelper(object):

    def file_in_git(self, repo, filename):
        return self.git_exit_code(['ls-files', filename, '--error-unmatch'], cwd=repo) == 0

    def get_changes(self, repo):
        """Return ``(staged, unstaged)`` booleans from a single git process.

        This is the one-call equivalent of ``has_staged_changes(repo)`` and
        ``has_unstaged_changes(repo)``: ``git status --porcelain -z
        --untracked-files=no`` reports the same index/worktree columns that
        ``git diff --quiet --cached`` / ``git diff --quiet`` base their exit
        codes on. Unmerged entries (``UU``, ``AA``, ``DD``, ``AU``, ...) have
        both columns set and so count as both staged and unstaged, which is
        what the two diff calls report for a conflict.
        """
        output = self.git_string(['status', '--porcelain', '-z', '--untracked-files=no'],
                                 cwd=repo, strip=False)

        staged, unstaged = False, False
        records = output.split('\x00')
        idx = 0
        while idx < len(records):
            record = records[idx]
            idx += 1
            if len(record) < 2:
                continue
            index, worktree = record[0], record[1]
            if index in ('R', 'C'):
                # with -z the original path follows as its own record
                idx += 1
            if index not in (' ', '?', '!'):
                staged = True
            if worktree not in (' ', '?', '!'):
                unstaged = True
            if staged and unstaged:
                break

        return staged, unstaged

    def has_changes(self, repo):
        return any(self.get_changes(repo))

    def has_staged_changes(self, repo):
        return self.git_exit_code(['diff', '--exit-code', '--quiet', '--cached'], cwd=repo) != 0

    def has_unstaged_changes(self, repo):
        return self.git_exit_code(['diff', '--exit-code', '--quiet'], cwd=repo) != 0

    def get_branch_and_status(self, repo):
        """Run one ``git status --porcelain=v2 --branch -z`` and return
        ``(branch, upstream, lines)``.

        ``branch`` is ``None`` for a detached HEAD, ``upstream`` is ``None``
        when the branch has none, and ``lines`` are the v1-style porcelain
        strings (``"XY path"`` / ``"XY old -> new"``) the rest of the plugin
        expects. Using the v2 format means the branch name and its upstream
        come out of the same process as the file status.
        """
        mode = self.get_untracked_mode()
        cmd = ['status', '--porcelain=v2', '--branch', '-z',
               ('--untracked-files=%s' % mode) if mode else None]

        output = self.git_string(cmd, cwd=repo, strip=False)
        return parse_porcelain_v2_z(output)

    def get_porcelain_status(self, repo):
        return self.get_branch_and_status(repo)[2]

    def get_files_status(self, repo, lines=None):
        untracked, unstaged, staged = [], [], []
        status = self.get_porcelain_status(repo) if lines is None else lines
        for l in status:
            state, filename = l[:2], l[3:]
            index, worktree = state
            if state in ('DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'):
                logger.warning("unmerged WTF: %s, %s", state, filename)
            elif state == '??':
                untracked.append(('?', filename))
            elif state == '!!':
                continue
            else:
                if worktree != ' ':
                    unstaged.append((worktree, filename))
                if index != ' ':
                    staged.append((index, filename))
        return untracked, unstaged, staged

    def get_untracked_mode(self):
        # get untracked files mode
        setting = get_setting('git_status_untracked_files', 'all')

        mode = 'all'
        if setting == 'none':
            mode = 'no'
        elif setting == 'auto':
            mode = None
        return mode


class GitDiffHelper(object):

    def get_diff(self, repo, path=None, cached=False, unified=None):
        try:
            unified = int(unified)
        except (TypeError, ValueError):
            unified = None
        args = ['diff',
                '--cached' if cached else None,
                '--unified=%s' % unified if unified else None]
        if path:
            args.extend(['--', path])
        diff = self.git_string(args, cwd=repo, strip=False)
        if not cached and path and os.path.normpath(path) != os.path.normpath(repo):
            diff += self.get_untracked_diff(repo, path, unified)
        return diff

    def get_untracked_diff(self, repo, path, unified=None):
        # show untracked files as new files, diffed against /dev/null
        untracked = self.git_lines(['ls-files', '--others', '--exclude-standard', '--', path], cwd=repo)
        diffs = []
        for f in untracked:
            if f:
                diffs.append(self.git_string(['diff', '--no-index',
                                              '--unified=%s' % unified if unified else None,
                                              '--', os.devnull, f], cwd=repo, strip=False))
        return ''.join(diffs)


class GitShowHelper(object):

    def get_show(self, repo, obj):
        return self.git_string(['show', '--format=medium', '--no-color', obj], cwd=repo)


class GitLogHelper(object):

    GIT_QUICK_LOG_FORMAT = ('%s%x03'   # subject
                            '%H%x03'   # sha1
                            '%an%x03'  # author name
                            '%aE%x03'  # author email
                            '%ad%x03'  # auth date
                            '%ar'    # auth date relative
                            '%x04')

    def get_quick_log(self, repo, path=None, follow=False):
        cmd = ['log', '--no-color', '--date=local', '--format=%s' % self.GIT_QUICK_LOG_FORMAT]
        if follow:
            cmd.append('--follow')
        if path:
            cmd.extend(['--', path])
        out = self.git_string(cmd, cwd=repo, strip=False)

        lines = []
        for line in out.split('\u0004'):
            line = line.strip()
            if line:
                parts = line.split('\u0003')
                if len(parts) != 6:
                    raise Exception("The line %s splits to %s", line, parts)
                lines.append(parts)
        return lines

    def format_quick_log(self, log):
        hashes = [l[1] for l in log]
        choices = []
        for subject, sha, name, email, dt, reldt in log:
            choices.append(sublime.QuickPanelItem(
                subject,
                details=format_details('%s by %s <%s>' % (sha[0:8], name, email), '%s (%s)' % (reldt, dt)),
                kind=KIND_COMMIT))
        return hashes, choices


class GitTagHelper(object):

    def get_tags(self, repo, annotate=True):
        if annotate:
            return self.git_lines(['tag', '--list', '-n1'], cwd=repo)
        else:
            return self.git_lines(['tag', '--list', '-n0', '--no-column'], cwd=repo)

    def format_quick_tags(self, tags):
        out = []
        for t in reversed(tags):
            tag, ann = t.split(' ', 1)
            out.append(sublime.QuickPanelItem(tag, details=format_details(ann.strip()), kind=KIND_TAG))
        return out
