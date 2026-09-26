// generated from rosidl_generator_c/resource/idl__struct.h.em
// with input from arm_msgs:msg/ArmJoint.idl
// generated code does not contain a copyright notice

#ifndef ARM_MSGS__MSG__DETAIL__ARM_JOINT__STRUCT_H_
#define ARM_MSGS__MSG__DETAIL__ARM_JOINT__STRUCT_H_

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>


// Constants defined in the message

/// Struct defined in msg/ArmJoint in the package arm_msgs.
typedef struct arm_msgs__msg__ArmJoint
{
  uint8_t id;
  int16_t joint;
  int16_t time;
} arm_msgs__msg__ArmJoint;

// Struct for a sequence of arm_msgs__msg__ArmJoint.
typedef struct arm_msgs__msg__ArmJoint__Sequence
{
  arm_msgs__msg__ArmJoint * data;
  /// The number of valid items in data
  size_t size;
  /// The number of allocated items in data
  size_t capacity;
} arm_msgs__msg__ArmJoint__Sequence;

#ifdef __cplusplus
}
#endif

#endif  // ARM_MSGS__MSG__DETAIL__ARM_JOINT__STRUCT_H_
