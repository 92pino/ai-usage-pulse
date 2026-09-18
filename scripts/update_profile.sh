#!/usr/bin/env bash
set -euo pipefail

: "${PROFILE_REPO:?Set PROFILE_REPO to the local path of your GitHub profile repository}"
: "${USAGE_CARD_DEVICE:?Set USAGE_CARD_DEVICE to a permanent ID for this computer}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "$(git -C "$PROFILE_REPO" status --porcelain)" ]]; then
  echo "Profile repository has uncommitted changes; commit or stash them first." >&2
  exit 1
fi

git -C "$PROFILE_REPO" pull --rebase

python3 "$PROJECT_DIR/src/usage_card.py" \
  --device "$USAGE_CARD_DEVICE" \
  --state-dir "$PROFILE_REPO/cards/devices" \
  --output "$PROFILE_REPO/cards" \
  --title "${USAGE_CARD_TITLE:-My AI Coding Usage}"

if [[ "${USAGE_CARD_VARIANT:-combo}" != "none" ]]; then
  python3 "$PROJECT_DIR/scripts/install_readme.py" \
    --repo "$PROFILE_REPO" \
    --variant "${USAGE_CARD_VARIANT:-combo}"
fi

git -C "$PROFILE_REPO" add README.md cards
if git -C "$PROFILE_REPO" diff --cached --quiet; then
  echo "Usage cards are already current."
  exit 0
fi

git -C "$PROFILE_REPO" commit -m "Update AI coding usage"
git -C "$PROFILE_REPO" push
