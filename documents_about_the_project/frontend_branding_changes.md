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

Change a color here once → it updates everywhere.

## Logos / images

All in `static/`. Swap the files (keep the names) — no code change needed.
- `favicon.png`, `static/favicon.png` — sidebar/header/avatar icon
- `static/splash.png`, `static/splash-dark.png` — loading screen logo
- `static/favicon.ico`, `favicon.svg`, `favicon-96x96.png`, `apple-touch-icon.png`, `web-app-manifest-{192,512}.png` — tab / PWA icons
- Source artwork: `static/mintify_circle.png`, `static/Square-logo-Blue.png`

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
- **Bewertung panel (bg, border, header, hover)** — `src/lib/components/chat/JudgementPanel.svelte`
- **Text selection, links, form accent-color** — `src/app.css` (`MINTIFY brand accents` block)
- **Placeholder logo size + alignment** — `src/lib/components/chat/Placeholder.svelte`, `ChatPlaceholder.svelte`

## Apply changes

```bash
docker compose build open-webui && docker compose up -d open-webui
# or for live dev: npm run dev  → localhost:5173
```
