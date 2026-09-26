# Installed V4 source

Preserved on 2026-09-22 from the existing STM32CubeIDE project:
`~/Downloads/M3Pro_MILO2_Feedback_Source/M3Pro_Safe_Flasher/source/M3PRO_ARM_V1`.
No new firmware was built or flashed from this directory.

The archived release `M3PRO_MILO2_FEEDBACK_V4.hex` contains 133708 programmed
bytes. Every addressed byte was compared with the 1 MiB image restored to the
controller on 2026-09-22: zero differences. Backup SHA-256:
`d8cbbc896c7cb5cabf6123499664b51a0063887673d982db96d30d051873183b`.

`APP/ros2_handle.c` also matches the preserved V4 source package byte for byte.
APP, Core, Drivers, Middlewares and Microros are preserved with their original
notices. Microros includes its supplied static library; its upstream library
source is not reconstructed here. Build output and temporary IDE state are
excluded. A new reproducible build has not been verified in this session.

Open `.project` in STM32CubeIDE (project name `Subscriber_uart_servo`), select
the Debug configuration and build with the installed ARM toolchain. Preserve
the historical HEX separately: a new compiler can produce different bytes.
Do not flash a newly built image merely to inspect the code.

V4 accepts J1-J4 targets 0..180, J5 0..270 and J6 30..180, and supports individual
and broadcast commands. These are firmware capabilities, NOT permission to
use those ranges on the assembled MILO. The host enforces the narrower MILO
limits and never commands J6 or uses broadcast position/torque commands.
