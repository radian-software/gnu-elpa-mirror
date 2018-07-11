#!/usr/bin/env python3

import fileinput
import os
import pathlib
import subprocess
import sys

os.chdir(os.path.dirname(__file__))

def clone_git_repo(git_url, repo_dir, shallow, all_branches=False):
    if not repo_dir.is_dir():
        cmd = ["git", "clone"]
        if shallow:
            cmd.extend(["--depth", "1"])
            if all_branches:
                cmd.append("--no-single-branch")
        cmd.extend([git_url, repo_dir])
        subprocess.run(cmd, check=True)
    else:
        subprocess.run(["git", "pull"], cwd=repo_dir)

# https://savannah.gnu.org/git/?group=emacs
GNU_ELPA_GIT_URL = "https://git.savannah.gnu.org/git/emacs/elpa.git"
EMACS_GIT_URL = "https://git.savannah.gnu.org/git/emacs.git"

GNU_ELPA_SUBDIR = pathlib.Path("gnu-elpa")
EMACS_SUBDIR = GNU_ELPA_SUBDIR / "emacs"

def mirror():
    print("--> clone/update GNU ELPA", file=sys.stderr)
    clone_git_repo(
        GNU_ELPA_GIT_URL, GNU_ELPA_SUBDIR, shallow=True, all_branches=True)
    print("--> clone/update Emacs", file=sys.stderr)
    clone_git_repo(EMACS_GIT_URL, EMACS_SUBDIR, shallow=True)
    print("--> install bugfix in GNU ELPA build script", file=sys.stderr)
    subprocess.run(
        ["git", "checkout", "admin/archive-contents.el"], cwd=GNU_ELPA_SUBDIR)
    with fileinput.FileInput(GNU_ELPA_SUBDIR / "admin" / "archive-contents.el",
                             inplace=True) as f:
        for line in f:
            line = line.replace(
                '(cons file-pattern "")',
                '(cons file-pattern (file-name-nondirectory file-pattern))')
            print(line, end="")
    print("--> retrieve GNU ELPA external packages")
    subprocess.run(["make", "externals"], cwd=GNU_ELPA_SUBDIR, check=True)

if __name__ == "__main__":
    mirror()
