#!/bin/bash
#
# wg_gen_config.sh - Initialize the WireGuard server (peer1_server), or
# add a new client peer (peerN or peerN_name).
#
# Generated structure:
#   config_dir/wireguard/
#     peer1_server/   privatekey, publickey, wg0.conf, serverip
#     peer2/          privatekey, publickey, presharedkey, peer2.conf, peer2.png
#     peer3_laptop/   privatekey, publickey, presharedkey, peer3_laptop.conf, peer3_laptop.png
#     ...
#
# Usage:
#   wg_gen_config.sh <config_dir> --server-ip <public_ip>
#   wg_gen_config.sh <config_dir> --add-peer [name]
#
# Examples:
#   wg_gen_config.sh config1 --server-ip 203.0.113.10
#   wg_gen_config.sh config1 --add-peer
#   wg_gen_config.sh config1 --add-peer laptop
#
# The internal subnet (10.X.Y.0/24) is generated randomly at init time.

set -euo pipefail
umask 077

usage() {
    sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

[[ $# -lt 2 ]] && usage

CONFIG_DIR="$1"
ACTION="$2"

WG_DIR="${CONFIG_DIR}/wireguard"
SERVER_DIR="${WG_DIR}/peer1_server"
WG0_CONF="${SERVER_DIR}/wg0.conf"
SERVERIP_FILE="${SERVER_DIR}/serverip"
PORT=51820
ALLOWEDIPS="0.0.0.0/0"

check_deps() {
    command -v wg >/dev/null 2>&1 || { echo "Error: 'wg' is required." >&2; exit 1; }
    command -v qrencode >/dev/null 2>&1 || echo "Warning: 'qrencode' not found, no QR code will be generated."
}

subnet_base_of() {
    local ip="${1%%/*}"
    echo "${ip%.*}"
}

random_subnet() {
    # Generate a random private subnet 10.X.Y.0/24 (X,Y between 1 and 254)
    local x y
    x=$(( (RANDOM % 254) + 1 ))
    y=$(( (RANDOM % 254) + 1 ))
    echo "10.${x}.${y}.0/24"
}

subnet_base_from_wg0() {
    local addr
    addr="$(grep -m1 -E '^Address' "$WG0_CONF" | sed -E 's/^Address\s*=\s*//')"
    [[ -z "$addr" ]] && { echo "Error: could not read Address from $WG0_CONF" >&2; exit 1; }
    subnet_base_of "$addr"
}

# Highest peer number already used among peer1_server, peer2, peer3_xxx, ...
next_peer_number() {
    local max=1 n
    for d in "$WG_DIR"/peer*; do
        [[ -d "$d" ]] || continue
        n="$(basename "$d" | sed -n 's/^peer\([0-9]\+\).*$/\1/p')"
        [[ -n "$n" && "$n" -gt "$max" ]] && max="$n"
    done
    echo $((max + 1))
}

next_ip_last_octet() {
    local subnet_base="$1" max=1 octet
    while IFS= read -r ip; do
        octet="${ip##*.}"
        octet="${octet%%/*}"
        [[ "$octet" =~ ^[0-9]+$ ]] && (( octet > max )) && max=$octet
    done < <(grep -oE "${subnet_base}\.[0-9]+/32" "$WG0_CONF" 2>/dev/null || true)
    echo $((max + 1))
}

init_server() {
    local server_ip="${1:-}"

    [[ -z "$server_ip" ]] && { echo "Error: the server's public IP must be provided (e.g. 203.0.113.10)" >&2; exit 1; }

    if [[ -f "${SERVER_DIR}/privatekey" ]]; then
        echo "Error: a server config already exists in ${SERVER_DIR}." >&2
        echo "Use --add-peer to add a client instead." >&2
        exit 1
    fi

    local subnet_cidr subnet_base
    subnet_cidr="$(random_subnet)"
    subnet_base="$(subnet_base_of "$subnet_cidr")"

    echo "==> Initializing server (subnet ${subnet_cidr}, public IP ${server_ip})..."
    mkdir -p "$SERVER_DIR"

    wg genkey | tee "${SERVER_DIR}/privatekey" | wg pubkey > "${SERVER_DIR}/publickey"
    local server_privkey
    server_privkey="$(cat "${SERVER_DIR}/privatekey")"

    echo "$server_ip" > "$SERVERIP_FILE"

    cat > "$WG0_CONF" <<EOF
[Interface]
Address = ${subnet_base}.1/24
ListenPort = ${PORT}
PrivateKey = ${server_privkey}
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

EOF

    echo "==> Server initialized (${SERVER_DIR})."
    echo "    Server public key: $(cat "${SERVER_DIR}/publickey")"
    echo
    echo "Now add a first client with:"
    echo "    $0 ${CONFIG_DIR} --add-peer"
}

add_peer() {
    local name_suffix="${1:-}"

    if [[ ! -f "${SERVER_DIR}/publickey" ]]; then
        echo "Error: no server config found in ${SERVER_DIR}." >&2
        echo "Initialize it first with: $0 ${CONFIG_DIR} --ip <server_ip>" >&2
        exit 1
    fi

    if [[ -n "$name_suffix" && ! "$name_suffix" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo "Error: name must only contain letters, digits, - and _" >&2
        exit 1
    fi

    local num dirname peer_dir
    num="$(next_peer_number)"
    dirname="peer${num}"
    [[ -n "$name_suffix" ]] && dirname="peer${num}_${name_suffix}"
    peer_dir="${WG_DIR}/${dirname}"

    if [[ -d "$peer_dir" ]]; then
        echo "Error: '${dirname}' already exists (${peer_dir})." >&2
        exit 1
    fi

    local subnet_base dns server_ip port peer_privkey peer_pubkey peer_psk server_pubkey last_octet peer_ip
    subnet_base="$(subnet_base_from_wg0)"
    dns="${subnet_base}.1"
    server_ip="$(cat "$SERVERIP_FILE")"
    port="$(grep -m1 -E '^ListenPort' "$WG0_CONF" | sed -E 's/^ListenPort\s*=\s*//')"
    port="${port:-$PORT}"

    mkdir -p "$peer_dir"
    wg genkey | tee "${peer_dir}/privatekey" | wg pubkey > "${peer_dir}/publickey"
    wg genpsk > "${peer_dir}/presharedkey"

    peer_privkey="$(cat "${peer_dir}/privatekey")"
    peer_pubkey="$(cat "${peer_dir}/publickey")"
    peer_psk="$(cat "${peer_dir}/presharedkey")"
    server_pubkey="$(cat "${SERVER_DIR}/publickey")"
    last_octet="$(next_ip_last_octet "$subnet_base")"
    peer_ip="${subnet_base}.${last_octet}"

    # ---- Client config file ----
    cat > "${peer_dir}/${dirname}.conf" <<EOF
[Interface]
Address = ${peer_ip}/32
PrivateKey = ${peer_privkey}
DNS = ${dns}

[Peer]
PublicKey = ${server_pubkey}
PresharedKey = ${peer_psk}
Endpoint = ${server_ip}:${port}
AllowedIPs = ${ALLOWEDIPS}
EOF

    # ---- Add the peer on the server side (wg0.conf) ----
    cat >> "$WG0_CONF" <<EOF
### begin ${dirname}
[Peer]
PublicKey = ${peer_pubkey}
PresharedKey = ${peer_psk}
AllowedIPs = ${peer_ip}/32
### end ${dirname}

EOF

    # ---- QR code ----
    if command -v qrencode >/dev/null 2>&1; then
        qrencode -t png -o "${peer_dir}/${dirname}.png" -r "${peer_dir}/${dirname}.conf"
    fi

    echo
    echo "==> '${dirname}' created successfully."
    echo "    Assigned IP  : ${peer_ip}/32"
    echo "    Config file  : ${peer_dir}/${dirname}.conf"
    [[ -f "${peer_dir}/${dirname}.png" ]] && echo "    QR code      : ${peer_dir}/${dirname}.png"
    echo
    echo "Don't forget to reload the interface on the server side, e.g.:"
    echo "    wg syncconf wg0 <(wg-quick strip ${WG0_CONF})"
}

# ---------------------------------- Main ---------------------------------------

check_deps
mkdir -p "$WG_DIR"

case "$ACTION" in
    --server-ip)
        init_server "${3:-}"
        ;;
    --add-peer)
        add_peer "${3:-}"
        ;;
    *)
        usage
        ;;
esac
