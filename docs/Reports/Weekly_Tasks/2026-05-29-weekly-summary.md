# Weekly Summary — 22–29 May 2026

## 🐛 Judge / Bewertung fixes (May 26)
- Fixed the Judge feature on the HTTPS production server (the `:8088` mixed-content bug); working locally and on the server. (PR #6)

## 🧹 Repo cleanup (May 27)
- Removed old docs, diagrams and unused scripts (~1,200 lines), wrote a new README, stripped the dead `8004` LLM port from the code. (PR #7)

## 🎨 Frontend / custom UI (May 27)
- Added frontend HMR dev workflow via Vite proxy (`npm run dev` → localhost:5173).
- Visual rebrand: main colors, loading splash icon, tab icon, OI icons; renamed to "KI Control Center"; configured sidebar text.
- Added a waiting/loading bar to the Judge panel. (PR #8)

## 📚 RAG update (May 28)
- Big RAG update (+504 lines / 8 files); deleted ~850 lines of old indexed documents.
---

## TL;DR

| Concern | Before | After |
|---|---|---|
| RAG retrieval per user question | 2 (OWUI's + rag-backend's) | 1 (rag-backend only) |
| System prompts stacked into one | 2 (OWUI per-model **+** pipeline persona) | 1 (per-model wins; persona is fallback) |
| Qdrant calls per chat (title, tags, query-gen) | 4 | 1 |
| Knowledge scoping | "search all 14 PDFs" | "search only the files attached to the active model" |
| OWUI `<source>` / `### Task` template noise in prompt | Yes | Stripped by the pipeline |

---

## 🚀 Production
- Hetzner server at `mintify.verbraucherinformatik.de` with Caddy reverse proxy:
  - `/rag-api/*` → `localhost:8088` (strip prefix)
  - everything else → `localhost:3000`
- Set up a **domain provided by the university** and pointed it at the host.
- Configured **HTTPS** via Caddy (automatic TLS on :443) — enabling the browser→backend calls (Judge, TTS) over HTTPS without mixed-content errors.
- RunPod tunnel runs via `scripts/server-runpod-tunnel.sh` in tmux with auto-respawn (ports 8002 vLLM, 8006 TTS).

Deploy: `git pull && docker compose build <service> && docker compose up -d <service>`
