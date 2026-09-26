// generated from rosidl_generator_c/resource/idl__functions.h.em
// with input from arm_msgs:msg/ArmJoints.idl
// generated code does not contain a copyright notice

#ifndef ARM_MSGS__MSG__DETAIL__ARM_JOINTS__FUNCTIONS_H_
#define ARM_MSGS__MSG__DETAIL__ARM_JOINTS__FUNCTIONS_H_

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdbool.h>
#include <stdlib.h>

#include "rosidl_runtime_c/visibility_control.h"
#include "arm_msgs/msg/rosidl_generator_c__visibility_control.h"

#include "arm_msgs/msg/detail/arm_joints__struct.h"

/// Initialize msg/ArmJoints message.
/**
 * If the init function is called twice for the same message without
 * calling fini inbetween previously allocated memory will be leaked.
 * \param[in,out] msg The previously allocated message pointer.
 * Fields without a default value will not be initialized by this function.
 * You might want to call memset(msg, 0, sizeof(
 * arm_msgs__msg__ArmJoints
 * )) before or use
 * arm_msgs__msg__ArmJoints__create()
 * to allocate and initialize the message.
 * \return true if initialization was successful, otherwise false
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
bool
arm_msgs__msg__ArmJoints__init(arm_msgs__msg__ArmJoints * msg);

/// Finalize msg/ArmJoints message.
/**
 * \param[in,out] msg The allocated message pointer.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
void
arm_msgs__msg__ArmJoints__fini(arm_msgs__msg__ArmJoints * msg);

/// Create msg/ArmJoints message.
/**
 * It allocates the memory for the message, sets the memory to zero, and
 * calls
 * arm_msgs__msg__ArmJoints__init().
 * \return The pointer to the initialized message if successful,
 * otherwise NULL
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
arm_msgs__msg__ArmJoints *
arm_msgs__msg__ArmJoints__create();

/// Destroy msg/ArmJoints message.
/**
 * It calls
 * arm_msgs__msg__ArmJoints__fini()
 * and frees the memory of the message.
 * \param[in,out] msg The allocated message pointer.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
void
arm_msgs__msg__ArmJoints__destroy(arm_msgs__msg__ArmJoints * msg);

/// Check for msg/ArmJoints message equality.
/**
 * \param[in] lhs The message on the left hand size of the equality operator.
 * \param[in] rhs The message on the right hand size of the equality operator.
 * \return true if messages are equal, otherwise false.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
bool
arm_msgs__msg__ArmJoints__are_equal(const arm_msgs__msg__ArmJoints * lhs, const arm_msgs__msg__ArmJoints * rhs);

/// Copy a msg/ArmJoints message.
/**
 * This functions performs a deep copy, as opposed to the shallow copy that
 * plain assignment yields.
 *
 * \param[in] input The source message pointer.
 * \param[out] output The target message pointer, which must
 *   have been initialized before calling this function.
 * \return true if successful, or false if either pointer is null
 *   or memory allocation fails.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
bool
arm_msgs__msg__ArmJoints__copy(
  const arm_msgs__msg__ArmJoints * input,
  arm_msgs__msg__ArmJoints * output);

/// Initialize array of msg/ArmJoints messages.
/**
 * It allocates the memory for the number of elements and calls
 * arm_msgs__msg__ArmJoints__init()
 * for each element of the array.
 * \param[in,out] array The allocated array pointer.
 * \param[in] size The size / capacity of the array.
 * \return true if initialization was successful, otherwise false
 * If the array pointer is valid and the size is zero it is guaranteed
 # to return true.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
bool
arm_msgs__msg__ArmJoints__Sequence__init(arm_msgs__msg__ArmJoints__Sequence * array, size_t size);

/// Finalize array of msg/ArmJoints messages.
/**
 * It calls
 * arm_msgs__msg__ArmJoints__fini()
 * for each element of the array and frees the memory for the number of
 * elements.
 * \param[in,out] array The initialized array pointer.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
void
arm_msgs__msg__ArmJoints__Sequence__fini(arm_msgs__msg__ArmJoints__Sequence * array);

/// Create array of msg/ArmJoints messages.
/**
 * It allocates the memory for the array and calls
 * arm_msgs__msg__ArmJoints__Sequence__init().
 * \param[in] size The size / capacity of the array.
 * \return The pointer to the initialized array if successful, otherwise NULL
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
arm_msgs__msg__ArmJoints__Sequence *
arm_msgs__msg__ArmJoints__Sequence__create(size_t size);

/// Destroy array of msg/ArmJoints messages.
/**
 * It calls
 * arm_msgs__msg__ArmJoints__Sequence__fini()
 * on the array,
 * and frees the memory of the array.
 * \param[in,out] array The initialized array pointer.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
void
arm_msgs__msg__ArmJoints__Sequence__destroy(arm_msgs__msg__ArmJoints__Sequence * array);

/// Check for msg/ArmJoints message array equality.
/**
 * \param[in] lhs The message array on the left hand size of the equality operator.
 * \param[in] rhs The message array on the right hand size of the equality operator.
 * \return true if message arrays are equal in size and content, otherwise false.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
bool
arm_msgs__msg__ArmJoints__Sequence__are_equal(const arm_msgs__msg__ArmJoints__Sequence * lhs, const arm_msgs__msg__ArmJoints__Sequence * rhs);

/// Copy an array of msg/ArmJoints messages.
/**
 * This functions performs a deep copy, as opposed to the shallow copy that
 * plain assignment yields.
 *
 * \param[in] input The source array pointer.
 * \param[out] output The target array pointer, which must
 *   have been initialized before calling this function.
 * \return true if successful, or false if either pointer
 *   is null or memory allocation fails.
 */
ROSIDL_GENERATOR_C_PUBLIC_arm_msgs
bool
arm_msgs__msg__ArmJoints__Sequence__copy(
  const arm_msgs__msg__ArmJoints__Sequence * input,
  arm_msgs__msg__ArmJoints__Sequence * output);

#ifdef __cplusplus
}
#endif

#endif  // ARM_MSGS__MSG__DETAIL__ARM_JOINTS__FUNCTIONS_H_
