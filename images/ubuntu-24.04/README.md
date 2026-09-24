# Ubuntu 24.04 (Noble)

Pinned project version: **3** (Nyabase workload bake `20260924_nb01`)  
Upstream snapshot input: `ubuntu:noble:amd64:default` @ `20260920_07:42`  
Baked with `recipes/workload` (sshd, nested Docker + NVIDIA toolkit, no guest DHCP, root key login).

Do not follow upstream daily builds. To refresh, bump `version` in
`image.json` and set `upstream_version` to a serial that still exists
upstream, then push. GitHub Actions publishes only on that change.
