# Releasing

Single-command release workflow. `make release` (interactive) or `make release-ci` (headless, no confirmation prompt) handles pre-flight, build, CHANGELOG rewrite, version bump, commit, and tag. Pushing is a separate deliberate step.

## Branches

- **`dev`** is the integration trunk. Feature branches branch off `dev` and merge back to `dev`. On `dev`, `__version__` carries the next release's `-dev` working marker (e.g. `0.3.0-dev`). Install the latest in-progress build with `pip install 'git+https://github.com/skoonin/daily-driver.git@dev'`.
- **`main`** holds only released, tagged states; it never carries a `-dev` version between releases.
- **Cutting a release** (`dev` → `main`):
  1. Create a `release/X.Y.Z` branch from a freshly fetched `dev`: `git fetch origin && git checkout -b release/X.Y.Z origin/dev`.
  2. On that branch, run `make release VERSION=X.Y.Z` (interactive) or `make release-ci VERSION=X.Y.Z` (headless — same steps, no confirmation prompt). Either strips the `-dev` suffix to `X.Y.Z`, rewrites the CHANGELOG `[Unreleased]` header to `[X.Y.Z]`, bumps `__version__`, commits `release: vX.Y.Z`, and tags `vX.Y.Z`.
  3. `make release-push` — pushes the release commit and tag. `release.yaml` fires on the tag and attaches build artifacts to the GitHub Release (see "GitHub Actions must be running" below).
  4. Land the release commit on `main` by **fast-forward**, so `main` points at the exact tagged commit. Do not merge the release pull request (PR) with any of GitHub's merge buttons. A merge commit adds a commit to `main` that `dev` does not have, so `main` no longer points at the tag and the next release cannot fast-forward. A rebase or squash merge rewrites the release commit to a new commit hash, so the `vX.Y.Z` tag is no longer in the history of `main`. `main` is always behind `dev`, so a fast-forward is always possible:

     ```bash
     git push origin vX.Y.Z^{commit}:refs/heads/main
     ```

     Open a `release/X.Y.Z` → `main` PR first for visibility if you like — GitHub auto-marks it merged once the commit lands on `main` via the fast-forward. The push goes straight to a protected branch, so it needs the admin bypass (see "Branch protection"). `main` is only ever updated by a release fast-forward, never a direct `dev` → `main` merge of trunk work.
  5. Reconcile `dev`: fast-forward `dev` to the tag (`git merge --ff-only vX.Y.Z`) so the CHANGELOG `[X.Y.Z]` section and the fresh empty `[Unreleased]` land on the trunk, then bump `dev`'s `__version__` to the next `X.Y.Z-dev` marker and commit. **Do not skip this** — without it, `dev`'s `[Unreleased]` still lists the just-released entries and the next cut would double-count them.

  (`make release` / `make release-ci` refuse any branch that is not `release/*`; `dev` carries the `-dev` marker, so cut from the `release/X.Y.Z` branch.)

## Branch protection

One GitHub ruleset, "Protect Main/Dev", covers `main`, `dev`, and `release/**`:

- **Deletion** of these branches is blocked.
- **Pull request required.** No approvals are needed, but review threads must be resolved. Allowed merge methods are merge commit and rebase.
- **Code scanning.** CodeQL must pass: the rule blocks on high-or-higher security alerts and on error-level alerts. CodeQL runs on pushes to `main` and `dev` and on pull requests that target them. It does not run on pushes to `release/**` branches.
- **Five status checks must pass:** `Lint & type check`, `Test (py3.11 on macos-latest)`, `Test (py3.12 on macos-latest)`, and the two `pip install from source` legs (py3.11, py3.12) from `install-smoke.yaml`. GitHub matches a status check by the job's `name:`, so renaming a CI job requires updating the ruleset in the same PR.
- **Bypass.** The Admin role can bypass the ruleset at any time. An admin merge skips the checks above, and the direct pushes to `main` (step 4 of "Cutting a release") and to `dev` (step 5) work only through this bypass. Use it knowingly.

## Version source of truth

```
source/daily_driver/__init__.py
```

```python
__version__ = "X.Y.Z"
```

`pyproject.toml` reads this dynamically (`[tool.setuptools.dynamic] version = { attr = "daily_driver.__version__" }`). Do not hardcode a version anywhere else. `make release` rewrites this file automatically.

| Context | Format | Example |
| --- | --- | --- |
| Source `__init__.py` | `X.Y.Z` | `0.2.0` |
| Git tag | `vX.Y.Z` | `v0.2.0` |
| Commit message | `release: vX.Y.Z` | `release: v0.2.0` |
| CHANGELOG header | `[X.Y.Z] — YYYY-MM-DD` | `[0.2.0] — 2026-05-01` |
| Pre-release | PEP 440 `X.Y.ZrcN` | `0.2.0rc1` |
| `dev` trunk marker | `X.Y.Z-dev` | `0.3.0-dev` |

## `make release`

```bash
make release VERSION=X.Y.Z
```

Runs six sequential steps; any failure stops the process:

1. Verify the branch is a `release/*` branch, the working tree is clean, the tag does not already exist, and the Python in use is a virtualenv (`make setup` creates one)
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

Pushes commit + tag to origin. `release.yaml` CI fires on `v*` tag push on macOS-latest. It asserts that `__version__` in the tagged commit equals the tag (a tag on a commit that was not bumped fails the run), runs `tox -e py311,py312`, builds wheel + sdist, smoke-installs the wheel, and attaches the artifacts to a GitHub Release with auto-generated notes. Releases are marked pre-release automatically when the tag contains `dev`, `rc`, `alpha`, or `beta`.

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

Maintain `[Unreleased]` as you work. `make release` rewrites the header to `[X.Y.Z] — YYYY-MM-DD` and inserts a fresh `[Unreleased]` section. Use `### Added`, `### Fixed`, `### Changed`, `### Removed` subsections.

On the `dev` trunk, `__version__` carries the next release's `-dev` working marker (e.g. `0.3.0-dev`); `make release` rewrites it to `X.Y.Z` when cutting from the `release/X.Y.Z` branch. Unreleased changelog entries live under `[Unreleased]` regardless of branch.

## Hotfix / rollback

Patch release from a release tag:

```bash
git checkout -b release/X.Y.Z vPREVIOUS.VERSION  # branch from the tagged release being patched
# apply fix, update [Unreleased] in CHANGELOG, commit
make release VERSION=X.Y.Z
make release-push
```

`make release` refuses any branch that is not `release/*`, so the fix branch must follow that naming even for a hotfix.

Remove a mistakenly pushed tag (before anyone installed from it):

```bash
git tag -d vX.Y.Z
git push origin --delete vX.Y.Z
```

Delete the GitHub Release manually if one was created.

## Distribution

Ships macOS arm64 only, installed from git (no PyPI publish):

```bash
pip install git+https://github.com/skoonin/daily-driver.git
pip install git+https://github.com/skoonin/daily-driver.git@vX.Y.Z
pip install 'git+https://github.com/skoonin/daily-driver.git@dev'  # latest in-progress build
```
