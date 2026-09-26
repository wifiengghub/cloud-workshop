#!/usr/bin/env bash
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Resolve ROOT_DIR: prefer the repo this script lives in (already cloned),
# then fall back to the legacy WORKSPACE_DIR clone behaviour.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_SCRIPT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_SCRIPT_ROOT/requirements.txt" && -d "$REPO_SCRIPT_ROOT/.git" ]]; then
    ROOT_DIR="$REPO_SCRIPT_ROOT"
    log_prefix="[start-linux]"
    printf '\n[start-linux] Using existing repo at: %s\n' "$ROOT_DIR"
else
    WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/workspaces}"
    REPO_URL="${REPO_URL:-https://github.com/arnabnexus/devicedatahub-end-to-end.git}"
    PROJECT_NAME="${PROJECT_NAME:-devicedatahub-end-to-end}"
    ROOT_DIR="$WORKSPACE_DIR/$PROJECT_NAME"
fi

VENV_DIR="$ROOT_DIR/.venv"
NAMESPACE="devicedatahub"
RUNTIME_DIR="$ROOT_DIR/.runtime"

log() {
    printf '\n[start-linux] %s\n' "$*"
}

fail() {
    printf '\n[start-linux] ERROR: %s\n' "$*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
install_prerequisites() {
    local missing=()
    command -v git    >/dev/null 2>&1 || missing+=(git)
    command -v python3 >/dev/null 2>&1 || missing+=(python3)
    command -v docker >/dev/null 2>&1 || missing+=(docker.io)
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv --help >/dev/null 2>&1 || missing+=(python3-venv)
    fi

    [[ "${#missing[@]}" -eq 0 ]] && return

    command -v apt-get >/dev/null 2>&1 || \
        fail "Missing packages: ${missing[*]}. Install them with your Linux package manager."
    command -v sudo >/dev/null 2>&1 || \
        fail "Missing packages: ${missing[*]}. Install them with apt-get as root."

    log "Installing prerequisites: ${missing[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y git python3 python3-venv ca-certificates curl docker.io
}

install_prerequisites

# ---------------------------------------------------------------------------
# Docker Engine
# ---------------------------------------------------------------------------
start_docker_engine() {
    docker info >/dev/null 2>&1 && return

    log "Starting Docker Engine"
    if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
        sudo systemctl enable --now docker
    elif command -v service >/dev/null 2>&1; then
        sudo service docker start
    else
        log "systemd/service unavailable; starting dockerd in the background"
        sudo nohup dockerd >/tmp/devicedatahub-dockerd.log 2>&1 </dev/null &
    fi

    for attempt in $(seq 1 30); do
        docker info >/dev/null 2>&1 && return
        sleep 1
    done

    fail "Docker Engine could not start. Check /tmp/devicedatahub-dockerd.log."
}

command -v docker >/dev/null 2>&1 || fail "Docker not found."
start_docker_engine

# ---------------------------------------------------------------------------
# Clone / pull (only when not already running from the repo root)
# ---------------------------------------------------------------------------
if [[ "$ROOT_DIR" != "$REPO_SCRIPT_ROOT" ]]; then
    mkdir -p "$(dirname "$ROOT_DIR")"
    if [[ -d "$ROOT_DIR/.git" ]]; then
        log "Clone exists; pulling latest: $ROOT_DIR"
        git -C "$ROOT_DIR" pull --ff-only || log "git pull failed (detached HEAD?); continuing with current state."
    elif [[ ! -e "$ROOT_DIR" ]]; then
        log "Cloning $REPO_URL -> $ROOT_DIR"
        git clone "$REPO_URL" "$ROOT_DIR"
    else
        fail "$ROOT_DIR exists but is not a Git repository. Move it or set WORKSPACE_DIR to a different path."
    fi

    [[ -d "$ROOT_DIR/.git" ]] || fail "Clone did not complete: $ROOT_DIR"
fi

log "Using project: $ROOT_DIR"

# ---------------------------------------------------------------------------
# Python virtual environment + dependencies
# ---------------------------------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating Python virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Activate the venv for the rest of this shell and all child processes.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
log "Installing Python requirements"
python -m pip install --upgrade pip -q
python -m pip install -r "$ROOT_DIR/requirements.txt" -q

# ---------------------------------------------------------------------------
# Simulator prompt (only when stdin is a terminal)
# ---------------------------------------------------------------------------
SIMULATE=false
if [[ -t 0 ]]; then
    read -r -p $'\n[start-linux] Enable local MQTT simulator and broker? [y/N]: ' simulation_answer || true
    if [[ "${simulation_answer:-}" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        SIMULATE=true
    fi
fi

# ---------------------------------------------------------------------------
# Kubernetes stack via startup.py
# ---------------------------------------------------------------------------
log "Starting the Kubernetes stack"
startup_args=(python3 "$ROOT_DIR/scripts/startup.py" --no-follow)
if [[ "$SIMULATE" == true ]]; then
    log "Simulation enabled: using helm/ai-flow/values.simulate.yaml"
    startup_args+=(--values "$ROOT_DIR/helm/ai-flow/values.simulate.yaml")
else
    log "Simulation disabled: using configured MQTT broker"
fi
"${startup_args[@]}"

# startup.py writes the kubeconfig to ~/.kube/config and patches it so kubectl
# can reach the kind API server from the host.  Export KUBECONFIG here so all
# subsequent kubectl calls in this script inherit the correct config
# (env-var changes inside the Python child process don't propagate back to bash).
export KUBECONFIG="$HOME/.kube/config"

# ---------------------------------------------------------------------------
# Locate kubectl
# ---------------------------------------------------------------------------
KUBECTL="$(command -v kubectl || true)"
[[ -z "$KUBECTL" && -x "$HOME/.local/bin/kubectl" ]] && KUBECTL="$HOME/.local/bin/kubectl"
[[ -n "$KUBECTL" ]] || fail "kubectl not found after startup.py completed."

# ---------------------------------------------------------------------------
# Wait for Services
# ---------------------------------------------------------------------------
log "Waiting for Kubernetes Services"
for attempt in $(seq 1 30); do
    if "$KUBECTL" get service ai-flow-grafana     -n "$NAMESPACE" >/dev/null 2>&1 \
    && "$KUBECTL" get service ai-flow-timescaledb -n "$NAMESPACE" >/dev/null 2>&1; then
        break
    fi
    if [[ "$attempt" == 30 ]]; then
        fail "Grafana or TimescaleDB Service did not become available."
    fi
    sleep 2
done

# ---------------------------------------------------------------------------
# Services are exposed via kind extraPortMappings (config/kind-cluster.yaml).
# NodePort 30300 → host port 3000  (Grafana)
# NodePort 30543 → host port 5433  (TimescaleDB)
# No kubectl port-forward is needed — ports are permanently bound at the
# kernel level for the lifetime of the kind cluster container.
# ---------------------------------------------------------------------------
log "Grafana:     http://localhost:3000  (admin / admin)"
log "TimescaleDB: localhost:5433, database: telemetry"
log ""
log "Streaming all Kubernetes component logs (Ctrl-C to exit)"
exec "$KUBECTL" logs \
    -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=ai-flow" \
    --all-containers=true \
    --max-log-requests=20 \
    --prefix \
    --tail=100 \
    -f

