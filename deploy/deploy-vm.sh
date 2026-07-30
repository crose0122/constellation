#!/usr/bin/env bash
# Deploy the pipeline to the pipeline host from git, and only from git.
#
# Why this exists: two agent sessions were scp'ing individual files to the VM
# and silently clobbering each other. One renamed the package brain/ ->
# constellation/; the other's cli.py landed on top and removed a flag that had
# just been added, so `curate --screen-captures` came back "unrecognized
# arguments" hours later. Before that, an even quieter failure: a stale cli.py
# meant argparse prefix-matched `--screens` to `--screenshots` and ran the
# WRONG pass without erroring at all.
#
# The VM cannot `git pull` — its remote still points at the old org and it has
# no deploy key (`Permission denied (publickey)`). So the rig, which can reach
# both GitHub and the VM, exports a tree from origin and rsyncs it over. The
# deployed state is therefore always exactly a committed, pushed commit —
# never one session's working copy.
#
#   deploy-vm.sh              # deploy origin/master
#   deploy-vm.sh <ref>        # deploy any ref
#   DRY=1 deploy-vm.sh        # show what would change, touch nothing
set -euo pipefail

REPO="${REPO:-$HOME/your-repo}"
VM="${VM:-you@your-pipeline-host}"
DEST="${DEST:-$HOME/your-repo/MemoryVault/.scripts}"
# The unit has been renamed once already (brain -> constellation), so find it
# rather than hardcode it. A deploy that "succeeds" while restarting nothing is
# worse than one that fails.
SERVICE="${SERVICE:-}"
REF="${1:-origin/master}"
DRY="${DRY:-}"

cd "$REPO"

# 1. Deploy only what is committed AND pushed. Deploying a dirty tree is how
#    the VM ended up holding code that existed nowhere else.
git fetch -q origin 2>/dev/null || echo "warning: could not fetch; using local refs"
if [ -n "$(git status --porcelain -- MemoryVault/.scripts)" ]; then
  echo "REFUSING: .scripts has uncommitted changes. Commit and push first:"
  git status --short -- MemoryVault/.scripts
  exit 1
fi
SHA=$(git rev-parse --short "$REF")
echo "deploying $REF ($SHA) -> $VM:$DEST"

# 2. Export the tree from git into a staging dir — never from the working copy.
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
git archive "$REF" MemoryVault/.scripts | tar -x -C "$STAGE"
SRC="$STAGE/MemoryVault/.scripts"
[ -d "$SRC/memoryvault" ] || { echo "REFUSING: export has no memoryvault package"; exit 1; }

# 3. Sync. --delete so the VM matches git exactly: a file deleted or renamed in
#    git (brain/ -> constellation/) must actually disappear, or stale modules
#    keep being importable and the next failure is a mystery.
# --delete-excluded too: __pycache__ inside a stale directory otherwise
# makes rsync report "cannot delete non-empty directory" and leave it.
RSYNC_OPTS=(-rlt --delete --delete-excluded \
            --exclude '__pycache__' --exclude '*.pyc')
[ -n "$DRY" ] && RSYNC_OPTS+=(--dry-run -i)
rsync "${RSYNC_OPTS[@]}" "$SRC/" "$VM:$DEST/"
[ -n "$DRY" ] && { echo "(dry run — nothing changed)"; exit 0; }

# 4. Prove the deployed code imports before restarting anything on top of it.
ssh "$VM" "cd $DEST && /opt/memoryvault/venv/bin/python -c \"
import sys; sys.path.insert(0, '.')
import memoryvault.cli, memoryvault.curate, memoryvault.screen, memoryvault.ingest
print('  imports OK')
\"" || { echo "DEPLOY FAILED: code does not import — service left running on the old tree"; exit 1; }

if [ -z "$SERVICE" ]; then
  SERVICE=$(ssh "$VM" "systemctl list-units --all --no-legend 'memoryvault-*.service' \
    | awk '{print \$1}' | grep -E 'constellation|brain' | head -1")
fi
[ -n "$SERVICE" ] || { echo "DEPLOY WARNING: no constellation/brain unit found; nothing restarted"; exit 1; }
ssh "$VM" "sudo systemctl restart $SERVICE" && sleep 4
state=$(ssh "$VM" "systemctl is-active $SERVICE")
echo "  service: $SERVICE -> $state"
[ "$state" = "active" ] || { echo "DEPLOY FAILED: service did not come back"; exit 1; }
# prove it actually serves, not merely that systemd says active
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://${VM#*@}:8484/" || echo 000)
echo "  http: $code"
[ "$code" = "200" ] || { echo "DEPLOY FAILED: service is up but not serving"; exit 1; }
echo "  deployed $SHA"
