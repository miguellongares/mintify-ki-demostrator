# Security: Docker ports exposed to the internet (Qdrant & co.)

Date found: 2026-10-23. Server: `162.55.47.233` (`ubuntu-16gb-fsn1-2`), project in `/home/mintify/mintify-ki-demonstrator`.

> **Status:** DEPLOYED on 2026-10-23. The server's `docker-compose.yml` was replaced with this repo's version (backup: `/root/docker-compose.yml.bak-20261023` on the server) and the containers were recreated. Verified: from outside, 6333/6334/9099/8088/3000 no longer answer; the site and `/rag-api/api/health` still work through Caddy; Qdrant works on the server's loopback. The change is still uncommitted in git, so commit and push it, otherwise the server's working tree differs from `main`.

---

## 1. What the issue was

A colleague found that Qdrant is reachable without authentication. I confirmed it from outside the server:

```bash
curl http://162.55.47.233:6333/collections
# -> {"result":{"collections":[{"name":"mintify_rag"}]},"status":"ok"}   (HTTP 200, no login)
```

### Why it happened

`docker-compose.yml` had:

```yaml
qdrant:
  ports:
    - "6333:6333"
    - "6334:6334"
```

`"6333:6333"` means "publish on **all** host interfaces" (`0.0.0.0` and `::`). Two things made it worse:

1. **Qdrant has no API key** configured, so anyone who reaches the port has full access.
2. **UFW does not protect Docker ports.** Docker publishes ports by writing its own iptables rules (nat/FORWARD), which are evaluated *before* UFW's INPUT rules. So UFW's "Default: deny incoming" never applied to published container ports, and there was no rule for 6333 anyway. `DOCKER-USER` (the chain meant for custom filters) was empty.

### Impact

Anyone on the internet could:
- read all indexed document chunks (including per-user personal-file chunks and their `user_id`s),
- delete, overwrite or poison the `mintify_rag` collection via REST (6333) or gRPC (6334), which would break RAG.

### Other ports with the same problem

| Port | Service | Needs to be public? | Notes |
|---|---|---|---|
| 6333 / 6334 | qdrant | No | no auth at all |
| 9099 | pipelines | No | only the pipelines API key protected it |
| 8088 | rag-service | No | Caddy proxies `/rag-api/*` to localhost:8088 |
| 3000 | open-webui | No | Caddy proxies everything else to localhost:3000; UFW also had an explicit allow for 3000 |

Inside Docker everything talks via service names (`http://qdrant:6333`, `http://pipelines:9099`, `http://open-webui:8080`), so none of these host-published ports are used by the stack itself.

---

## 2. The situation before the fix

- Public: 6333, 6334, 9099, 8088, 3000 (via Docker) plus 80/443 (Caddy), 22 (SSH).
- Caddy (`/etc/caddy/Caddyfile`) already proxies everything through `localhost:8088` and `localhost:3000`, so users never needed the direct ports.

## 3. The fix

Bind every published container port to loopback only, so it is reachable from the server itself (Caddy, SSH, `curl localhost`) but not from the internet:

```yaml
qdrant:
  ports:
    - "127.0.0.1:6333:6333"
    - "127.0.0.1:6334:6334"
rag-service:
  ports:
    - "127.0.0.1:8088:8080"
pipelines:
  ports:
    - "127.0.0.1:9099:9099"
open-webui:
  ports:
    - "127.0.0.1:3000:8080"
```

Why this and not "just a firewall rule"? Because UFW can't filter Docker-published ports; binding to `127.0.0.1` removes the exposure at the source, with nothing to forget or reset.

Local dev is unaffected: the browser on your laptop still reaches `localhost:3000` and `localhost:8088`.

Not done (optional hardening): a Qdrant API key (`QDRANT__SERVICE__API_KEY` + `api_key=` in the rag-backend's `QdrantClient`). With loopback binding it is defense in depth, not a necessity.

Out of scope (other host processes, not Docker, not part of MINTIFY): something listens on `0.0.0.0:5002` (python) and `0.0.0.0:8080` (uvicorn), both allowed in UFW. Worth checking with the colleague whether they are meant to be public.

---

## 4. How to deploy (on the server)

Containers are recreated, so expect a few seconds of downtime per service. Data lives in bind mounts under `./data/` and is untouched.

```bash
ssh -i ~/.ssh/.ssh_gpu/id_ed25519 root@162.55.47.233
cd /home/mintify/mintify-ki-demonstrator

cp docker-compose.yml /root/docker-compose.yml.bak      # backup
git pull                                                # after you commit+push the compose change
# (or copy the changed docker-compose.yml over manually)

docker compose config -q                                # validates the file
docker compose up -d                                    # recreates only containers whose ports changed
docker ps --format '{{.Names}}\t{{.Ports}}'             # every port must now show 127.0.0.1:...
```

Optional: the UFW allow rule for 3000 is now meaningless; remove it with `ufw delete allow 3000/tcp` (and the v6 one).

Rollback: restore `/root/docker-compose.yml.bak` and run `docker compose up -d`.

## 5. How to verify

From your laptop (all must time out or be refused):

```bash
for p in 6333 6334 9099 8088 3000; do
  curl -s -m5 -o /dev/null -w "$p -> HTTP %{http_code}\n" http://162.55.47.233:$p/ || echo "$p -> closed"
done
```

On the server (must still work):

```bash
curl -s localhost:6333/collections         # qdrant via loopback
curl -sI https://mintify.verbraucherinformatik.de | head -1   # site via Caddy
```

Then log in, ask a RAG question, and check `docker logs mintify-rag-service` for errors.

## 6. Lessons

- `ports: "X:Y"` in Docker Compose means *public*. Use `"127.0.0.1:X:Y"` unless a port must be public.
- UFW alone does not protect Docker-published ports.
- Databases (Qdrant, Postgres, Redis...) should never be published publicly without auth.
- Re-check with `ss -tlnp` and an external `curl` after any compose change.

---

## 7. Follow-up (2026-10-23): RunPod vLLM on port 8002 was public

**Issue:** the SSH tunnel to RunPod (`scripts/init_models.sh`, `-o GatewayPorts=yes -L 0.0.0.0:8002:...`) listens on all interfaces. Ports 8006 and 5001 were protected by UFW rules (allow only from Docker networks), but 8002 was reachable by anyone: `curl http://162.55.47.233:8002/v1/models` returned the model list, with no auth, so anyone could use the GPU. Cause: two hand-added **raw iptables** rules, `-A INPUT -p tcp --dport 8002 -j ACCEPT`, ahead of UFW's chains. They were not in UFW and not persisted (they would have vanished on reboot).

**Fix (server):**
```bash
ufw allow from 172.22.0.0/16 to any port 8002 proto tcp   # compose network
ufw allow from 172.17.0.0/16 to any port 8002 proto tcp   # default bridge (host.docker.internal)
iptables -D INPUT -p tcp -m tcp --dport 8002 -j ACCEPT    # run twice: removed both world-open rules
```
Same pattern as 8006/5001, and now persistent via UFW.

**Verified:** 8002 is closed from outside; rag-service and webui containers still reach it (HTTP 200); `/api/judge` through the public site still gets answers from vLLM.

**Left as is:** `iptables -A INPUT --dport 8004 -j ACCEPT` and `-A FORWARD --dport 8002 -j ACCEPT` are still open, but nothing listens on 8004. Remove the 8004 rule if the second vLLM is not planned.
