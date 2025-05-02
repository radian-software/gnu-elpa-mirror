#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any

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


def with_retries(workload):
    for backoff_duration_secs in (10, 10, 60, 300, 0):
        try:
            return workload()
        except Exception:
            traceback.print_exc()
            log(f"Exponential backoff: retry after {backoff_duration_secs} seconds...")
            if not backoff_duration_secs:
                raise
            time.sleep(backoff_duration_secs)
    raise RuntimeError("can't get here")


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
    recursive=False,
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
        if not output:
            # Probably a new/empty repository
            return
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
    if bare:
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", remote_head], cwd=repo_dir, check=True
        )
    else:
        local_head = remote_head.removeprefix("refs/heads/")
        subprocess.run(
            ["git", "checkout", "-B", local_head, remote_head, "--force"],
            cwd=repo_dir,
            check=True,
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
        if recursive:
            subprocess.run(
                [
                    "git",
                    "submodule",
                    "update",
                    "--init",
                    "--recursive",
                    "--checkout",
                    "--force",
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
            ["git", "symbolic-ref", "HEAD"],
            stdout=subprocess.PIPE,
            check=True,
            cwd=repo_dir,
        )
        .stdout.decode()
        .strip()
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
                "user.email=contact+gnu-elpa-mirror@radian.codes",
                "commit",
                "-m",
                message,
            ],
            cwd=repo_dir,
            check=True,
        )
    else:
        log("(no changes)")


THIS_DIR = Path(".").resolve()
REPOS_SUBDIR = THIS_DIR / "repos"
GNU_ELPA_SUBDIR = THIS_DIR / "gnu-elpa"


@dataclass
class ELPAPackage:
    name: str
    version: str

    @property
    def tarball_name(self) -> str:
        return f"{self.name}-{self.version}.tar"

    @property
    def tarball_url(self) -> str:
        return f"https://elpa.gnu.org/devel/{self.tarball_name}"


def make_commit_message(
    message: str, timestamp: datetime, pkg: ELPAPackage | None = None
):
    message += f"\n\nTimestamp: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
    if pkg:
        message += f"\nSourced from {pkg.name} version {pkg.version} on GNU ELPA Devel"
        message += f"\n(see https://elpa.gnu.org/devel/{pkg.name}.html)"
    return message


def read_elpa_index(path: Path) -> Any:
    # WARNING: path basename can't have special chars
    path = path.resolve()
    return json.loads(
        subprocess.run(
            [
                "emacs",
                "-Q",
                "--batch",
                "-l",
                "package",
                "-l",
                "json",
                "--eval",
                f"""\
(with-temp-buffer
  (insert-file-contents "{path.name}")
  (princ
   (json-encode
    (mapcar
     (lambda (spec)
       (cons
        (car spec)
        (package-version-join (aref (cdr spec) 0))))
    (seq-filter #'listp (read (current-buffer)))))))
""",
            ],
            stdout=subprocess.PIPE,
            cwd=path.parent,
        ).stdout.decode()
    )


def get_elpa_contents(archive_url: str) -> list[ELPAPackage]:
    if not archive_url.endswith("/"):
        archive_url += "/"
    resp = requests.get(archive_url + "archive-contents")
    resp.raise_for_status()
    with tempfile.NamedTemporaryFile("w") as f:
        f.write(resp.text)
        f.flush()
        data = read_elpa_index(Path(f.name))
    return [
        ELPAPackage(name, version)
        for name, version in data.items()
        # Denylist some special names to make sure there is nothing
        # unrelated that gets accidentally overwritten by somebody
        # publishing a naughty package on GNU ELPA.
        if name
        not in {"gnu-elpa-mirror", "epkgs", "emacsmirror-mirror", "org-mode", "elpa"}
    ]


def mirror_gnu_elpa(args, api, existing_repos):
    package_filter = (
        lambda name: name == args.mirror_only_one or not args.mirror_only_one
    )
    log("--> check timestamp and commit hashes")
    timestamp = datetime.now()
    commit_data = {
        "timestamp": timestamp,
    }
    log("--> check GNU ELPA archive index")
    elpa_packages = get_elpa_contents("https://elpa.gnu.org/devel/")
    log("--> download GNU ELPA tarballs")
    GNU_ELPA_SUBDIR.mkdir(exist_ok=True)
    existing_tarballs = set(os.listdir(GNU_ELPA_SUBDIR))
    for pkg in elpa_packages:
        if not package_filter(pkg.name):
            continue
        if pkg.tarball_name in existing_tarballs:
            continue
        log(f"----> download {pkg.tarball_url}")
        resp = requests.get(pkg.tarball_url, stream=True)
        resp.raise_for_status()
        with open(GNU_ELPA_SUBDIR / pkg.tarball_name, "wb") as f:
            for chunk in resp.iter_content(10 * 1024):
                f.write(chunk)
    log("--> clone/update mirror repositories")
    org = api.get_organization("emacs-straight")
    REPOS_SUBDIR.mkdir(exist_ok=True)
    for pkg in elpa_packages:
        if not package_filter(pkg.name):
            continue
        github_package = pkg.name.replace("+", "-plus")
        git_url = "https://raxod502:{}@github.com/emacs-straight/{}.git".format(
            ACCESS_TOKEN, github_package
        )
        repo_dir = REPOS_SUBDIR / pkg.name
        if github_package not in existing_repos:
            log("----> create mirror repository {}".format(pkg.name))
            org.create_repo(
                github_package,
                description=("Mirror of the {} package from GNU ELPA".format(pkg.name)),
                homepage=("https://elpa.gnu.org/packages/{}.html".format(pkg.name)),
                has_issues=False,
                has_wiki=False,
                has_projects=False,
                auto_init=False,
            )
        if args.skip_mirror_pulls and repo_dir.is_dir():
            continue
        log("----> clone/update mirror repository {}".format(pkg.name))
        clone_git_repo(git_url, repo_dir, private_url=True)
    log("--> update mirrored packages")
    for pkg in elpa_packages:
        if not package_filter(pkg.name):
            continue
        log("----> update package {}".format(pkg.name))
        repo_dir = REPOS_SUBDIR / pkg.name
        delete_contents(repo_dir)
        subprocess.run(
            [
                "tar",
                "-C",
                str(repo_dir),
                "-xf",
                str(GNU_ELPA_SUBDIR / pkg.tarball_name),
                "--strip-components=1",
            ],
            check=True,
        )
        # Remove files that may make GitHub interpret this repo
        # specially, as it should just be a static fork with the
        # packaging files.
        try:
            shutil.rmtree(repo_dir / ".github")
        except FileNotFoundError:
            pass
        # Add a file to tell people not to file pull requests.
        pr_template = repo_dir / ".github" / "PULL_REQUEST_TEMPLATE.md"
        pr_template.parent.mkdir()
        with open(pr_template, "w") as f:
            f.write(
                ":warning: This repo is a read-only mirror. Please submit changes upstream instead :warning:\n"
            )
        stage_and_commit(
            repo_dir, make_commit_message("Update " + pkg.name, timestamp, pkg)
        )
    if not args.skip_mirror_pushes:
        log("--> push changes to mirrored packages")
        for pkg in elpa_packages:
            if not package_filter(pkg.name):
                continue
            log("----> push changes to package {}".format(pkg.name))
            repo_dir = REPOS_SUBDIR / pkg.name
            github_package = pkg.name.replace("+", "-plus")
            git_url = "https://raxod502:{}@github.com/emacs-straight/{}.git".format(
                ACCESS_TOKEN, github_package
            )
            repo_obj = org.get_repo(github_package)
            with_retries(lambda: push_git_repo(git_url, repo_dir, repo_obj))
            log("----> update repo description for package {}".format(pkg.name))
            with_retries(
                lambda: repo_obj.edit(
                    description="Mirror of the {} package from GNU ELPA, current as of {}".format(
                        pkg.name,
                        timestamp.strftime("%Y-%m-%d"),
                    )
                )
            )
    if not args.skip_mirror_index:
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
        for pkg in elpa_packages:
            with open(repo_dir / pkg.name, "w"):
                pass
        stage_and_commit(repo_dir, make_commit_message("Update mirror list", timestamp))
        log("--> push changes to mirror list repository")
        repo = org.get_repo("gnu-elpa-mirror")
        push_git_repo(git_url, repo_dir, repo_obj=repo)
        log("--> update repo description for mirror list repository")
        repo.edit(description="List packages mirrored from GNU ELPA")


def mirror_emacsmirror(_, api, existing_repos):
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
            description="Light-weight mirror of the Emacsmirror index",
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
                orgname = m.group("org1") or m.group("org2")
                name = m.group("repo1") or m.group("repo2")
                if name == "sql-ident":
                    # Jonas made a typo and included a spurious
                    # submodule called sql-ident in addition to the
                    # real sql-indent one. Filter it out.
                    continue
                if orgname == "melpa" and name == "melpa":
                    continue
                elif orgname == "emacsmirror" and name == "emacswiki.org":
                    continue
                elif orgname == "emacsattic":
                    attic.write(name + "\n")
                    num_attic += 1
                elif orgname == "emacsmirror":
                    mirror.write(name + "\n")
                    num_mirror += 1
                elif orgname is None:
                    continue
                else:
                    assert False, line
    assert num_attic >= 500 and num_mirror >= 1000
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    repo = org.get_repo("emacsmirror-mirror")
    push_git_repo(epkgs_mirror_git_url, epkgs_mirror_dir, repo_obj=repo)
    log("--> update repo description for Emacsmirror mirror repository")
    repo.edit(description="Light-weight mirror of the Emacsmirror index")


def mirror_orgmode(_, api, existing_repos):
    brief_timestamp = datetime.now().strftime("%Y-%m-%d")
    org = api.get_organization("emacs-straight")
    orgmode_dir = REPOS_SUBDIR / "org-mode"
    orgmode_git_url = "https://git.savannah.gnu.org/git/emacs/org-mode.git"
    orgmode_mirror_git_url = (
        "https://raxod502:{}@github.com/emacs-straight/org-mode.git".format(
            ACCESS_TOKEN
        )
    )
    log("--> clone/update Org")
    with_retries(
        lambda: clone_git_repo(
            orgmode_git_url,
            orgmode_dir,
            private_url=False,
            bare=True,
        )
    )
    if "org-mode" not in existing_repos:
        log("--> create org-mode repository")
        org.create_repo(
            "org-mode",
            description="Mirror of org-mode from Savannah",
            homepage="https://git.savannah.gnu.org/git/emacs/org-mode.git",
            has_issues=False,
            has_wiki=False,
            has_projects=False,
            auto_init=False,
        )
    repo = org.get_repo("org-mode")
    log("--> push org-mode repository")
    push_git_repo(orgmode_mirror_git_url, orgmode_dir, repo_obj=repo)
    log("--> update repo description for Org")
    repo.edit(
        description="Mirror of org-mode from Savannah, current as of {}".format(
            brief_timestamp,
        )
    )


def mirror():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-gnu-elpa", action="store_true")
    parser.add_argument("--skip-emacsmirror", action="store_true")
    parser.add_argument("--skip-mirror-index", action="store_true")
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
