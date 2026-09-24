# Image catalog

Each directory is one Incus image pin. GitHub Actions publishes **only**
when `version` in that directory's `image.json` is new on the `stable`
release. Upstream linuxcontainers.org is not followed automatically.
The simplestreams `versions` key is `bake.serial` (must be
`YYYYMMDD…`); Incus skips short keys such as `1`. Project `version`
is the GitHub/Worker blob pin, not the catalog key.

| Directory | Alias | Pinned version | Bake serial | Upstream serial |
| :--- | :--- | :--- | :--- | :--- |
| `ubuntu-22.04/` | `ubuntu/22.04` | `3` | `20260924_nb01` | `20260920_07:42` |
| `ubuntu-24.04/` | `ubuntu/24.04` | `3` | `20260924_nb01` | `20260920_07:42` |

These aliases are **Nyabase workload images** (sshd, platform-managed networking), not vanilla linuxcontainers snapshots. Vanilla squashfs is kept on Release tag `stable` as `-vanilla-u…` input assets and is **not** a catalog product.

To refresh an image:

1. Set `upstream_version` to a serial that still exists on
   https://images.linuxcontainers.org (they keep only a few days).
2. Bump `version` (2 → 3) and set a new `bake.serial` (`YYYYMMDD_nbNN`).
3. Push. Action downloads that vanilla snapshot as a `-vanilla-u…` input,
   bakes the workload squashfs, and publishes it as the only catalog product.
