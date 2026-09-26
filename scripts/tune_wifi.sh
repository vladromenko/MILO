#!/usr/bin/env bash
# Opt-in system setting. Does not reconnect, change addresses, or restart MILO.
set -Eeuo pipefail
interface="${1:?usage: sudo bash scripts/tune_wifi.sh WIFI_INTERFACE}"
if [[ "$EUID" -ne 0 ]]; then
  echo 'Administrator access is required; run this script with sudo.' >&2
  exit 1
fi
if [[ ! -d "/sys/class/net/$interface/wireless" ]]; then
  echo 'Expected an existing Wi-Fi interface.' >&2
  exit 2
fi
connection="$(nmcli -g GENERAL.CON-UUID device show "$interface")"
if [[ ! "$connection" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo 'No active NetworkManager connection; nothing changed.' >&2
  exit 2
fi
nmcli connection modify uuid "$connection" 802-11-wireless.powersave 2
/usr/sbin/iw dev "$interface" set power_save off
/usr/sbin/iw dev "$interface" get power_save
