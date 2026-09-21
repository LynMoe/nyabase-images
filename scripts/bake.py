#!/usr/bin/env python3
"""Bake a Nyabase workload squashfs from a pinned vanilla Ubuntu snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "recipes" / "workload"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:
    log("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, **kwargs)


def combined_sha256(metadata: Path, squashfs: Path) -> str:
    digest = hashlib.sha256()
    digest.update(metadata.read_bytes())
    digest.update(squashfs.read_bytes())
    return digest.hexdigest()


def unpack_squashfs(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    run(["unsquashfs", "-f", "-d", str(dest), str(src)])


def pack_squashfs(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    run(
        [
            "mksquashfs",
            str(src),
            str(dest),
            "-comp",
            "xz",
            "-b",
            "1M",
            "-xattrs",
            "-noappend",
        ]
    )


def chroot_bake(rootfs: Path) -> None:
    bake_dir = rootfs / "nyabase-bake"
    bake_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(RECIPE / "packages.txt", bake_dir / "packages.txt")
    shutil.copy(RECIPE / "guest-bake.sh", bake_dir / "guest-bake.sh")
    shutil.copy(
        RECIPE / "files/etc/ssh/sshd_config.d/99-nyabase.conf",
        bake_dir / "99-nyabase.conf",
    )
    shutil.copy(
        RECIPE / "files/etc/netplan/99-nyabase.yaml",
        bake_dir / "99-nyabase.yaml",
    )
    os.chmod(bake_dir / "guest-bake.sh", 0o755)

    resolv = rootfs / "etc/resolv.conf"
    if resolv.is_symlink() or not resolv.exists():
        resolv.unlink(missing_ok=True)
        resolv.write_text("nameserver 1.1.1.1\n")
    (rootfs / "etc/machine-id").write_text("")

    mounts = []
    for name in ("proc", "sys", "dev"):
        target = rootfs / name
        target.mkdir(exist_ok=True)
        run(["mount", "--bind", f"/{name}", str(target)])
        mounts.append(target)
    env = os.environ.copy()
    env["SYSTEMD_OFFLINE"] = "1"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    try:
        run(
            ["chroot", str(rootfs), "/bin/bash", "/nyabase-bake/guest-bake.sh"],
            env=env,
        )
        run(["systemctl", "--root", str(rootfs), "mask", "systemd-resolved"], check=False)
        run(["systemctl", "--root", str(rootfs), "disable", "systemd-resolved"], check=False)
        run(["systemctl", "--root", str(rootfs), "disable", "NetworkManager"], check=False)
        enable = subprocess.run(
            ["systemctl", "--root", str(rootfs), "enable", "ssh.service"],
            check=False,
            text=True,
        )
        if enable.returncode != 0:
            wants = rootfs / "etc/systemd/system/multi-user.target.wants"
            wants.mkdir(parents=True, exist_ok=True)
            unit = "ssh.service"
            src = Path("/lib/systemd/system") / unit
            if not (rootfs / "lib/systemd/system/ssh.service").exists():
                src = Path("/usr/lib/systemd/system") / unit
            link = wants / unit
            if not link.exists():
                link.symlink_to(src)
        run(["chroot", str(rootfs), "env", "SYSTEMD_OFFLINE=1", "ssh-keygen", "-A"])
    finally:
        for target in reversed(mounts):
            subprocess.run(["umount", "-l", str(target)], check=False)
        shutil.rmtree(bake_dir, ignore_errors=True)
        resolv.unlink(missing_ok=True)
        resolv.write_text("# nyabase: runtime applyGuestNetwork writes nameservers\n")


def set_metadata_properties(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_props = False
    seen: set[str] = set()
    wrote_block = False
    for line in lines:
        if line.startswith("properties:"):
            in_props = True
            wrote_block = True
            out.append(line)
            continue
        if in_props:
            if line and not line[0].isspace() and not line.startswith("#"):
                for key, value in updates.items():
                    if key not in seen:
                        out.append(f"  {key}: {value}")
                in_props = False
                out.append(line)
                continue
            stripped = line.lstrip()
            key = stripped.split(":", 1)[0].strip() if ":" in stripped else ""
            indent = line[: len(line) - len(stripped)]
            if key in updates:
                out.append(f"{indent}{key}: {updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    if in_props:
        for key, value in updates.items():
            if key not in seen:
                out.append(f"  {key}: {value}")
    elif not wrote_block:
        out.append("properties:")
        for key, value in updates.items():
            out.append(f"  {key}: {value}")
    return "\n".join(out).rstrip() + "\n"


def rewrite_metadata(src_tar: Path, dest_tar: Path, image: dict) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        with tarfile.open(src_tar, "r:xz") as tar:
            tar.extractall(work, filter="data")
        meta_path = work / "metadata.yaml"
        text = meta_path.read_text() if meta_path.exists() else ""
        templates = work / "templates"
        if templates.is_dir():
            for path in list(templates.rglob("*")):
                if not path.is_file():
                    continue
                body = path.read_text(errors="ignore").lower()
                name = str(path.relative_to(templates))
                if any(
                    token in body or token in name
                    for token in ("netplan", "interfaces", "authorized_keys", "cloud-init")
                ):
                    path.unlink()
        text = set_metadata_properties(
            text,
            {
                "serial": str(image["bake"]["serial"]),
                "nyabase.bake_recipe": str(image["bake"]["recipe"]),
                "nyabase.upstream_serial": str(image["upstream_version"]),
                "nyabase.login_user": "root",
                "nyabase.network_managed_externally": "true",
                "nyabase.compressor": "xz",
            },
        )
        meta_path.write_text(text)
        with tarfile.open(dest_tar, "w:xz") as tar:
            for path in sorted(work.rglob("*")):
                if path.is_file() or path.is_symlink():
                    tar.add(path, arcname=str(path.relative_to(work)))


def bake(image: dict, vanilla_squash: Path, vanilla_meta: Path, out_dir: Path) -> tuple[Path, Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rootfs = Path(tmp) / "rootfs"
        unpack_squashfs(vanilla_squash, rootfs)
        chroot_bake(rootfs)
        install = rootfs / "etc/nyabase"
        install.mkdir(parents=True, exist_ok=True)
        (install / "image-provenance.json").write_text(
            json.dumps(
                {
                    "bake_recipe": image["bake"]["recipe"],
                    "bake_serial": image["bake"]["serial"],
                    "upstream_serial": image["upstream_version"],
                },
                indent=2,
            )
            + "\n"
        )
        baked_squash = out_dir / "rootfs.squashfs"
        pack_squashfs(rootfs, baked_squash)
        baked_meta = out_dir / "incus.tar.xz"
        rewrite_metadata(vanilla_meta, baked_meta, image)
        digest = combined_sha256(baked_meta, baked_squash)
        run(
            [
                "python3",
                str(ROOT / "scripts" / "verify_rootfs.py"),
                "--rootfs",
                str(rootfs),
                "--release",
                str(image["release"]),
                "--squashfs",
                str(baked_squash),
                "--metadata-tar",
                str(baked_meta),
                "--expected-combined",
                digest,
            ]
        )
        log(f"baked {image['id']} combined_sha256={digest}")
        return baked_meta, baked_squash, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-json", required=True, type=Path)
    parser.add_argument("--vanilla-squashfs", required=True, type=Path)
    parser.add_argument("--vanilla-incus-tar", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    image = json.loads(args.image_json.read_text())
    if "bake" not in image:
        raise SystemExit(f"{args.image_json} missing bake.serial")
    bake(image, args.vanilla_squashfs, args.vanilla_incus_tar, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
