#!/usr/bin/env bash
# Starts vLLM on RunPod (if not already running) and opens an SSH port-forward tunnel.
# Docker containers reach vLLM via host.docker.internal:8000.
#
# Usage:
#   ./scripts/runpod-tunnel.sh
#
# Keep this running in a separate terminal while the stack is up.

set -euo pipefail

RUNPOD_HOST="63.141.33.23"
RUNPOD_PORT="22171"
RUNPOD_USER="root"
SSH_KEY="$HOME/.ssh/.ssh_gpu/id_ed25519"
VLLM_PORT="8000"

SSH_OPTS="-p ${RUNPOD_PORT} -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=3"
SSH="ssh ${SSH_OPTS} ${RUNPOD_USER}@${RUNPOD_HOST}"

# ── Step 1: start vLLM if not already responding ─────────────────────────────

if $SSH "curl -sf http://localhost:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
    echo "vLLM already running — skipping start."
else
    echo "Starting vLLM on RunPod..."
    $SSH "tmux has-session -t vllm 2>/dev/null || \
        tmux new-session -d -s vllm \
            'cd /workspace/vllm-lab && source venv/bin/activate && \
             HUGGINGFACE_HUB_CACHE=/workspace/hf-cache \
             vllm serve Qwen/Qwen2.5-0.5B-Instruct --host 0.0.0.0 --port ${VLLM_PORT}'"

    # ── Step 2: wait until vLLM responds (poll every 5 s, up to 10 min) ──────
    echo "Waiting for vLLM to be ready (this may take a few minutes)..."
    for i in $(seq 1 120); do
        if $SSH "curl -sf http://localhost:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
            echo "vLLM is ready."
            break
        fi
        echo "  [${i}/120] still loading..."
        [ "$i" -eq 120 ] && { echo "ERROR: vLLM did not start within 10 min"; exit 1; }
        sleep 5
    done
fi

# ── Step 3: open the port-forward tunnel (blocks until Ctrl+C) ───────────────

echo "Opening SSH tunnel: localhost:${VLLM_PORT} → RunPod :${VLLM_PORT}"
echo "Press Ctrl+C to close."

ssh \
    -N \
    -L "${VLLM_PORT}:localhost:${VLLM_PORT}" \
    ${SSH_OPTS} \
    "${RUNPOD_USER}@${RUNPOD_HOST}"
