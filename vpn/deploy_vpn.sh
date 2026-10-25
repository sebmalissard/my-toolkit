#!/bin/bash
#
# Usage:
#    ./deploy_vpn.sh <USERNAME>@<IP_ADDRESS> <command> [options]
#    Commands: deploy, backup_config, restore_config, status, restart
#
# Examples:
#    ./deploy_vpn.sh <USERNAME>@<IP_ADDRESS> deploy
#    ./deploy_vpn.sh <USERNAME>@<IP_ADDRESS> backup_config <backup_dir>
#    ./deploy_vpn.sh <USERNAME>@<IP_ADDRESS> restore_config <server_config>
#    ./deploy_vpn.sh <USERNAME>@<IP_ADDRESS> status
#    ./deploy_vpn.sh <USERNAME>@<IP_ADDRESS> restart
#
set -euo pipefail

cmd_helper() {
    echo "Usage: $0 <username>@<ip_address> <command> [options]"
    echo "Commands: deploy, backup_config, restore_config, status, restart"
    echo " Options:"
    echo "  backup_config: <backup_path>"
    echo "  restore_config: <backup_file>"
}

cmd_deploy() {
    echo ">>> Running deploy..."
    #ssh "${USERNAME}@${IP_ADDRESS}" "bash -s" < setup_server.sh

    echo ">>> Deploying WireGuard..."
    ssh "${USERNAME}@${IP_ADDRESS}" "NAME=wireguard CONFIG_PATH=/home/${USERNAME}/wireguard docker compose -f - up -d" < docker-compose.yml
}

cmd_backup_config() {
    local dest="$1"
    if [[ ! -d "$dest" ]]; then
        echo "Error: backup directory not found: $dest"
        exit 1
    fi
    echo ">>> Backing up WireGuard server config to ${dest}..."
    scp "${USERNAME}@${IP_ADDRESS}:/home/${USERNAME}/wireguard/wg_confs/wg0.conf" "${dest}/"
}

cmd_restore_config() {
    local cfg="$1"
    if [[ ! -f "$cfg" ]]; then
        echo "Error: backup file not found: $cfg"
        exit 1
    fi
    echo ">>> Restoring WireGuard server config from ${cfg}..."
    ssh "${USERNAME}@${IP_ADDRESS}" "mkdir -p /home/${USERNAME}/wireguard/wg_confs"
    scp "${cfg}" "${USERNAME}@${IP_ADDRESS}:/home/${USERNAME}/wireguard/wg_confs/wg0.conf"
}

cmd_status() {
    echo ">>> Checking WireGuard status..."
    ssh "${USERNAME}@${IP_ADDRESS}" "docker exec wireguard wg show"
}

cmd_restart() {
    echo ">>> Restarting WireGuard..."
    ssh "${USERNAME}@${IP_ADDRESS}" "docker restart wireguard"
}

if [[ $# -lt 2 ]]; then
    cmd_helper
    exit 1
fi

USERNAME="${1%%@*}"
IP_ADDRESS="${1##*@}"
COMMAND="$2"
OPTIONS="${3:-}"

case "${COMMAND}" in
    deploy)
        cmd_deploy
        ;;
    backup_config)
        cmd_backup_config "${OPTIONS:-.}"
        ;;
    restore_config)
        cmd_restore_config "${OPTIONS:-./wg0.conf}"
        ;;
    status)
        cmd_status
        ;;
    restart)
        cmd_restart
        ;;
    *)
        cmd_helper
        exit 1
        ;;
esac
