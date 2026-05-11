# Ollama setup

Route headless AI tasks (enrichment, summary) to a local
[Ollama](https://ollama.com) server instead of the `claude` CLI. Local
models are free, have no rate limits, and run entirely on your machine —
a good fit for high-volume tasks like `jobs --backfill`.

Interactive launchers (`day-start`, `check-in`, `day-end`) always use
`claude`. They depend on session resume, agents, and workspace context
Ollama does not provide.

## Steps

| # | Step | Command | Notes |
|---|------|---------|-------|
| 1 | Install Ollama | `brew install ollama` | Or download from <https://ollama.com/download> |
| 2 | Start the server | `brew services start ollama` | Listens on `http://localhost:11434`. Skip if you installed the macOS app — it auto-starts |
| 3 | Pull a model | `ollama pull qwen2.5:14b` | ~9 GB. See model table below |
| 4 | Verify | `ollama list` | The model should appear in the output |
| 5 | Edit `.dd-config.yaml` | See [config block](#config-block) | Sets `ai.enrichment.provider: ollama` |
| 6 | Confirm | `daily-driver doctor` | New `AI providers` row should report `OK` |
| 7 | Smoke test | `daily-driver jobs run --backfill` | Populates product / fit / notes via ollama |

## Config block

```yaml
# .dd-config.yaml
ai:
  enrichment:
    provider: ollama
    model: qwen2.5:14b
  # summary stays on claude by default; uncomment to route it through ollama too:
  # summary:
  #   provider: ollama
  #   model: qwen2.5:14b
  ollama:
    endpoint: http://localhost:11434
    timeout: 60
```

Omitting the entire `ai:` block keeps the legacy claude-only behavior. No
migration is required for existing workspaces.

## Model picks (64 GB M-series)

| Model | Size | Best for |
|-------|------|----------|
| `qwen2.5:14b` | ~9 GB | Default — balanced quality + throughput |
| `phi4` | ~9 GB | Strong reasoning at the same size |
| `llama3.2:3b` | ~2 GB | Faster iteration; lower quality |
| `qwen2.5:32b` | ~20 GB | Higher quality if RAM permits |

Expect ~10–15 tokens/sec on a 14B model. A 50-job `--backfill` finishes
in a few minutes.

## Doctor output reference

| State | Status | Hint |
|-------|--------|------|
| Reachable + model present | `OK` | — |
| Server not running | `WARNING` | `Start the server: ollama serve` |
| Model not pulled | `WARNING` | `Pull the model: ollama pull <model>` |

The `AI providers` row only appears when at least one task is routed to
ollama — no extra noise for the default claude path.

## Troubleshooting

- **`connection refused on 11434`** — `ollama serve` not running.
- **First request is slow** — Ollama loads the model into RAM on demand.
  Subsequent requests are fast. Tune `OLLAMA_KEEP_ALIVE` to keep the model
  warm between sessions.
- **Output quality below claude path** — try a larger model, or keep
  `summary` on claude and route only `enrichment` to ollama.
- **Tune resource use (optional)** — `OLLAMA_NUM_PARALLEL`,
  `OLLAMA_MAX_LOADED_MODELS`. See
  <https://github.com/ollama/ollama/blob/main/docs/faq.md>.

## See also

- [usage.md](usage.md#choosing-an-ai-provider) — when to pick claude vs ollama.
- [configuration.md](configuration.md#ai) — full `ai:` block reference.
