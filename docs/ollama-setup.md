# Ollama setup

Daily Driver can route headless AI tasks (enrichment, summary) to a local
[Ollama](https://ollama.com) server instead of the `claude` CLI. Local
models are free, have no rate limits, and run entirely on your machine —
a good fit for high-volume tasks like `jobs --backfill`.

Interactive launchers (`day-start`, `check-in`, `day-end`) always use
`claude`. They depend on session resume, agents, and workspace context
that Ollama does not provide.

## Step 1: Install Ollama

macOS:

```
brew install ollama
```

Or download the installer from <https://ollama.com/download>.

## Step 2: Start the server

```
ollama serve
```

Leave this running in a terminal (or set up a launch agent / systemd unit).
By default it listens on `http://localhost:11434`.

## Step 3: Pull a model

For enrichment workloads on a 64 GB M-series Mac, a 14B-class instruction
model is a reasonable balance of quality and throughput:

```
ollama pull qwen2.5:14b
```

Smaller / faster alternatives: `phi4`, `llama3.2:3b`. Larger / slower:
`qwen2.5:32b`, `llama3.1:70b` (only if you have the RAM for it).

## Step 4: Verify the model is available

```
ollama list
```

You should see the model you pulled in the output.

## Step 5: Smoke-test the API

```
curl -s http://localhost:11434/api/tags | head
```

A JSON body listing pulled models confirms the server is reachable.

## Step 6: Tune resource use (optional)

Set `OLLAMA_NUM_PARALLEL` and `OLLAMA_MAX_LOADED_MODELS` to control
concurrency and memory pressure. See
<https://github.com/ollama/ollama/blob/main/docs/faq.md>.

## Step 7: Wire up daily-driver

Add an `ai:` block to your workspace's `.dd-config.yaml`:

```yaml
ai:
  enrichment:
    provider: ollama
    model: qwen2.5:14b
  # summary stays on claude by default — leave omitted, or set explicitly:
  # summary:
  #   provider: claude
  ollama:
    endpoint: http://localhost:11434
    timeout: 60
```

Verify the wiring:

```
daily-driver doctor
```

With ollama running, you should see a new `AI providers` row reporting
`OK`. If the server is down, the row reports `WARNING` with a hint to run
`ollama serve`. If the model is not pulled, the hint shows
`ollama pull <model>`.

Quick smoke test against a real `jobs.csv`:

```
daily-driver jobs run --backfill
```

The enrichment loop should populate the same fields as the claude path
(product, fit, notes). On a 64 GB M-series Mac at 14B parameters, expect
roughly 10-15 tokens/sec — a 50-job backfill takes a few minutes total.

If output quality is lower than the claude path, try a larger model or
keep `summary` on claude and route only `enrichment` to ollama.
