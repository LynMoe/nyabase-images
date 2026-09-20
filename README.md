# nyabase-images

Pinned Ubuntu **system container** images for Incus, mirrored from
[images.linuxcontainers.org](https://images.linuxcontainers.org).

GitHub stores the blobs. A Cloudflare Worker is the simplestreams HTTPS
front door (JSON + **302** to Release assets). GitHub Pages is not used
as the Incus remote.

## Pins (not rolling)

Versions live in `images/*/image.json`:

- `version` — this project's minor. Incus path uses this (`.../default/1/...`).
- `upstream_version` — exact linuxcontainers serial to snapshot.

Actions **do not** track upstream daily builds. Bump `version` (and set a
still-available `upstream_version`) then push.

Current pins: Ubuntu 22.04 **v1** and 24.04 **v1**, both serial
`20260920_07:42`, amd64 `default` (squashfs + metadata only).

## Incus remote

After the Worker is deployed (custom domain recommended):

```bash
incus remote add nyai https://images.example.com --protocol=simplestreams
incus image list nyai:
incus launch nyai:ubuntu/24.04 box
```

nyabase:

```yaml
incus:
  imageSourceServer: https://images.example.com
```

## Worker

`worker/worker.js` — constants at the top default to this repo:

`https://github.com/LynMoe/nyabase-images`, release tag `stable`.

```bash
cd worker && npx wrangler deploy
```

Or paste `worker.js` into the Cloudflare dashboard as an ES module.
Optional env: `GITHUB_OWNER`, `GITHUB_REPO`, `RELEASE_TAG`, `GITHUB_REPO_URL`.

| Path | Behavior |
| :--- | :--- |
| `/streams/v1/index.json` | Proxies Release `index.json` |
| `/streams/v1/images.json` | Proxies Release `images.json` |
| `/images/ubuntu/24.04/amd64/default/1/rootfs.squashfs` | 302 to Release asset |
| `/health` | `{ ok: true }` |

The Worker never streams squashfs through itself.

## Layout

```
images/ubuntu-22.04/image.json   # pin
images/ubuntu-24.04/image.json
scripts/sync.py                  # publish on version bump
worker/worker.js
.github/workflows/sync.yml
```

Blobs are **not** in git. They are GitHub Release assets named
`ubuntu-24.04-amd64-default-v1-rootfs.squashfs` (and `incus.tar.xz`).
