M3PRO_ARM_V1

Based directly on the user-provided Yahboom Subscriber_uart_servo STM32CubeIDE project.

Changes:
1. Fixes Arm_Set_Snyc_Buffer(): the original sample ignored s1..s6.
2. Keeps Yahboom's original USART3 bus-servo protocol and Arm_Set_Angle().
3. /arm_joint: one joint, with ID/angle/runtime validation.
4. /arm6_joints: all six joints using Yahboom sync-write.
5. /arm_torque: std_msgs/msg/Int32, 0=OFF, 1=ON.
6. Runtime clamped to 100..5000 ms.
7. Angle limits: J1-J4 0..180, J5 0..270, J6 30..180.

No HOME command is hard-coded because a safe home pose must first be confirmed on the physical arm.

IMPORTANT:
- This archive is SOURCE, not a prebuilt HEX.
- Build it with STM32CubeIDE on the Mac.
- Do not flash until the build succeeds and the generated HEX is copied into
  ~/Desktop/M3Pro_Safe_Flasher/firmware/custom/M3PRO_ARM_V1.hex
- Existing verified recovery backup remains the recovery path.
