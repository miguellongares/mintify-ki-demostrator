# Debugging the RAG Backend with debugpy

This document explains how the Python debugger (debugpy) was added to the `mintify-rag-service` container and how to use it for live debugging from VS Code.

---

## What was set up and why

The rag-backend is the most logic-heavy service in the stack (embedding, retrieval, LLM calls, judging). To be able to set breakpoints, step through code, and inspect variables during a live chat request, we added [debugpy](https://github.com/microsoft/debugpy) — the same engine VS Code uses for local Python debugging, but running inside Docker.

The setup avoids touching the normal `docker-compose.yml`. Instead, a separate override file (`docker-compose.debug.yml`) activates debug mode only when explicitly requested.

---

## Files created or modified

| File | Change |
|---|---|
| `rag-backend/requirements.txt` | Added `debugpy>=1.8.0` |
| `docker-compose.debug.yml` | New file — overrides the rag-backend command to run under debugpy |
| `.vscode/launch.json` | New file — VS Code attach configuration |

### `docker-compose.debug.yml`

```yaml
services:
  mintify-rag-service:
    command: >
      python -m debugpy
        --listen 0.0.0.0:5678
        --wait-for-client
        -m uvicorn app.main:app
        --host 0.0.0.0
        --port 8080
    ports:
      - "5678:5678"
```

- `--listen 0.0.0.0:5678` — debugpy listens for an incoming debugger connection on port 5678
- `--wait-for-client` — the container freezes at startup until VS Code attaches (uvicorn does not start until then)
- No `--reload` — uvicorn's file-watcher conflicts with debugpy (it spawns a subprocess that the debugger loses track of)

### `.vscode/launch.json`

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Attach to rag-backend (Docker)",
      "type": "debugpy",
      "request": "attach",
      "connect": {
        "host": "localhost",
        "port": 5678
      },
      "pathMappings": [
        {
          "localRoot": "${workspaceFolder}/rag-backend/app",
          "remoteRoot": "/app/app"
        }
      ]
    }
  ]
}
```

`pathMappings` tells VS Code how to translate between the path on your laptop and the path inside the container, so breakpoints set in local files land correctly in the running process.

---

## How to use it

### 1. Rebuild the image (only needed once, or after changing `requirements.txt`)

```bash
docker compose build mintify-rag-service
```

### 2. Start the stack in debug mode

```bash
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
```

The `-f` flags merge the two compose files. The debug override replaces only the rag-backend's command and adds port 5678. All other containers (`qdrant`, `pipelines`, `open-webui`) are unaffected.

The `mintify-rag-service` container will start and then pause, waiting for VS Code to connect.

### 3. Attach VS Code

1. Open VS Code in the project folder
2. Open the **Run & Debug** panel (`Cmd+Shift+D`)
3. Select **"Attach to rag-backend (Docker)"** from the dropdown
4. Press the green play button or `F5`

The container unblocks and uvicorn finishes starting. You will see this in the Debug Console:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080
```

### 4. Set breakpoints and trigger them

Open any file under `rag-backend/app/` in VS Code and click to the left of a line number to set a breakpoint (red dot). Then send a chat message in the browser at `http://localhost:3000`. Execution will pause at the breakpoint.

Useful files to explore:

| File | What happens there |
|---|---|
| `routers/rag.py` | Entry point for every chat message |
| `services/retriever.py` | Qdrant vector search |
| `services/embedder.py` | Query → vector conversion |
| `services/llm_client.py` | Streaming call to vLLM |
| `routers/judge.py` | Bewertung (judge) requests |

### 5. Debugger controls

| Key | Action |
|---|---|
| `F5` | Continue to next breakpoint |
| `F10` | Step over (next line, don't enter functions) |
| `F11` | Step into (enter the called function) |
| `Shift+F11` | Step out (finish current function, return to caller) |

The **Variables** panel on the left shows all local and global variables at the current line. The **Debug Console** lets you evaluate any Python expression live (e.g. type `len(chunks)` or `query` to inspect values).

### 6. Return to normal mode

```bash
docker compose up -d
```

Running without `-f docker-compose.debug.yml` starts the rag-backend normally (plain uvicorn, no debugpy, `--reload` active).

---

## Known limitations

- **No hot-reload in debug mode** — because `--reload` conflicts with debugpy. To test a code change while debugging, stop the container, edit the file, and restart with the debug command.
- **`--wait-for-client` blocks the health check** — while waiting for VS Code to attach, the rag-backend's health check fails. This is normal; dependent containers (`pipelines`) may show "Waiting" until you attach.
- **RunPod tunnel errors at startup are expected** — the `[Errno 101] Network is unreachable` log for port 8002 appears because the RunPod SSH tunnel is not running locally. It does not affect debugging.
