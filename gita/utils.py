import os
import yaml
import subprocess
from functools import lru_cache
from typing import List, Dict, Tuple


class Color:
    """
    Terminal color
    """
    red = '\x1b[31m'  # local diverges from remote
    green = '\x1b[32m'  # local == remote
    yellow = '\x1b[33m'  # local is behind
    purple = '\x1b[35m'  # local is ahead
    white = '\x1b[37m'  # no remote branch
    end = '\x1b[0m'


def get_path_fname() -> str:
    """
    Return the file name that stores the repo locations.
    """
    root = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
        os.path.expanduser('~'), '.config')
    return os.path.join(root, 'gita', 'repo_path')


@lru_cache()
def get_repos() -> Dict[str, str]:
    """
    Return a `dict` of repo name to repo absolute path
    """
    path_file = get_path_fname()
    paths = []
    if os.path.isfile(path_file) and os.stat(path_file).st_size > 0:
        with open(path_file) as f:
            paths = f.read().splitlines()[0].split(os.pathsep)
    data = ((os.path.basename(os.path.normpath(p)), p) for p in paths
            if is_git(p))
    repos = {}
    for name, path in data:
        if name not in repos:
            repos[name] = path
        else:
            par_name = os.path.basename(os.path.dirname(path))
            repos[os.path.join(par_name, name)] = path
    return repos


def get_choices() -> List[str]:
    """
    Return all repo names and an additional empty list. This is a workaround of
    argparse's problem with coexisting nargs='*' and choices.
    See https://utcc.utoronto.ca/~cks/space/blog/python/ArgparseNargsChoicesLimitation
    and
    https://bugs.python.org/issue27227
    """
    repos = list(get_repos())
    repos.append([])
    return repos


def is_git(path: str) -> bool:
    """
    Return True if the path is a git repo.
    """
    # An alternative is to call `git rev-parse --is-inside-work-tree`
    # I don't see why that one is better yet.
    return os.path.isdir(os.path.join(path, '.git'))


def add_repos(new_paths: List[str]):
    """
    Write new repo paths to file
    """
    existing_paths = set(get_repos().values())
    new_paths = set(os.path.abspath(p) for p in new_paths if is_git(p))
    new_paths = new_paths - existing_paths
    if new_paths:
        print(f"Found {len(new_paths)} new repo(s): {new_paths}.")
        existing_paths.update(new_paths)
        fname = get_path_fname()
        os.makedirs(os.path.dirname(fname), exist_ok=True)
        with open(fname, 'w') as f:
            f.write(os.pathsep.join(sorted(existing_paths)))
    else:
        print('No new repos found!')


def get_head(path: str) -> str:
    head = os.path.join(path, '.git', 'HEAD')
    with open(head) as f:
        return os.path.basename(f.read()).rstrip()


def has_remote() -> bool:
    """
    Return True if remote branch exists. It should be run inside the repo.
    """
    result = subprocess.run(
        'git diff --quiet @{u} @{0}'.split(), stderr=subprocess.PIPE)
    return not bool(result.stderr)


def has_untracked() -> bool:
    """
    Return True if untracked file/folder exists
    """
    result = subprocess.run(
        'git ls-files -zo --exclude-standard'.split(), stdout=subprocess.PIPE)
    return bool(result.stdout)


def get_commit_msg() -> str:
    """
    Return the last commit message.
    """
    result = subprocess.run(
        'git show -s --format=%s'.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True)
    if result.stderr:  # no commit yet
        return '\n'
    return result.stdout


def exec_git(path: str, cmd: str):
    """
    Execute git `cmd` in the `path` directory
    """
    os.chdir(path)
    # FIXME: I forgot why I check remote here, but it disables git command
    #        execution if the branch has no remote. Maybe this check is still
    #        needed for the commands not in the yml file?
    # if has_remote():
    os.system('git ' + cmd)


def get_common_commit() -> str:
    """
    Return the hash of the common commit of the local and upstream branches.
    """
    result = subprocess.run(
        'git merge-base @{0} @{u}'.split(),
        stdout=subprocess.PIPE,
        universal_newlines=True)
    return result.stdout


def _get_repo_status(path: str) -> Tuple[str]:
    """
    Return the status of one repo
    """
    os.chdir(path)
    dirty = '*' if os.system('git diff --quiet') else ''
    staged = '+' if os.system('git diff --cached --quiet') else ''
    untracked = '_' if has_untracked() else ''

    if has_remote():
        if os.system('git diff --quiet @{u} @{0}'):
            common_commit = get_common_commit()
            outdated = os.system('git diff --quiet @{u} ' + common_commit)
            if outdated:
                diverged = os.system('git diff --quiet @{0} ' + common_commit)
                color = Color.red if diverged else Color.yellow
            else:  # local is ahead of remote
                color = Color.purple
        else:  # remote == local
            color = Color.green
    else:  # no remote
        color = Color.white
    return dirty, staged, untracked, color


def describe(repos: Dict[str, str]) -> str:
    """
    Return the status of all repos
    """
    if repos:
        name_width = max(len(n) for n in repos) + 1
    for name in sorted(repos):
        path = repos[name]
        head = get_head(path)
        dirty, staged, untracked, color = _get_repo_status(path)
        yield f'{name:<{name_width}}{color}{head+" "+dirty+staged+untracked:<10}{Color.end} {get_commit_msg()}'


def get_cmds_from_files() -> Dict[str, Dict[str, str]]:
    """
    Parse delegated git commands from default config file
    and custom config file.

    Example return
    {
      'branch': {'help': 'show local branches'},
      'clean': {'cmd': 'clean -dfx',
                'help': 'remove all untracked files/folders'},
    }
    """
    # default config file
    fname = os.path.join(os.path.dirname(__file__), "cmds.yml")
    with open(fname, 'r') as stream:
        cmds = yaml.load(stream)

    # custom config file
    root = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
        os.path.expanduser('~'), '.config')
    fname = os.path.join(root, 'gita', 'cmds.yml')
    custom_cmds = {}
    if os.path.isfile(fname):
        with open(fname, 'r') as stream:
            custom_cmds = yaml.load(stream)

    # custom commands shadow default ones
    cmds.update(custom_cmds)
    return cmds


def assemble_shlex_input(args: List[str]) -> str:
    """
    """
    for i, arg in enumerate(args):
        # this is a simple detection of multi-word string
        if arg.count(' ') > 0:
            args[i] = f'"{arg}"'
    return ' '.join(args)