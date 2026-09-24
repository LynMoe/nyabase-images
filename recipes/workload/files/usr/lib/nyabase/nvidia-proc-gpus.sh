#!/bin/bash
# Create the /proc/driver/nvidia/gpus/<pci> dirs that nvidia-container-cli
# stats when Docker runs with --gpus. Incus nvidia.runtime injects a stub
# /proc/driver/nvidia without that tree. PCI addresses are known only after
# the guest has devices, so this runs every boot (the stub is a tmpfs).
#
# No-ops on CPU guests: missing procfs, missing nvidia-smi, or no cards.
set -euo pipefail

proc="${NYABASE_PROC_NVIDIA:-/proc/driver/nvidia}"
smi="${NYABASE_NVIDIA_SMI:-nvidia-smi}"

if [[ ! -d "$proc" ]]; then
  exit 0
fi
if [[ ! -x "$smi" ]] && ! command -v "$smi" >/dev/null 2>&1; then
  exit 0
fi

mapfile -t pcis < <("$smi" --query-gpu=pci.bus_id --format=csv,noheader 2>/dev/null || true)
for pci in "${pcis[@]}"; do
  pci="${pci//$'\r'/}"
  pci="${pci#"${pci%%[![:space:]]*}"}"
  pci="${pci%"${pci##*[![:space:]]}"}"
  if [[ -z "$pci" || "$pci" == "N/A" ]]; then
    continue
  fi
  pci="${pci/#00000000:/0000:}"
  mkdir -p "$proc/gpus/$pci"
done
