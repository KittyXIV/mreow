#!/usr/bin/env python3
"""Builds every plugin in plugins.txt, releases versions not released yet and rewrites repo.json."""

import argparse
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
)


def run(args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def read_plugins(owner):
    plugins = []
    for line in PLUGIN_LIST.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            slug = line if "/" in line else f"{owner}/{line}"
            plugins.append((slug, slug.split("/", 1)[1]))
    return plugins


def fetch_source(slug, name, token):
    dest = WORK / "src" / name
    shutil.rmtree(dest, ignore_errors=True)
    auth = f"x-access-token:{token}@" if token else ""
    # submodules (the Kitty framework) resolve against this url, so they clone with the same token
    result = run(["git", "clone", "--quiet", "--depth", "1", "--recurse-submodules", f"https://{auth}github.com/{slug}.git", str(dest)], check=False)
    if result.returncode != 0:
        # the clone url carries the token, so only git's own message is shown
        raise RuntimeError(f"clone of {slug} failed: {result.stderr.strip().replace(token, '***') if token else result.stderr.strip()}")
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


def build(src, csproj):
    name = csproj.stem
    result = run(["dotnet", "build", "-c", "Release", "--nologo", "-v", "q", str(csproj)], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"build failed:\n{result.stdout[-4000:]}{result.stderr[-2000:]}")
    out = src / "bin" / "x64" / "Release" / name
    return json.loads((out / f"{name}.json").read_text()), out / "latest.zip"


def changelog_top(src):
    path = src / "changelog.md"
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
    item = {key: manifest[key] for key in MANIFEST_KEYS if key in manifest}
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
    parser.add_argument("--local", type=Path, help="build from this folder of plugin checkouts instead of cloning; implies --dry-run")
    parser.add_argument("--dry-run", action="store_true", help="build and write repo.json without creating releases")
    parser.add_argument("--out", type=Path, default=REPO_JSON)
    args = parser.parse_args()
    if not args.repo or "/" not in args.repo:
        parser.error("--repo owner/name is required outside GitHub Actions")
    # a local tree can hold files git never saw (e.g. licensed art), so it is never published
    dry = args.dry_run or args.local is not None
    owner = args.repo.split("/", 1)[0]
    token = os.environ.get("SOURCES_TOKEN", "")
    dalamud_home = os.environ.get("DALAMUD_HOME") or str(Path.home() / ".xlcore" / "dalamud" / "Hooks" / "dev")
    hooks_api = hooks_api_level(dalamud_home)

    previous = {}
    if REPO_JSON.exists():
        previous = {item["InternalName"]: item for item in json.loads(REPO_JSON.read_text())}

    entries, failures = [], []
    for slug, repo_name in read_plugins(owner):
        name = repo_name
        try:
            src = args.local / repo_name if args.local else fetch_source(slug, repo_name, token)
            csproj = find_csproj(src, repo_name)
            name = csproj.stem
            version = csproj_value(csproj, r"<Version>([^<]+)</Version>", "<Version>")
            sdk_api = csproj_value(csproj, r"Dalamud\.NET\.Sdk/(\d+)\.", "Dalamud.NET.Sdk version")
            if sdk_api != hooks_api:
                raise RuntimeError(f"targets Dalamud.NET.Sdk {sdk_api} but the fetched Dalamud is API {hooks_api}")
            manifest, zip_path = build(src, csproj)
            tag = f"{name}-{version}"
            url = f"https://github.com/{args.repo}/releases/download/{tag}/{name}.zip"
            notes = changelog_top(src)
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
