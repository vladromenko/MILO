#include "app_uart_servo.h"
#include "app.h"
#include "usart.h"
#include "cmsis_os2.h"

#define ENABLE_USERVO_DEBUG 0

static void UartServo_Reset_Rx(void)
{
    /* Position replies are short (8 bytes), but the servo bus is asynchronous.
       Always start a new request with a clean HAL RX state. */
    HAL_UART_AbortReceive(&huart3);
    __HAL_UART_CLEAR_OREFLAG(&huart3);
    __HAL_UART_FLUSH_DRREGISTER(&huart3);
}

void UartServo_Send_Data(uint8_t* data, uint16_t len)
{
    HAL_UART_Transmit(&huart3, data, len, 0xFF);
#if ENABLE_USERVO_DEBUG
    printf("uservo send: ");
    for (int i = 0; i < len; i++) printf("0x%02x ", data[i]);
    printf("\n");
#endif
}

void UartServo_Set_Position(uint8_t id, uint16_t value, uint16_t time)
{
    uint8_t head1 = 0xff;
    uint8_t head2 = 0xff;
    uint8_t s_id = id & 0xff;
    uint8_t len = 0x07;
    uint8_t cmd = 0x03;
    uint8_t addr = 0x2a;

    if (value > USERVO_MAX_VALUE) value = USERVO_MAX_VALUE;
    else if (value < USERVO_MIN_VALUE) value = USERVO_MIN_VALUE;

    uint8_t pos_H = (value >> 8) & 0xff;
    uint8_t pos_L = value & 0xff;
    uint8_t time_H = (time >> 8) & 0xff;
    uint8_t time_L = time & 0xff;
    uint8_t checknum = (~(s_id + len + cmd + addr + pos_H + pos_L + time_H + time_L)) & 0xff;
    uint8_t data[] = {head1, head2, s_id, len, cmd, addr, pos_H, pos_L, time_H, time_L, checknum};
    UartServo_Send_Data(data, sizeof(data));
}

void UartServo_Set_Torque(uint8_t enable)
{
    uint8_t on_off = enable ? 0x01 : 0x00;
    uint8_t head1 = 0xff;
    uint8_t head2 = 0xff;
    uint8_t s_id = 0xfe;
    uint8_t len = 0x04;
    uint8_t cmd = 0x03;
    uint8_t addr = 0x28;
    uint8_t checknum = (~(s_id + len + cmd + addr + on_off)) & 0xff;
    uint8_t data[] = {head1, head2, s_id, len, cmd, addr, on_off, checknum};
    UartServo_Send_Data(data, sizeof(data));
}

void UartServo_Set_ID(uint8_t id)
{
    if (id >= 1 && id <= 250)
    {
        uint8_t head1 = 0xff;
        uint8_t head2 = 0xff;
        uint8_t s_id = 0xfe;
        uint8_t len = 0x04;
        uint8_t cmd = 0x03;
        uint8_t addr = 0x05;
        uint8_t set_id = id;
        uint8_t checknum = (~(s_id + len + cmd + addr + set_id)) & 0xff;
        uint8_t data[] = {head1, head2, s_id, len, cmd, addr, set_id, checknum};
        UartServo_Send_Data(data, sizeof(data));
    }
}

static int Frame_Checksum_OK(const uint8_t *frame)
{
    uint8_t checknum = (~(frame[2] + frame[3] + frame[4] + frame[5] + frame[6])) & 0xff;
    return checknum == frame[7];
}

static int16_t UartServo_Read_Position_Frame(uint8_t expected_id)
{
    uint8_t frame[MAX_RX_SIZE] = {0};
    uint8_t byte = 0;
    uint8_t state = 0;
    uint8_t index = 0;
    uint32_t deadline = HAL_GetTick() + RX_FRAME_TIMEOUT_MS;

    while ((int32_t)(deadline - HAL_GetTick()) > 0)
    {
        uint32_t remaining = deadline - HAL_GetTick();
        uint32_t timeout = remaining > RX_BYTE_TIMEOUT_MS ? RX_BYTE_TIMEOUT_MS : remaining;
        if (timeout == 0) timeout = 1;

        HAL_StatusTypeDef status = HAL_UART_Receive(&huart3, &byte, 1, timeout);
        if (status == HAL_TIMEOUT) continue;
        if (status != HAL_OK) return -1;

        if (state == 0)
        {
            if (byte == PTO_HEAD1)
            {
                frame[0] = byte;
                state = 1;
            }
            continue;
        }

        if (state == 1)
        {
            if (byte == PTO_HEAD2)
            {
                frame[1] = byte;
                state = 2;
                index = 2;
            }
            else if (byte == PTO_HEAD1)
            {
                /* FF FF F5 can occur if a stale/request byte is still visible.
                   Keep the newest FF as the possible start of the response. */
                frame[0] = byte;
            }
            else
            {
                state = 0;
            }
            continue;
        }

        frame[index++] = byte;
        if (index < MAX_RX_SIZE) continue;

        if (frame[2] == expected_id && Frame_Checksum_OK(frame))
        {
            uint16_t value = ((uint16_t)frame[5] << 8) | frame[6];
            if (value >= USERVO_MIN_VALUE && value <= USERVO_MAX_VALUE)
            {
#if ENABLE_USERVO_DEBUG
                printf("servo %u raw=%u\n", expected_id, value);
#endif
                return (int16_t)value;
            }
        }

        /* A complete but unrelated/corrupt frame was seen. Keep scanning until
           the deadline instead of assigning it to the requested joint. */
        state = 0;
        index = 0;
    }

    return -1;
}

int16_t UartServo_Get_Position(uint8_t servo_id)
{
    if (servo_id < 1 || servo_id > 250) return -1;

    uint8_t head1 = 0xff;
    uint8_t head2 = 0xff;
    uint8_t s_id = servo_id & 0xff;
    uint8_t len = 0x04;
    uint8_t cmd = 0x02;
    uint8_t param_H = 0x38;
    uint8_t param_L = 0x02;
    uint8_t checknum = (~(s_id + len + cmd + param_H + param_L)) & 0xff;
    uint8_t data[] = {head1, head2, s_id, len, cmd, param_H, param_L, checknum};

    for (uint8_t attempt = 0; attempt < RX_RETRIES; attempt++)
    {
        UartServo_Reset_Rx();
        UartServo_Send_Data(data, sizeof(data));
        int16_t value = UartServo_Read_Position_Frame(s_id);
        if (value >= 0) return value;
        osDelay(2);
    }

    return -1;
}
