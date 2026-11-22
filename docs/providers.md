# AI providers

A provider is a program or a model server that Daily Driver calls to run an Artificial Intelligence (AI) task. This page is the full reference for writing or changing a provider. For the short version, see [`ai` in Settings](configuration.md#ai). To run a model on your own computer, see [Local models](local-models.md).

## Provider settings

A provider is an entry under `ai.providers`. The entry name is the provider name. A provider that is a program has command templates. A provider that is a model server has a `base_url` and speaks the OpenAI-compatible chat Application Programming Interface (API). Daily Driver sends a model server a `POST` to `<base_url>/chat/completions`.

Run `summary` on a second `claude` entry that uses another model:

```yaml
ai:
  providers:
    claude-sonnet:
      parent_provider: claude
      model: sonnet
      tasks: [summary]
```

| Setting | Default | What it does |
| --- | --- | --- |
| `parent_provider` | none | Name of another provider. This entry starts from that provider's settings, then applies its own. One level only. |
| `tasks` | `[]` | Core tasks this provider runs: `interactive`, `summary`, `voice_update`. A provider that only a plugin uses needs none. |
| `run` | none | Programs only. Template that starts an interactive session. |
| `resume` | none | Programs only. Template that reopens a saved session. |
| `agent` | none | Programs only. Template for agent tasks. The prompt goes in on standard input. The program runs in the workspace folder, and its standard output is the answer. |
| `text` | none | Programs only. Template for text tasks. The prompt goes in on standard input. The program runs in an empty temporary folder, and its standard output is the answer. |
| `cleanup` | none | Programs only. Template that runs after each `agent` or `text` call that made a session ID. A failure is logged and does not fail the call. |
| `session_id` | none | Programs only. Format of the session identifiers (IDs) that Daily Driver makes, with one `{uuid}`, for example `"ses_{uuid}"`. Without it, Daily Driver makes no ID, and the `{session_id}` groups are dropped. |
| `base_url` | none | Servers only. Address of the OpenAI-compatible server. A provider has `base_url` or templates, not both. |
| `model` | none | Model name. A server that runs a task needs it. The `--model` option on a command wins. |
| `model_file` | none | Programs only. For a program that reads the model of an interactive session from its own JavaScript Object Notation (JSON) config file. `path` is the file, relative to the workspace. `key` is the setting in it. See [Sessions](#sessions). `null` turns it off. |
| `timeout` | none (180 s for text tasks, 900 s for agent tasks) | Seconds before an agent or text call gives up. A positive whole number. See [Time limits](#time-limits). |
| `max_parallel` | `4` | Text tasks only. Job reviews that run at the same time. `1` runs one at a time. It changes speed only. It does not change how many jobs are reviewed (`max_enrich_fit`) or the time limit of one call. |

The shipped settings of every built-in provider are in [`providers.default.yaml`](../source/daily_driver/resources/templates/providers.default.yaml).

## Change or add a provider

- An entry with the name of a built-in provider changes only the settings it lists. The other settings stay as shipped.
- An entry with another name is a new provider. Give it `parent_provider` to start from the settings of another provider. The parent can be a built-in provider or one of your own, as long as the parent has no `parent_provider` itself.
- `parent_provider` does not copy `tasks`. Each entry lists its own.
- An entry named after a built-in provider cannot set `parent_provider`.

The file that `init` creates has a live `claude` entry with `model`, `timeout`, and `tasks`, and also `max_parallel` when job search is on. It also has the shipped `claude` command templates as `# ` samples. To change a template, delete its `# ` and edit it. A template that you turn on no longer gets the changes of later versions. The file also has a sample of a provider of your own.

Run `summary` on a second `claude` entry, and `voice_update` on LM Studio:

```yaml
ai:
  providers:
    claude-opus:
      parent_provider: claude
      model: opus
      tasks: [summary]
    lmstudio:
      model: qwen3-coder
      tasks: [voice_update]
```

## Templates

A template is a list that Daily Driver turns into the command to run. Daily Driver starts the program directly and uses no shell. The first item is the program, as plain text with no braces. Each other item is one of these:

- A string. It becomes one argument. A placeholder in it, such as `{workspace}`, is filled in. A placeholder takes no format spec or conversion, such as `{model:>10}` or `{model!r}`. Write `{{` and `}}` for a literal brace.
- A group: a list of strings inside the list. The whole group is dropped when any placeholder in it has no value.

A placeholder outside a group that has no value is an error. Some placeholders can have no value, so Daily Driver requires a group for them when the config loads:

- `{model}`, when the provider sets no `model`.
- `{system}`, in `agent`, `text`, and `cleanup`.
- `{command}` and `{prompt_file}`, in `resume`.
- `{workspace}`, in `cleanup`, when the provider has a `text` template.

| Placeholder | Templates | Value |
| --- | --- | --- |
| `{workspace}` | `run`, `resume`, `agent`, `cleanup` | The workspace folder. Empty in `cleanup` after a text task. |
| `{command}` | `run`, `resume` | The slash command of the launcher, such as `/daily-driver:day-start`. Empty for `resume`. |
| `{prompt_file}` | `run`, `resume` | Path of the file that holds the instructions of the launcher. Empty for `resume`. Daily Driver writes the file only when the template uses this placeholder. |
| `{session_id}` | all | The session ID that was made or saved. The provider must have a `session_id` format. |
| `{session_name}` | `run`, `resume` | The `--session-name` option. |
| `{model}` | all | The `--model` option, else the `model` of the provider. |
| `{agent}` | `run`, `resume` | The `--agent` option. |
| `{system}` | `agent`, `text`, `cleanup` | The system text of a text task. When a `text` template has no `{system}`, Daily Driver puts the system text in front of the prompt. |

A template that uses a placeholder not listed for it is rejected when the config loads.

The shipped `claude` provider has this `agent` template. The group drops `--model` when no model is set:

```yaml
agent: ["claude", "-p", "--no-session-persistence", ["--model", "{model}"], "--add-dir", "{workspace}"]
```

## Time limits

A call to a provider gives up after the first of these that is set:

| Order | Limit | Default |
| --- | --- | --- |
| 1 | The `--timeout` option on `summary` or `voice-update`. For job reviews, `plugins.job_search.enrichment.enrich_timeout`. | `30` seconds for job reviews, otherwise none |
| 2 | The `timeout` of the provider. | none |
| 3 | The default of the mode. | 180 seconds for a text task, 900 seconds for an agent task |

An interactive session has no time limit.

## What Daily Driver checks

Daily Driver checks `ai` and the plugin routes when it reads the config. A command stops and prints the problem. Examples:

- A provider has both `base_url` and templates, or neither.
- A task name is not known.
- Two providers run the same core task.
- A task goes to a provider that lacks the setting its mode needs. For example, `summary` goes to `lmstudio`, which is a server and cannot run agent tasks.
- A server runs a task and has no `model`. The message names the fix, such as `set ai.providers.lmstudio.model`.
- A `model` is blank. To set no model, leave `model` out, or set it to `null` in an entry that uses `parent_provider`.
- A `model_file` is on a provider with no `run` template, its `path` is outside the workspace, or its `key` is blank.
- `parent_provider` names a provider that does not exist, or one that has a `parent_provider` itself.
- A plugin names a provider that is not defined, or a task the plugin does not have. A plugin that is off is not checked.

`daily-driver doctor` also shows one `ai:<provider>` row for each provider in use. See [`doctor`](commands.md#doctor---fix----reset--j----json).

## Sessions

Each interactive session gets an ID in the `session_id` format of the provider. Daily Driver saves the ID and the name of the provider. `resume` and a continuing `check-in` use them. If the `interactive` provider is not the one that started the saved session, `resume` stops and says so.

For Claude Code, the instructions for each launcher come from the `.claude/` files in the workspace. Daily Driver writes those files for every workspace. Other providers do not read `.claude/`. For a provider whose `run` template has `{prompt_file}`, Daily Driver writes the instructions to `.daily-driver/state/prompts/<command>.md` before each launch.

### Model of an opencode session

The opencode session screen has no model option. It reads the model from `opencode.json` in the workspace. Before each opencode session, Daily Driver sets `model` in that file to the model of the session: the `--model` option, else `ai.providers.opencode.model`. The shipped `model_file` setting of `opencode` does this. A provider of your own can use `model_file` for another program.

- Only the value of `model` changes. The other settings, the layout, the line endings, and the file permissions stay the same. If `opencode.json` is a link to a file in another folder, that file changes and the link stays. A link to a file that does not exist stops the session.
- If the file has no `model`, Daily Driver adds it as the last setting. If there is no file, Daily Driver makes one that holds only `model`.
- Daily Driver does not write the file when `model` already has that value, or when the session has no model.
- A model from `--model` stays in the file after the session. The next session with a model sets it again.
- If the file is not a valid JSON object, for example because it has comments, the session does not start and the message names the file.
