#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VOICE = ROOT / "deepfake-audio-video-inference"
VIRTUALCAM = ROOT / "deepfake-virtualcam-check"
RISKAPI = ROOT / "deepfake-riskapi"
MEDIA_TRANSPORT_SRC = ROOT / "deepfake-media-transport" / "src"
STREAM_SIGNATURE_SRC = ROOT / "deepfake-stream-signature" / "src"


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    cmd: list[str]
    cwd: Path
    env: dict[str, str] | None = None
    wait_before: tuple[str, str, int, float] | None = None
    required_modules: tuple[str, ...] = ()


def main() -> int:
    print("alfa launcher")
    print("=============")
    print("Ctrl-C stops all processes started by this launcher.\n")

    cfg = collect_config()
    specs = build_processes(cfg)
    if not specs:
        print("Nothing selected.")
        return 0

    print("\nPlanned processes:")
    for spec in specs:
        env_prefix = format_env_prefix(spec.env)
        print(f"- {spec.name}: cd {spec.cwd}")
        print(f"  {env_prefix}{shlex.join(spec.cmd)}")

    if not ask_bool("\nStart these processes?", True):
        return 0

    try:
        preflight_processes(specs)
    except RuntimeError as exc:
        print(f"\n{exc}")
        return 1

    return run_processes(specs)


def collect_config() -> dict[str, object]:
    cfg: dict[str, object] = {}

    print("Preset:")
    print("  1. Media stream: cluster server + SSH tunnel + local client")
    print("  2. Media stream with virtualcam-check proxy")
    print("  3. RiskAPI only")
    print("  4. Custom")
    preset = ask_choice("Choose preset", {"1", "2", "3", "4"}, "2")

    cfg["riskapi"] = preset == "3" or (preset == "4" and ask_bool("Start RiskAPI?", False))
    cfg["cluster_server"] = preset in {"1", "2"} or (
        preset == "4" and ask_bool("Start cluster stream_server over SSH?", True)
    )
    cfg["virtualcam_proxy"] = preset == "2" or (
        preset == "4" and ask_bool("Use virtualcam-check TCP proxy?", False)
    )
    cfg["plain_tunnel"] = preset == "1" or (
        preset == "4"
        and not cfg["virtualcam_proxy"]
        and ask_bool("Start plain SSH tunnel?", True)
    )
    cfg["stream_client"] = preset in {"1", "2"} or (
        preset == "4" and ask_bool("Start local stream_client?", True)
    )

    if cfg["cluster_server"] or cfg["plain_tunnel"] or cfg["virtualcam_proxy"]:
        cfg["ssh_key"] = ask_text("SSH key", "~/.ssh/id_ed25519")
        cfg["ssh_port"] = ask_text("SSH port", "22010")
        cfg["ssh_user"] = ask_text("SSH user", "master")
        cfg["ssh_host"] = ask_text("SSH host", "62.183.4.208")
        cfg["remote_dir"] = ask_text(
            "Remote audio-video-inference directory",
            "/home/master/work/alfa-deepfake/deepfake-audio-video-inference",
        )
        if cfg["cluster_server"]:
            cfg["restart_cluster_server"] = ask_bool(
                "Restart cluster stream_server if port 13000 is already busy?",
                False,
            )

    if cfg["cluster_server"] or cfg["stream_client"] or cfg["virtualcam_proxy"]:
        print("\nStream signature:")
        print("  off   - no signature verification/signing")
        print("  log   - verify on server, log bad signatures, accept packets")
        print("  block - verify on server, drop bad signatures")
        cfg["signature_policy"] = ask_choice(
            "Signature policy",
            {"off", "log", "block"},
            "off",
        )
        if cfg["signature_policy"] != "off":
            cfg["signature_key"] = ask_text("Signature shared secret", "dev-secret")
            cfg["signature_key_id"] = ask_text("Signature key id", "deepfake-client-test")
        else:
            cfg["signature_key"] = ""
            cfg["signature_key_id"] = "deepfake-client-test"

    if cfg["stream_client"]:
        cfg["video_device"] = ask_text("Video device", "0")
        cfg["video_width"] = ask_text("Video width", "512")
        cfg["video_height"] = ask_text("Video height", "288")
        cfg["video_fps"] = ask_text("Video FPS", "15")
        cfg["jpeg_quality"] = ask_text("JPEG quality", "65")
        cfg["source_wav"] = ask_text("Source wav instead of microphone (empty = mic)", "")

    if cfg["virtualcam_proxy"]:
        cfg["capture_duration"] = ask_text("Virtualcam capture duration seconds", "10")
        cfg["max_frames"] = ask_text("Virtualcam max frames", "120")
        cfg["device_label"] = ask_text("Source device label", "Integrated Camera")

    if cfg["riskapi"]:
        cfg["riskapi_host"] = ask_text("RiskAPI host", "127.0.0.1")
        cfg["riskapi_port"] = ask_text("RiskAPI port", "8000")
        cfg["mongodb_uri"] = ask_text(
            "MongoDB URI (empty = use project default)",
            os.environ.get("MONGODB_URI", ""),
        )

    return cfg


def build_processes(cfg: dict[str, object]) -> list[ProcessSpec]:
    specs: list[ProcessSpec] = []
    signature_policy = str(cfg.get("signature_policy", "off"))
    signature_key = str(cfg.get("signature_key", ""))
    signature_key_id = str(cfg.get("signature_key_id", "deepfake-client-test"))

    if cfg.get("riskapi"):
        python = python_bin(RISKAPI)
        env = os.environ.copy()
        if cfg.get("mongodb_uri"):
            env["MONGODB_URI"] = str(cfg["mongodb_uri"])
        specs.append(
            ProcessSpec(
                name="riskapi",
                cwd=RISKAPI,
                env=env,
                cmd=[
                    str(python),
                    "-m",
                    "uvicorn",
                    "main:app",
                    "--host",
                    str(cfg["riskapi_host"]),
                    "--port",
                    str(cfg["riskapi_port"]),
                ],
            )
        )

    if cfg.get("cluster_server"):
        remote_env = {
            "SIGNATURE_POLICY": signature_policy,
            "SIGNATURE_KEY": signature_key,
            "SIGNATURE_KEY_ID": signature_key_id,
        }
        remote_cmd = build_remote_server_command(
            remote_dir=str(cfg["remote_dir"]),
            remote_env=remote_env,
            restart=bool(cfg.get("restart_cluster_server", False)),
        )
        specs.append(
            ProcessSpec(
                name="cluster-server",
                cwd=ROOT,
                cmd=ssh_base(cfg) + [remote_cmd],
            )
        )

    if cfg.get("virtualcam_proxy"):
        env = os.environ.copy()
        env.update(
            {
                "SSH_KEY": expand_user(str(cfg["ssh_key"])),
                "SSH_PORT": str(cfg["ssh_port"]),
                "SSH_USER": str(cfg["ssh_user"]),
                "SSH_HOST": str(cfg["ssh_host"]),
                "REMOTE_DIR": str(cfg["remote_dir"]),
                "PYTHON_BIN": str(python_bin(VIRTUALCAM)),
                "PYTHONPATH": pythonpath(
                    [
                        VIRTUALCAM / "src",
                        MEDIA_TRANSPORT_SRC,
                        STREAM_SIGNATURE_SRC,
                    ],
                    os.environ.get("PYTHONPATH"),
                ),
                "CAPTURE_DURATION": str(cfg["capture_duration"]),
                "MAX_FRAMES": str(cfg["max_frames"]),
                "DEVICE_LABEL": str(cfg["device_label"]),
                "SIGNATURE_TRUSTED_KEY": trusted_key(signature_key_id, signature_key),
            }
        )
        specs.append(
            ProcessSpec(
                name="virtualcam-proxy",
                cwd=VIRTUALCAM,
                env=env,
                cmd=["./scripts/run-tcp-proxy.sh"],
                wait_before=("remote", "13000", 120, cfg),
            )
        )
    elif cfg.get("plain_tunnel"):
        specs.append(
            ProcessSpec(
                name="ssh-tunnel",
                cwd=ROOT,
                cmd=ssh_base(cfg)
                + [
                    "-N",
                    "-L",
                    "13000:127.0.0.1:13000",
                ],
                wait_before=("remote", "13000", 120, cfg),
            )
        )

    if cfg.get("stream_client"):
        python = python_bin(VOICE)
        env = os.environ.copy()
        env["PYTHONPATH"] = pythonpath(
            [VOICE, MEDIA_TRANSPORT_SRC, STREAM_SIGNATURE_SRC],
            env.get("PYTHONPATH"),
        )
        cmd = [
            str(python),
            "-m",
            "backend.media_gateway.stream_client",
            "--gateway-host",
            "127.0.0.1",
            "--gateway-port",
            "13000",
            "--video-device",
            str(cfg["video_device"]),
            "--video-width",
            str(cfg["video_width"]),
            "--video-height",
            str(cfg["video_height"]),
            "--video-fps",
            str(cfg["video_fps"]),
            "--jpeg-quality",
            str(cfg["jpeg_quality"]),
        ]
        if cfg.get("source_wav"):
            cmd.extend(["--source-wav", str(cfg["source_wav"])])
        if signature_key:
            cmd.extend(["--signature-key", signature_key, "--signature-key-id", signature_key_id])
        specs.append(
            ProcessSpec(
                name="stream-client",
                cwd=VOICE,
                env=env,
                cmd=cmd,
                wait_before=("local", "13000", 60, cfg),
                required_modules=("numpy", "cv2", "sounddevice", "PIL"),
            )
        )

    return specs


def run_processes(specs: list[ProcessSpec]) -> int:
    processes: list[tuple[ProcessSpec, subprocess.Popen[str]]] = []
    stop = threading.Event()

    def terminate_all() -> None:
        stop.set()
        for spec, proc in processes:
            if proc.poll() is None:
                print(f"\nStopping {spec.name}...")
                proc.terminate()
        deadline = time.time() + 5
        for _spec, proc in processes:
            remaining = max(0.0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()

    def handle_signal(_signum, _frame) -> None:
        terminate_all()
        raise KeyboardInterrupt

    previous_int = signal.signal(signal.SIGINT, handle_signal)
    previous_term = signal.signal(signal.SIGTERM, handle_signal)
    try:
        for spec in specs:
            wait_before_start(spec)
            proc = subprocess.Popen(
                spec.cmd,
                cwd=spec.cwd,
                env=spec.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            processes.append((spec, proc))
            threading.Thread(target=pipe_output, args=(spec.name, proc), daemon=True).start()
            time.sleep(0.3)

        print("\nProcesses started. Press Ctrl-C to stop.\n")
        while not stop.is_set():
            for spec, proc in processes:
                code = proc.poll()
                if code is not None:
                    print(f"\n{spec.name} exited with code {code}.")
                    terminate_all()
                    return code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        terminate_all()
    return 0


def pipe_output(name: str, proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[{name}] {line}", end="")


def preflight_process(spec: ProcessSpec) -> None:
    if not spec.required_modules:
        return
    code = (
        "import importlib.util, sys; "
        f"mods={list(spec.required_modules)!r}; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "sys.exit('\\n'.join(missing) if missing else 0)"
    )
    result = subprocess.run(
        [spec.cmd[0], "-c", code],
        cwd=spec.cwd,
        env=spec.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    missing = [line for line in result.stdout.splitlines() if line.strip()]
    detail = ", ".join(missing) if missing else result.stderr.strip()
    raise RuntimeError(
        f"{spec.name} Python is missing required modules: {detail}\n"
        f"Create/install the local workspace venv first:\n"
        f"  cd {ROOT}\n"
        f"  python3 -m venv .venv\n"
        f"  . .venv/bin/activate\n"
        f"  python -m pip install --upgrade pip\n"
        f"  python -m pip install -r requirements-client.txt"
    )


def preflight_processes(specs: list[ProcessSpec]) -> None:
    for spec in specs:
        preflight_process(spec)


def wait_before_start(spec: ProcessSpec) -> None:
    if spec.wait_before is None:
        return
    kind, port, timeout_s, cfg = spec.wait_before
    if kind == "local":
        print(f"Waiting for local port 127.0.0.1:{port} before starting {spec.name}...")
        wait_for_local_listener(int(port), timeout_s)
        return
    if kind == "remote":
        print(f"Waiting for cluster port 127.0.0.1:{port} before starting {spec.name}...")
        wait_for_remote_listener(cfg, port, timeout_s)


def wait_for_local_listener(port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["ss", "-ltn", f"sport = :{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if "LISTEN" in result.stdout:
            return
        time.sleep(0.5)
    raise RuntimeError(f"local port 127.0.0.1:{port} did not start listening")


def wait_for_remote_listener(cfg: dict[str, object], port: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    remote_test = f"ss -ltn 'sport = :{port}' | grep -q LISTEN"
    while time.monotonic() < deadline:
        result = subprocess.run(
            ssh_base(cfg) + [remote_test],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(2.0)
    raise RuntimeError(f"cluster port 127.0.0.1:{port} did not start listening")


def ask_bool(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes", "д", "да"}:
            return True
        if value in {"n", "no", "н", "нет"}:
            return False
        print("Please answer yes/no.")


def ask_choice(prompt: str, allowed: set[str], default: str) -> str:
    while True:
        value = input(f"{prompt} [{default}]: ").strip() or default
        if value in allowed:
            return value
        print(f"Allowed values: {', '.join(sorted(allowed))}")


def ask_text(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value if value else default


def python_bin(project: Path) -> Path | str:
    workspace_candidate = ROOT / ".venv" / "bin" / "python"
    if workspace_candidate.exists():
        return workspace_candidate
    candidate = project / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else sys.executable


def ssh_base(cfg: dict[str, object]) -> list[str]:
    return [
        "ssh",
        "-i",
        expand_user(str(cfg["ssh_key"])),
        "-p",
        str(cfg["ssh_port"]),
        f"{cfg['ssh_user']}@{cfg['ssh_host']}",
    ]


def build_remote_server_command(
    *,
    remote_dir: str,
    remote_env: dict[str, str],
    restart: bool,
) -> str:
    env_prefix = format_remote_env(remote_env)
    path_prefix = 'PATH="$HOME/.local/bin:$PATH" '
    workspace_venv_bootstrap = (
        "ALFA_ROOT=\"$(cd .. && pwd)\"; "
        "if [ -f \"$ALFA_ROOT/.venv/bin/activate\" ]; then "
        ". \"$ALFA_ROOT/.venv/bin/activate\"; "
        "fi; "
    )
    python_bootstrap = (
        "mkdir -p \"$HOME/.local/bin\" && "
        "if ! command -v python >/dev/null 2>&1; then "
        "ln -sfn \"$(command -v python3.10)\" \"$HOME/.local/bin/python\"; "
        "fi; "
    )
    quoted_dir = shlex.quote(remote_dir)
    if restart:
        return (
            f"cd {quoted_dir} && "
            f"{workspace_venv_bootstrap}"
            f"{python_bootstrap}"
            "if ss -ltn 'sport = :13000' | grep -q LISTEN; then "
            "echo 'stopping existing stream_server on 127.0.0.1:13000'; "
            "pkill -f '[b]ackend.media_gateway.stream_server' || true; "
            "sleep 2; "
            "fi; "
            f"{path_prefix}{env_prefix} exec ./scripts/server.sh"
        )
    return (
        f"cd {quoted_dir} && "
        f"{workspace_venv_bootstrap}"
        f"{python_bootstrap}"
        "if ss -ltn 'sport = :13000' | grep -q LISTEN; then "
        "echo 'stream_server is already listening on 127.0.0.1:13000'; "
        "echo 'Use restart=yes in this launcher if this existing server is stale.'; "
        "sleep infinity; "
        "else "
        f"{path_prefix}{env_prefix} exec ./scripts/server.sh; "
        "fi"
    )


def trusted_key(key_id: str, secret: str) -> str:
    return f"{key_id}={secret}" if secret else ""


def format_remote_env(values: dict[str, str]) -> str:
    parts = []
    for key, value in values.items():
        if value:
            parts.append(f"{key}={shlex.quote(value)}")
    return " ".join(parts)


def format_env_prefix(env: dict[str, str] | None) -> str:
    if not env:
        return ""
    shown = []
    for key in (
        "MONGODB_URI",
        "PYTHONPATH",
        "PYTHON_BIN",
        "SSH_KEY",
        "SSH_PORT",
        "SSH_USER",
        "SSH_HOST",
    ):
        if key in env and env[key]:
            shown.append(f"{key}={shlex.quote(env[key])}")
    return " ".join(shown) + (" " if shown else "")


def expand_user(value: str) -> str:
    return str(Path(value).expanduser())


def maybe_pathsep(value: str | None) -> str:
    return os.pathsep + value if value else ""


def pythonpath(paths: list[Path], existing: str | None = None) -> str:
    value = os.pathsep.join(str(path) for path in paths)
    return value + maybe_pathsep(existing)


if __name__ == "__main__":
    raise SystemExit(main())
