#!/usr/bin/env bash
set -Eeuo pipefail

CLI="/Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/STM32CubeProgrammer.app/Contents/MacOs/bin/STM32_Programmer_CLI"
PORT="${M3PRO_PORT:-/dev/cu.usbserial-02E0E664}"
BAUD="${M3PRO_BAUD:-115200}"
BACKUP_DIR="${1:-}"
FIRMWARE="${2:-}"
CONFIRM="${3:-}"
BACKUP_HASH="d8cbbc896c7cb5cabf6123499664b51a0063887673d982db96d30d051873183b"
FIRMWARE_HASH="4bb70edba4750288a5126f0cde1d6c8d00b8d608a84fe928178f63d2c11e8cc6"

[[ "$CONFIRM" == "FLASH-V6-6002" ]] || { echo "Explicit FLASH-V6-6002 confirmation required" >&2; exit 1; }
[[ -x "$CLI" && -f "$FIRMWARE" ]] || { echo "Programmer or firmware missing" >&2; exit 1; }
[[ -f "$BACKUP_DIR/current_flash_read1.bin" && -f "$BACKUP_DIR/current_flash_read2.bin" ]] || {
  echo "Fresh independent backups missing" >&2; exit 1;
}

actual_backup1="$(shasum -a 256 "$BACKUP_DIR/current_flash_read1.bin" | awk '{print $1}')"
actual_backup2="$(shasum -a 256 "$BACKUP_DIR/current_flash_read2.bin" | awk '{print $1}')"
actual_firmware="$(shasum -a 256 "$FIRMWARE" | awk '{print $1}')"
[[ "$actual_backup1" == "$BACKUP_HASH" && "$actual_backup2" == "$BACKUP_HASH" ]] || {
  echo "Fresh backup hash mismatch" >&2; exit 1;
}
[[ "$actual_firmware" == "$FIRMWARE_HASH" ]] || { echo "Firmware hash mismatch" >&2; exit 1; }

status="$($CLI -c port="$PORT" br="$BAUD")"
printf '%s\n' "$status"
grep -q 'Chip ID: 0x450' <<< "$status" || { echo "Wrong controller identity" >&2; exit 1; }

echo "Programming V6 protocol 6002 and verifying..."
"$CLI" -c port="$PORT" br="$BAUD" -d "$FIRMWARE" -v
echo "V6 programming and readback verification completed."
