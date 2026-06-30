# Ollama setup

Route headless AI tasks (job_search enrichment, summary) to a local [Ollama](https://ollama.com) server instead of the `claude` CLI. Local models are free, have no rate limits, and run entirely on your machine — a good fit for high-volume tasks like `jobs backfill`.

Interactive launchers (`day-start`, `check-in`, `day-end`) always use `claude`. They depend on session resume, agents, and workspace context Ollama does not provide.

## Install and run

The `ollama` binary comes from either Homebrew (`brew install ollama`) or the <https://ollama.com/download> installer — both give you `ollama serve`. Pick one way to run the server:

1. **Direct (recommended for tuning and experiments).** `OLLAMA_NUM_PARALLEL=4 OLLAMA_KEEP_ALIVE=30m ollama serve` — environment variables inline, immediate effect, no service manager. It dies when the terminal closes, so a scheduled `jobs run` won't find it; use one of the always-on options below for unattended runs.
2. **Homebrew service (always-on, launchd-managed).** `brew services start ollama`. Environment variables are not picked up from your shell — set them with `launchctl setenv` (or in the service plist) and restart the service.
3. **macOS app (always-on, auto-start, auto-update).** Installs a menu-bar app that starts at login. Set environment variables with `launchctl setenv` and restart the app for them to take effect.

All three listen on `http://localhost:11434`.

## Steps

| # | Step | Command | Notes |
|---|------|---------|-------|
| 1 | Install + run the server | see [Install and run](#install-and-run) | Listens on `http://localhost:11434` |
| 2 | Pull a model | `ollama pull qwen2.5:14b` | ~9 GB. See model table below |
| 3 | Verify | `ollama list` | The model should appear in the output |
| 4 | Edit `.dd-config.yaml` | See [config block](#config-block) | Sets `plugins.job_search.enrichment.provider: ollama` |
| 5 | Confirm | `daily-driver doctor` | The `Enrichment provider` row should report `OK` |
| 6 | Smoke test | `daily-driver jobs backfill` | Populates fit / notes via ollama |

## Test the model

Verify the pulled model actually generates before wiring it into daily-driver. Three quick checks, fastest to most representative:

**1. CLI smoke test** — confirms the model loads and produces output:

```
ollama run qwen2.5:14b "In one sentence, what does Stripe build?"
```

Expect a single-sentence answer. First invocation is slow (model loads into RAM); subsequent runs are fast.

**2. HTTP API test** — confirms the server answers a plain-text generate call over HTTP:

```
curl -s http://localhost:11434/api/generate \
  -d '{
    "model": "qwen2.5:14b",
    "prompt": "Answer in one line, no preamble: What does Stripe build? (max 12 words)",
    "stream": false
  }' | jq -r .response
```

You should see a short line back. An empty `response` or an `error` field means the model loaded but rejected the prompt — try a different model.

**3. JSON-mode test** — the call shape for structured enrichment (`enrich_fit_and_notes`). The `"format": "json"` flag forces valid JSON:

```
curl -s http://localhost:11434/api/generate \
  -d '{
    "model": "qwen2.5:14b",
    "prompt": "Return only valid JSON: {\"fit\": <int 1-10>, \"notes\": \"<one sentence max 15 words>\"}\nJob: Staff SRE at Cloudflare, Remote Canada, $180k-$220k USD.",
    "stream": false,
    "format": "json"
  }' | jq -r .response | jq .
```

Expect a parseable object: `{"fit": 9, "notes": "..."}`. If `jq` can't parse the inner `.response`, the model is drifting on JSON output — pick a stronger one from the [model table](#model-picks-64-gb-m-series) (qwen2.5 and phi4 are the most reliable).

## Config block

```yaml
# .dd-config.yaml
ai:
  # summary stays on claude by default; uncomment to route it through ollama too:
  # summary:
  #   provider: ollama
  #   model: qwen2.5:14b
  ollama:
    endpoint: http://localhost:11434
    timeout: 60
    # max_parallel: 4   # default; raise after raising server-side OLLAMA_NUM_PARALLEL
plugins:
  job_search:
    enrichment:
      provider: ollama
      model: qwen2.5:14b
```

Enrichment routing (`provider` / `model`) lives under `plugins.job_search.enrichment`; the shared `ai.ollama:` connection block (endpoint, timeout, max_parallel) stays in the core `ai:` block. Omitting the `ai:` block keeps summary on claude. If you have a pre-split config using `ai.enrichment`, move that routing to the plugin — `ai.enrichment` is now rejected (see [configuration.md](configuration.md#ai) for the before/after).

## Model picks (64 GB M-series)

| Model | Size | Best for |
|-------|------|----------|
| `qwen2.5:14b` | ~9 GB | Default — balanced quality + throughput |
| `phi4` | ~9 GB | Strong reasoning at the same size |
| `llama3.2:3b` | ~2 GB | Faster iteration; lower quality |
| `qwen2.5:32b` | ~20 GB | Higher quality if RAM permits |

Expect ~10-15 tokens/sec on a 14B model. A 50-job `jobs backfill` finishes in a few minutes.

> **RAM caveat for `max_parallel > 1`.** Each parallel call holds its own KV cache, which scales with model size and context length. Default `max_parallel: 4` with a 14 GB model can drive ~50 GB peak RAM under load. On a 32 GB Mac drop to `max_parallel: 2`, set `OLLAMA_KV_CACHE_TYPE=q8_0` (see [Tuning](#tuning)), or pick `llama3.2:3b`. `daily-driver doctor` shows the configured value; tune in `.dd-config.yaml`.

## Tuning

These are Ollama server-side environment variables (set them per [Install and run](#install-and-run)), not daily-driver config. The effective context-window default (`OLLAMA_CONTEXT_LENGTH`) depends on the machine — commonly 4k, scaling toward 32k/256k with available VRAM — and prompts past it are silently truncated. daily-driver guards against that by sending a per-request `num_ctx` sized to each prompt (estimated tokens plus output headroom, floored at 4096 and capped at 16384), so enrichment prompts are not cut off even when the server default is small.

- `OLLAMA_NUM_PARALLEL` — parallel requests the model serves at once (default 1). Required RAM scales by parallel × context, so this is the main memory lever. Example: `OLLAMA_NUM_PARALLEL=4`.
- `OLLAMA_CONTEXT_LENGTH` — server default context window (machine-dependent, often 4k). daily-driver overrides it per request via `num_ctx`, so you rarely need to raise this for enrichment. Example: `OLLAMA_CONTEXT_LENGTH=8192`.
- `OLLAMA_KEEP_ALIVE` — how long a model stays resident after a request (default `5m`). A per-request `keep_alive` overrides it. Raise it so the model survives between enrichment bursts and scheduled runs without reload stalls. Example: `OLLAMA_KEEP_ALIVE=30m`.
- `OLLAMA_MAX_QUEUE` — requests queued before the server returns 503 (default 512). Relevant only at very wide fan-out. Example: `OLLAMA_MAX_QUEUE=512`.
- `OLLAMA_FLASH_ATTENTION` — set to `1` to enable Flash Attention, which lowers memory growth as context grows. Example: `OLLAMA_FLASH_ATTENTION=1`.
- `OLLAMA_KV_CACHE_TYPE` — KV-cache quantization (default `f16`). `q8_0` roughly halves KV-cache memory with minimal quality loss (requires Flash Attention). Example: `OLLAMA_KV_CACHE_TYPE=q8_0`.

Other server knobs (`OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_HOST`, `OLLAMA_MODELS`, `OLLAMA_DEBUG`, ...) exist but are not relevant to single-user enrichment; see the [Ollama FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.mdx).

### Recommended settings

For this tool's use case — one user, a single enrichment model, bursty parallel calls during `jobs run` — start the server directly with:

```
OLLAMA_NUM_PARALLEL=4 OLLAMA_KEEP_ALIVE=30m OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

- `OLLAMA_NUM_PARALLEL=4` — match `ai.ollama.max_parallel`. If you leave it at the default 1, set `ai.ollama.max_parallel: 1` instead so enrichment runs serial rather than queueing against the timeout.
- `OLLAMA_KEEP_ALIVE=30m` — keep the model warm across enrichment bursts and check-in-scheduled runs; avoids a cold reload each time.
- `OLLAMA_FLASH_ATTENTION=1` — lower memory at larger contexts.
- `OLLAMA_KV_CACHE_TYPE=q8_0` — roughly halves KV-cache memory with minimal quality cost; the lever that makes `NUM_PARALLEL > 1` fit on one machine.
- `OLLAMA_MAX_LOADED_MODELS=1` — single-model use; avoid evicting the enrichment model.

If the server is down during a scheduled run, enrichment calls fail and are counted, the scrape itself still completes and appends its rows, and a later `jobs backfill` fills the gaps.

## Doctor output reference

| State | Status | Hint |
|-------|--------|------|
| Reachable + model present | `OK` | — |
| Server not running | `WARNING` | `Start the server: ollama serve` |
| Model not pulled | `WARNING` | `Pull the model: ollama pull <model>` |

When enrichment routes to ollama, the `Enrichment provider` row reports reachability and model presence. With `ai.ollama.max_parallel > 1`, an `Ollama NUM_PARALLEL` row reminds you to set the matching server-side `OLLAMA_NUM_PARALLEL` (the API can't report it). The core `AI providers` row covers the summary task on the same basis. None of these rows appear on the default claude path.

## Troubleshooting

- **Start with `-vv`.** Per-job enrichment traces (prompt sent, raw response, parsed fit/notes, whether each field was actually written) are at DEBUG level. Re-run with `-vv` when a backfill "succeeds" but cells stay empty:

  ```bash
  daily-driver jobs backfill -vv 2>&1 | tee /tmp/backfill.log
  ```

  Look for `[enrich-fit-notes] <company>: pre fit=... -> got fit=... (wrote_fit=False ...)` lines — these reveal cases where the model returned a value but the column already had one, or returned an empty string. `-v` alone gives startup and end-of-pass totals (`enriching up to N jobs`, `done: X enriched, Y failed`) without per-row spam.
- **`connection refused on 11434`** — `ollama serve` not running.
- **`LLM enrichment skipped this run`** — `jobs run` pings ollama once at the start of enrichment; if the server is unreachable or the model is not pulled, it skips the fit / notes pass (one warning naming the endpoint or model) instead of burning a per-call timeout on every job. Detail pages still run. Start the server (or `ollama pull <model>`), then `jobs backfill` to fill the empty rows.
- **First request is slow** — Ollama loads the model into RAM on demand; subsequent requests are fast. Tune `OLLAMA_KEEP_ALIVE` to keep it warm between sessions.
- **Output quality below claude path** — try a larger model, or keep `summary` on claude and route only enrichment to ollama.
- **Tune resource use (optional)** — see [Tuning](#tuning) for the server-side environment variables. The client-side counterpart is `ai.ollama.max_parallel` (default 4); raise it only after raising server-side `OLLAMA_NUM_PARALLEL`, or set it to `1` for serial enrichment.
- **Ctrl-C during a long backfill** — first press shows `Stopping — waiting for N companies still being enriched...`. The command finishes in-flight model calls (up to `ai.ollama.timeout` each), saves partial progress, and exits. A second Ctrl-C force-quits and loses what's in progress.
- **Old `jobs.csv.bak.*` files piling up** — each `jobs backfill` drops a timestamped backup before mutating `jobs.csv`, and `daily-driver doctor` warns once you have more than 5. Keep the 2-3 most recent and delete the rest with `rm <output_dir>/jobs.csv.bak.*`.

## See also

- [usage.md](usage.md#choosing-an-ai-provider) — when to pick claude vs ollama.
- [configuration.md](configuration.md#ai) — full `ai:` block reference.
