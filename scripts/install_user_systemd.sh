#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
project_dir_escaped="${project_dir//&/\\&}"
unit_dir="${HOME}/.config/systemd/user"

mkdir -p "${unit_dir}"

for service in campushallen-bot.service campushallen-bot-dry-run.service; do
  sed "s|__PROJECT_DIR__|${project_dir_escaped}|g" \
    "${project_dir}/systemd/${service}" > "${unit_dir}/${service}"
done

cp "${project_dir}"/systemd/campushallen-bot*.timer "${unit_dir}/"
systemctl --user daemon-reload
systemctl --user enable --now campushallen-bot-dry-run.timer
systemctl --user list-timers 'campushallen*'
