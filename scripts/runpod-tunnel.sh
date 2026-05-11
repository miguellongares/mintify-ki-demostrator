#!/usr/bin/env bash
# Starts one vLLM instance on RunPod and opens an SSH tunnel forwarding the port.
#
#   Port 8002 → Qwen/Qwen3-32B-AWQ (gpu-memory-utilization 0.75)
#
# Usage:
#   ./scripts/runpod-tunnel.sh
#
# Keep this running in a separate terminal while the stack is up.
# Verify with:
#   curl http://localhost:8002/v1/models

set -euo pipefail

RUNPOD_HOST="63.141.33.23"
RUNPOD_PORT="22171"
RUNPOD_USER="root"
SSH_KEY="$HOME/.ssh/.ssh_gpu/id_ed25519"
SSH_OPTS="-p ${RUNPOD_PORT} -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ServerAliveInterval=60 -o ServerAliveCountMax=60"
SSH="ssh ${SSH_OPTS} ${RUNPOD_USER}@${RUNPOD_HOST}"

# ── Model definitions ─────────────────────────────────────────────────────────
# Format: "MODEL:PORT:GPU_MEM_UTIL:TMUX_SESSION:MAX_MODEL_LEN:REASONING_PARSER"
INSTANCES=(
  "Qwen/Qwen3-32B-AWQ:8002:0.75:vllm-8002:32768:deepseek_r1"
)

# ── Step 1: start each vLLM instance if not already responding ────────────────
for entry in "${INSTANCES[@]}"; do
  IFS=: read -r MODEL PORT GPU_MEM TMUX_SESSION MAX_LEN REASONING_PARSER <<< "$entry"

  echo "──────────────────────────────────────────────────────────"
  echo "Model          : ${MODEL}"
  echo "Port           : ${PORT}  |  GPU mem util: ${GPU_MEM}  |  tmux: ${TMUX_SESSION}"
  echo "Max model len  : ${MAX_LEN}"
  echo "Reasoning parser: ${REASONING_PARSER}"

  if $SSH "curl -sf http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "→ Already running — skipping start."
  else
    echo "→ Starting vLLM..."
    $SSH "tmux kill-session -t ${TMUX_SESSION} 2>/dev/null || true"
    $SSH "tmux new-session -d -s ${TMUX_SESSION} \
            'cd /workspace/vllm-lab && source venv/bin/activate && \
             HUGGINGFACE_HUB_CACHE=/workspace/hf-cache \
             vllm serve ${MODEL} \
               --host 0.0.0.0 \
               --port ${PORT} \
               --gpu-memory-utilization ${GPU_MEM} \
               --max-model-len ${MAX_LEN} \
               --reasoning-parser ${REASONING_PARSER}'"

    echo "→ Waiting for vLLM to be ready (this may take a few minutes)..."
    for i in $(seq 1 120); do
      if $SSH "curl -sf http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "→ vLLM ready on port ${PORT}."
        break
      fi
      echo "  [${i}/120] still loading..."
      [ "$i" -eq 120 ] && { echo "ERROR: vLLM (${MODEL}) did not start within 10 min"; exit 1; }
      sleep 5
    done
  fi
done

# ── Step 2: open the port-forward tunnel (blocks until Ctrl+C) ────────────────
echo "──────────────────────────────────────────────────────────"
echo "Opening SSH tunnel:"
echo "  localhost:8002 → RunPod:8002  (Qwen/Qwen3-32B-AWQ)"
echo "Press Ctrl+C to close."

ssh \
  -N \
  -o GatewayPorts=yes \
  -L "0.0.0.0:8002:localhost:8002" \
  ${SSH_OPTS} \
  "${RUNPOD_USER}@${RUNPOD_HOST}"