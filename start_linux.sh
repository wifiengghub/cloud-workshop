#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

command -v python3 >/dev/null 2>&1 || fail "python3 is required."
command -v docker >/dev/null 2>&1 || fail "Docker is required."
docker info >/dev/null 2>&1 || fail "Docker is not running."

if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating Python virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Activate the environment for the rest of this shell and all child commands.
source "$VENV_DIR/bin/activate"
log "Installing Python requirements"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT_DIR/requirements.txt"

log "Starting the Kubernetes stack"
cd "$ROOT_DIR"
python startup.py --no-follow

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

if ! run_forward "DeviceDataHub Grafana" "service/ai-flow-grafana" "3000:3000"; then
    log "No graphical terminal emulator found; starting detached port-forwards."
    start_background_forward "grafana" "service/ai-flow-grafana" "3000:3000"
    start_background_forward "timescaledb" "service/ai-flow-timescaledb" "5433:5432"
    log "Grafana: http://localhost:3000"
    log "Grafana log: $RUNTIME_DIR/grafana.port-forward.log"
    log "TimescaleDB: localhost:5433"
    log "TimescaleDB log: $RUNTIME_DIR/timescaledb.port-forward.log"
    exit 0
fi

if ! run_forward "DeviceDataHub TimescaleDB" "service/ai-flow-timescaledb" "5433:5432"; then
    log "Grafana terminal opened, but no second terminal emulator was available."
    start_background_forward "timescaledb" "service/ai-flow-timescaledb" "5433:5432"
    exit 1
fi

log "Child terminals started"
log "Grafana: http://localhost:3000"
log "TimescaleDB: localhost:5433, database telemetry"
log "Keep both child terminals open while using Grafana or the database."
