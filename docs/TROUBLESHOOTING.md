# Troubleshooting

Start with read-only evidence:

```bash
cd /home/vlad/MILO
./milo status --json
./milo logs
ssh milo-pi 'systemctl --user --no-pager status milo-edge milo-agent milo-vlm'
```

## Phone Page Does Not Open

Confirm the phone is connected to `MILO-NET`, not mobile data or another Wi-Fi
network, and open `http://10.42.0.1/` exactly. Check `milo-web.socket` at the
system level and `milo-web.service` in the Jetson user session. The Wi-Fi password
and web access code are different; retrieve them locally with `./milo wifi` and
`./milo web-code`.

## Pi Is Disconnected

From Jetson, verify `ping -c 2 10.42.0.2` and the dedicated SSH key/known-hosts
files. Check `milo-rpc` and `milo-dds` separately. A working phone page proves the
Jetson AP, not the Jetson-to-Pi tunnel.

## Camera Works but Objects Do Not

Hailo is deliberately isolated from face and camera paths. On Pi inspect
`/dev/hailo*`, `hailortcli fw-control identify`, the edge journal, and the bounded
recovery unit. A PCIe/DDR firmware timeout may require a cold power cycle; repeated
module reloads are not proof of hardware health.

## No Speech or False Activations

Check `/proc/asound/cards` on Pi and ensure the configured input/output hints
match stable device names. Keep microphone and speaker physically separated.
The pipeline suppresses frames during playback, applies WebRTC VAD and an energy
floor, and requires bounded silence before STT. Diagnose recordings locally and
never commit them.

## Arm Does Not Move

Do not respond by repeatedly clearing faults. Check controller power, recovery
switch, stable CP2104 path, exclusive serial ownership, fresh joint feedback,
`data/ESTOP`, and the reported motion reason. Begin with `start --no-home`, then
one attended small J1 command. The application never commands J6. Firmware V4
rejects negative J4 commands; software cannot override a firmware protocol rule.

## Restore

Stop MILO, check out the desired Git commit into a clean directory, restore the
matching private configuration and data backup, rerun bootstrap and preflight,
and repeat hardware acceptance. Never run two installations against the same
controller.
