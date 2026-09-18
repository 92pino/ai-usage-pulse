#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runtime_dir="$project_dir/.runtime"
config_file="$runtime_dir/config"

if [[ ! -f "$config_file" ]]; then
  echo "Not configured. Run ./setup.sh <device-id> first." >&2
  exit 1
fi

# Values are written by setup.sh after strict identifier validation.
# shellcheck disable=SC1090
source "$config_file"

profile_repo="$runtime_dir/profile"
data_repo="$runtime_dir/data"
generated_dir="$runtime_dir/generated"

for repo_dir in "$profile_repo" "$data_repo"; do
  if [[ ! -d "$repo_dir/.git" ]]; then
    echo "Managed checkout is missing: $repo_dir. Run setup.sh again." >&2
    exit 1
  fi
  if [[ -n "$(git -C "$repo_dir" status --porcelain)" ]]; then
    echo "Managed checkout has uncommitted changes: $repo_dir" >&2
    exit 1
  fi
done

git -C "$profile_repo" pull --rebase
if git -C "$data_repo" rev-parse --verify HEAD >/dev/null 2>&1; then
  git -C "$data_repo" pull --rebase
else
  git -C "$data_repo" switch -C main
fi

mkdir -p "$data_repo/devices" "$generated_dir" "$profile_repo/cards"

python3 "$project_dir/src/usage_card.py" \
  --device "$DEVICE_ID" \
  --state-dir "$data_repo/devices" \
  --output "$generated_dir" \
  --title "$GITHUB_USER's AI Usage"

case "$CARD_VARIANT" in
  dashboard) stems=(ai-usage) ;;
  combo) stems=(ai-usage-combo) ;;
  full) stems=(ai-usage-full) ;;
  compact) stems=(ai-usage-compact) ;;
  half) stems=(ai-usage-half) ;;
  grass) stems=(ai-usage-grass) ;;
  half-grass) stems=(ai-usage-half-grass) ;;
  split) stems=(ai-usage-half ai-usage-grass) ;;
  *) echo "Unsupported CARD_VARIANT in $config_file: $CARD_VARIANT" >&2; exit 1 ;;
esac

for stem in "${stems[@]}"; do
  cp "$generated_dir/$stem-dark.svg" "$profile_repo/cards/"
  cp "$generated_dir/$stem-light.svg" "$profile_repo/cards/"
done

python3 "$project_dir/scripts/install_readme.py" \
  --repo "$profile_repo" \
  --variant "$CARD_VARIANT"

git -C "$data_repo" add devices
if ! git -C "$data_repo" diff --cached --quiet; then
  git -C "$data_repo" commit -m "Update $DEVICE_ID usage"
  git -C "$data_repo" push -u origin main
fi

git -C "$profile_repo" add README.md cards
if ! git -C "$profile_repo" diff --cached --quiet; then
  git -C "$profile_repo" commit -m "Update AI usage dashboard"
  git -C "$profile_repo" push
fi

echo "AI usage dashboard is up to date."
