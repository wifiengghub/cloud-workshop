#!/usr/bin/env bash
set -Eeuo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/workspaces}"
REPO_URL="${REPO_URL:-https://github.com/arnabnexus/devicedatahub-end-to-end.git}"
PROJECT_NAME="${PROJECT_NAME:-devicedatahub-end-to-end}"
ROOT_DIR="$WORKSPACE_DIR/$PROJECT_NAME"
VENV_DIR="$ROOT_DIR/.venv"
NAMESPACE="devicedatahub"
GRAFANA_FORWARD="service/ai-flow-grafana 3000:3000"
TIMESCALE_FORWARD="service/ai-flow-timescaledb 5433:5432"
RUNTIME_DIR="$ROOT_DIR/.runtime"

log() {
    printf '\n[start-linux] %s\n' "$*"
}

fail() {
    printf '\n[start-linux] ERROR: %s\n' "$*" >&2
    exit 1
}

install_wsl_prerequisites() {
    local missing=()
    command -v git >/dev/null 2>&1 || missing+=(git)
    command -v python3 >/dev/null 2>&1 || missing+=(python3)
    command -v docker >/dev/null 2>&1 || missing+=(docker.io)
    if command -v python3 >/dev/null 2>&1; then
        python3 -m venv --help >/dev/null 2>&1 || missing+=(python3-venv)
    fi

    if [[ "${#missing[@]}" -eq 0 ]]; then
        return
    fi

    command -v apt-get >/dev/null 2>&1 || fail "Missing WSL packages: ${missing[*]}. Install them with your Linux package manager."
    command -v sudo >/dev/null 2>&1 || fail "Missing WSL packages: ${missing[*]}. Install them with apt-get as root."

    log "Installing WSL prerequisites: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y git python3 python3-venv ca-certificates curl docker.io
}

install_wsl_prerequisites

start_docker_engine() {
    if docker info >/dev/null 2>&1; then
        return
    fi

    log "Starting Docker Engine"
    if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
        sudo systemctl enable --now docker
    elif command -v service >/dev/null 2>&1; then
        sudo service docker start
    else
        log "systemd/service unavailable; starting dockerd in the background"
        sudo nohup dockerd >/tmp/devicedatahub-dockerd.log 2>&1 < /dev/null &
    fi

    for attempt in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
            return
        fi
        sleep 1
    done

    fail "Docker Engine could not start. Check /tmp/devicedatahub-dockerd.log and WSL systemd/cgroup support."
}

command -v docker >/dev/null 2>&1 || fail "Docker Engine installation failed."
start_docker_engine

if command -v gh >/dev/null 2>&1; then
    log "GitHub CLI detected. Public HTTPS cloning does not require gh auth login."
else
    log "GitHub CLI is not required for this public HTTPS repository; using git clone."
fi

mkdir -p "$WORKSPACE_DIR"
if [[ -d "$ROOT_DIR/.git" ]]; then
    log "Clone exists; skipping clone: $ROOT_DIR"
    log "Pulling latest changes"
    git -C "$ROOT_DIR" pull --ff-only
elif [[ ! -e "$ROOT_DIR" ]]; then
    log "Clone not found; cloning $REPO_URL"
    git clone "$REPO_URL" "$ROOT_DIR"
else
    fail "$ROOT_DIR exists but is not a Git repository. Move it or set WORKSPACE_DIR."
fi

if [[ ! -d "$ROOT_DIR/.git" ]]; then
    fail "Clone did not complete successfully: $ROOT_DIR"
fi

cd "$ROOT_DIR"
log "Using project: $ROOT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating Python virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Activate the environment for the rest of this shell and all child commands.
source "$VENV_DIR/bin/activate"
log "Installing Python requirements"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT_DIR/requirements.txt"

SIMULATE=false
if [[ -t 0 ]]; then
    read -r -p "Enable local MQTT simulator and broker? [y/N]: " simulation_answer
    if [[ "$simulation_answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        SIMULATE=true
    fi
fi

log "Starting the Kubernetes stack"
startup_args=(scripts/startup.py --no-follow)
if [[ "$SIMULATE" == true ]]; then
    log "Simulation enabled: using helm/ai-flow/values.simulate.yaml"
    startup_args+=(--values helm/ai-flow/values.simulate.yaml)
else
    log "Simulation disabled: using the configured ngrok MQTT broker"
fi
python "${startup_args[@]}"

KUBECTL="$(command -v kubectl || true)"
if [[ -z "$KUBECTL" && -x "$HOME/.local/bin/kubectl" ]]; then
    KUBECTL="$HOME/.local/bin/kubectl"
fi
[[ -n "$KUBECTL" ]] || fail "kubectl was not found after startup.py completed."

log "Waiting for Kubernetes Services"
for attempt in $(seq 1 30); do
    if "$KUBECTL" get service ai-flow-grafana -n "$NAMESPACE" >/dev/null 2>&1 \
        && "$KUBECTL" get service ai-flow-timescaledb -n "$NAMESPACE" >/dev/null 2>&1; then
        break
    fi
    if [[ "$attempt" == 30 ]]; then
        fail "Grafana or TimescaleDB Service did not become available."
    fi
    sleep 2
done

run_forward() {
    local title="$1"
    local resource="$2"
    local ports="$3"
    local command="cd $(printf '%q' "$ROOT_DIR") && echo 'Forwarding $resource $ports' && '$KUBECTL' port-forward '$resource' '$ports' --namespace='$NAMESPACE'"

    if command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal --title="$title" -- bash -lc "$command; exec bash" &
    elif command -v konsole >/dev/null 2>&1; then
        konsole --new-tab -p tabtitle="$title" -e bash -lc "$command; exec bash" &
    elif command -v x-terminal-emulator >/dev/null 2>&1; then
        x-terminal-emulator -T "$title" -e bash -lc "$command; exec bash" &
    elif command -v xterm >/dev/null 2>&1; then
        xterm -T "$title" -e bash -lc "$command; exec bash" &
    else
        return 1
    fi
}

start_background_forward() {
    local name="$1"
    local resource="$2"
    local ports="$3"
    mkdir -p "$RUNTIME_DIR"
    nohup "$KUBECTL" port-forward "$resource" "$ports" --namespace="$NAMESPACE" \
        >"$RUNTIME_DIR/$name.port-forward.log" 2>&1 < /dev/null &
    local pid=$!
    printf '%s\n' "$pid" >"$RUNTIME_DIR/$name.port-forward.pid"
    log "$name port-forward started in background (PID $pid)"
}

follow_logs() {
    local log_args=(
        logs
        -n "$NAMESPACE"
        -l "app.kubernetes.io/instance=ai-flow"
        --all-containers=true
        --max-log-requests=20
        --prefix
        --tail=100
        -f
    )
    log "Starting all Kubernetes component logs in this terminal"
    log "Log command: $KUBECTL ${log_args[*]}"
    exec "$KUBECTL" "${log_args[@]}"
}

if ! run_forward "DeviceDataHub Grafana" "service/ai-flow-grafana" "3000:3000"; then
    log "No graphical terminal emulator found; starting detached port-forwards."
    start_background_forward "grafana" "service/ai-flow-grafana" "3000:3000"
    start_background_forward "timescaledb" "service/ai-flow-timescaledb" "5433:5432"
    log "Grafana: http://localhost:3000"
    log "Grafana log: $RUNTIME_DIR/grafana.port-forward.log"
    log "TimescaleDB: localhost:5433"
    log "TimescaleDB log: $RUNTIME_DIR/timescaledb.port-forward.log"
    follow_logs
fi

if ! run_forward "DeviceDataHub TimescaleDB" "service/ai-flow-timescaledb" "5433:5432"; then
    log "Grafana terminal opened, but no second terminal emulator was available."
    start_background_forward "timescaledb" "service/ai-flow-timescaledb" "5433:5432"
    follow_logs
fi

log "Child terminals started"
log "Grafana: http://localhost:3000"
log "TimescaleDB: localhost:5433, database telemetry"
log "Keep both child terminals open while using Grafana or the database."
follow_logs
