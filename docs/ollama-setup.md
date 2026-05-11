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

## Test the model

Verify the pulled model actually generates before wiring it into
daily-driver. Three quick checks, fastest to most representative:

**1. CLI smoke test** — confirms the model loads and produces output:

```
ollama run qwen2.5:14b "In one sentence, what does Stripe build?"
```

Expect a single-sentence answer. First invocation is slow (model loads
into RAM); subsequent runs are fast.

**2. HTTP API test** — exactly the call shape daily-driver uses for
free-text enrichment (`enrich_company_descriptions`):

```
curl -s http://localhost:11434/api/generate \
  -d '{
    "model": "qwen2.5:14b",
    "prompt": "Answer in exactly 2 lines, no preamble:\nLine 1: What does Stripe build? (max 12 words)\nLine 2: Glassdoor rating (e.g. 4.1), or '\''unknown'\'' if unsure.",
    "stream": false
  }' | jq -r .response
```

You should see two short lines back. If you get an empty `response` or
an `error` field, the model is loaded but the prompt rejected — try a
different model.

**3. JSON-mode test** — call shape used for structured enrichment
(`enrich_fit_and_notes`). The `"format": "json"` flag forces the model
to emit valid JSON:

```
curl -s http://localhost:11434/api/generate \
  -d '{
    "model": "qwen2.5:14b",
    "prompt": "Return only valid JSON: {\"fit\": <int 1-10>, \"notes\": \"<one sentence max 15 words>\"}\nJob: Staff SRE at Cloudflare, Remote Canada, $180k-$220k USD.",
    "stream": false,
    "format": "json"
  }' | jq -r .response | jq .
```

Expect a parseable object: `{"fit": 9, "notes": "..."}`. If `jq` fails
to parse the inner `.response`, your model is drifting on JSON output
— pick a stronger model from the [model table](#model-picks-64-gb-m-series)
(qwen2.5 and phi4 are the most reliable here).

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
