# Plugin repository

Custom Dalamud plugin repository for aki's plugins. `repo.json` lists every plugin; the zips are attached to this repository's releases.

## Installing

In game, open `/xlsettings`, go to Experimental, and add the raw link to `repo.json` on this repository's `master` branch (`https://raw.githubusercontent.com/<owner>/<repo>/master/repo.json`) under Custom Plugin Repositories. Tick Enabled, save, then install the plugins from `/xlplugins`. Updates arrive like any other plugin's.

## Releasing

1. In the plugin's own repository, raise `<Version>` in the csproj, add a `changelog.md` entry and push.
2. Here, run the Publish workflow from the Actions tab.

The workflow builds every plugin in `plugins.txt` against the current Dalamud, creates a `<Name>-<Version>` release for each version not released yet, and rewrites `repo.json`. A version already released is left alone, so a plugin only updates for players when its version goes up.

A line of `plugins.txt` is a repository (`Name` under this owner, or `owner/Name`), optionally followed by `:path/to/Project.csproj` when the plugin is not the project at the repository's root. A repository named on several lines is cloned once. Submodules are cloned with it, so plugins on the Kitty framework (`lib/Kitty`) build; the framework commit a plugin points at must be on GitHub. A trailing `testing` lists a plugin only for players who turned on plugin testing builds (`/xlsettings`, Experimental).

Plugin repositories and their submodules can be private if the secret `PLUGIN_SOURCES_TOKEN` holds a fine-grained token with read access to their contents. This repository must stay public so Dalamud can download `repo.json` and the zips.

`python3 publish.py --local .. --repo <owner>/<repo>` builds from local checkouts and writes `repo.json` without releasing anything.
