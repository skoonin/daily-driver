# Releasing

Single-command release workflow. `make release` handles pre-flight, build, CHANGELOG rewrite, version bump, commit, and tag. Pushing is a separate deliberate step.

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

## `make release`

```bash
make release VERSION=X.Y.Z
```

Runs six sequential steps; any failure stops the process:

1. Verify working tree is clean and tag does not already exist
2. `tox -e py311,py312` (full suite, both interpreters)
3. Install smoke test in a temporary venv (entry point + import)
4. Build sdist + wheel into `dist/`
5. Prompt for confirmation before permanent changes
6. Rewrite `CHANGELOG.md`, bump `__version__`, commit `release: vX.Y.Z`, create annotated tag

The tag message body is the changelog section for this release.

## Push

```bash
make release-push
```

Pushes commit + tag to origin. `release.yml` CI fires on `v*` tag push, builds wheel + sdist on macOS-latest, attaches them to a GitHub Release with auto-generated notes. Releases are marked pre-release automatically when the tag contains `dev`, `rc`, `alpha`, or `beta`.

## Semver

| Increment | Trigger |
| --- | --- |
| MAJOR | Breaking CLI surface, workspace schema, or plugin API |
| MINOR | New commands or backwards-compatible features |
| PATCH | Bug fixes, documentation, dependency updates |

## CHANGELOG discipline

Maintain `[Unreleased]` as you work. `make release` rewrites the header to `[X.Y.Z] - YYYY-MM-DD` and inserts a fresh `[Unreleased]` section. Use `### Added`, `### Fixed`, `### Changed`, `### Removed` subsections.

There is no `-dev` suffix convention on the default branch. Unreleased changes are signaled solely by the `[Unreleased]` section.

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
```

No PyPI publish in v0.1.0.
