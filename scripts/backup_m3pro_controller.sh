#!/usr/bin/env bash
set -Eeuo pipefail

CLI="/Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/STM32CubeProgrammer.app/Contents/MacOs/bin/STM32_Programmer_CLI"
PORT="${M3PRO_PORT:-/dev/cu.usbserial-02E0E664}"
BAUD="${M3PRO_BAUD:-115200}"
BASE=$((0x08000000))
FLASH_SIZE=$((0x100000))
CHUNK=$((0x4000))
OUTPUT="${1:-}"

[[ -x "$CLI" ]] || { echo "STM32CubeProgrammer CLI not found" >&2; exit 1; }
[[ -n "$OUTPUT" ]] || { echo "Usage: $0 NEW_OUTPUT_DIRECTORY" >&2; exit 1; }
[[ ! -e "$OUTPUT" ]] || { echo "Output already exists; refusing to overwrite: $OUTPUT" >&2; exit 1; }
mkdir -m 700 "$OUTPUT"

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    echo "Backup incomplete; controller was not erased or programmed." >&2
  fi
  exit "$status"
}
trap cleanup EXIT

"$CLI" -c port="$PORT" br="$BAUD" | tee "$OUTPUT/controller_info.txt"
"$CLI" -c port="$PORT" br="$BAUD" -ob displ | tee "$OUTPUT/option_bytes.txt"

read_image() {
  local label="$1"
  local image="$OUTPUT/$2"
  local parts="$OUTPUT/.$label.parts"
  mkdir -m 700 "$parts"
  : > "$image"
  for ((offset=0; offset<FLASH_SIZE; offset+=CHUNK)); do
    local address part
    printf -v address '0x%08X' $((BASE + offset))
    printf -v part '%s/block_%02d.bin' "$parts" $((offset / CHUNK))
    echo "$label: reading $address"
    success=0
    for attempt in 1 2 3; do
      if "$CLI" -c port="$PORT" br="$BAUD" -u "$address" "$CHUNK" "$part" \
          >> "$OUTPUT/read.log" 2>&1; then
        success=1
        break
      fi
      echo "$label: retry $attempt/3 for $address"
      sleep 1
    done
    [[ "$success" -eq 1 ]] || { echo "Could not read $address after 3 attempts" >&2; exit 1; }
    [[ "$(stat -f%z "$part")" -eq "$CHUNK" ]]
    cat "$part" >> "$image"
  done
  [[ "$(stat -f%z "$image")" -eq "$FLASH_SIZE" ]]
  rm -r "$parts"
}

read_image read1 current_flash_read1.bin
read_image read2 current_flash_read2.bin

hash1="$(shasum -a 256 "$OUTPUT/current_flash_read1.bin" | awk '{print $1}')"
hash2="$(shasum -a 256 "$OUTPUT/current_flash_read2.bin" | awk '{print $1}')"
[[ "$hash1" == "$hash2" ]] || { echo "Independent reads differ. DO NOT FLASH." >&2; exit 1; }

printf '%s  current_flash_read1.bin\n%s  current_flash_read2.bin\n' "$hash1" "$hash2" > "$OUTPUT/SHA256SUMS"
chmod a-w "$OUTPUT/current_flash_read1.bin" "$OUTPUT/current_flash_read2.bin" "$OUTPUT/SHA256SUMS"
printf 'Verified two identical 1 MiB reads.\nSHA-256: %s\n' "$hash1"
