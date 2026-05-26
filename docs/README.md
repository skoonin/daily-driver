# Documentation

Daily Driver documentation is split by audience and purpose.

**Users** — start with [quick-start.md](quick-start.md) to scaffold a workspace, then read [usage.md](usage.md) for the daily flow. Read [concepts.md](concepts.md) once for the mental model. Look things up in [commands.md](commands.md), [configuration.md](configuration.md), and [troubleshooting.md](troubleshooting.md) as needed.

**Developers** — start with [dev/developer.md](dev/developer.md) for the architecture map, then [dev/extending.md](dev/extending.md) for recipes.

## User docs

Tables below list docs in recommended reading order — read-first entries first, then references.

| Doc | What it covers |
| --- | --- |
| [quick-start.md](quick-start.md) | Read first — minimal scaffold checklist for first-time setup |
| [usage.md](usage.md) | Read second — end-to-end daily flow with worked examples |
| [concepts.md](concepts.md) | Read once — mental model: workspace, surfaces, and design decisions |
| [commands.md](commands.md) | Reference — every subcommand and non-obvious flag behavior |
| [configuration.md](configuration.md) | Reference — `.dd-config.yaml` schema, defaults, and customization |
| [cli-tree.md](cli-tree.md) | Reference — at-a-glance command tree (orientation only) |
| [ollama-setup.md](ollama-setup.md) | How-to — local LLM provider for enrichment and summary |
| [troubleshooting.md](troubleshooting.md) | How-to — failure modes and recovery steps |
| [install.md](install.md) | How-to — install, upgrade, and Playwright setup |

## Developer docs

| Doc | What it covers |
| --- | --- |
| [dev/developer.md](dev/developer.md) | Architecture, module map, runtime flow, init contract |
| [dev/extending.md](dev/extending.md) | Adding subcommands and scraper sources |
| [dev/releasing.md](dev/releasing.md) | Release workflow, semver, CHANGELOG |
