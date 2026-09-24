#!/usr/bin/env python3
"""Publish pinned images to the rolling GitHub Release tag.

Publishes only when this repo's catalog `version` is not already on the
release. Upstream linuxcontainers.org is never followed automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "images"
UPSTREAM_BASE = os.environ.get(
    "UPSTREAM_BASE", "https://images.linuxcontainers.org"
).rstrip("/")
RELEASE_TAG = os.environ.get("RELEASE_TAG", "stable")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "LynMoe/nyabase-images")
WORK = Path(os.environ.get("SYNC_WORK_DIR", ROOT / ".work"))
USER_AGENT = "nyabase-images-sync/1.0"
CONTAINER_ITEMS = ("incus.tar.xz", "root.squashfs")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def http_download(url: str, dest: Path, expected_sha256: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=600) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
    actual = digest.hexdigest()
    if expected_sha256 and actual != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"sha256 mismatch for {url}: got {actual} want {expected_sha256}")
    tmp.replace(dest)


def load_catalog() -> list[dict]:
    images = []
    for path in sorted(IMAGES_DIR.glob("*/image.json")):
        data = json.loads(path.read_text())
        for key in ("id", "os", "release", "arch", "variant", "version", "upstream_product", "upstream_version"):
            if not data.get(key):
                raise SystemExit(f"{path} missing {key}")
        data["_path"] = path
        images.append(data)
    if not images:
        raise SystemExit("no images/*/image.json found")
    return images


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def published_images_json() -> dict | None:
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{RELEASE_TAG}/images.json"
    try:
        return json.loads(http_get(url))
    except Exception:
        return None


def product_key(image: dict) -> str:
    return f"{image['os']}:{image['release']}:{image['arch']}:{image['variant']}"


def asset_name(image: dict, filename: str) -> str:
    return (
        f"{image['os']}-{image['release']}-{image['arch']}-"
        f"{image['variant']}-v{image['version']}-{filename}"
    )


def stream_path(image: dict, filename: str) -> str:
    return (
        f"images/{image['os']}/{image['release']}/{image['arch']}/"
        f"{image['variant']}/{image['version']}/{filename}"
    )


def vanilla_asset_name(image: dict, filename: str) -> str:
    serial = str(image["upstream_version"]).replace(":", "-")
    return (
        f"{image['os']}-{image['release']}-{image['arch']}-"
        f"{image['variant']}-vanilla-u{serial}-{filename}"
    )


def simplestreams_version(image: dict) -> str:
    """Incus skips version ids shorter than 8 chars or not YYYYMMDD-prefixed.

    Project `version` is the GitHub/Worker blob pin. The catalog version key
    is bake.serial (YYYYMMDD_nbNN) when present, else the upstream serial.
    """
    bake = image.get("bake") or {}
    serial = str(bake.get("serial") or image["upstream_version"])
    if len(serial) < 8:
        raise SystemExit(
            f"{image['id']}: upstream_version {serial!r} is not an Incus "
            "simplestreams version (need YYYYMMDD…)"
        )
    try:
        time.strptime(serial[:8], "%Y%m%d")
    except ValueError as exc:
        raise SystemExit(
            f"{image['id']}: upstream_version {serial!r} must start with YYYYMMDD"
        ) from exc
    return serial


def path_marker(image: dict) -> str:
    return f"/{image['variant']}/{image['version']}/"


def product_has_project_blobs(product: dict, image: dict) -> bool:
    marker = path_marker(image)
    for version in (product.get("versions") or {}).values():
        for item in (version.get("items") or {}).values():
            if marker in str(item.get("path") or ""):
                return True
    return False


def versions_incus_visible(product: dict) -> bool:
    for name in product.get("versions") or {}:
        if len(name) >= 8:
            try:
                time.strptime(name[:8], "%Y%m%d")
                return True
            except ValueError:
                continue
    return False


def already_published(image: dict, current: dict | None) -> bool:
    if current is None:
        return False
    product = (current.get("products") or {}).get(product_key(image)) or {}
    return product_has_project_blobs(product, image)


def release_asset_url(name: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/releases/download/{RELEASE_TAG}/{name}"


def try_download_release_asset(name: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        http_download(release_asset_url(name), dest)
        return True
    except Exception as exc:
        dest.unlink(missing_ok=True)
        log(f"release asset miss {name}: {exc}")
        return False


def product_from_pin(image: dict) -> dict:
    serial = simplestreams_version(image)
    return {
        "aliases": ",".join(image.get("aliases") or []),
        "arch": image["arch"],
        "os": image.get("os_title") or str(image["os"]).capitalize(),
        "release": image["release"],
        "release_title": image["release"],
        "variant": image["variant"],
        "requirements": {},
        "versions": {
            serial: {
                "items": {
                    "incus.tar.xz": {
                        "ftype": "incus.tar.xz",
                        "path": stream_path(image, "incus.tar.xz"),
                    },
                    "root.squashfs": {
                        "ftype": "squashfs",
                        "path": stream_path(image, "rootfs.squashfs"),
                    },
                },
                "os_upstream_version": serial,
                "label": f"nyabase-v{image['version']}",
            }
        },
    }


def catalog_combined_hash(product: dict) -> str | None:
    for version in (product.get("versions") or {}).values():
        for item in (version.get("items") or {}).values():
            digest = item.get("combined_squashfs_sha256")
            if digest:
                return str(digest)
    return None


def force_reupload_identical(image: dict, existing: dict) -> None:
    dest_dir = WORK / image["id"] / f"v{image['version']}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    meta_name = asset_name(image, "incus.tar.xz")
    squash_name = asset_name(image, "rootfs.squashfs")
    meta = dest_dir / meta_name
    squash = dest_dir / squash_name
    for name, dest in ((meta_name, meta), (squash_name, squash)):
        if dest.exists():
            continue
        if not try_download_release_asset(name, dest):
            raise SystemExit(
                f"FORCE {image['id']} v{image['version']}: {name} is not on {RELEASE_TAG}; "
                "refusing to re-bake a new combined hash"
            )
    digest = hashlib.sha256(meta.read_bytes() + squash.read_bytes()).hexdigest()
    expected = catalog_combined_hash(existing)
    if expected and expected != digest:
        raise SystemExit(
            f"FORCE refused {image['id']}: combined {digest} != catalog {expected}"
        )
    upload(meta)
    upload(squash)
    log(f"FORCE re-uploaded identical {image['id']} v{image['version']} combined {digest}")


def relabel_product_version(image: dict, product: dict) -> dict:
    serial = simplestreams_version(image)
    marker = path_marker(image)
    chosen: dict | None = None
    for version in (product.get("versions") or {}).values():
        items = version.get("items") or {}
        if any(marker in str(item.get("path") or "") for item in items.values()):
            chosen = version
            break
    if chosen is None:
        raise SystemExit(f"{image['id']}: no items for project version {image['version']}")
    labeled = dict(chosen)
    labeled["os_upstream_version"] = serial
    out = dict(product)
    out["versions"] = {serial: labeled}
    return out


def load_upstream() -> dict:
    return json.loads(http_get(f"{UPSTREAM_BASE}/streams/v1/images.json"))


def rewrite_product(image: dict, upstream_product: dict) -> dict:
    serial = image["upstream_version"]
    versions = upstream_product.get("versions") or {}
    if serial not in versions:
        available = ", ".join(sorted(versions)[-5:])
        raise SystemExit(
            f"{image['id']}: upstream_version {serial} not on {UPSTREAM_BASE} "
            f"(still present: {available or 'none'}). "
            "linuxcontainers only keeps a few days of builds; bump while the serial exists."
        )
    src_items = versions[serial].get("items") or {}
    missing = [name for name in CONTAINER_ITEMS if name not in src_items]
    if missing:
        raise SystemExit(f"{image['id']} {serial} missing items: {missing}")

    items = {}
    meta = dict(src_items["incus.tar.xz"])
    squash = dict(src_items["root.squashfs"])
    meta["path"] = stream_path(image, "incus.tar.xz")
    squash["path"] = stream_path(image, "rootfs.squashfs")
    items["incus.tar.xz"] = meta
    items["root.squashfs"] = squash

    aliases = image.get("aliases") or []
    version_entry: dict = {
        "items": items,
        "os_upstream_version": serial,
        "label": f"nyabase-v{image['version']}",
    }
    return {
        "aliases": ",".join(aliases),
        "arch": image["arch"],
        "os": image.get("os_title") or str(image["os"]).capitalize(),
        "release": image["release"],
        "release_title": image["release"],
        "variant": image["variant"],
        "requirements": upstream_product.get("requirements") or {},
        "versions": {
            simplestreams_version(image): version_entry,
        },
    }, src_items


def ensure_release() -> None:
    view = gh("release", "view", RELEASE_TAG, "--repo", GITHUB_REPO, check=False)
    if view.returncode == 0:
        return
    log(f"creating release {RELEASE_TAG}")
    gh(
        "release",
        "create",
        RELEASE_TAG,
        "--repo",
        GITHUB_REPO,
        "--title",
        RELEASE_TAG,
        "--notes",
        "Rolling Incus simplestreams assets. Version pins live in images/*/image.json.",
    )


def upload(path: Path) -> None:
    log(f"upload {path.name}")
    gh(
        "release",
        "upload",
        RELEASE_TAG,
        str(path),
        "--repo",
        GITHUB_REPO,
        "--clobber",
    )


def main() -> int:
    catalog = load_catalog()
    current = published_images_json()
    ensure_release()

    products: dict[str, dict] = {}
    if current and isinstance(current.get("products"), dict):
        products = dict(current["products"])

    changed = False
    upstream = None
    WORK.mkdir(parents=True, exist_ok=True)

    for image in catalog:
        key = product_key(image)
        existing = products.get(key) or {}
        if already_published(image, current):
            if FORCE:
                force_reupload_identical(image, existing)
            elif versions_incus_visible(existing) and simplestreams_version(image) in (
                existing.get("versions") or {}
            ):
                log(f"skip {image['id']} version {image['version']} (already on {RELEASE_TAG})")
                continue
            products[key] = relabel_product_version(image, existing)
            changed = True
            log(
                f"relabel {image['id']} simplestreams version "
                f"{simplestreams_version(image)} (blobs already on {RELEASE_TAG})"
            )
            continue
        serial = image["upstream_version"]
        dest_dir = WORK / image["id"] / f"v{image['version']}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        vanilla_dir = WORK / "vanilla" / image["id"] / str(serial).replace(":", "-")
        vanilla_dir.mkdir(parents=True, exist_ok=True)
        vanilla_files = {
            "incus.tar.xz": vanilla_asset_name(image, "incus.tar.xz"),
            "rootfs.squashfs": vanilla_asset_name(image, "rootfs.squashfs"),
        }
        missing_vanilla = [
            filename
            for filename, vanilla_name in vanilla_files.items()
            if not (vanilla_dir / vanilla_name).exists()
        ]
        for filename in list(missing_vanilla):
            vanilla_name = vanilla_files[filename]
            dest = vanilla_dir / vanilla_name
            if try_download_release_asset(vanilla_name, dest):
                log(f"reuse release vanilla {vanilla_name}")
                missing_vanilla.remove(filename)
        product: dict | None = None
        if missing_vanilla:
            if upstream is None:
                upstream = load_upstream()
            src = (upstream.get("products") or {}).get(image["upstream_product"])
            if not src:
                raise SystemExit(f"upstream product not found: {image['upstream_product']}")
            product, src_items = rewrite_product(image, src)
            upstream_names = {
                "incus.tar.xz": "incus.tar.xz",
                "rootfs.squashfs": "root.squashfs",
            }
            for filename in missing_vanilla:
                dest = vanilla_dir / vanilla_files[filename]
                src_item = src_items[upstream_names[filename]]
                url = f"{UPSTREAM_BASE}/{src_item['path']}"
                log(f"download vanilla {image['id']} {serial} {filename}")
                try:
                    http_download(url, dest, src_item.get("sha256"))
                except Exception as exc:
                    raise SystemExit(
                        f"vanilla input missing for {image['id']} {serial}: "
                        f"not on {RELEASE_TAG} and upstream fetch failed: {exc}"
                    ) from exc
                upload(dest)
        if product is None:
            product = product_from_pin(image)

        if not image.get("bake"):
            raise SystemExit(f"{image['id']} missing bake.serial; vanilla is not a catalog product")
        bake_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "bake.py"),
            "--image-json",
            str(image["_path"]),
            "--vanilla-squashfs",
            str(vanilla_dir / vanilla_files["rootfs.squashfs"]),
            "--vanilla-incus-tar",
            str(vanilla_dir / vanilla_files["incus.tar.xz"]),
            "--out-dir",
            str(dest_dir),
        ]
        log(f"bake {image['id']}")
        subprocess.run(bake_cmd, check=True)

        baked_meta = dest_dir / "incus.tar.xz"
        baked_squash = dest_dir / "rootfs.squashfs"
        meta_asset = dest_dir / asset_name(image, "incus.tar.xz")
        squash_asset = dest_dir / asset_name(image, "rootfs.squashfs")
        shutil.copy2(baked_meta, meta_asset)
        shutil.copy2(baked_squash, squash_asset)
        upload(meta_asset)
        upload(squash_asset)

        digest = hashlib.sha256(meta_asset.read_bytes() + baked_squash.read_bytes()).hexdigest()
        version_key = simplestreams_version(image)
        items = product["versions"][version_key]["items"]
        items["incus.tar.xz"]["sha256"] = hashlib.sha256(meta_asset.read_bytes()).hexdigest()
        items["incus.tar.xz"]["size"] = meta_asset.stat().st_size
        items["root.squashfs"]["sha256"] = hashlib.sha256(squash_asset.read_bytes()).hexdigest()
        items["root.squashfs"]["size"] = squash_asset.stat().st_size
        items["root.squashfs"]["combined_squashfs_sha256"] = digest
        items["incus.tar.xz"]["combined_squashfs_sha256"] = digest

        products[key] = product
        changed = True
        log(f"published baked {image['id']} version {image['version']} serial {version_key}")

    index = {
        "format": "index:1.0",
        "index": {
            "images": {
                "datatype": "image-downloads",
                "path": "streams/v1/images.json",
                "format": "products:1.0",
                "products": sorted(products),
            }
        },
    }
    images_json = {
        "content_id": "images",
        "datatype": "image-downloads",
        "format": "products:1.0",
        "products": products,
    }

    if not changed and current is not None:
        log("no catalog version changes; leaving release as-is")
        return 0

    index_path = WORK / "index.json"
    images_path = WORK / "images.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n")
    images_path.write_text(json.dumps(images_json, indent=2) + "\n")
    upload(index_path)
    upload(images_path)
    log("sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
