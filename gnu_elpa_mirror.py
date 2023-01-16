#!/usr/bin/env python3

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import dotenv
import github
import requests

os.chdir(os.path.dirname(__file__))
dotenv.load_dotenv()


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


WEBHOOK_URL = os.environ.get("WEBHOOK_URL")


def clone_git_repo(
    git_url,
    repo_dir,
    *,
    private_url,
    bare=False,
    exclude_patterns=[],
    additional_refspecs=[],
):
    # Basically reimplement --mirror ourselves because it is the most
    # elegant way to solve https://stackoverflow.com/a/54413257/3538165.
    # We previously used --bare instead, but that doesn't do anything
    # like what we actually want, and I clearly wasn't thinking very
    # hard when I wrote the original code.
    if not repo_dir.is_dir():
        cmd = ["git", "init"]
        if bare:
            cmd += ["--bare"]
        cmd += [repo_dir]
        subprocess.run(cmd, check=True)
    try:
        subprocess.run(
            [
                "git",
                "fetch",
                "--prune",
                "--force",
                "--update-head-ok",
                git_url,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                "+refs/change/*:refs/change/*",
                *additional_refspecs,
            ],
            cwd=repo_dir,
            check=True,
        )
    except subprocess.CalledProcessError:
        if private_url:
            die("cloning repository failed (details omitted for security)")
        raise
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--symref", git_url, "HEAD"],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            check=True,
        )
        output = result.stdout.decode().splitlines()
        match = re.fullmatch(
            r"ref: (refs/heads/.+?)\s+HEAD",
            output[0],
        )
        if not match:
            die("failed to parse ls-remote output: " + "\n".join(output))
        remote_head = match.group(1)  # type: ignore
    except subprocess.CalledProcessError:
        if private_url:
            die("determining remote HEAD failed (details omitted for security)")
        raise
    if not bare:
        subprocess.run(
            ["git", "checkout", remote_head, "--force"], cwd=repo_dir, check=True
        )
        subprocess.run(
            [
                "git",
                "clean",
                "-ffdx",
                *("--exclude=" + pat for pat in exclude_patterns),
            ],
            cwd=repo_dir,
            check=True,
        )


def push_git_repo(git_url, repo_dir, repo_obj):
    try:
        subprocess.run(
            [
                "git",
                "push",
                "--prune",
                "--force",
                git_url,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                "+refs/change/*:refs/change/*",
            ],
            cwd=repo_dir,
            check=True,
        )
    except subprocess.CalledProcessError:
        die("cloning repository failed (details omitted for security)")
    branch = (
        subprocess.run(
            ["git", "symbolic-ref", "HEAD"], stdout=subprocess.PIPE, check=True
        )
        .stdout.decode()
        .removeprefix("refs/heads/")
    )
    repo_obj.edit(default_branch=branch)


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

REPOS_SUBDIR = pathlib.Path("repos")
GNU_ELPA_SUBDIR = REPOS_SUBDIR / "gnu-elpa"
GNU_ELPA_PACKAGES_SUBDIR = GNU_ELPA_SUBDIR / "packages"
EMACS_SUBDIR = GNU_ELPA_SUBDIR / "emacs"


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
        private_url=False,
        exclude_patterns=["/emacs"],
        additional_refspecs=["^refs/heads/elpa-admin"],
    )
    subprocess.run(
        ["git", "remote", "remove", "origin"], cwd=GNU_ELPA_SUBDIR, check=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", GNU_ELPA_GIT_URL],
        cwd=GNU_ELPA_SUBDIR,
        check=True,
    )
    log("--> clone/update Emacs")
    clone_git_repo(
        EMACS_GIT_URL,
        EMACS_SUBDIR,
        private_url=False,
    )
    log("--> check timestamp and commit hashes")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    brief_timestamp = datetime.datetime.now().strftime("%Y-%m-%d")
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
    elpa_config = json.loads(
        subprocess.run(
            [
                "emacs",
                "-Q",
                "--batch",
                "-l",
                "json",
                "--eval",
                """\
(with-temp-buffer
  (insert-file-contents "elpa-packages")
  (princ (json-encode (read (current-buffer)))))
""",
            ],
            stdout=subprocess.PIPE,
            cwd=GNU_ELPA_SUBDIR,
        ).stdout.decode()
    )
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
            "gnu-elpa",
        ):
            continue
        packages.append(subdir.name)
    log("--> clone/update mirror repositories")
    org = api.get_organization("emacs-straight")
    REPOS_SUBDIR.mkdir(exist_ok=True)
    for package in packages:
        if args.mirror_only_one and package != args.mirror_only_one:
            continue
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
        clone_git_repo(git_url, repo_dir, private_url=True)
    log("--> update mirrored packages")
    for package in packages:
        if args.mirror_only_one and package != args.mirror_only_one:
            continue
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
                is_relative_symlink = source.is_symlink() and str(
                    source.resolve()
                ).startswith(str(package_dir.resolve()))
                shutil.copyfile(source, target, follow_symlinks=not is_relative_symlink)
        # Check for custom lisp-dir and copy files to top level if needed
        # https://github.com/radian-software/gnu-elpa-mirror/issues/7
        #
        # Note the check for a list is because some of the entries in
        # the elpa-packages datastructure are malformed, because of
        # course they are, and json-serialize into unexpected types.
        if isinstance(elpa_config.get(package), dict) and (
            lisp_dir_name := elpa_config[package].get("lisp-dir")
        ):
            lisp_dir = repo_dir / lisp_dir_name
            for source in sorted(lisp_dir.iterdir()):
                target = repo_dir / source.name
                if target.name == lisp_dir.name:
                    continue
                if source.is_dir() and not source.is_symlink():
                    shutil.copytree(source, target)
                else:
                    is_relative_symlink = source.is_symlink() and str(
                        source.resolve()
                    ).startswith(str(repo_dir.resolve()))
                    shutil.copyfile(
                        source, target, follow_symlinks=not is_relative_symlink
                    )
            shutil.rmtree(lisp_dir)
        stage_and_commit(
            repo_dir, make_commit_message("Update " + package, commit_data)
        )
    if not args.skip_mirror_pushes:
        log("--> push changes to mirrored packages")
        for package in packages:
            if args.mirror_only_one and package != args.mirror_only_one:
                continue
            log("----> push changes to package {}".format(package))
            repo_dir = REPOS_SUBDIR / package
            github_package = package.replace("+", "-plus")
            git_url = "https://raxod502:{}@github.com/emacs-straight/{}.git".format(
                ACCESS_TOKEN, github_package
            )
            repo_obj = org.get_repo(github_package)
            push_git_repo(git_url, repo_dir, repo_obj)
            log("----> update repo description for package {}".format(package))
            github_package = package.replace("+", "-plus")
            repo_obj.edit(
                description="Mirror of the {} package from GNU ELPA, current as of {}".format(
                    package,
                    brief_timestamp,
                )
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
    clone_git_repo(git_url, repo_dir, private_url=True)
    log("--> update mirror list repository")
    delete_contents(repo_dir)
    for package in packages:
        with open(repo_dir / package, "w"):
            pass
    stage_and_commit(repo_dir, make_commit_message("Update mirror list", commit_data))
    log("--> push changes to mirror list repository")
    push_git_repo(git_url, repo_dir)


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
    clone_git_repo(epkgs_git_url, epkgs_dir, private_url=False)
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
                    "|".join(
                        regex + r"\n"
                        for regex in (
                            r'\[submodule "[^"]+"\]',
                            r"\tpath = .+",
                            r"\turl = https://git.savannah.gnu.org/git/emacs/elpa(?:\.git)?",
                            r"\turl = https://git.savannah.gnu.org/git/emacs/nongnu(?:\.git)?",
                            r"\turl = https://code.orgmode.org/bzg/org-mode(?:\.git)?",
                            r"\turl = git@github.com:(?P<org1>[^/]+)/(?P<repo1>.+?)(?:\.git)?",
                            r"\turl = https://github.com/(?P<org2>[^/]+)/(?P<repo2>.+?)(?:\.git)?",
                            r"\tbranch = .+",
                        )
                    ),
                    line,
                )
                assert m, line
                org = m.group("org1") or m.group("org2")
                name = m.group("repo1") or m.group("repo2")
                if name == "sql-ident":
                    # Jonas made a typo and included a spurious
                    # submodule called sql-ident in addition to the
                    # real sql-indent one. Filter it out.
                    continue
                if org == "melpa" and name == "melpa":
                    continue
                elif org == "emacsmirror" and name == "emacswiki.org":
                    continue
                elif org == "emacsattic":
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
    push_git_repo(epkgs_mirror_git_url, epkgs_mirror_dir)


def mirror_orgmode(args, api, existing_repos):
    brief_timestamp = datetime.datetime.now().strftime("%Y-%m-%d")
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
        private_url=False,
        bare=True,
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
    push_git_repo(orgmode_mirror_git_url, orgmode_dir)
    log("----> update repo description for Org")
    org.get_repo("org-mode").edit(
        description="Mirror of org-mode from orgmode.org, current as of {}".format(
            brief_timestamp,
        )
    )


def mirror():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-gnu-elpa", action="store_true")
    parser.add_argument("--skip-emacsmirror", action="store_true")
    parser.add_argument("--skip-mirror-pulls", action="store_true")
    parser.add_argument("--skip-mirror-pushes", action="store_true")
    parser.add_argument("--skip-orgmode", action="store_true")
    parser.add_argument("--mirror-only-one", type=str)
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
    if WEBHOOK_URL:
        log("--> update webhook")
        resp = requests.get(WEBHOOK_URL)
        log(resp)


if __name__ == "__main__":
    mirror()
