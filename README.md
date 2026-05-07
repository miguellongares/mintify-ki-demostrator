# MINTIFY KI-Demonstrator

A German-language AI chat system for financial literacy education, developed as part of the MINTIFY research project at Hochschule Bonn-Rhein-Sieg.

Built on **Open Web UI v0.6.5** with full source access for frontend customization.

---

## Architecture

```
Browser → Open Web UI (Frontend, port 3000)
              ↓ OpenAI-compatible API
         Pipelines (RAG + LLM, port 9099)
              ↓ OpenAI-compatible API          ↓ vector search
         vLLM on RunPod (SSH tunnel :8000)   Qdrant (Vector DB, port 6333)
```

| Component | Role |
|---|---|
| `mintify-webui` | Frontend — Open Web UI v0.6.5 built from source |
| `mintify-pipelines` | RAG pipeline — embeds queries, retrieves context, streams responses |
| `mintify-qdrant` | Vector database — stores PDF chunk embeddings |
| vLLM on RunPod | LLM inference on remote GPU, reached via SSH tunnel (`scripts/runpod-tunnel.sh`) |

---

## Connecting to RunPod (LLM inference)

MINTIFY does not run an LLM locally. Instead, inference is delegated to a GPU instance on RunPod running **vLLM**. The Docker containers reach the model through an SSH tunnel that maps `localhost:8002` on your machine to the vLLM server on the remote instance.

**The tunnel must be open before starting the stack.** Without it, the RAG pipeline has no route to the model and every chat request will fail.

To start the tunnel, run the following in a dedicated terminal and keep it open for as long as the stack is running:

```bash
./scripts/runpod-tunnel.sh
```

The script will:
1. Check whether vLLM is already responding on RunPod.
2. If not, launch it inside a `tmux` session on the remote host and wait until it is ready (this can take a few minutes on first load).
3. Open the SSH port-forward (`localhost:8002 → RunPod:8002`) and hold it open until you press **Ctrl+C**.

Once you see *"Opening SSH tunnel"* in the output, the stack is ready to receive requests.

---

## Prerequisites

- [Docker Desktop](https://docs.docker.com/get-docker/) 4.40+ with Docker Compose v2
- A RunPod instance with vLLM installed at `/workspace/vllm-lab` (venv: `venv`)
- SSH access to the RunPod (`~/.ssh/.ssh_gpu/id_ed25519`)
- ~10 GB free disk space

---

## First Run

### 1. Clone this repository

```bash
git clone <your-repo-url> mintify-ki-demostrator
cd mintify-ki-demostrator
```

### 2. Set up environment variables

```bash
cp .env.example .env
# Edit .env and set WEBUI_SECRET_KEY:
openssl rand -hex 32
```

### 3. Start vLLM on RunPod and open the SSH tunnel

```bash
./scripts/runpod-tunnel.sh
```

This will start vLLM on RunPod (if not already running) and forward port 8000 to localhost. Keep this terminal open while the stack is running.

### 4. Build and start all containers

```bash
docker compose up --build -d
```

The first build takes several minutes (compiling the Svelte frontend and installing Python dependencies).

### 6. Open the app

```
http://localhost:3000
```

Create your admin account on the first visit. Select **MINTIFY – Finanzielle Bildung** from the model dropdown to chat.

---

## Uploading Documents (RAG)

PDFs are automatically indexed into Qdrant on the next chat message after upload.

```bash
./scripts/upload-document.sh path/to/document.pdf
```

To verify indexing, check the pipeline logs:

```bash
docker logs mintify-pipelines 2>&1 | grep "Indexed"
```

The Qdrant dashboard is available at `http://localhost:6333/dashboard`.

---

## Stopping and restarting

```bash
# Stop (data preserved in ./data/)
docker compose down

# Restart
docker compose up -d

# Rebuild after frontend changes
docker compose up --build -d open-webui
```

---

## Modifying the Frontend

The entire Open Web UI frontend source lives in `open-webui-src/src/`. It is a **SvelteKit** application.

Key directories:
- `open-webui-src/src/lib/components/` — all UI components
- `open-webui-src/src/app.css` — global styles
- `open-webui-src/src/routes/` — page routes

After making changes, rebuild:

```bash
docker compose up --build -d open-webui
```

---

## Modifying the Pipeline

The RAG pipeline is at `pipelines/mintify_pipeline.py`. It is **hot-reloaded** — no rebuild needed after changes.

Configuration (model, chunk size, system prompt, etc.) is managed via the Admin Panel → Pipelines → MINTIFY valves, or directly in `pipelines/mintify_pipeline/valves.json`.

---

## Project structure

```
mintify-ki-demostrator/
├── docker-compose.yml
├── .env
├── .gitignore
├── README.md
├── documets_about_the_project/     # Project spec and reference docs
├── open-webui-src/                 # Open Web UI v0.6.5 source
│   ├── Dockerfile
│   ├── src/                        # SvelteKit frontend — editable
│   └── backend/                    # FastAPI backend
├── pipelines/
│   ├── Dockerfile
│   ├── mintify_pipeline.py         # RAG pipeline (pipe-type, appears as model)
│   └── mintify_pipeline/
│       └── valves.json             # Pipeline configuration
├── scripts/
│   ├── runpod-tunnel.sh            # Start vLLM on RunPod + open SSH port-forward tunnel
│   └── upload-document.sh          # Copy PDFs into pipeline for RAG indexing
└── data/                           # Runtime data (gitignored)
    ├── uploads/                    # PDFs for RAG
    ├── qdrant/                     # Vector DB storage
    └── open-webui/                 # OWUI database and cache
```

---

## Verification checklist

- [ ] `./scripts/runpod-tunnel.sh` — prints "vLLM is ready." and opens tunnel
- [ ] `curl http://localhost:8000/v1/models` — vLLM responds through the tunnel
- [ ] `docker compose ps` — all containers show `running`
- [ ] `http://localhost:6333/dashboard` — Qdrant dashboard loads
- [ ] `curl http://localhost:9099/models` — pipeline responds
- [ ] `http://localhost:3000` — loads in German, titled "MINTIFY"
- [ ] Select **MINTIFY – Finanzielle Bildung** → send a message → response streams
- [ ] Upload a PDF → send a question about it → response references document content
