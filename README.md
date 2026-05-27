# MINTIFY KI-Demonstrator

A German-language RAG chat system for financial literacy education, developed at Hochschule Bonn-Rhein-Sieg. Users ask questions in a browser; the system retrieves relevant PDF chunks, injects them into a prompt, and streams an LLM answer back. A sidebar panel ("Bewertung") runs three independent LLM judges to rate each answer, and a TTS button reads the answer aloud.

---

## Architecture

```
Browser
  → Open Web UI (port 3000)
    → Pipelines server (port 9099)
      → RAG backend (port 8088)
        → Qdrant (port 6333)          vector search
        → vLLM on RunPod (port 8002)  LLM inference via SSH tunnel

Side channels (browser → RAG backend directly):
  → POST /api/judge    → 3 parallel LLM judges  (Bewertung panel)
  → POST /api/audio/speech → Qwen3-TTS on RunPod (TTS playback)
```

| Container | Port | Role |
|---|---|---|
| `mintify-webui` | 3000 | Open Web UI frontend |
| `mintify-pipelines` | 9099 | Thin HTTP proxy to RAG backend |
| `mintify-rag-service` | 8088 | RAG logic, judge, TTS proxy |
| `mintify-qdrant` | 6333 | Vector database |

---


## Key features

- **RAG chat** — PDFs dropped in `data/uploads/` are automatically indexed into Qdrant and retrieved on the next query.
- **Bewertung panel** — after each answer, three independent LLM judges rate it as `gut / mittel / schlecht` with a short reasoning.
- **Text-to-speech** — the OWUI TTS button streams audio via Qwen3-TTS running on RunPod.

---

## Project structure

```
mintify-ki-demostrator/
├── docker-compose.yml
├── pipelines/
│   └── mintify_pipeline.py       thin proxy to RAG backend
├── rag-backend/
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── dependencies.py
│       ├── routers/              rag, judge, tts, models
│       └── services/             embedder, retriever, indexer, llm_client, judge_client, tts_client
├── tts-service/                  Qwen3-TTS server (deployed to RunPod)
├── open-webui-src/               custom OWUI fork (Svelte)
│   └── src/lib/components/chat/JudgementPanel.svelte
├── scripts/
│   ├── runpod-tunnel.sh          local dev tunnel
│   └── server-runpod-tunnel.sh   production respawn loop
└── data/                         gitignored, bind-mounted
    ├── uploads/                  PDFs to index
    ├── qdrant/
    └── open-webui/
```

---

## Common commands

```bash
# Start / stop
docker compose up -d
docker compose down

# Watch logs
docker logs -f mintify-rag-service
docker logs -f mintify-pipelines

# Rebuild after code changes
docker compose build mintify-rag-service && docker compose up -d mintify-rag-service
docker compose build open-webui && docker compose up -d open-webui

# Check Qdrant vector count
curl http://localhost:6333/collections/mintify_rag
```

---

## Production

Production runs on a Hetzner server at `mintify.verbraucherinformatik.de` with Caddy as a reverse proxy.

```
Browser
  ─ https ─→ Caddy (:443)
              ├─ /rag-api/* ─→ localhost:8088  (strip prefix)
              └─ everything else ─→ localhost:3000
```

Deploy a code change:
```bash
git pull
docker compose build <service>
docker compose up -d <service>
```

The RunPod tunnel on the server runs via `scripts/server-runpod-tunnel.sh` in a tmux session with automatic respawn.
