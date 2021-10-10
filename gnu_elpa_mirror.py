#!/usr/bin/env python3

import argparse
import datetime
import github
import os
import pathlib
import re
import requests
import shutil
import subprocess
import sys

os.chdir(os.path.dirname(__file__))


def remove_prefix(prefix, string):
    if string.startswith(prefix):
        return string[len(prefix) :]
    else:
        return string


def log(message):
    print(message, file=sys.stderr)


def die(message):
    log("gnu_elpa_mirror: " + message)
    sys.exit(1)


try:
    ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
except KeyError:
    die("please export ACCESS_TOKEN to a valid GitHub API token")


def clone_git_repo(
    git_url, repo_dir, shallow, all_branches, private_url, mirror=False, branch=None
):
    if not repo_dir.is_dir():
        cmd = ["git", "clone"]
        if shallow:
            cmd.extend(["--depth", "1"])
            if all_branches:
                cmd.append("--no-single-branch")
        if mirror:
            # Use --bare instead of --mirror, see
            # <https://stackoverflow.com/a/54413257/3538165>.
            cmd.append("--bare")
        if branch:
            cmd.extend(["--branch", branch])
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
            cwd=repo_dir,
            check=True,
            stdout=subprocess.PIPE,
        )
        branch = remove_prefix("refs/heads/", result.stdout.decode().strip())
        ref = "refs/remotes/origin/{}".format(branch)
        subprocess.run(["git", "fetch"], cwd=repo_dir, check=True)
        result = subprocess.run(
            ["git", "show-ref", ref], cwd=repo_dir, stdout=subprocess.DEVNULL
        )
        # Check if there is a master branch to merge from upstream.
        # Also, avoid creating merges or rebases due to a diverging
        # history.
        if result.returncode == 0:
            subprocess.run(["git", "reset", "--hard", ref], cwd=repo_dir, check=True)


def delete_contents(path):
    for entry in sorted(path.iterdir()):
        if entry.name == ".git":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            try:
                entry.unlink()
            except FileNotFoundError:
                pass


def stage_and_commit(repo_dir, message):
    # Note the use of --force because some packages like AUCTeX need
    # files to be checked into version control that are nevertheless
    # in their .gitignore. See [1].
    #
    # [1]: https://github.com/raxod502/straight.el/issues/299
    subprocess.run(["git", "add", "--all", "--force"], cwd=repo_dir, check=True)
    anything_staged = (
        subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir).returncode
        != 0
    )
    if anything_staged:
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=GNU ELPA Mirror Bot",
                "-c",
                "user.email=emacs-devel@gnu.org",
                "commit",
                "-m",
                message,
            ],
            cwd=repo_dir,
            check=True,
        )
    else:
        log("(no changes)")


# https://savannah.gnu.org/git/?group=emacs
GNU_ELPA_GIT_URL = "https://git.savannah.gnu.org/git/emacs/elpa.git"
EMACS_GIT_URL = "https://git.savannah.gnu.org/git/emacs.git"

GNU_ELPA_SUBDIR = pathlib.Path("gnu-elpa")
GNU_ELPA_PACKAGES_SUBDIR = GNU_ELPA_SUBDIR / "packages"
EMACS_SUBDIR = GNU_ELPA_SUBDIR / "emacs"
REPOS_SUBDIR = pathlib.Path("repos")


def make_commit_message(message, data):
    return (
        "{}\n\n"
        "Timestamp: {}\n"
        "GNU ELPA commit: {}\n"
        "Emacs commit: {}".format(
            message, data["timestamp"], data["gnu_elpa_commit"], data["emacs_commit"]
        )
    )


def mirror_gnu_elpa(args, api, existing_repos):
    log("--> clone/update GNU ELPA")
    clone_git_repo(
        GNU_ELPA_GIT_URL,
        GNU_ELPA_SUBDIR,
        shallow=False,
        all_branches=True,
        private_url=False,
        branch="main",
    )
    log("--> clone/update Emacs")
    clone_git_repo(
        EMACS_GIT_URL,
        EMACS_SUBDIR,
        shallow=False,
        all_branches=False,
        private_url=False,
    )
    log("--> check timestamp and commit hashes")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    gnu_elpa_commit = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=GNU_ELPA_SUBDIR,
            stdout=subprocess.PIPE,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    emacs_commit = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=EMACS_SUBDIR,
            stdout=subprocess.PIPE,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    commit_data = {
        "timestamp": timestamp,
        "gnu_elpa_commit": gnu_elpa_commit,
        "emacs_commit": emacs_commit,
    }
    log("--> retrieve/update GNU ELPA external packages")
    subprocess.run(["make", "setup", "-f", "Makefile"], cwd=GNU_ELPA_SUBDIR, check=True)
    subprocess.run(["make", "worktrees"], cwd=GNU_ELPA_SUBDIR, check=True)
    packages = []
    for subdir in sorted(GNU_ELPA_PACKAGES_SUBDIR.iterdir()):
        if not subdir.is_dir():
            continue
        # Prevent monkey business.
        if subdir.name in (
            "gnu-elpa-mirror",
            "epkgs",
            "emacsmirror-mirror",
            "org-mode",
        ):
            continue
        packages.append(subdir.name)
    log("--> clone/update mirror repositories")
    org = api.get_organization("emacs-straight")
    REPOS_SUBDIR.mkdir(exist_ok=True)
    for package in packages:
        github_package = package.replace("+", "-plus")
        git_url = "https://raxod502:{}@github.com/emacs-straight/{}.git".format(
            ACCESS_TOKEN, github_package
        )
        repo_dir = REPOS_SUBDIR / package
        if github_package not in existing_repos:
            log("----> create mirror repository {}".format(package))
            org.create_repo(
                github_package,
                description=("Mirror of the {} package from GNU ELPA".format(package)),
                homepage=("https://elpa.gnu.org/packages/{}.html".format(package)),
                has_issues=False,
                has_wiki=False,
                has_projects=False,
                auto_init=False,
            )
        if args.skip_mirror_pulls and repo_dir.is_dir():
            continue
        log("----> clone/update mirror repository {}".format(package))
        clone_git_repo(
            git_url, repo_dir, shallow=True, all_branches=False, private_url=True
        )
    log("--> update mirrored packages")
    for package in packages:
        log("----> update package {}".format(package))
        package_dir = GNU_ELPA_PACKAGES_SUBDIR / package
        repo_dir = REPOS_SUBDIR / package
        delete_contents(repo_dir)
        for source in sorted(package_dir.iterdir()):
            if source.name == ".git":
                continue
            target = repo_dir / source.name
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, target)
            else:
                shutil.copyfile(source, target, follow_symlinks=False)
        stage_and_commit(
            repo_dir, make_commit_message("Update " + package, commit_data)
        )
    if not args.skip_mirror_pushes:
        log("--> push changes to mirrored packages")
        for package in packages:
            log("----> push changes to package {}".format(package))
            repo_dir = REPOS_SUBDIR / package
            subprocess.run(
                ["git", "push", "origin", "master"], cwd=repo_dir, check=True
            )
    git_url = "https://raxod502:{}@github.com/emacs-straight/{}.git".format(
        ACCESS_TOKEN, "gnu-elpa-mirror"
    )
    repo_dir = REPOS_SUBDIR / "gnu-elpa-mirror"
    if "gnu-elpa-mirror" not in existing_repos:
        log("--> create mirror list repository")
        org.create_repo(
            "gnu-elpa-mirror",
            description="List packages mirrored from GNU ELPA",
            homepage="https://elpa.gnu.org/packages/",
            has_issues=False,
            has_wiki=False,
            has_projects=False,
            auto_init=False,
        )
    log("--> clone/update mirror list repository")
    clone_git_repo(
        git_url, repo_dir, shallow=True, all_branches=False, private_url=True
    )
    log("--> update mirror list repository")
    delete_contents(repo_dir)
    for package in packages:
        with open(repo_dir / package, "w"):
            pass
    stage_and_commit(repo_dir, make_commit_message("Update mirror list", commit_data))
    log("--> push changes to mirror list repository")
    subprocess.run(["git", "push", "origin", "master"], cwd=repo_dir, check=True)


def mirror_emacsmirror(args, api, existing_repos):
    org = api.get_organization("emacs-straight")
    epkgs_dir = REPOS_SUBDIR / "epkgs"
    epkgs_git_url = "https://github.com/emacsmirror/epkgs.git"
    epkgs_mirror_dir = REPOS_SUBDIR / "emacsmirror-mirror"
    epkgs_mirror_git_url = (
        "https://raxod502:{}@github.com/emacs-straight/emacsmirror-mirror.git".format(
            ACCESS_TOKEN
        )
    )
    log("--> clone/update Emacsmirror")
    clone_git_repo(
        epkgs_git_url, epkgs_dir, shallow=True, all_branches=False, private_url=False
    )
    if "emacsmirror-mirror" not in existing_repos:
        log("--> create Emacsmirror mirror repository")
        org.create_repo(
            "emacsmirror-mirror",
            description="Lightweight mirror of the Emacsmirror index",
            homepage="https://github.com/emacsmirror/epkgs",
            has_issues=False,
            has_wiki=False,
            has_projects=False,
            auto_init=False,
        )
    log("--> clone/update Emacsmirror mirror repository")
    clone_git_repo(
        epkgs_mirror_git_url,
        epkgs_mirror_dir,
        shallow=True,
        all_branches=False,
        private_url=True,
    )
    log("--> update Emacsmirror mirror")
    delete_contents(epkgs_mirror_dir)
    attic_file = epkgs_mirror_dir / "attic"
    mirror_file = epkgs_mirror_dir / "mirror"
    num_attic = 0
    num_mirror = 0
    with open(attic_file, "w") as attic, open(mirror_file, "w") as mirror:
        with open(epkgs_dir / ".gitmodules") as gitmodules:
            for line in gitmodules:
                m = re.fullmatch(
                    r'\[submodule "[^"]+"\]\n|'
                    r"\tpath = .+\n|"
                    r"\turl = git@github.com:emacsmirror/emacswiki.org.git\n|"
                    r"\turl = https://git.savannah.gnu.org/git/emacs/elpa.git\n|"
                    r"\turl = https://code.orgmode.org/bzg/org-mode.git\n|"
                    r"\turl = git@github.com:melpa/melpa.git\n|"
                    r"\turl = git@github.com:([^/]+)/(.+)\.git\n|"
                    r"\tbranch = .+\n",
                    line,
                )
                assert m, line
                org = m.group(1)
                name = m.group(2)
                if org == "bsvingen":
                    # Jonas made a typo and included a spurious
                    # submodule called sql-ident in addition to the
                    # real sql-indent one. The spurious submodule has
                    # a wrong repo URL.
                    continue
                if org == "emacsattic":
                    attic.write(name + "\n")
                    num_attic += 1
                elif org == "emacsmirror":
                    mirror.write(name + "\n")
                    num_mirror += 1
                elif org is None:
                    continue
                else:
                    assert False, line
    assert num_attic >= 500 and num_mirror >= 1000
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    epkgs_commit = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=epkgs_dir,
            stdout=subprocess.PIPE,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    stage_and_commit(
        epkgs_mirror_dir,
        "Update Emacsmirror mirror\n\nTimestamp: {}\nEmacsmirror commit: {}".format(
            timestamp, epkgs_commit
        ),
    )
    log("--> push changes to Emacsmirror mirror repository")
    subprocess.run(
        ["git", "push", "origin", "master"], cwd=epkgs_mirror_dir, check=True
    )


def mirror_orgmode(args, api, existing_repos):
    org = api.get_organization("emacs-straight")
    orgmode_dir = REPOS_SUBDIR / "org-mode"
    orgmode_git_url = "https://code.orgmode.org/bzg/org-mode.git"
    orgmode_mirror_git_url = (
        "https://raxod502:{}@github.com/emacs-straight/org-mode.git".format(
            ACCESS_TOKEN
        )
    )
    log("--> clone/update Org")
    clone_git_repo(
        orgmode_git_url,
        orgmode_dir,
        shallow=False,
        all_branches=True,
        private_url=False,
        mirror=True,
    )
    if "org-mode" not in existing_repos:
        log("--> create org-mode repository")
        org.create_repo(
            "org-mode",
            description="Mirror of org-mode from orgmode.org",
            homepage="https://code.orgmode.org/bzg/org-mode",
            has_issues=False,
            has_wiki=False,
            has_projects=False,
            auto_init=False,
        )
    log("--> push org-mode repository")
    result = subprocess.run(
        ["git", "push", "--mirror", orgmode_mirror_git_url], cwd=orgmode_dir
    )
    if result.returncode != 0:
        die("pushing repository failed (details omitted for security)")


def mirror():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-gnu-elpa", action="store_true")
    parser.add_argument("--skip-emacsmirror", action="store_true")
    parser.add_argument("--skip-mirror-pulls", action="store_true")
    parser.add_argument("--skip-mirror-pushes", action="store_true")
    parser.add_argument("--skip-orgmode", action="store_true")
    args = parser.parse_args()
    api = github.Github(ACCESS_TOKEN)
    log("--> get list of mirror repositories")
    existing_repos = []
    for repo in api.get_user("emacs-straight").get_repos():
        existing_repos.append(repo.name)
    if not args.skip_gnu_elpa:
        mirror_gnu_elpa(args, api, existing_repos)
    if not args.skip_emacsmirror:
        mirror_emacsmirror(args, api, existing_repos)
    if not args.skip_orgmode:
        mirror_orgmode(args, api, existing_repos)
    if os.environ.get("GEM_SNITCH"):
        log("--> update Dead Man's Snitch")
        resp = requests.get("https://nosnch.in/6c16713f1f")
        log(resp)


if __name__ == "__main__":
    mirror()
