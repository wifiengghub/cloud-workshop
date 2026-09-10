#!/usr/bin/env python3
"""Stop and remove the DeviceDataHub AI flow from local Kubernetes."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
RUNTIME_DIR = ROOT / ".runtime"
LOCAL_BIN = Path.home() / ".local" / "bin"
KIND_CLUSTER = "devicedatahub"
NAMESPACE = "devicedatahub"
RELEASE = "ai-flow"
IMAGE = "devicedatahub-ai-flow:latest"
TOOLS = ("kind", "kubectl", "helm")


def command_path(name: str) -> str | None:
    return shutil.which(name) or (str(LOCAL_BIN / name) if (LOCAL_BIN / name).exists() else None)


def run(command: list[str], *, check: bool = True) -> bool:
    print(f"$ {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.returncode == 0


def has_cluster(kind: str) -> bool:
    result = subprocess.run(
        [kind, "get", "clusters"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return result.returncode == 0 and KIND_CLUSTER in result.stdout.splitlines()


def remove_path(path: Path) -> None:
    if path.exists():
        print(f"Removing {path}", flush=True)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def stop_port_forwards() -> None:
    for pid_file in RUNTIME_DIR.glob("*.port-forward.pid"):
        try:
            pid = int(pid_file.read_text().strip())
            print(f"Stopping port-forward PID {pid}", flush=True)
            subprocess.run(["kill", str(pid)], check=False)
        except (OSError, ValueError):
            pass
    remove_path(RUNTIME_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stop and remove the DeviceDataHub AI flow from Kubernetes"
    )
    parser.add_argument(
        "--keep-cluster",
        action="store_true",
        help="Remove the Helm release and namespace but preserve the kind cluster",
    )
    parser.add_argument(
        "--keep-image",
        action="store_true",
        help="Preserve the local Docker image",
    )
    parser.add_argument(
        "--keep-venv",
        action="store_true",
        help="Preserve the project .venv directory",
    )
    parser.add_argument(
        "--remove-tools",
        action="store_true",
        help="Also remove kind, kubectl, and Helm downloaded by startup.py",
    )
    args = parser.parse_args()

    stop_port_forwards()

    kind = command_path("kind")
    kubectl = command_path("kubectl")
    helm = command_path("helm")
    docker = command_path("docker")

    if kind and has_cluster(kind):
        if helm:
            run(
                [helm, "uninstall", RELEASE, "--namespace", NAMESPACE],
                check=False,
            )
        if kubectl:
            run([kubectl, "delete", "namespace", NAMESPACE, "--ignore-not-found=true"], check=False)
        if not args.keep_cluster:
            run([kind, "delete", "cluster", "--name", KIND_CLUSTER], check=False)
    else:
        print(f"Kind cluster '{KIND_CLUSTER}' is not running; skipping Kubernetes cleanup.", flush=True)

    if docker and not args.keep_image:
        run([docker, "image", "rm", "-f", IMAGE], check=False)

    if not args.keep_venv:
        remove_path(VENV)

    if args.remove_tools:
        for tool in TOOLS:
            remove_path(LOCAL_BIN / tool)

    print("Shutdown cleanup complete.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"Shutdown command failed with exit code {error.returncode}.", file=sys.stderr)
        raise SystemExit(error.returncode) from error
