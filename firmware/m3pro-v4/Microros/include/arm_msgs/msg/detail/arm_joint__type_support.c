// generated from rosidl_typesupport_introspection_c/resource/idl__type_support.c.em
// with input from arm_msgs:msg/ArmJoint.idl
// generated code does not contain a copyright notice

#include <stddef.h>
#include "arm_msgs/msg/detail/arm_joint__rosidl_typesupport_introspection_c.h"
#include "arm_msgs/msg/rosidl_typesupport_introspection_c__visibility_control.h"
#include "rosidl_typesupport_introspection_c/field_types.h"
#include "rosidl_typesupport_introspection_c/identifier.h"
#include "rosidl_typesupport_introspection_c/message_introspection.h"
#include "arm_msgs/msg/detail/arm_joint__functions.h"
#include "arm_msgs/msg/detail/arm_joint__struct.h"


#ifdef __cplusplus
extern "C"
{
#endif

void arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_init_function(
  void * message_memory, enum rosidl_runtime_c__message_initialization _init)
{
  // TODO(karsten1987): initializers are not yet implemented for typesupport c
  // see https://github.com/ros2/ros2/issues/397
  (void) _init;
  arm_msgs__msg__ArmJoint__init(message_memory);
}

void arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_fini_function(void * message_memory)
{
  arm_msgs__msg__ArmJoint__fini(message_memory);
}

static rosidl_typesupport_introspection_c__MessageMember arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_member_array[3] = {
  {
    "id",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_UINT8,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(arm_msgs__msg__ArmJoint, id),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  },
  {
    "joint",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_INT16,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(arm_msgs__msg__ArmJoint, joint),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  },
  {
    "time",  // name
    rosidl_typesupport_introspection_c__ROS_TYPE_INT16,  // type
    0,  // upper bound of string
    NULL,  // members of sub message
    false,  // is array
    0,  // array size
    false,  // is upper bound
    offsetof(arm_msgs__msg__ArmJoint, time),  // bytes offset in struct
    NULL,  // default value
    NULL,  // size() function pointer
    NULL,  // get_const(index) function pointer
    NULL,  // get(index) function pointer
    NULL,  // fetch(index, &value) function pointer
    NULL,  // assign(index, value) function pointer
    NULL  // resize(index) function pointer
  }
};

static const rosidl_typesupport_introspection_c__MessageMembers arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_members = {
  "arm_msgs__msg",  // message namespace
  "ArmJoint",  // message name
  3,  // number of fields
  sizeof(arm_msgs__msg__ArmJoint),
  arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_member_array,  // message members
  arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_init_function,  // function to initialize message memory (memory has to be allocated)
  arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_fini_function  // function to terminate message instance (will not free memory)
};

// this is not const since it must be initialized on first access
// since C does not allow non-integral compile-time constants
static rosidl_message_type_support_t arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_type_support_handle = {
  0,
  &arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_members,
  get_message_typesupport_handle_function,
};

ROSIDL_TYPESUPPORT_INTROSPECTION_C_EXPORT_arm_msgs
const rosidl_message_type_support_t *
ROSIDL_TYPESUPPORT_INTERFACE__MESSAGE_SYMBOL_NAME(rosidl_typesupport_introspection_c, arm_msgs, msg, ArmJoint)() {
  if (!arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_type_support_handle.typesupport_identifier) {
    arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_type_support_handle.typesupport_identifier =
      rosidl_typesupport_introspection_c__identifier;
  }
  return &arm_msgs__msg__ArmJoint__rosidl_typesupport_introspection_c__ArmJoint_message_type_support_handle;
}
#ifdef __cplusplus
}
#endif
