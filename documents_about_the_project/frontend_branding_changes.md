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
  - **Style — segmented dock:** a white / `gray-850` rounded-**full** pill
    (`border-gray-100` / `gray-800`, soft shadow). Each model is its own circular
    segment with a ring: inactive = `ring-2 ring-gray-200` (`gray-700` dark) and
    dimmed to `opacity-70`; **active = `ring-[3px] ring-mintify-green` + `scale-110`**
    (the ring + scale is the only active indicator). Hover lifts inactive icons
    (`scale-105`, full opacity).
  - `home` prop: on the landing/new-chat page the dock is rendered bigger
    (`size-12` icons) and lower (`mt-6`); during an active chat it's compact
    (`size-9`). Transition is animated.
- **Mounted** in `src/lib/components/chat/Navbar.svelte`: a centered row directly
  below the main navbar row, gated by the existing `showModelSelector` flag, with
  `home={!history.currentId && !$chatId}` (same landing-state test the banners use).

### Model icon image specs

The dock crops each image to a **circle** (`rounded-full` + `object-cover`) and
shows it small (`size-9` = 36 px in chat, `size-12` = 48 px on home; 2–3× on
retina). Set it per model in **Workspace → Models → profile image**.

- **Square (1:1)**, **512×512 px**, **PNG** (transparent) or SVG.
- Background **transparent** or a solid brand color (blue `#1C3E73` / green `#A7F57E`).
- **One simple, bold, centered symbol** kept within the middle ~75% (the circular
  crop eats the corners); high contrast so it reads on both the light and dark pill.
- Use the **same style** (background, stroke weight, padding) across all models so
  the bar looks like a coherent set.

**Fill-in-the-blank generation prompt** (swap the first blank per model):

> A flat, minimalist app icon of a **______** [e.g. graduation cap / magnifying
> glass / shopping bag / person], centered on a **1:1 square** canvas at **512×512 px**.
> Single bold symbol, clean thick strokes, **generous empty padding around the
> symbol** (subject fills only the center ~75%, safe for a circular crop). Solid
> **[MINTIFY blue #1C3E73 / or transparent]** background, symbol in
> **[white / MINTIFY green #A7F57E]**, high contrast. No text, no shadows, no
> gradients, no photographic detail — simple vector-style flat design, legible when
> scaled down to a tiny circular avatar.

## Chat UI changes (2026-08-10)

Four independent changes — each can be reverted on its own.

### 1. Auto-generated chat titles without emoji

The title the sidebar shows comes from a separate LLM call whose prompt used to
ask for an emoji ("3-5 word title **with an emoji**", 3 of 6 examples emoji-led).

- `backend/open_webui/config.py` → `DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE`:
  dropped "with an emoji", inverted the "Use emojis…" guideline into a ban,
  removed the emoji from the examples.
- `backend/open_webui/utils/middleware.py` → `EMOJI_PATTERN` constant + one strip
  where the title is parsed (~line 1020), so an emoji the model adds anyway never
  reaches the DB or the `chat:title` socket event.
- Only affects **new** titles — existing chat titles keep their emoji.
- ⚠️ The prompt must keep starting with `### Task:` — `pipelines/mintify_pipeline.py`
  `_is_meta_task()` uses that prefix to route title generation to `/api/passthrough`
  instead of the full RAG pipe.
- **Revert:** restore both blocks. (Admin → Settings → Interface → "Title Generation
  Prompt" also overrides the code default when non-empty.)

### 2. Sidebar "Neuer Chat" button works again

- `src/lib/components/chat/Navbar.svelte` → re-added the button with
  `id="new-chat-button"` calling `initNewChat()`.
- Why: the custom chat navbar had dropped it, but `Sidebar.svelte`,
  `ModelSelector/Selector.svelte` and `routes/(app)/+layout.svelte` all start a new
  chat via `document.getElementById('new-chat-button').click()`. With no such
  element the click was a no-op — only the URL changed, the old chat stayed on
  screen. (The stock `#new-chat-button` still exists in `layout/Navbar.svelte`, but
  that file is only used by the share route `/s/[id]`.)
- Visible only when the sidebar is collapsed (`md:hidden` while open) — stock OWUI
  behaviour; a `display:none` element still receives programmatic clicks.
- **Revert:** delete the button — the sidebar entry, the temporary-chat toggle and
  Ctrl+Shift+O stop working again.

### 3. Navbar buttons match the model dock height

- `ModelDock.svelte` → module script exports `DOCK_HEIGHT` / `DOCK_SQUARE`
  (`2.875rem` on the landing page, `2.125rem` inside a chat = avatar + padding +
  border) and applies them to the pills — visually a no-op, it just pins the number.
- `chat/Navbar.svelte` → `isHome` / `navButtonHeight` / `navButtonSquare` drive the
  New-Chat, "Richter Modus" and user-avatar buttons; icon, avatar and label sizes
  scale with the mode, all three use the dock's `transition-all duration-150 ease-out`.
- Shapes kept: Richter Modus stays a `rounded-full` pill, the avatar stays circular.
  The icon buttons use `size-[…]`, not `aspect-square` — the user button's `w-full`
  would override an aspect ratio.
- Not touched: the ☰ sidebar-toggle on the far left is still 36 px.
- **Revert:** drop the two exports and restore the fixed `px-*` / `py-*` classes.

### 4. Only UI-created models appear in the model bar

- `src/lib/utils/index.ts` → `isUiCreatedModel()` (`preset === true || arena === true`)
  and `getSelectableModels()` (falls back to the full list if nothing was built in
  the UI, so the picker is never empty).
- Applied in `ModelDock.svelte`, `ModelSelector.svelte` (the mobile dropdown) and
  `Chat.svelte` → `initNewChat()`, which now validates the selection against the
  filtered list so a saved default pointing at a raw model falls through to the
  first visible one instead of leaving the bar with nothing highlighted.
- The backend marks Workspace models `preset: true` and arena models `arena: true`
  (`backend/open_webui/utils/models.py`); a raw pipeline/connection model has
  neither → hidden. Verified live: Arena Model / KI Lehrer / Shopping Mode /
  Suchmodell shown, `MINTIFY – cyankiwi/Qwen3.5-27B-AWQ-4bit` hidden.
- `$models` itself is untouched — the raw model is still available as a base model
  in Workspace → Models, in the playground and in admin settings.
- Opening an **old** chat that ran on a hidden model keeps that model and shows no
  highlighted pill (deliberate — existing chats are not rewritten).
- **Revert:** use `$models` directly in the three call sites; the helpers can stay.

## Apply changes

```bash
docker compose build open-webui && docker compose up -d open-webui
# or for live dev: npm run dev  → localhost:5173
```

**Gotchas:**
- Backend changes (`env.py`, `backend/open_webui/static/`) only take effect after a **rebuild** — a running container keeps the old build.
- Browser **favicon cache** is sticky and survives Cmd+Shift+R. Use a hard refresh / new tab / incognito to see a changed tab icon.
