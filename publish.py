#!/usr/bin/env python3
"""Builds every plugin in plugins.txt, releases versions not released yet and rewrites repo.json."""

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_LIST = ROOT / "plugins.txt"
REPO_JSON = ROOT / "repo.json"
WORK = ROOT / "work"

# copied from the packager's manifest as is; everything else in an entry is set here
MANIFEST_KEYS = (
    "Author", "Name", "InternalName", "AssemblyVersion", "Punchline", "Description", "ApplicableVersion",
    "Tags", "DalamudApiLevel", "LoadRequiredState", "LoadSync", "CanUnloadAsync", "LoadPriority",
    "RepoUrl", "IconUrl", "CategoryTags",
)


def run(args, cwd=None, check=True, env=None):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True, env=env)


def read_plugins(owner):
    # (owner/Repository, project path inside it or "")
    plugins = []
    for line in PLUGIN_LIST.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            repo, _, project = line.partition(":")
            slug = repo if "/" in repo else f"{owner}/{repo}"
            plugins.append((slug, project.strip()))
    return plugins


def git_env(token):
    # the token goes in a header on every github.com request, so private submodules clone too and no url or
    # .git/config holds it; git hands GIT_CONFIG_* on to submodule clones
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader", GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    return env


def fetch_source(slug, env, clones):
    if slug in clones:
        return clones[slug]
    dest = WORK / "src" / slug
    shutil.rmtree(dest, ignore_errors=True)
    result = run(["git", "clone", "--quiet", "--depth", "1", "--recurse-submodules", f"https://github.com/{slug}.git", str(dest)], check=False, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"clone of {slug} failed: {result.stderr.strip()}")
    clones[slug] = dest
    return dest


def find_csproj(src, repo_name):
    # the repository name, else the one project at its root (repo chat-3.0 builds Chat3.csproj)
    named = src / f"{repo_name}.csproj"
    if named.exists():
        return named
    found = sorted(src.glob("*.csproj"))
    if len(found) != 1:
        raise RuntimeError(f"no {repo_name}.csproj and {len(found)} other .csproj files at the repository root")
    return found[0]


def csproj_value(csproj, pattern, what):
    match = re.search(pattern, csproj.read_text())
    if not match:
        raise RuntimeError(f"{csproj.name}: no {what}")
    return match.group(1)


def hooks_api_level(dalamud_home):
    deps = Path(dalamud_home) / "Dalamud.deps.json"
    match = re.search(r'"Dalamud/(\d+)\.', deps.read_text())
    if not match:
        raise RuntimeError(f"no Dalamud version in {deps}")
    return match.group(1)


def project_file(src, slug, project):
    if not project:
        return find_csproj(src, slug.split("/", 1)[1])
    if not project.endswith(".csproj"):
        raise RuntimeError(f"{project} is not a .csproj")
    csproj = src / project
    if not csproj.is_file():
        raise RuntimeError(f"{slug} has no {project}")
    return csproj


def build(csproj):
    name = csproj.stem
    result = run(["dotnet", "build", "-c", "Release", "--nologo", "-v", "q", str(csproj)], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"build failed:\n{result.stdout[-4000:]}{result.stderr[-2000:]}")
    out = csproj.parent / "bin" / "x64" / "Release" / name
    return json.loads((out / f"{name}.json").read_text()), out / "latest.zip"


def changelog_top(folder):
    path = folder / "changelog.md"
    if not path.exists():
        return ""
    sections = re.split(r"^# Version .*$", path.read_text(), flags=re.MULTILINE)
    return sections[1].strip() if len(sections) > 1 else ""


def release_created(tag):
    result = run(["gh", "release", "view", tag, "--json", "createdAt", "-q", ".createdAt"], check=False)
    if result.returncode != 0:
        return None
    return int(datetime.datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00")).timestamp())


def create_release(tag, title, notes, zip_path, name):
    asset = WORK / "assets" / f"{name}.zip"
    asset.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(zip_path, asset)
    run(["gh", "release", "create", tag, str(asset), "--title", title, "--notes", notes or title])


def entry(manifest, url, last_update, changelog):
    item = {key: manifest[key] for key in MANIFEST_KEYS if manifest.get(key) not in (None, "", [])}
    item.update({
        "DownloadLinkInstall": url,
        "DownloadLinkUpdate": url,
        "DownloadLinkTesting": url,
        "LastUpdate": last_update,
        "Changelog": changelog,
        "IsHide": False,
        "IsTestingExclusive": False,
        # Dalamud's feedback button reports to goatcorp, not to a custom repo's author
        "AcceptsFeedback": False,
    })
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/name of this repository (download links)")
    parser.add_argument("--local", type=Path, help="build from this folder of repository checkouts instead of cloning; implies --dry-run")
    parser.add_argument("--dry-run", action="store_true", help="build and write repo.json without creating releases")
    parser.add_argument("--out", type=Path, default=REPO_JSON)
    args = parser.parse_args()
    if not args.repo or "/" not in args.repo:
        parser.error("--repo owner/name is required outside GitHub Actions")
    # a local tree can hold files git never saw (e.g. licensed art), so it is never published
    dry = args.dry_run or args.local is not None
    owner = args.repo.split("/", 1)[0]
    env = git_env(os.environ.get("SOURCES_TOKEN", ""))
    dalamud_home = os.environ.get("DALAMUD_HOME") or str(Path.home() / ".xlcore" / "dalamud" / "Hooks" / "dev")
    hooks_api = hooks_api_level(dalamud_home)

    previous = {}
    if REPO_JSON.exists():
        previous = {item["InternalName"]: item for item in json.loads(REPO_JSON.read_text())}

    entries, failures, clones = [], [], {}
    for slug, project in read_plugins(owner):
        name = Path(project).stem if project else slug.split("/", 1)[1]
        try:
            src = args.local / slug.split("/", 1)[1] if args.local else fetch_source(slug, env, clones)
            csproj = project_file(src, slug, project)
            name = csproj.stem
            version = csproj_value(csproj, r"<Version>([^<]+)</Version>", "<Version>")
            sdk_api = csproj_value(csproj, r"Dalamud\.NET\.Sdk/(\d+)\.", "Dalamud.NET.Sdk version")
            if sdk_api != hooks_api:
                raise RuntimeError(f"targets Dalamud.NET.Sdk {sdk_api} but the fetched Dalamud is API {hooks_api}")
            manifest, zip_path = build(csproj)
            tag = f"{name}-{version}"
            url = f"https://github.com/{args.repo}/releases/download/{tag}/{name}.zip"
            notes = changelog_top(csproj.parent)
            old = previous.get(name)
            if dry:
                last_update = old["LastUpdate"] if old and old.get("AssemblyVersion") == version else int(time.time())
                state = "dry run"
            else:
                last_update = release_created(tag)
                state = "already released"
                if last_update is None:
                    create_release(tag, f"{manifest.get('Name', name)} {version}", notes, zip_path, name)
                    last_update = int(time.time())
                    state = "released"
            entries.append(entry(manifest, url, last_update, notes))
            print(f"{name} {version}: {state}")
        except Exception as ex:
            failures.append(name)
            print(f"{name}: {ex}", file=sys.stderr)
            if name in previous:
                entries.append(previous[name])

    entries.sort(key=lambda item: item.get("Name", item["InternalName"]).lower())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out} with {len(entries)} plugins")
    if failures:
        print(f"failed: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
