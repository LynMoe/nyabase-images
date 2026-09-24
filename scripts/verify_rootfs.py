#!/usr/bin/env python3
"""Assert a baked Nyabase workload rootfs matches the delivery contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    raise SystemExit(f"verify_rootfs: {msg}")


def run_chroot(rootfs: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SYSTEMD_OFFLINE"] = "1"
    mounts: list[Path] = []
    for name in ("proc", "dev"):
        target = rootfs / name
        target.mkdir(exist_ok=True)
        subprocess.run(["mount", "--bind", f"/{name}", str(target)], check=False)
        mounts.append(target)
    try:
        return subprocess.run(
            ["chroot", str(rootfs), *args],
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )
    finally:
        for target in reversed(mounts):
            subprocess.run(["umount", "-l", str(target)], check=False)


def assert_exists(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"missing {label}: {path}")


def _is_enabled_unit(rootfs: Path, unit: str) -> bool:
    for wants in (
        rootfs / "etc/systemd/system/multi-user.target.wants" / unit,
        rootfs / "etc/systemd/system/docker.service.wants" / unit,
        rootfs / "etc/systemd/system" / unit,
    ):
        if wants.exists() or wants.is_symlink():
            return True
    return False


def _is_masked_unit(rootfs: Path, unit: str) -> bool:
    link = rootfs / "etc/systemd/system" / unit
    if not link.is_symlink():
        return False
    return os.readlink(link) == "/dev/null"


def assert_nested_docker(rootfs: Path) -> None:
    daemon_path = rootfs / "etc/docker/daemon.json"
    assert_exists(daemon_path, "docker daemon.json")
    daemon = json.loads(daemon_path.read_text())
    if daemon.get("default-runtime"):
        fail("docker default-runtime must stay unset so CPU guests keep runc")
    nvidia = (daemon.get("runtimes") or {}).get("nvidia") or {}
    if nvidia.get("path") != "nvidia-container-runtime":
        fail("docker daemon.json is missing the nvidia runtime")

    toml = rootfs / "etc/nvidia-container-runtime/config.toml"
    assert_exists(toml, "nvidia-container-runtime config.toml")
    body = toml.read_text()
    if "no-cgroups = true" not in body:
        fail("nvidia config.toml must set no-cgroups = true")
    if "load-kmods = false" not in body:
        fail("nvidia config.toml must set load-kmods = false")

    script = rootfs / "usr/lib/nyabase/nvidia-proc-gpus.sh"
    assert_exists(script, "nvidia-proc-gpus.sh")
    mode = stat.S_IMODE(script.stat().st_mode)
    if mode & 0o111 == 0:
        fail("nvidia-proc-gpus.sh is not executable")
    assert_nvidia_proc_gpus_script(script)

    if not _is_enabled_unit(rootfs, "docker.service"):
        fail("docker.service is not enabled")
    if not _is_enabled_unit(rootfs, "nyabase-nvidia-proc-gpus.service"):
        fail("nyabase-nvidia-proc-gpus.service is not enabled")
    for unit in ("nvidia-cdi-refresh.service", "nvidia-cdi-refresh.path"):
        if not _is_masked_unit(rootfs, unit):
            fail(f"{unit} is not masked")


def assert_nvidia_proc_gpus_script(script: Path) -> None:
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "missing"
        env["NYABASE_PROC_NVIDIA"] = str(missing)
        env["NYABASE_NVIDIA_SMI"] = str(Path(tmp) / "no-smi")
        skipped = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
        if skipped.returncode != 0:
            fail(f"nvidia-proc-gpus.sh without procfs failed: {skipped.stderr}")

        proc = Path(tmp) / "nvidia"
        proc.mkdir()
        smi = Path(tmp) / "nvidia-smi"
        smi.write_text("#!/bin/sh\nexit 1\n")
        smi.chmod(0o755)
        env["NYABASE_PROC_NVIDIA"] = str(proc)
        env["NYABASE_NVIDIA_SMI"] = str(smi)
        failed_smi = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
        if failed_smi.returncode != 0:
            fail(f"nvidia-proc-gpus.sh must ignore nvidia-smi errors: {failed_smi.stderr}")
        if (proc / "gpus").exists():
            fail("nvidia-proc-gpus.sh created gpus/ after nvidia-smi failure")

        smi.write_text("#!/bin/sh\necho '00000000:41:00.0'\necho ' 00000000:81:00.0 '\n")
        ok = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
        if ok.returncode != 0:
            fail(f"nvidia-proc-gpus.sh with fake GPUs failed: {ok.stderr}")
        for pci in ("0000:41:00.0", "0000:81:00.0"):
            if not (proc / "gpus" / pci).is_dir():
                fail(f"nvidia-proc-gpus.sh did not create gpus/{pci}")
        if (proc / "gpus" / "00000000:41:00.0").exists():
            fail("nvidia-proc-gpus.sh kept the 8-digit PCI domain")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument("--squashfs", type=Path)
    parser.add_argument("--metadata-tar", type=Path)
    parser.add_argument("--expected-combined", type=str)
    parser.add_argument("--max-bytes", type=int, default=419_430_400)
    args = parser.parse_args()
    rootfs: Path = args.rootfs
    if not rootfs.is_dir():
        fail(f"rootfs is not a directory: {rootfs}")

    for pkg in (
        "openssh-server",
        "iproute2",
        "iputils-ping",
        "ca-certificates",
        "bash",
        "curl",
        "wget",
        "vim",
        "htop",
        "docker.io",
        "iptables",
        "gnupg",
        "nvidia-container-toolkit",
    ):
        dpkg = run_chroot(rootfs, ["dpkg-query", "-W", "-f=${Status}", pkg])
        if dpkg.returncode != 0 or "install ok installed" not in dpkg.stdout:
            fail(f"required package missing: {pkg}")
    assert_exists(rootfs / "usr/sbin/sshd", "sshd")
    assert_exists(rootfs / "usr/bin/wget", "wget")
    assert_exists(rootfs / "usr/bin/curl", "curl")
    assert_exists(rootfs / "usr/bin/docker", "docker")
    assert_exists(rootfs / "usr/bin/nvidia-container-runtime", "nvidia-container-runtime")
    assert_exists(rootfs / "usr/bin/nvidia-container-cli", "nvidia-container-cli")
    assert_nested_docker(rootfs)
    resolved = rootfs / "etc/systemd/system/systemd-resolved.service"
    if not (resolved.is_symlink() and os.readlink(resolved) == "/dev/null"):
        fail("systemd-resolved.service is not masked")
    ssh_unit = rootfs / "lib/systemd/system/ssh.service"
    ssh_unit_usr = rootfs / "usr/lib/systemd/system/ssh.service"
    if not ssh_unit.exists() and not ssh_unit_usr.exists():
        fail("ssh.service unit missing")
    wants = rootfs / "etc/systemd/system/multi-user.target.wants/ssh.service"
    if not wants.exists() and not wants.is_symlink():
        fail("ssh.service is not enabled (no multi-user.target.wants symlink)")

    (rootfs / "run/sshd").mkdir(parents=True, exist_ok=True)
    keygen = run_chroot(rootfs, ["ssh-keygen", "-A"])
    if keygen.returncode != 0:
        fail(f"ssh-keygen -A failed: {keygen.stderr}")
    dumped = run_chroot(rootfs, ["sshd", "-T"])
    if dumped.returncode != 0:
        fail(f"sshd -T failed: {dumped.stderr}")
    text = dumped.stdout.lower()
    if "passwordauthentication no" not in text:
        fail("sshd -T: PasswordAuthentication is not no")
    if "permitrootlogin prohibit-password" not in text and "permitrootlogin without-password" not in text:
        fail("sshd -T: PermitRootLogin is not prohibit-password")
    if "listenaddress 0.0.0.0" not in text and "listenaddress *" not in text:
        fail("sshd -T: ListenAddress is not 0.0.0.0")
    if args.release.startswith("22"):
        if (
            "kbdinteractiveauthentication no" not in text
            and "challengeresponseauthentication no" not in text
        ):
            fail("sshd -T: kbd/challenge auth not disabled (jammy)")
    elif "kbdinteractiveauthentication no" not in text:
        fail("sshd -T: KbdInteractiveAuthentication is not no")

    resolv = rootfs / "etc/resolv.conf"
    if resolv.is_symlink():
        fail("resolv.conf must be a regular file, not a symlink")

    dpkg = run_chroot(rootfs, ["dpkg-query", "-W", "-f=${Status}", "cloud-init"])
    if dpkg.returncode == 0 and "install ok installed" in dpkg.stdout:
        fail("cloud-init is still installed")

    netplan_dir = rootfs / "etc/netplan"
    if netplan_dir.is_dir():
        for path in netplan_dir.rglob("*"):
            if path.is_file():
                body = path.read_text(errors="ignore").lower()
                if "dhcp4: true" in body or "dhcp6: true" in body:
                    fail(f"netplan DHCP still enabled in {path}")

    for gen_dir in (
        rootfs / "lib/systemd/system-generators",
        rootfs / "usr/lib/systemd/system-generators",
    ):
        if not gen_dir.is_dir():
            continue
        leftover = [
            p.name
            for p in gen_dir.iterdir()
            if p.name.startswith("netplan")
        ]
        if leftover:
            fail(f"netplan generators still present in {gen_dir}: {leftover}")

    network_dir = rootfs / "etc/systemd/network"
    if network_dir.is_dir():
        for path in network_dir.glob("*.network"):
            body = path.read_text(errors="ignore")
            if "Name=eth0" in body or "Name=en" in body:
                fail(f"baked eth0/en .network file {path.name} would shadow runtime persist")

    if args.squashfs is not None:
        size = args.squashfs.stat().st_size
        if size > args.max_bytes:
            fail(f"squashfs {size} bytes exceeds {args.max_bytes}")
        log(f"squashfs size {size} ok")

    if args.metadata_tar is not None:
        if not args.metadata_tar.is_file():
            fail(f"metadata tar missing: {args.metadata_tar}")
        with tarfile.open(args.metadata_tar, "r:xz") as tar, tempfile.TemporaryDirectory() as tmp:
            tar.extractall(tmp, filter="data")
            templates = Path(tmp) / "templates"
            if templates.is_dir():
                for path in templates.rglob("*"):
                    if not path.is_file():
                        continue
                    body = path.read_text(errors="ignore").lower()
                    name = str(path.relative_to(templates))
                    if any(
                        token in body or token in name
                        for token in ("netplan", "interfaces", "authorized_keys", "cloud-init")
                    ):
                        fail(f"metadata template still writes {name}")

    if args.expected_combined:
        if args.squashfs is None or args.metadata_tar is None:
            fail("--expected-combined requires --squashfs and --metadata-tar")
        digest = hashlib.sha256(
            args.metadata_tar.read_bytes() + args.squashfs.read_bytes()
        ).hexdigest()
        if digest != args.expected_combined:
            fail(f"combined hash {digest} != expected {args.expected_combined}")

    log(f"verify_rootfs ok release={args.release}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
