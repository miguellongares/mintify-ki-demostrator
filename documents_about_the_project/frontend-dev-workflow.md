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
| `/rag-api` | rag-backend on port 8088 (Bewertung judges, Persönliche Dateien) — mirrors the Caddy route in production |

---

## Mobile preview

`npm run dev` already passes `--host`, so Vite binds `0.0.0.0` and prints two URLs:

```
➜  Local:   http://localhost:5173/
➜  Network: http://192.168.178.161:5173/     ← open this one on the phone
```

Open the **Network** URL on a phone on the same Wi-Fi. HMR works there too, so
edits appear on the phone instantly. If it does not connect, macOS is blocking
inbound `node` — allow it in System Settings → Network → Firewall.

**Why the `/rag-api` proxy route matters here.** The rag-backend is called
directly from the browser, so the base URL has to be resolved at runtime. That
logic lives in **one** place, `src/lib/utils/ragApi.ts`:

| Where the page is served from | Base URL |
|---|---|
| Vite dev server (localhost *or* LAN IP) | `/rag-api` → proxy → `localhost:8088` |
| Baked Docker build on `localhost:3000` | `http://localhost:8088` |
| Production behind Caddy | `${origin}/rag-api` |

The dev case is *relative* on purpose: a hardcoded `localhost:8088` resolves to
the **phone itself**, which is why the Bewertung panel and Persönliche Dateien
used to fail silently in a LAN preview. Never hardcode the port at a call site —
extend `ragApiBase()` instead.

**Writing phone-only styles.** Tailwind's `md:` breakpoint (768px) and the
`mobile` store (`src/lib/stores/index.ts`, set from `innerWidth < 768` in
`src/routes/+layout.svelte`) are the *same* boundary. So:

- **Styling** — write the phone value unprefixed, re-assert the desktop value
  behind `md:`: `px-2 md:px-3`, `hidden md:inline`.
- **Behaviour** — branch on `$mobile` in the script.

⚠️ Tailwind v4 scans source files as plain text, so a responsive class must
appear as a **literal string**. `md:{someVariable}` will not generate any CSS.
A literal inside a ternary (`{isHome ? 'px-2 md:px-3.5' : 'px-2 md:px-3'}`) is
fine. To verify a class was actually emitted:

```bash
curl -s 'http://localhost:5173/src/tailwind.css?direct' | grep -F '.md\:inline'
```

---

## Production deploy (after finalising frontend changes)

```bash
docker compose build open-webui
docker compose up -d open-webui
```
