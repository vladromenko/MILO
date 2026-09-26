#!/usr/bin/env bash
set -Eeuo pipefail

CLI="/Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/STM32CubeProgrammer.app/Contents/MacOs/bin/STM32_Programmer_CLI"
PORT="${M3PRO_PORT:-/dev/cu.usbserial-02E0E664}"
BAUD="${M3PRO_BAUD:-115200}"
BACKUP="${1:-}"
CONFIRM="${2:-}"
EXPECTED="d8cbbc896c7cb5cabf6123499664b51a0063887673d982db96d30d051873183b"

[[ "$CONFIRM" == "RESTORE-V4-BACKUP" ]] || { echo "Explicit RESTORE-V4-BACKUP confirmation required" >&2; exit 1; }
[[ -x "$CLI" && -f "$BACKUP" ]] || { echo "Programmer or backup missing" >&2; exit 1; }
[[ "$(stat -f%z "$BACKUP")" -eq 1048576 ]] || { echo "Backup size mismatch" >&2; exit 1; }
[[ "$(shasum -a 256 "$BACKUP" | awk '{print $1}')" == "$EXPECTED" ]] || {
  echo "Backup hash mismatch" >&2; exit 1;
}
"$CLI" -c port="$PORT" br="$BAUD" -d "$BACKUP" 0x08000000 -v
echo "Fresh V4 backup restored and verified."
