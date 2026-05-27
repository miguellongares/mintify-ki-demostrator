# Frontend Dev Workflow (HMR — no Docker rebuild needed)

## Prerequisites

- Docker stack running
- Node 22 via fnm

## One-time setup

### 1. Add fnm to shell (if not done)
```bash
echo 'eval "$(fnm env --use-on-cd)"' >> ~/.zshrc
source ~/.zshrc
```

### 2. Install Node 22
```bash
fnm install 22
```

### 3. Install frontend dependencies
```bash
cd open-webui-src
fnm use 22
npm install
```

### 4. Changes made to enable HMR

**`open-webui-src/vite.config.ts`** — added Vite proxy so the dev server forwards API calls to the running Docker container:
```ts
server: {
  proxy: {
    '/api':   { target: 'http://localhost:3000', changeOrigin: true, ws: true },
    '/oauth': { target: 'http://localhost:3000', changeOrigin: true },
    '/ws':    { target: 'http://localhost:3000', changeOrigin: true, ws: true }
  }
}
```

**`open-webui-src/src/lib/constants.ts` line 7`** — removed hardcoded `http://localhost:8080` in dev mode so the Vite proxy is used instead:
```ts
export const WEBUI_BASE_URL = browser ? `` : ``;
```

---

## Daily dev workflow

```bash
# 1. Start the backend stack (if not already running)
docker compose up -d

# 2. Switch to Node 22 and start the dev server
cd open-webui-src
fnm use 22
npm run dev
```

Open **http://localhost:5173** — edits to Svelte files, CSS, logos, and colors appear instantly via HMR.

---

## What each proxy route does

| Route | Purpose |
|---|---|
| `/api` | All backend calls: login, chat, models, settings |
| `/oauth` | Authentication/login redirects |
| `/ws` | WebSocket for streaming chat responses |

---

## Production deploy (after finalising frontend changes)

```bash
docker compose build open-webui
docker compose up -d open-webui
```
