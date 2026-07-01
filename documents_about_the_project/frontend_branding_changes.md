# MINTIFY Frontend Branding — Change Map

Where the custom branding lives, so it's easy to find and tweak. All paths under `open-webui-src/`.

## Brand colors (single source of truth)

`tailwind.config.js` → `theme.extend.colors.mintify`:

| Token | Hex | Use |
|---|---|---|
| `mintify-blue` | `#1C3E73` | primary accent (buttons, links, focus) |
| `mintify-blue-light` | `#2a5499` | blue hover |
| `mintify-blue-dark` | `#142d54` | — |
| `mintify-green` | `#A7F57E` | dark-mode accent, hover |
| `mintify-green-dark` | `#8fe05f` | green hover |
| `mintify-tint` | `#e9eef7` | sidebar bg (light) |
| `mintify-night` | `#0e1a2e` | sidebar bg (dark) |
| `mintify-chat` | `#FEFFFA` | chat background |
| `mintify-pill` | `#d4dcea` | "..." menu pill on selected/hover row (light) |
| `mintify-pill-dark` | `#12233f` | same (dark) |
| `mintify-pill-active` | `#c0cbdc` | "..." pill on active row (light) |
| `mintify-pill-active-dark` | `#14284a` | same (dark) |

Change a color here once → it updates everywhere.

## Logos / images

Swap the files (keep the names) — no code change needed. **Two static folders exist and BOTH must be updated:**

- **Frontend** `static/` — used by in-app UI (sidebar/header/avatars, splash):
  - `favicon.png`, `static/favicon.png` — sidebar/header/avatar icon
  - `static/splash.png`, `static/splash-dark.png` — loading screen logo
  - `static/favicon.ico`, `favicon.svg`, `favicon-96x96.png`, `apple-touch-icon.png`, `web-app-manifest-{192,512}.png` — PWA icons
- **Backend** `backend/open_webui/static/` — **this is what the browser tab favicon actually loads** (the backend serves `/static/favicon.*`):
  - `favicon.png`, `favicon.ico`, `favicon.svg`, `favicon-96x96.png`, `favicon-dark.png`, `logo.png` (manifest icon), `splash.png`, `splash-dark.png`
- Source artwork: `static/mintify_circle.png` (circle M), `static/Square-logo-Blue.png` (full mintify logo)

## Browser tab title & favicon

- **Title** → `WEBUI_NAME=MINTIFY` in `docker-compose.yml`. The `" (Open WebUI)"` suffix that OWUI normally appends was removed in `backend/open_webui/env.py` (the `if WEBUI_NAME != "Open WebUI"` block).
- **Favicon** → the backend static files above. After changing them you MUST rebuild, AND the browser caches favicons in a separate store that survives normal reloads — do a **hard refresh** (or incognito) to see the new one.

## Loading screen

`src/app.html`
- Logo sizes: `#logo` height, `#logo-her` height
- Progress bar: blue→green gradient on `#progress-bar`

## Color accents

- **Send + Call buttons** — `src/lib/components/chat/MessageInput.svelte` (search `bg-mintify-blue`)
- **Chat input focus ring** — `MessageInput.svelte` (`focus-within:border-mintify-blue/40`)
- **Chat input area bg** — `MessageInput.svelte` (`bg-mintify-chat`)
- **Chat / app background** — `src/routes/(app)/+layout.svelte` (`bg-mintify-chat`)
- **Left sidebar bg + hovers** — `src/lib/components/layout/Sidebar.svelte`
- **Sidebar chat item (active/selected/hover)** — `src/lib/components/layout/Sidebar/ChatItem.svelte`
- **"..." menu pill** (opaque, hides title behind icons; color matches the row) — `ChatItem.svelte` (the `from-mintify-pill*` gradient block)
- **Bolder sidebar text** — chat titles (`ChatItem.svelte`), date headers like "Heute" (`Sidebar.svelte`), "Chats"/folder labels (`src/lib/components/common/Folder.svelte` — shared, also affects workspace folders)
- **Bewertung panel (bg, border, header, hover)** — `src/lib/components/chat/JudgementPanel.svelte`
- **Text selection, links, form accent-color** — `src/app.css` (`MINTIFY brand accents` block)
- **Placeholder logo size + alignment** — `src/lib/components/chat/Placeholder.svelte`, `ChatPlaceholder.svelte`
- **Model dock bar** (see below) — `src/lib/components/chat/ModelDock.svelte`, mounted in `Navbar.svelte`

## Model dock bar

A dock-like horizontal icon bar for picking the model/"mode", added **alongside**
the existing dropdown model selector (the dropdown still works; the dock stays in
sync with it).

- **New component** `src/lib/components/chat/ModelDock.svelte`
  - Renders one circular avatar per model from the `$models` store, using each
    model's `info.meta.profile_image_url` (falls back to `/static/favicon.png`) —
    same image the dropdown uses. Set a model's icon in **Workspace → Models**.
  - Binds the same `selectedModels` array as the dropdown; clicking an icon does
    `selectedModels = [model.id]`, so dock ↔ dropdown selection is always synced.
    No `Chat.svelte` / pipeline / backend changes were needed.
  - Dock feel: hover scale, active model gets a ring + dot (`mintify-blue` light /
    `mintify-green` dark) and stays fully opaque; inactive icons are dimmed.
  - Background is a light MINTIFY green: `bg-mintify-green/35 border-mintify-green-dark/40`
    (light) and `bg-mintify-green-dark/20` (dark). **To adjust the green,** change
    the `/35` opacity (higher = greener, lower = lighter).
  - `home` prop: on the landing/new-chat page the dock is rendered bigger
    (`size-12` icons) and lower (`mt-6`); during an active chat it's compact
    (`size-9`). Transition is animated.
- **Mounted** in `src/lib/components/chat/Navbar.svelte`: a centered row directly
  below the main navbar row, gated by the existing `showModelSelector` flag, with
  `home={!history.currentId && !$chatId}` (same landing-state test the banners use).

## Apply changes

```bash
docker compose build open-webui && docker compose up -d open-webui
# or for live dev: npm run dev  → localhost:5173
```

**Gotchas:**
- Backend changes (`env.py`, `backend/open_webui/static/`) only take effect after a **rebuild** — a running container keeps the old build.
- Browser **favicon cache** is sticky and survives Cmd+Shift+R. Use a hard refresh / new tab / incognito to see a changed tab icon.
