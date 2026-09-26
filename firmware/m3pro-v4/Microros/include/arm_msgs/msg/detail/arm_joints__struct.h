// generated from rosidl_generator_c/resource/idl__struct.h.em
// with input from arm_msgs:msg/ArmJoints.idl
// generated code does not contain a copyright notice

#ifndef ARM_MSGS__MSG__DETAIL__ARM_JOINTS__STRUCT_H_
#define ARM_MSGS__MSG__DETAIL__ARM_JOINTS__STRUCT_H_

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>


// Constants defined in the message

/// Struct defined in msg/ArmJoints in the package arm_msgs.
typedef struct arm_msgs__msg__ArmJoints
{
  int16_t joint1;
  int16_t joint2;
  int16_t joint3;
  int16_t joint4;
  int16_t joint5;
  int16_t joint6;
  int16_t time;
} arm_msgs__msg__ArmJoints;

// Struct for a sequence of arm_msgs__msg__ArmJoints.
typedef struct arm_msgs__msg__ArmJoints__Sequence
{
  arm_msgs__msg__ArmJoints * data;
  /// The number of valid items in data
  size_t size;
  /// The number of allocated items in data
  size_t capacity;
} arm_msgs__msg__ArmJoints__Sequence;

#ifdef __cplusplus
}
#endif

#endif  // ARM_MSGS__MSG__DETAIL__ARM_JOINTS__STRUCT_H_
