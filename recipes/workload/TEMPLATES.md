# incus.tar.xz templates

Dump the upstream tarball (`tar -tf`) when bumping a pin. Keep/strip:

| Path | Action |
| :--- | :--- |
| `metadata.yaml` | rewrite properties; keep architecture / creation_date as needed |
| `templates/hostname.tpl` | keep |
| `templates/hosts.tpl` | keep |
| `templates/machine-id.tpl` | keep |
| anything writing `/etc/netplan/*` | strip |
| anything writing `/etc/network/interfaces` | strip |
| anything writing `authorized_keys` | strip |
| anything writing cloud-init userdata/network | strip |
