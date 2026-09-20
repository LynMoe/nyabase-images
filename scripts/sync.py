#!/usr/bin/env python3
"""Publish pinned images to the rolling GitHub Release tag.

Publishes only when this repo's catalog `version` is not already on the
release. Upstream linuxcontainers.org is never followed automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
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


def already_published(image: dict, current: dict | None) -> bool:
    if FORCE or current is None:
        return False
    product = (current.get("products") or {}).get(product_key(image)) or {}
    versions = product.get("versions") or {}
    return image["version"] in versions


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
    return {
        "aliases": ",".join(aliases),
        "arch": image["arch"],
        "os": image.get("os_title") or str(image["os"]).capitalize(),
        "release": image["release"],
        "release_title": image["release"],
        "variant": image["variant"],
        "requirements": upstream_product.get("requirements") or {},
        "versions": {
            image["version"]: {
                "items": items,
                "os_upstream_version": serial,
            }
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
        if already_published(image, current):
            log(f"skip {image['id']} version {image['version']} (already on {RELEASE_TAG})")
            continue
        if upstream is None:
            upstream = load_upstream()
        src = (upstream.get("products") or {}).get(image["upstream_product"])
        if not src:
            raise SystemExit(f"upstream product not found: {image['upstream_product']}")
        product, src_items = rewrite_product(image, src)
        serial = image["upstream_version"]
        dest_dir = WORK / image["id"] / f"v{image['version']}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        file_map = {
            "incus.tar.xz": "incus.tar.xz",
            "root.squashfs": "rootfs.squashfs",
        }
        for item_name, filename in file_map.items():
            src_item = src_items[item_name]
            url = f"{UPSTREAM_BASE}/{src_item['path']}"
            dest = dest_dir / asset_name(image, filename)
            log(f"download {image['id']} {serial} {filename}")
            http_download(url, dest, src_item.get("sha256"))
            upload(dest)

        products[key] = product
        changed = True
        log(f"published {image['id']} version {image['version']} from {serial}")

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
