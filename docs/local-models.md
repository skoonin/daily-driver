# Local models

LM Studio and ollama are apps that run AI models on your own computer. Each one runs a server that Daily Driver calls over the Hypertext Transfer Protocol (HTTP). Both are built-in providers. Local models have no rate limits, which suits high-volume work such as `jobs backfill`. Their answers can be weaker than those of a hosted model such as Claude. You do not need this page to use Daily Driver.

A model server runs text tasks only:

| Task | Command | Runs on a model server |
| --- | --- | --- |
| `voice_update` | `voice-update` | Yes |
| `job_search.fit_notes` | `jobs run`, `jobs backfill` | Yes |
| `summary` | `summary` | No. It needs a program that reads files, such as Claude Code or opencode. |
| `interactive` | `day-start`, `day-end`, `check-in`, `resume` | No. It needs a program, such as Claude Code or opencode. |

A task that you route to a model server and that it cannot run makes the command stop with a message when it reads `.dd-config.yaml`.

The job review settings turn job search on. To use a model server only for `voice-update`, leave out `plugins.job_search`.

## Steps

| # | Step | Command or action |
| --- | --- | --- |
| 1 | Install a model server and start it. | [LM Studio](#lm-studio) or [ollama](#ollama) |
| 2 | Load or download a model. | Steps for each app are below. |
| 3 | List the models that the server offers. Copy the exact name of yours. | `curl http://localhost:1234/v1/models` for LM Studio, or `curl http://localhost:11434/v1/models` for ollama |
| 4 | Add the settings from [Settings to add](#settings-to-add) to `.dd-config.yaml`. Use the model name from step 3. | |
| 5 | Check that Daily Driver can reach the server. | `daily-driver doctor`. The `ai:<provider>` row must show `OK`. |
| 6 | Try it. | `daily-driver jobs backfill` for job review, or `daily-driver voice-update --from <file>` for `voice-update`. |

## LM Studio

- The built-in provider `lmstudio` uses `http://localhost:1234/v1`.
- Start the server in the Developer tab of LM Studio, or run `lms server start`. See the [LM Studio server documentation](https://lmstudio.ai/docs/developer/core/server).
- LM Studio serves the OpenAI-compatible API. See the [LM Studio documentation](https://lmstudio.ai/docs/developer/openai-compat).

## ollama

- The built-in provider `ollama` uses `http://localhost:11434/v1`.
- Install the app from <https://ollama.com/download> and open it. If it asks to install the `ollama` command, allow it. The app runs the server and starts again at login.
- Download a model, for example `ollama pull qwen2.5:14b`. Run `ollama list` to see your models.
- ollama has no way to set the context size per request in the OpenAI-compatible API. To set it, build a model with a Modelfile that has a `PARAMETER num_ctx` line, run `ollama create`, and use the new model name. See the [ollama OpenAI compatibility documentation](https://docs.ollama.com/api/openai-compatibility).

## Settings to add

Merge these settings into your existing `ai:` and `plugins:` sections. If a file has two `ai:` or two `plugins:` sections, the second one replaces the first without an error.

A provider that runs a task needs a `model`. The built-in providers have none, because the names depend on what you installed.

For job review. This turns job search on:

```yaml
ai:
  providers:
    lmstudio:
      model: qwen3-coder
      # How many job reviews run at the same time. The default is 4.
      # max_parallel: 4
plugins:
  job_search:
    provider: lmstudio
    enrichment:
      # Seconds before one review gives up. The default is 30.
      enrich_timeout: 120
```

For `voice-update`:

```yaml
ai:
  providers:
    lmstudio:
      model: qwen3-coder
      tasks: [voice_update]
```

To use ollama, change `lmstudio` to `ollama` and use an ollama model name, such as `qwen2.5:14b`.

- The time limit of a job review is `enrich_timeout`. The `timeout` of the provider does not apply to job reviews.
- Leave out `plugins.job_search.provider` to keep job review on the default provider.
- To use a second model on the same server, add an entry with `parent_provider`. See [AI providers](providers.md#change-or-add-a-provider).
- For every provider setting, see [AI providers](providers.md).

## Choose a model

These ollama models were used with job review:

| Model | Download size | Best for |
| --- | --- | --- |
| `qwen2.5:14b` | about 9 gigabytes (GB) | A good balance of quality and speed. |
| `phi4` | about 9 GB | Strong reasoning at the same size. |
| `llama3.2:3b` | about 2 GB | Faster. Lower quality. |
| `qwen2.5:32b` | about 20 GB | Higher quality. Needs more memory. |

Each parallel request uses extra memory. On a Mac with 32 GB of memory, set `max_parallel: 2` on the provider, or choose a smaller model.

## What `doctor` shows

`doctor` shows one `ai:<provider>` row for each provider in use. The row lists the tasks that the provider runs.

| State | Status | Hint |
| --- | --- | --- |
| The server answers, and the model is in its list | `OK` | none |
| The server does not answer, answers with an error, or sends an unreadable model list | `WARNING` | `Start the server at <base_url>, or check that ai.providers.<provider>.base_url points at an OpenAI-compatible server.` |
| The model is not in the list | `WARNING` | `Load or download <model>, or change ai.providers.<provider>.model.` |

No row appears for a built-in provider that runs no task.

## Troubleshooting

- **`not reachable at` or `connection refused`.** The model server is not running. Start LM Studio's server, or open the ollama app.
- **`enrichment skipped this run`.** `jobs run` checks the server once, at the start of the job review. If the server is not running or does not list the model, `jobs run` skips the review of new jobs. It prints one warning that names the address or the model. The new jobs are still saved to `jobs.csv`. Start the server and load the model, or fix `ai.providers.<provider>.base_url`. Then run `jobs backfill` to fill the empty cells.
- **`has no model`.** A provider that runs a task needs a `model`. Set `ai.providers.<provider>.model`.
- **`returned no answer (finish_reason: length)`.** The model used all of its context window before it wrote an answer. Give the model a larger context window in LM Studio or ollama, or use a model with a larger one.
- **`returned no answer (finish_reason: stop; refusal: ...)`.** The model refused the prompt. Try another model.
- **`HTTP 404` or `sent an unreadable reply`.** The address answers, but not as an OpenAI-compatible server. Check `ai.providers.<provider>.base_url`. For LM Studio and ollama, it ends in `/v1`.
- **Cells stay empty after `jobs backfill`.** Run it again with `-vv`. See [Start with -v or -vv](troubleshooting.md#start-with--v-or--vv).
- **A review times out.** Raise `plugins.job_search.enrichment.enrich_timeout`. A model that loads on its first request is slow at first and fast after.
- **Answers are weaker than those of a hosted model.** Try a larger model. Or keep job review on the default provider and use the local model only for `voice-update`.
- **Ctrl-C during a long `jobs backfill`.** Press it once to stop. The run prints `Stopping` and waits for the model calls in progress, up to `enrich_timeout` seconds each. It then saves your progress and exits. A second Ctrl-C quits at once and loses the work in progress.
- **Many `jobs.csv.bak.*` files.** They are in `<workspace>/backups/`. `doctor` warns when more than 5 exist. See [Job search](job-search.md#enrichment).

## Advanced: ollama server settings

These settings belong to the ollama server, not to Daily Driver.

| Setting | What it does | Suggested value |
| --- | --- | --- |
| `OLLAMA_NUM_PARALLEL` | Number of requests the model answers at the same time. More requests use more memory. The default is 1. | `4`. Match `max_parallel` on the provider. |
| `OLLAMA_KEEP_ALIVE` | How long the model stays in memory after a request. The default is 5 minutes. | `30m` |
| `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE` | Together they cut the memory that each request holds while it runs by about half, with little loss in quality. | `1` and `q8_0` |

For the ollama app, run one `launchctl setenv` command for each setting, then restart the app:

```bash
launchctl setenv OLLAMA_NUM_PARALLEL 4
launchctl setenv OLLAMA_KEEP_ALIVE 30m
launchctl setenv OLLAMA_FLASH_ATTENTION 1
launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
```

If you run the server in a terminal, put the settings in front of `ollama serve`. The server stops when you close the terminal, so a scheduled `jobs run` cannot reach it.

```bash
OLLAMA_NUM_PARALLEL=4 OLLAMA_KEEP_ALIVE=30m OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```

If you leave `OLLAMA_NUM_PARALLEL` at its default of 1, set `max_parallel: 1` on the ollama provider. Then job reviews run one at a time and do not wait for each other. A Homebrew service (`brew services start ollama`) does not read settings from your terminal; set them with `launchctl setenv`, then restart the service. For other settings, see the ollama frequently asked questions (FAQ) at <https://github.com/ollama/ollama/blob/main/docs/faq.mdx>.

## See also

- [AI providers](providers.md): every provider setting.
- [Daily guide](usage.md#choosing-an-ai-provider): when to pick a hosted provider or a local model.
