# Multi-Model vLLM Setup — MINTIFY

This document explains how two vLLM model instances run simultaneously on RunPod, how they are exposed to the Docker stack via SSH tunnels, and how the pipeline discovers and routes messages to the correct model.

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  RunPod GPU (46 068 MiB VRAM)                                       │
│                                                                     │
│  ┌─────────────────────────────┐  ┌─────────────────────────────┐  │
│  │  tmux: vllm-8002            │  │  tmux: vllm-8004            │  │
│  │  vllm serve                 │  │  vllm serve                 │  │
│  │  Qwen/Qwen3-1.7B            │  │  Qwen/Qwen2.5-3B-Instruct  │  │
│  │  --port 8002                │  │  --port 8004                │  │
│  │  --gpu-memory-utilization   │  │  --gpu-memory-utilization   │  │
│  │    0.40  (~18 400 MiB)      │  │    0.45  (~20 700 MiB)      │  │
│  └────────────┬────────────────┘  └────────────┬────────────────┘  │
│               │ :8002                           │ :8004             │
└───────────────┼─────────────────────────────────┼───────────────────┘
                │ SSH port-forward                │ SSH port-forward
                │ (runpod-tunnel.sh)              │ (runpod-tunnel.sh)
                ▼                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Mac host (localhost)                                               │
│  localhost:8002 ──────────────────────────────────────────────────▶ │
│  localhost:8004 ──────────────────────────────────────────────────▶ │
└─────────────┬───────────────────────────────────────────────────────┘
              │ host.docker.internal
              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Docker network                                                     │
│                                                                     │
│  ┌──────────────────┐    ┌───────────────────┐    ┌─────────────┐  │
│  │  open-webui      │───▶│  pipelines        │    │  qdrant     │  │
│  │  :3000           │    │  :9099            │───▶│  :6333      │  │
│  │                  │    │                   │    │             │  │
│  │  chat UI         │    │  mintify_pipeline │    │  vector DB  │  │
│  └──────────────────┘    │                   │    └─────────────┘  │
│                          │  pipelines()      │                     │
│                          │  ├─ :8002/models  │                     │
│                          │  └─ :8004/models  │                     │
│                          │                   │                     │
│                          │  pipe()           │                     │
│                          │  ├─ Qwen3-1.7B ──────────▶ :8002       │
│                          │  └─ Qwen2.5-3B ─────────▶ :8004       │
│                          └───────────────────┘                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Request flow (step by step)

```
User sends message in Open Web UI
        │
        ▼
Open Web UI (port 3000)
  → POST /api/chat  to pipelines container (port 9099)
        │
        ▼
mintify_pipeline.pipe()
  1. Embed user query with all-MiniLM-L6-v2 (sentence-transformers)
  2. Query Qdrant → retrieve top-K relevant chunks
  3. Inject chunks into system prompt
  4. Look up which vLLM port serves the selected model (_model_url_map)
  5. Stream response from the correct vLLM instance
        │
        ├─── model = Qwen/Qwen3-1.7B ──────▶ host.docker.internal:8002
        └─── model = Qwen/Qwen2.5-3B ──────▶ host.docker.internal:8004
```

---

## GPU memory allocation

The RunPod pod has **46 068 MiB** total VRAM. Each vLLM instance uses
`--gpu-memory-utilization` to cap how much of the total GPU memory it
pre-allocates. The remaining capacity after model weights is used for the
KV cache (attention state during inference).

| Instance | Model | Port | `--gpu-memory-utilization` | Allocated | Model weights (fp16) | KV cache |
|---|---|---|---|---|---|---|
| vllm-8002 | Qwen/Qwen3-1.7B | 8002 | **0.40** | ~18 427 MiB | ~3 400 MiB | ~15 000 MiB |
| vllm-8004 | Qwen/Qwen2.5-3B-Instruct | 8004 | **0.45** | ~20 731 MiB | ~6 000 MiB | ~14 700 MiB |
| **Total** | | | | **~39 158 MiB (85%)** | | |

15% (~6 900 MiB) is left free for CUDA overhead, PyTorch internals, and the
OS.

> **Rule of thumb:** sum of all `gpu-memory-utilization` values must stay
> below `1.0`. Keep the total ≤ 0.90 to avoid OOM during model load.

---

## `scripts/runpod-tunnel.sh`

### What it does

1. Defines two model instances as a bash array (model, port, GPU util, tmux session name)
2. SSH-checks each port — if vLLM is already responding, skips the launch
3. Kills any stale tmux session with the same name, then starts a fresh one
4. Polls each port until vLLM responds (up to 10 minutes)
5. Opens **one SSH connection** forwarding all ports simultaneously (blocks until Ctrl+C)

### Key snippet — instance definitions

```bash
INSTANCES=(
    "Qwen/Qwen3-1.7B:8002:0.40:vllm-8002"
    "Qwen/Qwen2.5-3B-Instruct:8004:0.45:vllm-8004"
)
```

Each entry is colon-separated: `MODEL:PORT:GPU_MEM_UTIL:TMUX_SESSION`.

### Key snippet — starting a vLLM instance

```bash
$SSH "tmux kill-session -t ${TMUX_SESSION} 2>/dev/null || true"
$SSH "tmux new-session -d -s ${TMUX_SESSION} \
    'cd /workspace/vllm-lab && source venv/bin/activate && \
     HUGGINGFACE_HUB_CACHE=/workspace/hf-cache \
     vllm serve ${MODEL} \
       --host 0.0.0.0 \
       --port ${PORT} \
       --gpu-memory-utilization ${GPU_MEM} \
       --enforce-eager'"
```

`--enforce-eager` disables CUDA graph capture — this slows per-token inference
slightly but dramatically reduces cold-start time (no warmup graph build).
Recommended when loading multiple models.

### Key snippet — multi-port SSH tunnel

```bash
ssh \
    -N \
    -L "8002:localhost:8002" \
    -L "8004:localhost:8004" \
    -p ${RUNPOD_PORT} -i ${SSH_KEY} \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=60 \
    root@${RUNPOD_HOST}
```

`-N` means "no remote command" — the SSH process just holds the port-forwards
open. `-L local:remote:remoteport` binds a local port to a port on the remote
host. Both ports are forwarded in a single connection.

---

## `pipelines/mintify_pipeline.py`

### New valve: `VLLM_BASE_URLS`

```python
class Valves(BaseModel):
    VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://host.docker.internal:8002")
    VLLM_BASE_URLS: str = os.getenv(
        "VLLM_BASE_URLS",
        "http://host.docker.internal:8002,http://host.docker.internal:8004",
    )
    ...
```

`VLLM_BASE_URLS` is a comma-separated list of all vLLM endpoints. It can be
changed at runtime in **Admin Panel → Pipelines → MINTIFY** without restarting
the container. `VLLM_BASE_URL` (singular) is kept as a fallback for
backwards-compatibility.

### New instance variable: `_model_url_map`

```python
def __init__(self):
    ...
    self._model_url_map: dict = {}  # model_id → base_url
```

This dictionary is populated by `pipelines()` and consumed by `pipe()`. It
maps a raw model ID (e.g. `"Qwen/Qwen3-1.7B"`) to the base URL of the vLLM
instance that serves it (e.g. `"http://host.docker.internal:8002"`).

### Updated `pipelines()` — discovers models from all endpoints

```python
def pipelines(self) -> list:
    import httpx

    urls = [u.strip() for u in self.valves.VLLM_BASE_URLS.split(",") if u.strip()]
    if not urls:
        urls = [self.valves.VLLM_BASE_URL]

    entries = []
    self._model_url_map = {}

    for url in urls:
        try:
            resp = httpx.get(f"{url}/v1/models", timeout=5)
            resp.raise_for_status()
            for m in resp.json().get("data", []):
                mid = m["id"]
                self._model_url_map[mid] = url          # store the URL for later routing
                entries.append({
                    "id": mid.replace("/", "__"),        # "/" → "__" (HTTP-safe)
                    "name": f" – {mid}",
                })
        except Exception as e:
            print(f"[MINTIFY] Could not fetch models from {url}: {e}")

    return entries if entries else [{"id": "unavailable", "name": "vLLM unavailable"}]
```

Open Web UI calls `pipelines()` every time it refreshes the model dropdown.
If one vLLM instance is down, it is silently skipped and the remaining models
still appear.

### Updated `pipe()` — routes to the correct endpoint

```python
# Strip manifold prefix and reverse slash sanitization
actual_model = model_id.removeprefix(f"{self.id}.").replace("__", "/")
# e.g. "mintify_pipeline.Qwen__Qwen3-1.7B" → "Qwen/Qwen3-1.7B"

# Look up which vLLM instance serves this model
base_url = self._model_url_map.get(actual_model, self.valves.VLLM_BASE_URL)

return self._stream_vllm(enriched, body, actual_model, base_url)
```

### Updated `_stream_vllm()` — creates a per-request OpenAI client

```python
def _stream_vllm(self, messages, body, model, base_url=None):
    from openai import OpenAI
    url = base_url or self.valves.VLLM_BASE_URL
    client = OpenAI(base_url=f"{url}/v1", api_key="vllm")
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            temperature=body.get("temperature", 0.7),
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as e:
        print(f"[MINTIFY] ERROR in _stream_vllm (url={url}, model={model}): {e}")
        yield f"Fehler bei der Modellanfrage: {e}"
```

vLLM exposes an OpenAI-compatible API, so the standard `openai` Python SDK
works without any modifications. `api_key="vllm"` is a placeholder — vLLM
accepts any non-empty string.

---

## vLLM flags reference

| Flag | Value | Effect |
|---|---|---|
| `--host 0.0.0.0` | — | Listen on all interfaces (required for SSH tunnel access) |
| `--port` | 8002 / 8004 | Port the HTTP server binds to on RunPod |
| `--gpu-memory-utilization` | 0.40 / 0.45 | Fraction of total GPU VRAM pre-allocated. Remainder used for KV cache. Must sum ≤ 0.90 across all instances |
| `--enforce-eager` | — | Disables CUDA graph capture. Slower inference, much faster cold start. Recommended when running multiple instances |
| `HUGGINGFACE_HUB_CACHE` | `/workspace/hf-cache` | Shared model cache across restarts. Models downloaded once, reused forever |

---

## Adding a third model

1. **Download it on RunPod** (one-time):
   ```bash
   huggingface-cli download <org>/<model> --cache-dir /workspace/hf-cache
   ```

2. **Add an entry to `INSTANCES` in `runpod-tunnel.sh`**:
   ```bash
   INSTANCES=(
       "Qwen/Qwen3-1.7B:8002:0.40:vllm-8002"
       "Qwen/Qwen2.5-3B-Instruct:8004:0.45:vllm-8004"
       "<org>/<model>:8006:0.XX:vllm-8006"   # ← new entry
   )
   ```
   Choose a `gpu-memory-utilization` so that all three values still sum ≤ 0.90.

3. **Add the port to `VLLM_BASE_URLS`** in the pipeline valve (Admin Panel or `valves.json`):
   ```
   http://host.docker.internal:8002,http://host.docker.internal:8004,http://host.docker.internal:8006
   ```

No other code changes are needed.

---

## Verification

```bash
# 1. Confirm both vLLM instances are reachable through the tunnel
curl http://localhost:8002/v1/models
curl http://localhost:8004/v1/models

# 2. Restart the pipeline container (picks up any code or valve changes)
docker compose restart pipelines

# 3. Watch pipeline logs — you should see both URLs queried
docker logs -f mintify-pipelines

# 4. Confirm routing — after sending a message with each model,
#    the logs show which URL was used:
docker logs mintify-pipelines 2>&1 | grep "url="
```
