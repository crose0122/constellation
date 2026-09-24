#!/usr/bin/env bash
# Memory Vault — Proxmox VM provisioning (SPEC.md §10, Phase B).
# Run ON EACH PROXMOX HOST (once per host, different VMID/name per host):
#
#   ./provision-vm.sh                     # defaults: a VM, the photo server VM
#   VMID=402 VMNAME=photo-vm-2 ./provision-vm.sh   # on the second host
#
# Creates an Ubuntu 24.04 cloud-init VM with a dedicated data disk for the
# photo library. Adjust STORAGE/BRIDGE to your Proxmox names (`pvesm status`,
# `ip link`) if the defaults don't match.
set -euo pipefail

VMID="${VMID:-401}"
VMNAME="${VMNAME:-the photo server VM}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
CORES="${CORES:-4}"
MEMORY_MB="${MEMORY_MB:-8192}"
OS_DISK_GB="${OS_DISK_GB:-32}"
DATA_DISK_GB="${DATA_DISK_GB:-500}"      # photo library + vault + db
CIUSER="${CIUSER:-photos}"
SSHKEY="${SSHKEY:-$HOME/.ssh/authorized_keys}"
IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
IMG="/var/lib/vz/template/iso/noble-server-cloudimg-amd64.img"

if qm status "$VMID" &>/dev/null; then
  echo "VMID $VMID already exists — pick another with VMID=... " >&2
  exit 1
fi

[ -f "$IMG" ] || wget -O "$IMG" "$IMG_URL"

qm create "$VMID" --name "$VMNAME" --cores "$CORES" --memory "$MEMORY_MB" \
  --cpu host --net0 "virtio,bridge=$BRIDGE" --agent enabled=1 --ostype l26
# --cpu host matters: the default kvm64 vCPU lacks x86-64-v2, and modern NumPy
# wheels refuse to start on it (hit live on the photo server VM, 2026-07-23).
qm importdisk "$VMID" "$IMG" "$STORAGE"
qm set "$VMID" --scsihw virtio-scsi-pci --scsi0 "$STORAGE:vm-$VMID-disk-0"
qm disk resize "$VMID" scsi0 "${OS_DISK_GB}G"
qm set "$VMID" --scsi1 "$STORAGE:${DATA_DISK_GB}"          # /srv/data
qm set "$VMID" --ide2 "$STORAGE:cloudinit" --boot order=scsi0
qm set "$VMID" --ciuser "$CIUSER" --sshkeys "$SSHKEY" --ipconfig0 ip=dhcp
qm start "$VMID"

echo
echo "VM $VMID ($VMNAME) started. Once it has an IP (qm guest cmd $VMID network-get-interfaces):"
echo "  1. ssh $CIUSER@<vm-ip>"
echo "  2. format+mount the data disk:  sudo mkfs.ext4 /dev/sdb && \\"
echo "     echo '/dev/sdb /srv/data ext4 defaults 0 2' | sudo tee -a /etc/fstab && sudo mount -a"
echo "  3. run deploy/setup-vm.sh (from this repo) inside the VM"
