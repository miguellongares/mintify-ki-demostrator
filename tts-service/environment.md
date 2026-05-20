# MINTIFY TTS Service — Reproduction Blueprint
Generated: $(date)

## Hardware

## GPU
```
name, driver_version, memory.total [MiB]
NVIDIA A40, 570.211.01, 46068 MiB
```

## CUDA
```
nvcc: NVIDIA (R) Cuda compiler driver
Copyright (c) 2005-2025 NVIDIA Corporation
Built on Fri_Feb_21_20:23:50_PST_2025
Cuda compilation tools, release 12.8, V12.8.93
Build cuda_12.8.r12.8/compiler.35583870_0
```

## OS
```
PRETTY_NAME="Ubuntu 24.04.3 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.3 LTS (Noble Numbat)"
VERSION_CODENAME=noble
6.8.0-57-generic
```

## Python
```
Python 3.12.3
/usr/bin/python3
```

## System packages (apt)
```
ii  ffmpeg         7:6.1.1-3ubuntu5           amd64        Tools for transcoding, streaming and playing of multimedia files
un  libsox-fmt-all <none>                     <none>       (no description available)
ii  sox            14.4.2+git20190427-4build4 amd64        Swiss army knife of sound processing
```

## venv location
```

```

## Critical package versions
```
torch:        2.8.0+cu128
cuda:         12.8
flash_attn:   2.8.3
transformers: 4.57.3
fastapi:      0.136.1
numpy:        2.1.2
qwen_tts:     /workspace/Qwen3-TTS-streaming/qwen_tts/__init__.py
```

## TTS fork commit
```
aad92ef8746d8ebcf8aaf8767d7e70b2d5106116
aad92ef README.md update
```

## Full pip freeze
```
accelerate==1.12.0
annotated-doc==0.0.4
annotated-types==0.7.0
anyio==4.11.0
argon2-cffi==25.1.0
argon2-cffi-bindings==25.1.0
arrow==1.3.0
asttokens==3.0.0
async-lru==2.0.5
attrs==25.4.0
audioread==3.1.0
babel==2.17.0
beautifulsoup4==4.14.2
bleach==6.2.0
brotli==1.2.0
certifi==2025.10.5
cffi==2.0.0
charset-normalizer==3.4.3
click==8.4.0
comm==0.2.3
debugpy==1.8.17
decorator==5.2.1
defusedxml==0.7.1
distlib==0.4.0
einops==0.8.2
executing==2.2.1
fastapi==0.136.1
fastjsonschema==2.21.2
filelock==3.20.0
flash-attn==2.8.3
flatbuffers==25.12.19
fqdn==1.5.1
fsspec==2024.6.1
gradio==6.14.0
gradio-client==2.5.0
groovy==0.1.2
h11==0.16.0
hf-gradio==0.4.1
hf-transfer==0.1.9
hf-xet==1.5.0
httpcore==1.0.9
httptools==0.7.1
httpx==0.28.1
huggingface-hub==0.36.2
idna==3.10
ipykernel==6.30.1
ipython==9.6.0
ipython-pygments-lexers==1.1.1
ipywidgets==8.1.7
isoduration==20.11.0
jedi==0.19.2
jinja2==3.1.6
joblib==1.5.3
json5==0.12.1
jsonpointer==3.0.0
jsonschema==4.25.1
jsonschema-specifications==2025.9.1
jupyter-archive==3.4.0
jupyter-client==8.6.3
jupyter-core==5.8.1
jupyter-events==0.12.0
jupyter-lsp==2.3.0
jupyter-server==2.17.0
jupyter-server-terminals==0.5.3
jupyterlab==4.4.9
jupyterlab-pygments==0.3.0
jupyterlab-server==2.27.3
jupyterlab-widgets==3.0.15
lark==1.3.0
lazy-loader==0.5
librosa==0.11.0
llvmlite==0.47.0
markdown-it-py==4.2.0
markupsafe==3.0.3
matplotlib-inline==0.1.7
mdurl==0.1.2
mistune==3.1.4
mpmath==1.3.0
msgpack==1.1.2
nbclient==0.10.2
nbconvert==7.16.6
nbformat==5.10.4
nest-asyncio==1.6.0
networkx==3.3
notebook==7.4.2
notebook-shim==0.2.4
numba==0.65.1
numpy==2.1.2
nvidia-cublas-cu12==12.8.4.1
nvidia-cuda-cupti-cu12==12.8.90
nvidia-cuda-nvrtc-cu12==12.8.93
nvidia-cuda-runtime-cu12==12.8.90
nvidia-cudnn-cu12==9.10.2.21
nvidia-cufft-cu12==11.3.3.83
nvidia-cufile-cu12==1.13.1.3
nvidia-curand-cu12==10.3.9.90
nvidia-cusolver-cu12==11.7.3.90
nvidia-cusparse-cu12==12.5.8.93
nvidia-cusparselt-cu12==0.7.1
nvidia-nccl-cu12==2.27.3
nvidia-nvjitlink-cu12==12.8.93
nvidia-nvtx-cu12==12.8.90
onnxruntime==1.26.0
orjson==3.11.9
packaging==25.0
pandas==3.0.3
pandocfilters==1.5.1
parso==0.8.5
pexpect==4.9.0
pillow==11.0.0
pip==25.2
platformdirs==4.5.0
pooch==1.9.0
prometheus-client==0.23.1
prompt-toolkit==3.0.52
protobuf==7.34.1
psutil==7.1.0
ptyprocess==0.7.0
pure-eval==0.2.3
pycparser==2.23
pydantic==2.13.4
pydantic-core==2.46.4
pydub==0.25.1
pygments==2.19.2
python-dateutil==2.9.0.post0
python-dotenv==1.2.2
python-json-logger==4.0.0
python-multipart==0.0.29
pytz==2026.2
pyyaml==6.0.3
pyzmq==27.1.0
-e file:///workspace/Qwen3-TTS-streaming
referencing==0.36.2
regex==2026.5.9
requests==2.32.5
rfc3339-validator==0.1.4
rfc3986-validator==0.1.1
rfc3987-syntax==1.1.0
rich==15.0.0
rpds-py==0.27.1
safehttpx==0.1.7
safetensors==0.7.0
scikit-learn==1.8.0
scipy==1.17.1
semantic-version==2.10.0
send2trash==1.8.3
setuptools==80.9.0
shellingham==1.5.4
sniffio==1.3.1
soundfile==0.13.1
soupsieve==2.8
sox==1.5.0
soxr==1.1.0
stack-data==0.6.3
starlette==1.0.0
sympy==1.13.3
terminado==0.18.1
threadpoolctl==3.6.0
tinycss2==1.4.0
tokenizers==0.22.2
tomlkit==0.14.0
torch==2.8.0+cu128
torchaudio==2.8.0+cu128
torchvision==0.23.0+cu128
tornado==6.5.2
tqdm==4.67.3
traitlets==5.14.3
transformers==4.57.3
triton==3.4.0
typer==0.25.1
types-python-dateutil==2.9.0.20251008
typing-extensions==4.15.0
typing-inspection==0.4.2
uri-template==1.3.0
urllib3==2.5.0
uvicorn==0.47.0
uvloop==0.22.1
virtualenv==20.34.0
watchfiles==1.2.0
wcwidth==0.2.14
webcolors==24.11.1
webencodings==0.5.1
websocket-client==1.9.0
websockets==16.0
widgetsnbextension==4.0.14
```
