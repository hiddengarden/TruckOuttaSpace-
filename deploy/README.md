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

`./deploy/install.sh`, run from the repo root, automates everything that
doesn't require a real secret value or a running Postiz: copies the repo to
`~/agency` (matching `agency-run-all.service`'s `WorkingDirectory=%h/agency`
convention -- skipped if you're already running from there), creates a
venv and installs dependencies, creates `.env` from the template, creates
every data directory under `~/Storage/agency-stack` (the NVMe drive, not
the OS's shared btrfs partition -- matches ComfyUI's own convention and
keeps the OS drive from filling up), copies the three secrets templates to
`~/agency-stack/secrets/` (outside the repo, never committed), installs the
Quadlet units to `~/.config/containers/systemd`, and runs
`systemctl --user daemon-reload`. It's idempotent -- safe to re-run, and it
never overwrites an `.env`, secrets file, or org customer YAML that already
exists. It prints a numbered checklist of what's left (filling in secrets,
bringing the containers up in dependency order, generating a Postiz API
key, etc.) since none of that can be automated without real values from
you.

```bash
./deploy/install.sh
```

The checklist it prints covers the same steps that used to be spelled out
manually here -- follow it. The one thing worth calling out ahead of time:
containers come up in dependency order (`postiz-postgres`/`postiz-redis`/
`postiz-temporal-postgres`/`postiz-temporal-elasticsearch` first, then
`postiz-temporal`, then `postiz`; `wordpress-db` before `wordpress`) --
each unit's own `Requires=`/`After=` will also pull in what it needs, but
starting explicitly in order the first time makes a failure easier to
diagnose than letting systemd's dependency resolution do it silently.

Verify once it's up: `curl http://localhost:5000` (Postiz), `curl
http://localhost:8090` (WordPress), then `python -m agency.cli preflight`
from `~/agency` to confirm the agency app itself can reach everything.

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
