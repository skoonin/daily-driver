# Releasing

Single-command release workflow. `make release` (interactive) or `make release-ci` (headless, no confirmation prompt) handles pre-flight, build, CHANGELOG rewrite, version bump, commit, and tag. Pushing is a separate deliberate step.

## Branches

- **`dev`** is the integration trunk. Feature branches branch off `dev` and merge back to `dev`. On `dev`, `__version__` carries the next release's `-dev` working marker (e.g. `0.3.0-dev`). Install the latest in-progress build with `pip install 'git+https://github.com/skoonin/daily-driver.git@dev'`.
- **`main`** holds only released, tagged states; it never carries a `-dev` version between releases.
- **Cutting a release** (`dev` → `main`):
  1. Create a `release/X.Y.Z` branch from `dev`.
  2. On that branch, run `make release VERSION=X.Y.Z` (interactive) or `make release-ci VERSION=X.Y.Z` (headless — same steps, no confirmation prompt). Either strips the `-dev` suffix to `X.Y.Z`, rewrites the CHANGELOG `[Unreleased]` header to `[X.Y.Z]`, bumps `__version__`, commits `release: vX.Y.Z`, and tags `vX.Y.Z`.
  3. `make release-push` — pushes the release commit and tag. `release.yaml` fires on the tag and attaches build artifacts to the GitHub Release (see "GitHub Actions must be running" below).
  4. Land the release commit on `main` by **fast-forward**, so `main` points at the exact tagged commit. This repo has merge commits **disabled** (`allow_merge_commit: false`) and keeps `main` linear, so a PR "Merge"/"Squash"/"Rebase" will not work here — squash and rebase both rewrite the commit to a new SHA and float the `vX.Y.Z` tag off `main`'s history, and merge commits are rejected outright. `main` is always behind `dev`, so a fast-forward is always possible:

     ```bash
     git push origin vX.Y.Z^{commit}:refs/heads/main
     ```

     Open a `release/X.Y.Z` → `main` PR first for visibility if you like — GitHub auto-marks it merged once the commit lands on `main` via the fast-forward. `main` is only ever updated by a release fast-forward, never a direct `dev` → `main` merge of trunk work.
  5. Reconcile `dev`: fast-forward `dev` to the tag (`git merge --ff-only vX.Y.Z`) so the CHANGELOG `[X.Y.Z]` section and the fresh empty `[Unreleased]` land on the trunk, then bump `dev`'s `__version__` to the next `X.Y.Z-dev` marker and commit. **Do not skip this** — without it, `dev`'s `[Unreleased]` still lists the just-released entries and the next cut would double-count them.

  (`make release` / `make release-ci` refuse to run on `dev` itself — they carry the `-dev` marker, so cut from the `release/*` branch.)

## Version source of truth

```
source/daily_driver/__init__.py
```

```python
__version__ = "0.1.0"
```

`pyproject.toml` reads this dynamically (`[tool.setuptools.dynamic] version = { attr = "daily_driver.__version__" }`). Do not hardcode a version anywhere else. `make release` rewrites this file automatically.

| Context | Format | Example |
| --- | --- | --- |
| Source `__init__.py` | `X.Y.Z` | `0.2.0` |
| Git tag | `vX.Y.Z` | `v0.2.0` |
| Commit message | `release: vX.Y.Z` | `release: v0.2.0` |
| CHANGELOG header | `[X.Y.Z] - YYYY-MM-DD` | `[0.2.0] - 2026-05-01` |
| Pre-release | PEP 440 `X.Y.ZrcN` | `0.2.0rc1` |
| `dev` trunk marker | `X.Y.Z-dev` | `0.3.0-dev` |

## `make release`

```bash
make release VERSION=X.Y.Z
```

Runs six sequential steps; any failure stops the process:

1. Verify the branch is not `dev`, the working tree is clean, and the tag does not already exist
2. `tox -e py311,py312` (full suite, both interpreters)
3. Install smoke test in a temporary venv (entry point + import)
4. Build sdist + wheel into `dist/`
5. Prompt for confirmation before permanent changes
6. Rewrite `CHANGELOG.md`, bump `__version__`, commit `release: vX.Y.Z`, create annotated tag

The tag message body is the changelog section for this release.

### Headless — `make release-ci`

```bash
make release-ci VERSION=X.Y.Z
```

Identical to `make release` but skips step 5's confirmation prompt, for automated or agent-driven cuts. Every other guard (branch check, clean tree, tests, smoke, build) still runs, and it still stops on any failure. It does **not** push — `make release-push` remains a separate, deliberate step. Use `make release` for a hands-on cut where you want to eyeball the pre-flight output first.

## Push

```bash
make release-push
```

Pushes commit + tag to origin. `release.yaml` CI fires on `v*` tag push, builds wheel + sdist on macOS-latest, attaches them to a GitHub Release with auto-generated notes. Releases are marked pre-release automatically when the tag contains `dev`, `rc`, `alpha`, or `beta`.

### GitHub Actions must be running

The GitHub Release + artifacts depend on `release.yaml` actually triggering. This repo has had Actions disabled/paused at times — when Actions are off, the tag push creates no run, so no GitHub Release is built (the tag itself still installs fine via `pip install git+https://github.com/skoonin/daily-driver.git@vX.Y.Z`). After `make release-push`, confirm a run started (`gh run list --workflow=release.yaml --limit 1`) and that `gh release view vX.Y.Z` exists. If Actions were off, re-enable them and either re-push the tag or build and attach manually:

```bash
make build
gh release create vX.Y.Z dist/* --title vX.Y.Z --generate-notes
```

## Semver

| Increment | Trigger |
| --- | --- |
| MAJOR | Breaking CLI surface, workspace schema, or plugin API |
| MINOR | New commands or backwards-compatible features |
| PATCH | Bug fixes, documentation, dependency updates |

## CHANGELOG discipline

Maintain `[Unreleased]` as you work. `make release` rewrites the header to `[X.Y.Z] - YYYY-MM-DD` and inserts a fresh `[Unreleased]` section. Use `### Added`, `### Fixed`, `### Changed`, `### Removed` subsections.

On the `dev` trunk, `__version__` carries the next release's `-dev` working marker (e.g. `0.3.0-dev`); `make release` strips it to `X.Y.Z` when cutting from `main`. Unreleased changelog entries live under `[Unreleased]` regardless of branch.

## Hotfix / rollback

Patch release from a release tag:

```bash
git checkout -b hotfix/0.1.1 v0.1.0
# apply fix, update [Unreleased] in CHANGELOG, commit
make release VERSION=0.1.1
make release-push
```

Remove a mistakenly pushed tag (before anyone installed from it):

```bash
git tag -d v0.1.0
git push origin --delete v0.1.0
```

Delete the GitHub Release manually if one was created.

## Distribution

v0.1.0 ships macOS arm64 only, installed from git:

```bash
pip install git+https://github.com/skoonin/daily-driver.git
pip install git+https://github.com/skoonin/daily-driver.git@v0.1.0
pip install 'git+https://github.com/skoonin/daily-driver.git@dev'  # latest in-progress build
```

No PyPI publish in v0.1.0.
