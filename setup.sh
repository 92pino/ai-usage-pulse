#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: ./setup.sh <device-id>" >&2
  echo "Example: ./setup.sh work-macbook" >&2
  exit 2
fi

device_id="$1"
if [[ ! "$device_id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]; then
  echo "Device ID must use 1-64 letters, numbers, underscores, or hyphens." >&2
  exit 2
fi

for command_name in git gh python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

gh auth status >/dev/null

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runtime_dir="$project_dir/.runtime"
github_user="$(gh api user --jq .login)"
profile_slug="$github_user/$github_user"
data_slug="${AI_USAGE_DATA_REPO:-$github_user/ai-usage-pulse-data}"
if [[ ! "$data_slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "AI_USAGE_DATA_REPO must use the OWNER/REPOSITORY format." >&2
  exit 2
fi

mkdir -p "$runtime_dir"

if ! gh repo view "$data_slug" >/dev/null 2>&1; then
  echo "Creating private ledger repository: $data_slug"
  gh repo create "$data_slug" \
    --private \
    --description "Private multi-device ledgers for AI Usage Pulse"
fi

if [[ ! -d "$runtime_dir/profile/.git" ]]; then
  gh repo clone "$profile_slug" "$runtime_dir/profile"
fi

if [[ ! -d "$runtime_dir/data/.git" ]]; then
  gh repo clone "$data_slug" "$runtime_dir/data"
fi

cat > "$runtime_dir/config" <<EOF
DEVICE_ID=$device_id
GITHUB_USER=$github_user
PROFILE_SLUG=$profile_slug
DATA_SLUG=$data_slug
CARD_VARIANT=combo
EOF

echo "Configured device: $device_id"
echo "Profile: $profile_slug"
echo "Private ledgers: $data_slug"

exec "$project_dir/update.sh"
