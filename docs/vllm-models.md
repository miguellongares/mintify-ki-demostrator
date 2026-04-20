# vLLM Model Connection & Adding New Models

## How the connection works

### 1. SSH tunnel (`scripts/runpod-tunnel.sh`)

The pipeline container cannot reach RunPod directly. An SSH port-forward is opened on the **host machine** that maps a local port to vLLM running on the RunPod instance. Docker containers then reach it via `host.docker.internal`.

```bash
# Key variables
RUNPOD_HOST="63.141.33.23"
RUNPOD_PORT="22171"
VLLM_PORT="8002"          # local port ↔ RunPod vLLM port

# The tunnel itself (blocks until Ctrl+C)
ssh -N -L "${VLLM_PORT}:localhost:${VLLM_PORT}" \
    -p ${RUNPOD_PORT} -i ${SSH_KEY} \
    root@${RUNPOD_HOST}
```

The script also starts vLLM on RunPod if it isn't already running:

```bash
vllm serve Qwen/Qwen2.5-3B-Instruct \
    --host 0.0.0.0 \
    --port ${VLLM_PORT} \
    --enforce-eager
```

### 2. Docker Compose environment (`docker-compose.yml`)

The pipelines container receives the vLLM address via an environment variable:

```yaml
environment:
  - VLLM_BASE_URL=http://host.docker.internal:8002
```

`host.docker.internal` resolves to the Docker host — where the SSH tunnel is listening.

### 3. OpenAI client (`pipelines/mintify_pipeline.py`, `on_startup`)

The pipeline connects to vLLM using the standard OpenAI SDK, which vLLM is compatible with:

```python
# on_startup() — line 93
self.llm_client = OpenAI(
    base_url=f"{self.valves.VLLM_BASE_URL}/v1",   # e.g. http://host.docker.internal:8002/v1
    api_key="vllm",                                # vLLM accepts any non-empty key
)
```

### 4. Model discovery (`pipelines()` method)

The pipeline is a **manifold** type — it calls vLLM's model list endpoint every time Open Web UI refreshes the dropdown. Whatever is loaded in vLLM appears automatically:

```python
# pipelines() — line 52
def pipelines(self) -> list:
    resp = httpx.get(f"{self.valves.VLLM_BASE_URL}/v1/models", timeout=5)
    models = resp.json().get("data", [])
    # Slashes in HuggingFace model IDs (e.g. "Qwen/Qwen2.5-3B-Instruct")
    # are replaced with __ to avoid breaking HTTP routing.
    return [{"id": m["id"].replace("/", "__"), "name": f" – {m['id']}"} for m in models]
```

### 5. Request routing (`pipe()` method)

When the user sends a message, `pipe()` receives the selected model ID, reverses the `__` sanitization, and forwards the request to vLLM:

```python
# pipe() — strips pipeline prefix and restores the slash
actual_model = model_id.removeprefix(f"{self.id}.").replace("__", "/")
# e.g. "mintify_pipeline.Qwen__Qwen2.5-3B-Instruct" → "Qwen/Qwen2.5-3B-Instruct"
```

---

## How to add more models

There are two levels: loading a model into vLLM on RunPod, and (optionally) serving multiple models simultaneously.

### Option A — Replace the current model

Edit `scripts/runpod-tunnel.sh` and change the model name in the `vllm serve` command:

```bash
vllm serve mistralai/Mistral-7B-Instruct-v0.3 \
    --host 0.0.0.0 \
    --port ${VLLM_PORT} \
    --enforce-eager
```

Run the script again. The new model will be downloaded from Hugging Face (cached in `/workspace/hf-cache` on RunPod) and served. After the tunnel is open, refresh the OWUI dropdown — the new model appears automatically.

> **Note:** vLLM downloads from Hugging Face on first use. Gated models (e.g. Llama 3) require a HF token:
> ```bash
> HUGGINGFACE_TOKEN=hf_xxx vllm serve meta-llama/Meta-Llama-3-8B-Instruct ...
> ```

### Option B — Serve multiple models at once (recommended for comparison)

vLLM can serve several models in parallel if you run multiple instances on different ports. Update the tunnel script to forward each port:

```bash
# In scripts/runpod-tunnel.sh — replace the single tunnel with multi-port
ssh -N \
    -L 8002:localhost:8002 \
    -L 8003:localhost:8003 \
    ${SSH_OPTS} "${RUNPOD_USER}@${RUNPOD_HOST}"
```

Start each model in its own tmux window on RunPod:

```bash
# Window 1 — 3B model
vllm serve Qwen/Qwen2.5-3B-Instruct --host 0.0.0.0 --port 8002 --enforce-eager

# Window 2 — 7B model
vllm serve mistralai/Mistral-7B-Instruct-v0.3 --host 0.0.0.0 --port 8003 --enforce-eager
```

Then update `pipelines()` to query both endpoints and merge the lists:

```python
def pipelines(self) -> list:
    endpoints = [
        self.valves.VLLM_BASE_URL,          # http://host.docker.internal:8002
        "http://host.docker.internal:8003",  # second vLLM instance
    ]
    result = []
    for base_url in endpoints:
        try:
            resp = httpx.get(f"{base_url}/v1/models", timeout=5)
            resp.raise_for_status()
            for m in resp.json().get("data", []):
                result.append({
                    "id": m["id"].replace("/", "__"),
                    "name": f" – {m['id']}",
                    "_base_url": base_url,   # store so pipe() knows where to send
                })
        except Exception as e:
            print(f"[MINTIFY] Could not reach {base_url}: {e}")
    return result
```

You would also need `pipe()` to look up the correct base URL for the selected model and use a matching OpenAI client, rather than the single `self.llm_client`.

### Option C — Pre-download models and switch without re-downloading

On RunPod, pre-download models to the shared cache directory so switching is fast:

```bash
# On RunPod — pre-download without serving
huggingface-cli download Qwen/Qwen2.5-7B-Instruct \
    --cache-dir /workspace/hf-cache
```

Then change the model in the `vllm serve` command and restart. The model loads from cache in seconds instead of downloading again.

---

## Quick reference

| What you want | Where to change |
|---|---|
| Change which model vLLM serves | `scripts/runpod-tunnel.sh` — `vllm serve <model>` |
| Change the vLLM port | `scripts/runpod-tunnel.sh` (`VLLM_PORT`) and `docker-compose.yml` (`VLLM_BASE_URL`) |
| Change the base URL at runtime | Admin Panel → Pipelines → MINTIFY → `VLLM_BASE_URL` valve |
| Add a second vLLM instance | Add a second port-forward in the tunnel script and update `pipelines()` |
