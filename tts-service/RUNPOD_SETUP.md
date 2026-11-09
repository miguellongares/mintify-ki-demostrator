# TTS on RunPod — setup commands

Venv lives on the persistent volume: `/workspace/tts_service/tts-venv`.
After a pod reset only the steps marked **[every reset]** are needed.

## One-time setup (fresh volume)

```bash
# files: main.py, german_ref.wav, requirements.txt -> /workspace/tts_service
# fork:  git clone https://github.com/dffdeeq/Qwen3-TTS-streaming /workspace/Qwen3-TTS-streaming
#        cd /workspace/Qwen3-TTS-streaming && git checkout aad92ef

cd /workspace/tts_service
python3 -m venv tts-venv && source tts-venv/bin/activate      # check prompt shows (tts-venv)!
pip install -U pip wheel setuptools
pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
grep -v -E '^(-e |python-apt|dbus-python|PyGObject|launchpadlib|wadllib|lazr|torch|flash_attn|nvidia-|triton)' freeze.txt > req-filtered.txt
pip install -r req-filtered.txt
cd /workspace/Qwen3-TTS-streaming && pip install -e . --no-deps
MAX_JOBS=4 pip install flash-attn==2.8.3 --no-build-isolation  # compiles (tmux!); wheel is cached in /workspace/.cache/pip
```

## [every reset] system packages (not on the volume)

```bash
apt-get update && apt-get install -y ffmpeg sox libsox-fmt-all
```

## Start

```bash
# from the laptop (starts vLLM + TTS + docling in tmux on the pod, opens the tunnels):
./scripts/init_models.sh

# or by hand on the pod:
tmux new -s qwen-tts
cd /workspace/tts_service && source tts-venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8006
```

Cold start takes ~90-180 s (model load + 3 torch.compile warmups).

## Verify

```bash
python -c "import torch, flash_attn, qwen_tts; print(torch.__version__, torch.cuda.is_available())"
curl localhost:8006/health
curl -s -X POST localhost:8006/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"input":"Hallo, das ist ein Test."}' -o /tmp/test.wav
```

## Gotchas

- Always check `which pip` points into `tts-venv` — otherwise packages land in the global Python and vanish on reset.
- Never `rsync --delete` over `/workspace/tts_service` (wipes the venv).
- Model weights: set `HF_HOME=/workspace/.cache/huggingface` so they survive resets.
