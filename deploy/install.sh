#!/usr/bin/env bash
# Idempotent installer for the agency + Postiz + WordPress-staging stack.
#
# Safe to re-run: never overwrites an existing .env, secrets file, or org
# customer YAML. Does NOT start any containers or fill in secrets for you --
# those need real values first. Prints a manual-steps checklist at the end.
#
# Run from the repo root: ./deploy/install.sh
set -euo pipefail

AGENCY_HOME="${AGENCY_HOME:-$HOME/agency}"
DATA_ROOT="${DATA_ROOT:-$HOME/Storage/agency-stack}"
SECRETS_DIR="${SECRETS_DIR:-$HOME/agency-stack/secrets}"

log() { printf '\n==> %s\n' "$1"; }
warn() { printf '\n!! %s\n' "$1" >&2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log "Repo root: $REPO_ROOT"
log "Deploy target: $AGENCY_HOME"
log "Data root (off the OS drive): $DATA_ROOT"

# --- 1. Prerequisites -------------------------------------------------
command -v python3 >/dev/null || { warn "python3 not found on PATH"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    warn "python3 is $(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'); this project needs >=3.11"
    exit 1
}
if ! command -v podman >/dev/null; then
    warn "podman not found on PATH -- needed for illustrate/animate/compose and the Postiz/WordPress deploy (not for text-only commands like draft/run-all --dry-run)"
fi

# The Postiz stack alone is 6 containers, including Elasticsearch and a
# second Postgres for Temporal -- on a host also running Ollama, ComfyUI,
# Paperless, Khoj, and n8n, headroom is the thing most likely to bite
# silently (OOM-killed containers restart-looping rather than failing
# loudly). Check now, not after everything's already enabled.
if [ -r /proc/meminfo ]; then
    MEM_AVAILABLE_KB=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
    MEM_TOTAL_KB=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
    MEM_AVAILABLE_GB=$((MEM_AVAILABLE_KB / 1024 / 1024))
    MEM_TOTAL_GB=$((MEM_TOTAL_KB / 1024 / 1024))
    log "System memory: ${MEM_AVAILABLE_GB}GB available of ${MEM_TOTAL_GB}GB total"
    if [ "$MEM_AVAILABLE_KB" -lt $((6 * 1024 * 1024)) ]; then
        warn "Less than 6GB available right now. Elasticsearch/Temporal/2x Postgres/Redis/Postiz/MariaDB want roughly that much just to start, before Ollama or ComfyUI load a model. Close some of what's already running, or expect OOM restart-looping rather than a clean failure -- check with 'podman stats' / 'free -h' after bringing the stack up."
    fi
else
    warn "Can't read /proc/meminfo to check RAM headroom (not Linux?) -- the Postiz stack alone is 6 containers including Elasticsearch; check free memory yourself before enabling it."
fi

# --- 2. Copy the repo to AGENCY_HOME (matches agency-run-all.service's
#        %h/agency convention) -- skipped if already running from there.
#        Never deletes anything already present at the destination. ---
if [ "$REPO_ROOT" != "$AGENCY_HOME" ]; then
    log "Copying repo to $AGENCY_HOME"
    mkdir -p "$AGENCY_HOME"
    if command -v rsync >/dev/null; then
        rsync -a --exclude .venv --exclude __pycache__ --exclude .git --exclude state --exclude '*.pyc' \
            "$REPO_ROOT"/ "$AGENCY_HOME"/
    else
        cp -r "$REPO_ROOT"/. "$AGENCY_HOME"/
    fi
else
    log "Already running from $AGENCY_HOME, skipping copy"
fi
cd "$AGENCY_HOME"

# --- 3. Python virtualenv + dependencies ------------------------------
if [ ! -d .venv ]; then
    log "Creating virtualenv"
    python3 -m venv .venv
fi
log "Installing dependencies (pip install -e .[dev])"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"

# --- 4. .env ------------------------------------------------------------
if [ ! -f .env ]; then
    log "Creating .env from .env.example -- fill in the blanks before running anything live"
    cp .env.example .env
    chmod 600 .env
else
    log ".env already exists, leaving it alone"
fi

# --- 5. Data directories on the separate drive, not the OS partition ---
log "Creating data directories under $DATA_ROOT"
mkdir -p "$DATA_ROOT"/agency/knowledge "$DATA_ROOT"/agency/state "$DATA_ROOT"/agency/assets "$DATA_ROOT"/agency/content
mkdir -p "$DATA_ROOT"/postiz/postgres "$DATA_ROOT"/postiz/redis "$DATA_ROOT"/postiz/temporal-postgres \
    "$DATA_ROOT"/postiz/temporal-elasticsearch "$DATA_ROOT"/postiz/config "$DATA_ROOT"/postiz/uploads
mkdir -p "$DATA_ROOT"/wordpress/db "$DATA_ROOT"/wordpress/html

# --- 6. Secrets templates (outside the repo, never committed) ----------
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
for f in postiz.env postiz-temporal-postgres.env wordpress.env; do
    if [ ! -f "$SECRETS_DIR/$f" ]; then
        log "Creating $SECRETS_DIR/$f from template -- fill in the blanks"
        cp "deploy/${f}.example" "$SECRETS_DIR/$f"
        chmod 600 "$SECRETS_DIR/$f"
    else
        log "$SECRETS_DIR/$f already exists, leaving it alone"
    fi
done

# --- 7. org/ customer file ------------------------------------------
if ! find org -maxdepth 1 -name '*.yaml' ! -name 'example_customer.yaml' 2>/dev/null | grep -q .; then
    warn "No customer file under org/ yet besides the example. Copy org/example_customer.yaml to org/<your-customer>.yaml and fill it in before running draft/run-all."
fi

# --- 8. Quadlet units + the run-all timer ------------------------------
log "Installing Quadlet units to ~/.config/containers/systemd"
mkdir -p "$HOME/.config/containers/systemd"
cp deploy/systemd/*.container deploy/systemd/*.network "$HOME/.config/containers/systemd/"

log "Installing agency-run-all timer to ~/.config/systemd/user"
mkdir -p "$HOME/.config/systemd/user"
cp deploy/systemd/agency-run-all.service deploy/systemd/agency-run-all.timer "$HOME/.config/systemd/user/"

if systemctl --user daemon-reload 2>/dev/null; then
    log "systemctl --user daemon-reload succeeded"
else
    warn "systemctl --user daemon-reload failed (no systemd user session in this shell?) -- run it yourself once logged into a real session: systemctl --user daemon-reload"
fi

log "Running 'agency preflight' (informational -- most checks fail until the checklist below is done)"
.venv/bin/python -m agency.cli preflight || true

cat <<CHECKLIST

==================== Manual steps still required ====================
1. Fill in every blank value in:
     $AGENCY_HOME/.env
     $SECRETS_DIR/postiz.env
     $SECRETS_DIR/postiz-temporal-postgres.env
     $SECRETS_DIR/wordpress.env
   Random secrets (JWT_SECRET, DB passwords): openssl rand -hex 32

2. Copy org/example_customer.yaml to org/<your-customer>.yaml under
   $AGENCY_HOME and fill in real brand info (Postiz group/integration IDs
   come after step 4).

3. Bring up Postiz + WordPress, in order (see deploy/README.md for why):
     systemctl --user enable --now postiz-postgres.service postiz-redis.service \\
       postiz-temporal-postgres.service postiz-temporal-elasticsearch.service
     systemctl --user enable --now postiz-temporal.service
     systemctl --user enable --now postiz.service
     systemctl --user enable --now wordpress-db.service wordpress.service

4. Once Postiz is up (http://localhost:5000): create an account, connect
   integrations, generate a Public API key (Settings -> Public API), put it
   in .env as POSTIZ_API_KEY, then fill in postiz_group_id/integration_ids
   in your org YAML using:
     curl -H "Authorization: \$POSTIZ_API_KEY" http://localhost:5000/public/v1/groups
     curl -H "Authorization: \$POSTIZ_API_KEY" http://localhost:5000/public/v1/integrations

5. Generate a Paperless-ngx API token (My Profile -> API Token) and put it
   in .env as PAPERLESS_API_TOKEN, if you want that knowledge source.

6. Re-run this preflight check -- it should be all green once 1-5 are done:
     cd $AGENCY_HOME && .venv/bin/python -m agency.cli preflight

7. Try one post end-to-end before touching the scheduler:
     .venv/bin/python -m agency.cli draft --org org/<your-customer>.yaml \\
       --brand <your-brand> --topic "test post" --dry-run

8. Once you've tested manually, enable the scheduled run:
     systemctl --user enable --now agency-run-all.timer
=======================================================================
CHECKLIST
