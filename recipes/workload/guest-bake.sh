#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export SYSTEMD_OFFLINE=1

printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d

install -d -m 0755 /nyabase-bake
if [[ -f /nyabase-bake/packages.txt ]]; then
  mapfile -t packages < /nyabase-bake/packages.txt
else
  packages=(openssh-server iproute2 iputils-ping ca-certificates bash curl wget vim htop docker.io iptables gnupg)
fi

apt-get update -qq
apt-get install -y --no-install-recommends "${packages[@]}"

install -d -m 0755 /usr/share/keyrings
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
chmod 0644 /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
cat > /etc/apt/sources.list.d/nvidia-container-toolkit.list <<'EOF'
deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /
EOF
apt-get update -qq
apt-get install -y --no-install-recommends nvidia-container-toolkit

apt-get purge -y 'cloud-init*' || true
if ! apt-get purge -y netplan.io 2>/dev/null; then
  install -d -m 0755 /etc/netplan
  if [[ -f /nyabase-bake/99-nyabase.yaml ]]; then
    cp /nyabase-bake/99-nyabase.yaml /etc/netplan/99-nyabase.yaml
  fi
fi
rm -f /etc/netplan/10-lxc.yaml /etc/netplan/*lxc* || true
rm -f /lib/systemd/system-generators/netplan* \
  /usr/lib/systemd/system-generators/netplan* || true
if [[ -d /etc/netplan ]]; then
  find /etc/netplan -type f -name '*.yaml' ! -name '99-nyabase.yaml' -delete || true
fi

rm -f /etc/systemd/network/*eth0* /etc/systemd/network/*en* || true

install -d -m 0755 /etc/ssh/sshd_config.d
if [[ -f /nyabase-bake/99-nyabase.conf ]]; then
  cp /nyabase-bake/99-nyabase.conf /etc/ssh/sshd_config.d/99-nyabase.conf
fi

install -d -m 0755 /run/sshd
install -d -m 0700 /root/.ssh
chmod 0700 /root/.ssh
rm -f /root/.ssh/authorized_keys /root/.ssh/id_* || true
if id ubuntu >/dev/null 2>&1; then
  passwd -l ubuntu >/dev/null
  rm -f /home/ubuntu/.ssh/authorized_keys || true
fi

apt-get clean
rm -rf /var/lib/apt/lists/*
rm -f /usr/sbin/policy-rc.d
