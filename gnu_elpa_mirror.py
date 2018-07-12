#!/usr/bin/env python3

import datetime
import fileinput
import github
import os
import pathlib
import shutil
import subprocess
import sys

os.chdir(os.path.dirname(__file__))

def log(message):
    print(message, file=sys.stderr)

def die(message):
    log("gnu_elpa_mirror: " + message)
    sys.exit(1)

try:
    ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
except KeyError:
    die("please export ACCESS_TOKEN to a valid GitHub API token")

def clone_git_repo(git_url, repo_dir, shallow, all_branches, private_url):
    if not repo_dir.is_dir():
        cmd = ["git", "clone"]
        if shallow:
            cmd.extend(["--depth", "1"])
            if all_branches:
                cmd.append("--no-single-branch")
        cmd.extend([git_url, repo_dir])
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            if private_url:
                die("cloning repository failed (details omitted for security)")
            raise
    else:
        result = subprocess.run(
            ["git", "symbolic-ref", "HEAD"],
            cwd=repo_dir, check=True, stdout=subprocess.PIPE)
        branch = result.stdout.decode().strip()
        ref = "refs/remotes/origin/{}".format(branch)
        subprocess.run(["git", "fetch"], cwd=repo_dir, check=True)
        result = subprocess.run(["git", "show-ref", ref], cwd=repo_dir)
        # Check if there is a master branch to merge from upstream.
        # Also, avoid creating merges or rebases due to a diverging
        # history.
        if result.returncode == 0:
            subprocess.run(["git", "reset", "--hard", ref],
                           cwd=repo_dir, check=True)

# https://savannah.gnu.org/git/?group=emacs
GNU_ELPA_GIT_URL = "https://git.savannah.gnu.org/git/emacs/elpa.git"
EMACS_GIT_URL = "https://git.savannah.gnu.org/git/emacs.git"

GNU_ELPA_SUBDIR = pathlib.Path("gnu-elpa")
GNU_ELPA_PACKAGES_SUBDIR = GNU_ELPA_SUBDIR / "packages"
EMACS_SUBDIR = GNU_ELPA_SUBDIR / "emacs"
REPOS_SUBDIR = pathlib.Path("repos")

def mirror(args):
    api = github.Github(ACCESS_TOKEN)
    log("--> clone/update GNU ELPA")
    clone_git_repo(
        GNU_ELPA_GIT_URL, GNU_ELPA_SUBDIR,
        shallow=True, all_branches=True, private_url=False)
    log("--> clone/update Emacs")
    clone_git_repo(
        EMACS_GIT_URL, EMACS_SUBDIR,
        shallow=True, all_branches=False, private_url=False)
    log("--> check timestamp and commit hashes")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    gnu_elpa_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GNU_ELPA_SUBDIR, stdout=subprocess.PIPE,
        check=True).stdout.decode().strip()
    emacs_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=EMACS_SUBDIR, stdout=subprocess.PIPE,
        check=True).stdout.decode().strip()
    log("--> install bugfix in GNU ELPA build script")
    subprocess.run(
        ["git", "checkout", "admin/archive-contents.el"], cwd=GNU_ELPA_SUBDIR)
    with fileinput.FileInput(GNU_ELPA_SUBDIR / "admin" / "archive-contents.el",
                             inplace=True) as f:
        for line in f:
            line = line.replace(
                '(cons file-pattern "")',
                '(cons file-pattern (file-name-nondirectory file-pattern))')
            print(line, end="")
    log("--> retrieve/update GNU ELPA external packages")
    subprocess.run(["make", "externals"], cwd=GNU_ELPA_SUBDIR, check=True)
    log("--> get list of mirror repositories")
    existing_repos = []
    for repo in api.get_user("emacs-straight").get_repos():
        existing_repos.append(repo.name)
    packages = []
    for subdir in GNU_ELPA_PACKAGES_SUBDIR.iterdir():
        if not subdir.is_dir():
            continue
        packages.append(subdir.name)
    log("--> clone/update mirror repositories")
    org = api.get_organization("emacs-straight")
    REPOS_SUBDIR.mkdir(exist_ok=True)
    for package in packages:
        git_url = ("https://raxod502:{}@github.com/emacs-straight/{}.git"
                   .format(ACCESS_TOKEN, package))
        repo_dir = REPOS_SUBDIR / package
        if package not in existing_repos:
            log("----> create mirror repository {}".format(package))
            org.create_repo(
                package,
                description=("Mirror of the {} package from GNU ELPA"
                             .format(package)),
                homepage=("https://elpa.gnu.org/packages/{}.html"
                          .format(package)),
                has_issues=False,
                has_wiki=False,
                has_projects=False,
                auto_init=False)
        if "--skip-mirror-pulls" in args and repo_dir.is_dir():
            continue
        log("----> clone/update mirror repository {}".format(package))
        clone_git_repo(git_url, repo_dir,
                       shallow=True, all_branches=False, private_url=True)
    log("--> update mirrored packages")
    for package in packages:
        log("----> update package {}".format(package))
        package_dir = GNU_ELPA_PACKAGES_SUBDIR / package
        repo_dir = REPOS_SUBDIR / package
        for entry in repo_dir.iterdir():
            if entry.name == ".git":
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                try:
                    entry.unlink()
                except FileNotFoundError:
                    pass
        for source in package_dir.iterdir():
            target = repo_dir / source.name
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, target)
            else:
                shutil.copyfile(source, target)
        subprocess.run(["git", "add", "--all"], cwd=repo_dir, check=True)
        anything_staged = (
            subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=repo_dir).returncode != 0)
        if anything_staged:
            subprocess.run(["git", "commit", "-m",
                            ("Update {}\n\nTimestamp: {}\n"
                             "GNU ELPA commit: {}\nEmacs commit: {}")
                            .format(package, timestamp,
                                    gnu_elpa_commit, emacs_commit)],
                           cwd=repo_dir, check=True)
        else:
            log("(no changes)")
    log("--> push changes")
    for package in packages:
        log("----> push changes to package {}".format(package))
        repo_dir = REPOS_SUBDIR / package
        subprocess.run(["git", "push"], cwd=repo_dir, check=True)

if __name__ == "__main__":
    mirror(sys.argv[1:])
