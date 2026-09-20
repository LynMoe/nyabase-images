# Image catalog

Each directory is one Incus image pin. GitHub Actions publishes **only**
when `version` in that directory's `image.json` is new on the `stable`
release. Upstream linuxcontainers.org is not followed automatically.

| Directory | Alias | Pinned version | Upstream serial |
| :--- | :--- | :--- | :--- |
| `ubuntu-22.04/` | `ubuntu/22.04` | `1` | `20260920_07:42` |
| `ubuntu-24.04/` | `ubuntu/24.04` | `1` | `20260920_07:42` |

To refresh an image:

1. Set `upstream_version` to a serial that still exists on
   https://images.linuxcontainers.org (they keep only a few days).
2. Bump `version` (1 → 2).
3. Push. Action downloads that snapshot onto Release tag `stable`.
