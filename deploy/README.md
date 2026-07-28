# Deploying Postiz + WordPress staging on this host

Generated from `discover_environment.py`'s output for this specific Fedora
Silverblue / rootless Podman host. Two real findings from that output shaped
the decisions below -- both worth knowing before you touch these files.

## Networking: host, with one exception

The live DNS test in `discover_environment.py`'s output looked like a
failure (`RESULT: FAIL (aardvark-dns not resolving container names)`), but
reading the raw `nslookup` output it embeds shows the opposite: querying the
bare name `agency-diag-a` *did* resolve, to `agency-diag-a.dns.podman` /
`10.89.3.2` -- netavark's aardvark-dns injects `dns.podman` into each
container's own search domains, so application code using the bare name
works fine. The `NXDOMAIN` the script flagged was a *second*, unrelated
lookup attempt against the host's own `.Home` search domain, not a failure
of the first one. The script's "FAIL" verdict was a false negative from a
crude heuristic, not a real problem with this host's Podman networking.

That correction doesn't end up mattering for the final decision, though:
given the choice, you said to put it on the host. So every container in
this stack (Postiz's full 7-service stack, below) uses `Network=host` --
reachable at `127.0.0.1:<port>`, exactly like Ollama/ComfyUI/Paperless/Khoj
already are. Two consequences worth knowing:
- Every container's ports share **one port namespace with the host itself**
  -- there's no per-container isolation the way a bridge network gives you.
  Concretely: Postiz's own compose runs *two* separate Postgres instances
  (`postiz-postgres`, `temporal-postgresql`), both defaulting to 5432 --
  under host networking they'd collide, so `postiz-temporal-postgres.container`
  is remapped to 5433 via `Exec=postgres -p 5433` (see that file's comment).
- **WordPress is the one exception.** The official `wordpress` image
  hardcodes Apache to port 80 with no env var to change it, and rootless
  Podman can't reliably bind privileged (<1024) host ports under host
  networking without a host-wide sysctl change (`net.ipv4.ip_unprivileged_port_start`)
  -- not something to change for one container. So `wordpress.network` +
  `wordpress.container`/`wordpress-db.container` use a private bridge
  network instead, published at `127.0.0.1:8090`, via the same
  `rootlessport` mechanism already serving `paperless_default`/
  `khoj_default` on this host today. WordPress doesn't need to reach
  Ollama/ComfyUI directly anyway (only the agency's own Python process talks
  to it, over that published port), so isolating it costs nothing.

## Postiz needs more than Postgres+Redis now

Current Postiz (>= v2.12) added a **Temporal** dependency for background
workflows, which itself needs its own Postgres *and* Elasticsearch (for
Temporal's visibility store). That's 7 services total, fetched verbatim
from `gitroomhq/postiz-docker-compose`'s own `docker-compose.yaml` and
ported to Quadlet + host networking:

| Unit | Image | Host port |
|---|---|---|
| `postiz-postgres.container` | `postgres:17-alpine` | 5432 |
| `postiz-redis.container` | `redis:7.2` | 6379 |
| `postiz-temporal-postgres.container` | `postgres:16` | 5433 (remapped) |
| `postiz-temporal-elasticsearch.container` | `elasticsearch:7.17.27` | 9200 |
| `postiz-temporal.container` | `temporalio/auto-setup:1.28.1` | 7233 |
| `postiz.container` | `ghcr.io/gitroomhq/postiz-app:latest` | 5000 |

Two services from upstream's compose are **deliberately omitted**:
`temporal-ui` (its default port 8080 collides with Syncthing, already
running on this host at `127.0.0.1:8080`; it's an operator debugging
dashboard, not required for Postiz to function) and `spotlight` (an
optional Sentry debug profile). Neither affects Postiz's actual operation.

Elasticsearch's heap is capped at 256MB (`ES_JAVA_OPTS`, matching upstream's
own value exactly) -- still real, persistent RAM/CPU overhead alongside
Ollama/ComfyUI on one machine; worth knowing before enabling this on a
loaded box, though upstream ships it this small specifically for
single-machine deployments like this one.

## WordPress staging

| Unit | Image | Host port |
|---|---|---|
| `wordpress-db.container` | `mariadb:11` | (private network only) |
| `wordpress.container` | `wordpress:latest` | 8090 |

## Setup

1. Data lives on the NVMe `Storage` drive (`%h/Storage/agency-stack/...`),
   not the OS's shared btrfs partition (`%h` itself is on it) -- matches
   ComfyUI's own existing convention on this host and keeps the OS drive
   from filling up. Only the container code/Quadlet files under `%h/agency`
   are small enough that living on the OS partition doesn't matter.

2. Secrets are NOT committed to this repo. Copy the templates outside the
   repo and fill them in:
   ```bash
   mkdir -p ~/agency-stack/secrets
   cp deploy/postiz.env.example ~/agency-stack/secrets/postiz.env
   cp deploy/postiz-temporal-postgres.env.example ~/agency-stack/secrets/postiz-temporal-postgres.env
   cp deploy/wordpress.env.example ~/agency-stack/secrets/wordpress.env
   chmod 600 ~/agency-stack/secrets/*.env
   # fill in every blank value in all three files
   ```

3. Deploy this repo (code + Quadlet files) to `~/agency` -- matches
   `agency-run-all.service`'s existing `WorkingDirectory=%h/agency`
   convention, and `postiz-temporal.container` mounts
   `%h/agency/deploy/temporal-dynamicconfig` directly:
   ```bash
   mkdir -p ~/agency && cp -r . ~/agency
   ```

4. Install the Quadlet units and bring the stack up, in dependency order
   (each unit's own `Requires=`/`After=` will also pull in what it needs,
   but starting explicitly in order the first time makes failures easier to
   diagnose):
   ```bash
   mkdir -p ~/.config/containers/systemd
   cp deploy/systemd/*.container deploy/systemd/*.network ~/.config/containers/systemd/
   systemctl --user daemon-reload

   systemctl --user enable --now postiz-postgres.service postiz-redis.service \
     postiz-temporal-postgres.service postiz-temporal-elasticsearch.service
   systemctl --user enable --now postiz-temporal.service
   systemctl --user enable --now postiz.service

   systemctl --user enable --now wordpress-db.service
   systemctl --user enable --now wordpress.service
   ```

5. Verify: `curl http://localhost:5000` (Postiz), `curl http://localhost:8090`
   (WordPress), then set `POSTIZ_BASE_URL=http://localhost:5000` and
   `POSTIZ_API_KEY=...` (Settings -> Public API in the Postiz dashboard once
   it's up) in the agency's own `.env`, and run `python -m agency.cli
   preflight` to confirm the agency app itself can reach it.

## SELinux

`getenforce` reported `Enforcing` in the discovery output, so every
`Volume=` line in these units ends in `:Z` (private SELinux relabel) --
without it, the containerized processes would get permission-denied
reading/writing their own data directories under enforcing mode.

## One known risk, not yet verified against this exact Podman version

There's an open Podman issue (#17906) about `EnvironmentFile=` paths that
start with a systemd specifier (`%h/...`) not always being recognized as
absolute in some Quadlet versions. All the `EnvironmentFile=` lines here use
`%h/agency-stack/secrets/...`. If a container fails to start with an error
about a missing/unreadable env file, check `journalctl --user -u
<service>.service` first -- the fix, if this bites, is switching that one
line to the fully-expanded absolute path instead of `%h`.
