#!/usr/bin/env bash
# Starts one vLLM instance on RunPod and opens an SSH tunnel forwarding the port.
#
#   Port 8002 → cyankiwi/Qwen3.5-27B-AWQ-4bit (gpu-memory-utilization 0.70)
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
# Format: "MODEL:PORT:GPU_MEM_UTIL:TMUX_SESSION:MAX_MODEL_LEN"
INSTANCES=(
  "cyankiwi/Qwen3.5-27B-AWQ-4bit:8002:0.70:vllm-8002:32768"
)

# ── Extra services (non-vLLM) ────────────────────────────────────────────────
# Format: "NAME:PORT:TMUX_SESSION:HEALTH_PATH:START_CMD"
# HEALTH_PATH is appended to http://localhost:PORT/ (no leading slash).
EXTRA_SERVICES=(
  "qwen3-tts:8006:qwen-tts:health:cd /workspace/vllm-lab/tts-service && source ../.venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8006"
)

# ── Step 1: start each vLLM instance if not already responding ────────────────
for entry in "${INSTANCES[@]}"; do
  IFS=: read -r MODEL PORT GPU_MEM TMUX_SESSION MAX_LEN <<< "$entry"

  echo "──────────────────────────────────────────────────────────"
  echo "Model          : ${MODEL}"
  echo "Port           : ${PORT}  |  GPU mem util: ${GPU_MEM}  |  tmux: ${TMUX_SESSION}"
  echo "Max model len  : ${MAX_LEN}"

  if $SSH "curl -sf http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "→ Already running — skipping start."
  else
    echo "→ Starting vLLM..."
    $SSH "tmux kill-session -t ${TMUX_SESSION} 2>/dev/null || true"
    $SSH "tmux new-session -d -s ${TMUX_SESSION} 'cd /workspace/vllm-lab && source .venv/bin/activate && vllm serve ${MODEL} --host 0.0.0.0 --port ${PORT} --gpu-memory-utilization ${GPU_MEM} --max-model-len ${MAX_LEN}'"

    echo "→ Waiting for vLLM to be ready (this may take a few minutes)..."
    LAST_LINES=""
    for i in $(seq 1 120); do
      if $SSH "curl -sf http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
        echo "→ vLLM ready on port ${PORT}."
        break
      fi
      NEW_LINES=$($SSH "tmux capture-pane -pt ${TMUX_SESSION} -S -5 2>/dev/null || echo '(tmux session not found)'")
      if [ "$NEW_LINES" != "$LAST_LINES" ]; then
        echo "── tmux: ${TMUX_SESSION} ──────────────────────────────────"
        echo "$NEW_LINES"
        LAST_LINES="$NEW_LINES"
      fi
      echo "  [${i}/120] still loading..."
      [ "$i" -eq 120 ] && { echo "ERROR: vLLM (${MODEL}) did not start within 10 min"; exit 1; }
      sleep 5
    done
  fi
done

# ── Step 1b: start each extra service if not already responding ───────────────
for entry in "${EXTRA_SERVICES[@]}"; do
  IFS=: read -r NAME PORT TMUX_SESSION HEALTH_PATH START_CMD <<< "$entry"

  echo "──────────────────────────────────────────────────────────"
  echo "Service        : ${NAME}"
  echo "Port           : ${PORT}  |  tmux: ${TMUX_SESSION}  |  health: /${HEALTH_PATH}"

  if $SSH "curl -sf http://localhost:${PORT}/${HEALTH_PATH}" >/dev/null 2>&1; then
    echo "→ Already running — skipping start."
  else
    echo "→ Starting ${NAME}..."
    $SSH "tmux kill-session -t ${TMUX_SESSION} 2>/dev/null || true"
    $SSH "tmux new-session -d -s ${TMUX_SESSION} '${START_CMD}'"

    echo "→ Waiting for ${NAME} to be ready (model load may take a few minutes)..."
    LAST_LINES=""
    for i in $(seq 1 120); do
      if $SSH "curl -sf http://localhost:${PORT}/${HEALTH_PATH}" >/dev/null 2>&1; then
        echo "→ ${NAME} ready on port ${PORT}."
        break
      fi
      NEW_LINES=$($SSH "tmux capture-pane -pt ${TMUX_SESSION} -S -5 2>/dev/null || echo '(tmux session not found)'")
      if [ "$NEW_LINES" != "$LAST_LINES" ]; then
        echo "── tmux: ${TMUX_SESSION} ──────────────────────────────────"
        echo "$NEW_LINES"
        LAST_LINES="$NEW_LINES"
      fi
      echo "  [${i}/120] still loading..."
      [ "$i" -eq 120 ] && { echo "ERROR: ${NAME} did not start within 10 min"; exit 1; }
      sleep 5
    done
  fi
done

# ── Step 2: open the port-forward tunnel (blocks until Ctrl+C) ────────────────
echo "──────────────────────────────────────────────────────────"
echo "Opening SSH tunnel:"
echo "  localhost:8002 → RunPod:8002  (cyankiwi/Qwen3.5-27B-AWQ-4bit)"
echo "  localhost:8006 → RunPod:8006  (Qwen3-TTS inference server)"
echo "Press Ctrl+C to close."

ssh \
  -N \
  -o GatewayPorts=yes \
  -L "0.0.0.0:8002:localhost:8002" \
  -L "0.0.0.0:8006:localhost:8006" \
  ${SSH_OPTS} \
  "${RUNPOD_USER}@${RUNPOD_HOST}"
