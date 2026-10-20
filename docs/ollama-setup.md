# Local AI setup

Ollama is a free app that runs AI models on your own computer. Daily Driver can use it instead of Claude for three features: job review in job search, `summary`, and `voice-update`. Local models have no rate limits, which suits high-volume work such as `jobs backfill`. Their answers can be weaker than Claude's. You do not need this page to use Daily Driver.

Your daily chats (`day-start`, `check-in`, and `day-end`) always use Claude.

The job review settings turn job search on. To use Ollama only for `summary` and `voice-update`, use the second settings block and leave out `plugins.job_search`.

## Steps

| # | Step | Command or action |
| --- | --- | --- |
| 1 | Install the Ollama app. | Download it from <https://ollama.com/download> and open it. If it asks to install the `ollama` command, allow it. |
| 2 | Leave the app running. | The app runs the server and starts again at login. |
| 3 | Download a model. It is about 9 gigabytes (GB). | `ollama pull qwen2.5:14b` |
| 4 | Check that the model is there. | `ollama list` |
| 5 | Add the settings from [Settings to add](#settings-to-add) to `.dd-config.yaml`. | |
| 6 | Check that Daily Driver can reach Ollama. | `daily-driver doctor`. The row for your feature must show `OK`. |
| 7 | Try it. | `daily-driver jobs backfill` for job review, or `daily-driver summary -r today` for `summary`. |

## Settings to add

Merge these settings into your existing `ai:` and `plugins:` sections. If a file has two `ai:` or two `plugins:` sections, the second one replaces the first without an error.

For job review. This turns job search on:

```yaml
ai:
  ollama:
    endpoint: http://localhost:11434
    timeout: 60
    # How many job reviews run at the same time. The default is 4.
    # Raise it only after you raise OLLAMA_NUM_PARALLEL on the server.
    # max_parallel: 4
plugins:
  job_search:
    enrichment:
      provider: ollama
      model: qwen2.5:14b
```

For `summary` and `voice-update`:

```yaml
ai:
  summary:
    provider: ollama
    model: qwen2.5:14b
  voice_update:
    provider: ollama
    model: qwen2.5:14b
```

Job review settings go under `plugins.job_search.enrichment`. The shared Ollama settings, `endpoint`, `timeout`, and `max_parallel`, go under `ai.ollama`. Leave out `ai` to keep `summary` on Claude. For every `ai` setting, see [Settings](configuration.md#ai).

## Choose a model

| Model | Download size | Best for |
| --- | --- | --- |
| `qwen2.5:14b` | about 9 GB | The default. A good balance of quality and speed. |
| `phi4` | about 9 GB | Strong reasoning at the same size. |
| `llama3.2:3b` | about 2 GB | Faster. Lower quality. |
| `qwen2.5:32b` | about 20 GB | Higher quality. Needs more memory. |

On an Apple silicon Mac with 64 GB of memory, a 50-job `jobs backfill` finishes in a few minutes.

Each parallel request uses extra memory. With `max_parallel: 4` and the `qwen2.5:14b` model, peak use can reach about 50 GB. On a Mac with 32 GB of memory, set `ai.ollama.max_parallel: 2`, or choose `llama3.2:3b`. You can also use the memory-saving settings in [Tuning](#tuning). `daily-driver doctor` shows the value you set.

## Check the model

Run the model once to see that it answers:

```bash
ollama run qwen2.5:14b "Say hello in one short sentence."
```

Expect one short sentence. The first run is slow because the model loads into memory. Later runs are fast.

## What `doctor` shows

| State | Status | Hint |
| --- | --- | --- |
| Reachable, and the model is there | `OK` | none |
| Server not running | `WARNING` | `Start the server: ollama serve` |
| Model not downloaded | `WARNING` | `Pull the model: ollama pull <model>` |

Which row shows depends on the feature. `Enrichment provider` is for job review. `AI providers` is for `summary` and `voice-update`. When job review uses Ollama and `ai.ollama.max_parallel` is above 1, an `Ollama NUM_PARALLEL` row reminds you to set the same number on the server. Daily Driver cannot read that number from Ollama. No Ollama row appears while every feature uses Claude.

## Troubleshooting

- **`Ollama not reachable` or `connection refused`.** The Ollama server is not running. Open the Ollama app, or run `ollama serve`.
- **`enrichment skipped this run`.** `jobs run` checks Ollama once, at the start of the job review. If the server is not running or the model is not downloaded, `jobs run` skips the review of new jobs. It prints one warning that names the address or the model. The new jobs are still saved to `jobs.csv`. Start the server, or run `ollama pull <model>`. Then run `jobs backfill` to fill the empty cells.
- **Cells stay empty after `jobs backfill`.** Run it again with `-vv`. See [Start with -v or -vv](troubleshooting.md#start-with--v-or--vv).
- **The first request is slow.** Ollama loads the model into memory when it first needs it. Later requests are fast. Raise `OLLAMA_KEEP_ALIVE` to keep the model in memory longer. See [Tuning](#tuning).
- **Answers are weaker than Claude's.** Try a larger model. Or keep `summary` on Claude and use Ollama only for job review.
- **Ctrl-C during a long `jobs backfill`.** Press it once to stop. The run prints `Stopping` and waits for the model calls in progress, up to `ai.ollama.timeout` seconds each. It then saves your progress and exits. A second Ctrl-C quits at once and loses the work in progress.
- **Many `jobs.csv.bak.*` files.** See [Settings](configuration.md#daily_driver) for where they are and when `doctor` warns.

## Advanced

### Other ways to run the server

- Terminal. Run `ollama serve` and leave the terminal open. The server stops when you close the terminal, so a scheduled `jobs run` cannot reach it. Use this way to try settings.
- Homebrew service. Run `brew install ollama`, then `brew services start ollama`. The server stays on. It does not read settings from your terminal. Set them with `launchctl setenv`, then restart the service.

Every way listens on `http://localhost:11434`.

### Tuning

These settings belong to the Ollama server, not to Daily Driver.

| Setting | What it does | Suggested value |
| --- | --- | --- |
| `OLLAMA_NUM_PARALLEL` | Number of requests the model answers at the same time. More requests use more memory. The default is 1. | `4`. Match `ai.ollama.max_parallel`. |
| `OLLAMA_KEEP_ALIVE` | How long the model stays in memory after a request. The default is 5 minutes. | `30m` |
| `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE` | Together they cut the memory that each request holds while it runs by about half, with little loss in quality. | `1` and `q8_0` |

For the Ollama app, run one `launchctl setenv` command for each setting, then restart the app:

```bash
launchctl setenv OLLAMA_NUM_PARALLEL 4
launchctl setenv OLLAMA_KEEP_ALIVE 30m
launchctl setenv OLLAMA_FLASH_ATTENTION 1
launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
```

If you run the server in a terminal, put the settings in front of `ollama serve`:

```bash
OLLAMA_NUM_PARALLEL=4 OLLAMA_KEEP_ALIVE=30m OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```

If you leave `OLLAMA_NUM_PARALLEL` at its default of 1, set `ai.ollama.max_parallel: 1`. Then job reviews run one at a time and do not wait for each other. For other settings, see the Ollama frequently asked questions (FAQ) at <https://github.com/ollama/ollama/blob/main/docs/faq.mdx>.

## See also

- [usage.md](usage.md#choosing-an-ai-provider): when to pick Claude or Ollama.
- [Settings](configuration.md#ai): every `ai` setting.
