#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
NAMESPACE="devicedatahub"
RUNTIME_DIR="$ROOT_DIR/.runtime"

chmod 755 "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py

log() {
    printf '\n[start-cloud] %s\n' "$*"
}

fail() {
    printf '\n[start-cloud] ERROR: %s\n' "$*" >&2
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

git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || fail "Run this script from a Git checkout of the project. Expected repository at $ROOT_DIR."

if [[ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
    log "Pulling latest changes for the existing checkout"
    git -C "$ROOT_DIR" pull --ff-only \
        || fail "Could not update the checkout. Resolve local changes or pull conflicts, then rerun."
else
    log "Local changes detected; skipping git pull."
fi

export PATH="$ROOT_DIR/.local/bin:$PATH"

start_docker_engine() {
    if docker info >/dev/null 2>&1; then
        return
    fi

    log "Starting Docker Engine inside Ubuntu"
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

    fail "Docker Engine could not start or is not accessible. Ensure your user is in the docker group and check /tmp/devicedatahub-dockerd.log."
}

command -v docker >/dev/null 2>&1 || fail "Docker Engine installation failed."
start_docker_engine

if command -v gh >/dev/null 2>&1; then
    log "GitHub CLI detected. Public HTTPS pulls do not require gh auth login."
else
    log "GitHub CLI is not required for this public HTTPS repository; using git pull."
fi

cd "$ROOT_DIR"
log "Using project: $ROOT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating Python virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Activate the environment for the rest of this shell and all child commands.
source "$VENV_DIR/bin/activate"
log "Python dependencies are checked by scripts/startup.py and installed only when requirements.txt changes."

SIMULATE=false
if [[ -t 0 ]]; then
    read -r -p "Enable local MQTT simulator and broker? [y/N]: " simulation_answer
    if [[ "$simulation_answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        SIMULATE=true
    fi
fi

log "Starting the Kubernetes stack"
startup_args=(scripts/startup.py)
if [[ "${SKIP_IMAGE_BUILD:-false}" =~ ^([Tt][Rr][Uu][Ee]|1|[Yy]([Ee][Ss])?)$ ]]; then
    startup_args+=(--no-build)
fi
if [[ "${ACCEPT_SPLUNK_TERMS:-false}" =~ ^([Tt][Rr][Uu][Ee]|1|[Yy]([Ee][Ss])?)$ ]]; then
    startup_args+=(--accept-splunk-terms)
fi
if [[ "$SIMULATE" == true ]]; then
    log "Simulation enabled: using helm/ai-flow/values.simulate.yaml"
    startup_args+=(--values helm/ai-flow/values.simulate.yaml)
else
    log "Simulation disabled: using the configured ngrok MQTT broker"
fi
python "${startup_args[@]}"

KUBECTL="$(command -v kubectl || true)"
if [[ -z "$KUBECTL" && -x "$ROOT_DIR/.local/bin/kubectl" ]]; then
    KUBECTL="$ROOT_DIR/.local/bin/kubectl"
fi
[[ -n "$KUBECTL" ]] || fail "kubectl was not found after startup.py completed."

log "Waiting for Kubernetes Services"
for attempt in $(seq 1 30); do
    if "$KUBECTL" get service ai-flow-grafana -n "$NAMESPACE" >/dev/null 2>&1 \
        && "$KUBECTL" get service ai-flow-timescaledb -n "$NAMESPACE" >/dev/null 2>&1 \
        && "$KUBECTL" get service ai-flow-splunk -n "$NAMESPACE" >/dev/null 2>&1 \
        && "$KUBECTL" get service ai-flow-monitor -n "$NAMESPACE" >/dev/null 2>&1; then
        break
    fi
    if [[ "$attempt" == 30 ]]; then
        fail "Grafana, TimescaleDB, Splunk, or monitor Service did not become available."
    fi
    sleep 2
done

start_background_forward() {
    local name="$1"
    local resource="$2"
    local ports="$3"
    mkdir -p "$RUNTIME_DIR"
    nohup bash "$SCRIPT_DIR/port_forward.sh" "$RUNTIME_DIR/$name.port-forward.pid" \
        "$KUBECTL" port-forward "$resource" "$ports" --namespace="$NAMESPACE" \
        >"$RUNTIME_DIR/$name.port-forward.log" 2>&1 < /dev/null &
    local pid=$!
    log "$name port-forward started in background (PID $pid)"
}

print_followup_commands() {
    printf '\n[start-linux] Run these commands from another terminal:\n'
    printf '  cd %q\n' "$ROOT_DIR"
    printf '  export PATH=%q:"$PATH"\n' "$ROOT_DIR/.local/bin"
    printf '  kubectl get pods -n devicedatahub -o wide\n'
    printf '  kubectl get services -n devicedatahub\n'
    printf '  kubectl logs -n devicedatahub deploy/ai-flow-consumer --tail=50 -f\n'
    printf '  kubectl logs -n devicedatahub deploy/ai-flow-inference -c inference --tail=50 -f\n'
    printf '  kubectl logs -n devicedatahub deploy/ai-flow-splunk --tail=50 -f\n'
    printf '  kubectl logs -n devicedatahub daemonset/ai-flow-fluent-bit --tail=50 -f\n'
    printf '  Splunk searches (Search & Reporting; time range: Last 15 minutes):\n'
    printf '    All project logs: index=main\n'
    printf '    Inference logs: index=main "ai-flow-inference"\n'
    printf '    Consumer logs: index=main "ai-flow-consumer"\n'
    printf '    Simulator logs (simulation mode): index=main "ai-flow-simulator"\n'
    printf '    Pod metadata filter: index=main kubernetes.namespace_name=devicedatahub kubernetes.pod_name="ai-flow-inference*"\n'
    printf '  Splunk username: admin\n'
    printf '  Splunk password: admin123\n'
    printf '  Splunk UI: http://localhost:4000\n'
    printf '  Splunk live pod logs dashboard: http://localhost:4000/en-US/app/device_datahub_monitor/pod_logs\n'
    printf '  MQTT message UI: http://localhost:5000\n'
    printf '  TimescaleDB records UI: http://localhost:6080\n'
    printf '    Time ranges: Last 5 minutes, 15 minutes, 1 hour, 24 hours, or All time\n'
    printf '    Both pages auto-refresh every 30 seconds; use Load older records to paginate\n'
    printf '  python3 scripts/shutdown.py\n'
}

log "Starting background port-forwards..."
start_background_forward "grafana" "service/ai-flow-grafana" "3000:3000"
start_background_forward "timescaledb" "service/ai-flow-timescaledb" "5433:5432"
start_background_forward "splunk" "service/ai-flow-splunk" "4000:8000"
start_background_forward "mqtt-ui" "service/ai-flow-monitor" "5000:5000"
start_background_forward "telemetry-ui" "service/ai-flow-monitor" "6080:6000"

log "Grafana: http://localhost:3000"
log "Grafana log: $RUNTIME_DIR/grafana.port-forward.log"
log "TimescaleDB: localhost:5433"
log "TimescaleDB log: $RUNTIME_DIR/timescaledb.port-forward.log"
log "Splunk UI: http://localhost:4000"
log "Splunk log: $RUNTIME_DIR/splunk.port-forward.log"
log "MQTT messages UI: http://localhost:5000"
log "MQTT log: $RUNTIME_DIR/mqtt-ui.port-forward.log"
log "TimescaleDB records UI: http://localhost:6080"
log "TimescaleDB UI log: $RUNTIME_DIR/telemetry-ui.port-forward.log"
log "Monitor pages auto-refresh every 30 seconds; choose a time range and use Load older records."
print_followup_commands
