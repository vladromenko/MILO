#ifndef __APP_UART_SERVO_H__
#define __APP_UART_SERVO_H__

#include "stdint.h"

#define PTO_HEAD1            0xFF
#define PTO_HEAD2            0xF5

#define MAX_RX_SIZE          8
#define RX_FRAME_TIMEOUT_MS  25
#define RX_BYTE_TIMEOUT_MS   3
#define RX_RETRIES           2

#define USERVO_MAX_VALUE     4000
#define USERVO_MIN_VALUE     96

void UartServo_Send_Data(uint8_t* data, uint16_t len);
void UartServo_Set_Position(uint8_t id, uint16_t value, uint16_t time);
void UartServo_Set_Torque(uint8_t enable);
void UartServo_Set_ID(uint8_t id);
int16_t UartServo_Get_Position(uint8_t servo_id);

#endif /* __APP_UART_SERVO_H__ */
