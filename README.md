# Plugin repository

Custom Dalamud plugin repository for aki's plugins. `repo.json` lists every plugin; the zips are attached to this repository's releases.

## Installing

In game, open `/xlsettings`, go to Experimental, and add the raw link to `repo.json` on this repository's `main` branch (`https://raw.githubusercontent.com/<owner>/<repo>/main/repo.json`) under Custom Plugin Repositories. Tick Enabled, save, then install the plugins from `/xlplugins`. Updates arrive like any other plugin's.

## Releasing

1. In the plugin's own repository, raise `<Version>` in the csproj, add a `changelog.md` entry and push.
2. Here, run the Publish workflow from the Actions tab.

The workflow builds every plugin in `plugins.txt` against the current Dalamud, creates a `<Name>-<Version>` release for each version not released yet, and rewrites `repo.json`. A version already released is left alone, so a plugin only updates for players when its version goes up.

Plugin repositories under the same owner can be private if the secret `PLUGIN_SOURCES_TOKEN` holds a fine-grained token with read access to their contents. This repository must stay public so Dalamud can download `repo.json` and the zips.

`python3 publish.py --local .. --repo <owner>/<repo>` builds from local checkouts and writes `repo.json` without releasing anything.
