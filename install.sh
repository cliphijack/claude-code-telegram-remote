#!/usr/bin/env bash
# install.sh — cross-platform installer for the Telegram remote poller.
#
# Supported platforms:
#   - macOS (launchd user agent)
#   - Linux (systemd user service)
#
# Usage:
#   ./install.sh            # install and start the service
#   ./install.sh --uninstall  # stop and remove the service
#
# Assumes this script lives at ~/.claude/channels/telegram/ (repo layout).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${SCRIPT_DIR}/deploy"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '\033[36m→\033[0m %s\n' "$*"; }

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)  echo "linux" ;;
    *)      echo "unsupported" ;;
  esac
}

require_file() {
  if [ ! -f "$1" ]; then
    red "Missing required file: $1"
    exit 1
  fi
}

bootstrap_env() {
  local env_file="${SCRIPT_DIR}/.env"
  local env_example="${SCRIPT_DIR}/.env.example"

  if [ -f "$env_file" ]; then
    info ".env exists — leaving as-is"
    return
  fi

  require_file "$env_example"
  cp "$env_example" "$env_file"
  chmod 600 "$env_file"
  red "Created $env_file from template."
  red "Edit it now with your TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TMUX_TARGET / ALLOWED_USER_IDS, then re-run this script."
  exit 2
}

check_deps() {
  local missing=()
  command -v python3 >/dev/null 2>&1 || missing+=("python3")
  command -v curl    >/dev/null 2>&1 || missing+=("curl")
  command -v tmux    >/dev/null 2>&1 || missing+=("tmux")
  if [ ${#missing[@]} -gt 0 ]; then
    red "Missing dependencies: ${missing[*]}"
    red "Install them first, then re-run."
    exit 1
  fi
}

install_macos() {
  local label="com.$(whoami).tg-poll"
  local plist_target="${HOME}/Library/LaunchAgents/${label}.plist"
  local template="${DEPLOY_DIR}/com.example.tg-poll.plist"
  require_file "$template"

  # Pin the absolute python3 path so launchd doesn't fall back to Apple's
  # /usr/bin/python3 which lacks Quartz (PyObjC) for window-scoped screenshots.
  local python_bin
  python_bin="$(command -v python3 || echo /usr/bin/python3)"

  mkdir -p "${HOME}/Library/LaunchAgents"
  sed -e "s|__HOME__|${HOME}|g" \
      -e "s|__PYTHON__|${python_bin}|g" \
      -e "s|com.example.tg-poll|${label}|g" \
      "$template" > "$plist_target"

  # Unload any previous version so the new plist is re-read.
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist_target"
  launchctl kickstart -k "gui/$(id -u)/${label}"

  green "Installed ${label} (launchd)."
  info  "Logs: ${SCRIPT_DIR}/launchd.out.log"
  info  "Restart: launchctl kickstart -k gui/\$(id -u)/${label}"
}

uninstall_macos() {
  local label="com.$(whoami).tg-poll"
  local plist_target="${HOME}/Library/LaunchAgents/${label}.plist"
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  rm -f "$plist_target"
  green "Removed ${label} (launchd)."
}

install_linux() {
  local unit_target="${HOME}/.config/systemd/user/tg-poll.service"
  local template="${DEPLOY_DIR}/tg-poll.service"
  require_file "$template"

  if ! command -v systemctl >/dev/null 2>&1; then
    red "systemctl not found. This installer supports systemd-based Linux only."
    red "For other init systems, run tg_poll.py manually or write a wrapper."
    exit 1
  fi

  local python_bin
  python_bin="$(command -v python3 || echo /usr/bin/python3)"

  mkdir -p "$(dirname "$unit_target")"
  sed -e "s|__PYTHON__|${python_bin}|g" "$template" > "$unit_target"

  systemctl --user daemon-reload
  systemctl --user enable --now tg-poll.service

  # Ensure the service keeps running after logout (requires linger).
  if command -v loginctl >/dev/null 2>&1; then
    if ! loginctl show-user "$(whoami)" 2>/dev/null | grep -q 'Linger=yes'; then
      info "Enabling user lingering so the service survives logout (sudo required)."
      sudo loginctl enable-linger "$(whoami)" || info "Skipped lingering — service will stop on logout."
    fi
  fi

  green "Installed tg-poll.service (systemd --user)."
  info  "Logs: ${SCRIPT_DIR}/systemd.out.log  (or: journalctl --user -u tg-poll -f)"
  info  "Restart: systemctl --user restart tg-poll"
}

uninstall_linux() {
  systemctl --user stop tg-poll.service 2>/dev/null || true
  systemctl --user disable tg-poll.service 2>/dev/null || true
  rm -f "${HOME}/.config/systemd/user/tg-poll.service"
  systemctl --user daemon-reload 2>/dev/null || true
  green "Removed tg-poll.service (systemd --user)."
}

main() {
  local action="install"
  if [ "${1:-}" = "--uninstall" ]; then
    action="uninstall"
  fi

  local platform
  platform="$(detect_platform)"

  if [ "$platform" = "unsupported" ]; then
    red "Unsupported platform: $(uname -s)"
    red "Only macOS and Linux are supported. For Windows, use WSL2."
    exit 1
  fi

  if [ "$action" = "uninstall" ]; then
    case "$platform" in
      macos) uninstall_macos ;;
      linux) uninstall_linux ;;
    esac
    return
  fi

  check_deps
  bootstrap_env

  case "$platform" in
    macos) install_macos ;;
    linux) install_linux ;;
  esac

  green "Done. Send /help to your bot in Telegram to verify it's running."
}

main "$@"
