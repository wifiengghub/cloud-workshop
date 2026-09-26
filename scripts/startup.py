#!/usr/bin/env python3
"""Bootstrap the complete DeviceDataHub AI flow on a local kind cluster."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
LOCAL_BIN = Path.home() / ".local" / "bin"
KIND_CLUSTER = "devicedatahub"
NAMESPACE = "devicedatahub"
IMAGE = "devicedatahub-ai-flow:latest"


def command_path(name: str) -> str | None:
    return shutil.which(name) or (str(LOCAL_BIN / name) if (LOCAL_BIN / name).exists() else None)


def run(
    command: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=check, input=input_text, text=True)


def output(command: list[str]) -> str:
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def download(url: str, destination: Path) -> None:
    print(f"Downloading {destination.name}...", flush=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_venv() -> None:
    python = VENV / "bin" / "python"
    if not python.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", "requirements.txt"])
    print(f"Virtual environment ready: {VENV}", flush=True)
    print(f"Activate it in your shell with: source {VENV}/bin/activate", flush=True)


def ensure_tools() -> dict[str, str]:
    docker = command_path("docker")
    if docker is None:
        raise SystemExit("Docker is required. Install Docker Engine and start it first.")
    if subprocess.run([docker, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        raise SystemExit("Docker is installed but not running.")

    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    kind = command_path("kind")
    if kind is None:
        kind_path = LOCAL_BIN / "kind"
        download("https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64", kind_path)
        kind = str(kind_path)

    kubectl = command_path("kubectl")
    if kubectl is None:
        version = urllib.request.urlopen("https://dl.k8s.io/release/stable.txt").read().decode().strip()
        kubectl_path = LOCAL_BIN / "kubectl"
        download(f"https://dl.k8s.io/release/{version}/bin/linux/amd64/kubectl", kubectl_path)
        kubectl = str(kubectl_path)

    helm = command_path("helm")
    if helm is None:
        archive = ROOT / ".helm.tgz"
        download("https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz", archive)
        with tarfile.open(archive, "r:gz") as bundle:
            member = bundle.getmember("linux-amd64/helm")
            member.name = "helm"
            bundle.extract(member, LOCAL_BIN, filter="data")
        archive.unlink()
        helm = str(LOCAL_BIN / "helm")

    return {"docker": docker, "kind": kind, "kubectl": kubectl, "helm": helm}


def cluster_container_running(docker: str) -> bool:
    result = subprocess.run(
        [docker, "inspect", "-f", "{{.State.Running}}", f"{KIND_CLUSTER}-control-plane"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


KIND_CLUSTER_CONFIG = ROOT / "config" / "kind-cluster.yaml"


def ensure_cluster(kind: str, docker: str) -> None:
    clusters = output([kind, "get", "clusters"]).splitlines()
    if KIND_CLUSTER not in clusters:
        run([kind, "create", "cluster", "--name", KIND_CLUSTER,
             "--config", str(KIND_CLUSTER_CONFIG), "--wait", "5m"])
    elif not cluster_container_running(docker):
        print(
            f"Existing kind cluster '{KIND_CLUSTER}' is stopped; recreating it.",
            flush=True,
        )
        run([kind, "delete", "cluster", "--name", KIND_CLUSTER], check=False)
        run([kind, "create", "cluster", "--name", KIND_CLUSTER,
             "--config", str(KIND_CLUSTER_CONFIG), "--wait", "5m"])
    else:
        print(f"Using existing kind cluster: {KIND_CLUSTER}", flush=True)


def _setup_kubeconfig(docker: str) -> None:
    """Write a working kubeconfig, working around snap Docker's kubeconfig limitations.

    ``kind get kubeconfig`` fails under snap Docker because the snap shim
    cannot exec into containers the same way the native Docker binary can.
    We instead read the admin.conf directly via ``docker exec`` and patch the
    server address to use 127.0.0.1 with the host-mapped API-server port
    (kind always publishes 6443/tcp to a random host port at container startup).
    """
    control_plane = f"{KIND_CLUSTER}-control-plane"
    kubeconfig_dir = Path.home() / ".kube"
    kubeconfig_path = kubeconfig_dir / "config"
    kubeconfig_dir.mkdir(parents=True, exist_ok=True)

    # Read admin.conf from the control-plane container.
    result = subprocess.run(
        [docker, "exec", control_plane, "cat", "/etc/kubernetes/admin.conf"],
        capture_output=True,
        text=True,
        check=True,
    )
    kubeconfig_raw = result.stdout

    # Find the host-mapped port for the API server (6443/tcp → random host port).
    port_result = subprocess.run(
        [docker, "port", control_plane, "6443/tcp"],
        capture_output=True,
        text=True,
        check=True,
    )
    # Output is like "0.0.0.0:12345" or "127.0.0.1:12345"
    host_port = port_result.stdout.strip().split(":")[-1]

    # Patch the server to use the loopback address + host-mapped port.
    # The in-container hostname is only resolvable inside the kind Docker network.
    kubeconfig_patched = kubeconfig_raw.replace(
        f"https://{control_plane}:6443",
        f"https://127.0.0.1:{host_port}",
    )

    kubeconfig_path.write_text(kubeconfig_patched)
    kubeconfig_path.chmod(0o600)

    # Ensure current-context is set so kubectl doesn't fall back to localhost:8080.
    # admin.conf extracted from the container uses "kubernetes-admin@<cluster>" as context name.
    subprocess.run(
        [shutil.which("kubectl") or str(LOCAL_BIN / "kubectl"),
         "config", "use-context", f"kubernetes-admin@{KIND_CLUSTER}",
         "--kubeconfig", str(kubeconfig_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Ensure all subsequent subprocess calls (kubectl, helm) pick up this config.
    os.environ["KUBECONFIG"] = str(kubeconfig_path)
    print(f"kubeconfig written to {kubeconfig_path} (server: 127.0.0.1:{host_port})", flush=True)


def _is_snap_docker(docker: str) -> bool:
    """Return True if the Docker binary is the snap-confined version.

    Snap Docker's shim lives at /snap/bin/docker but resolves (via execve) to
    /usr/bin/snap, so Path.resolve() is unreliable.  Instead we check:
      1. Whether the raw binary path starts with /snap/bin/ (most common case).
      2. As a fallback, whether ``snap list docker`` exits 0 (snap package present).

    Snap's AppArmor profile blocks creating new directories under /tmp, which
    breaks ``kind load docker-image``.
    """
    # Primary check: raw path (before symlink resolution).
    if docker.startswith("/snap/bin/") or "/snap/bin/" in docker:
        return True
    # Secondary check: ask snapd whether docker is a snap package.
    try:
        result = subprocess.run(
            ["snap", "list", "docker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # snap not installed at all → definitely not snap Docker.
        return False


def load_image_into_kind(docker: str, kind: str, image_name: str = IMAGE) -> None:
    """Load a Docker image into kind, working around the snap Docker /tmp restriction.

    ``kind load docker-image`` internally runs ``docker save -o /tmp/...`` which
    fails when Docker is installed via snap (snap's AppArmor profile disallows
    creating new directories under /tmp).  When snap Docker is detected we skip
    straight to the ``kind load image-archive`` path using a temp file inside the
    user's home directory (always accessible to snap-confined processes).
    """
    snap_docker = _is_snap_docker(docker)

    if not snap_docker:
        # Non-snap Docker: try the fast direct path first.
        result = subprocess.run(
            [kind, "load", "docker-image", image_name, "--name", KIND_CLUSTER],
            cwd=ROOT,
            check=False,
        )
        if result.returncode == 0:
            return
        print(
            f"kind load docker-image failed for {image_name}; falling back to image-archive method.",
            flush=True,
        )
    else:
        print(
            f"Snap Docker detected — using image-archive method to load {image_name} into kind "
            "(snap AppArmor profile blocks the /tmp path used by 'kind load docker-image').",
            flush=True,
        )

    # Save to a temp file in the home directory, then load via archive.
    tmp_dir = Path.home() / ".cache" / "kind-load"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    clean_name = image_name.replace("/", "_").replace(":", "_")
    tmp_tar = tmp_dir / f"{clean_name}.tar"
    try:
        print(f"$ docker save {image_name} -o {tmp_tar}", flush=True)
        subprocess.run([docker, "save", image_name, "-o", str(tmp_tar)], check=True)
        run([kind, "load", "image-archive", str(tmp_tar), "--name", KIND_CLUSTER])
    finally:
        if tmp_tar.exists():
            tmp_tar.unlink()
        try:
            tmp_dir.rmdir()  # Remove only if empty
        except OSError:
            pass


def install_stack(tools: dict[str, str], *, rebuild: bool, values_file: str | None) -> None:
    docker = tools["docker"]
    image_exists = subprocess.run(
        [docker, "image", "inspect", IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0

    if rebuild or not image_exists:
        if not rebuild and not image_exists:
            print(
                f"Image '{IMAGE}' not found locally — building it now "
                "(pass no flag or remove --no-build to suppress this message).",
                flush=True,
            )
        run([docker, "build", "-t", IMAGE, "."])
    load_image_into_kind(docker, tools["kind"], IMAGE)

    # Also preload Loki and Promtail if present in host Docker cache
    for extra_img in ("grafana/loki:latest", "grafana/promtail:latest"):
        if subprocess.run([docker, "image", "inspect", extra_img],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            load_image_into_kind(docker, tools["kind"], extra_img)

    namespace_yaml = subprocess.check_output(
        [tools["kubectl"], "create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml"],
        cwd=ROOT,
        text=True,
    )
    run([tools["kubectl"], "apply", "-f", "-"], input_text=namespace_yaml)
    command = [
        tools["helm"],
        "upgrade",
        "--install",
        "ai-flow",
        "helm/ai-flow",
        "--namespace",
        NAMESPACE,
        "--set",
        "image.repository=devicedatahub-ai-flow",
        "--set",
        "image.tag=latest",
        "--wait",
        "--timeout",
        "10m",
    ]
    if values_file:
        command.extend(["--values", values_file])
    run(command)


def show_logs(kubectl: str, follow: bool) -> None:
    run([kubectl, "get", "pods", "-n", NAMESPACE, "-o", "wide"])
    if follow:
        run(
            [
                kubectl,
                "logs",
                "-n",
                NAMESPACE,
                "-l",
                "app.kubernetes.io/instance=ai-flow",
                "--all-containers=true",
                "--max-log-requests=20",
                "--prefix",
                "--tail=100",
                "-f",
            ]
        )
    else:
        print(
            f"Follow all component logs with: {kubectl} logs -n {NAMESPACE} "
            "-l app.kubernetes.io/instance=ai-flow --all-containers=true "
            "--max-log-requests=20 --prefix --tail=100 -f",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the DeviceDataHub AI flow on Kubernetes")
    parser.add_argument("--no-build", action="store_true", help="Reuse the local image")
    parser.add_argument("--no-follow", action="store_true", help="Start pods and return after showing status")
    parser.add_argument("--values", help="Private Helm values file for broker and database settings")
    parser.add_argument("--delete-cluster", action="store_true", help="Delete the kind cluster and exit")
    args = parser.parse_args()

    ensure_venv()
    tools = ensure_tools()
    if args.delete_cluster:
        if KIND_CLUSTER in output([tools["kind"], "get", "clusters"]).splitlines():
            run([tools["kind"], "delete", "cluster", "--name", KIND_CLUSTER])
        return 0

    ensure_cluster(tools["kind"], tools["docker"])
    _setup_kubeconfig(tools["docker"])
    install_stack(tools, rebuild=not args.no_build, values_file=args.values)
    show_logs(tools["kubectl"], follow=not args.no_follow)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"Startup command failed with exit code {error.returncode}.", file=sys.stderr)
        raise SystemExit(error.returncode) from error
