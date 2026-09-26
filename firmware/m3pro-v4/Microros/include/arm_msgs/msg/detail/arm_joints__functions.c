// generated from rosidl_generator_c/resource/idl__functions.c.em
// with input from arm_msgs:msg/ArmJoints.idl
// generated code does not contain a copyright notice
#include "arm_msgs/msg/detail/arm_joints__functions.h"

#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "rcutils/allocator.h"


bool
arm_msgs__msg__ArmJoints__init(arm_msgs__msg__ArmJoints * msg)
{
  if (!msg) {
    return false;
  }
  // joint1
  // joint2
  // joint3
  // joint4
  // joint5
  // joint6
  // time
  return true;
}

void
arm_msgs__msg__ArmJoints__fini(arm_msgs__msg__ArmJoints * msg)
{
  if (!msg) {
    return;
  }
  // joint1
  // joint2
  // joint3
  // joint4
  // joint5
  // joint6
  // time
}

bool
arm_msgs__msg__ArmJoints__are_equal(const arm_msgs__msg__ArmJoints * lhs, const arm_msgs__msg__ArmJoints * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  // joint1
  if (lhs->joint1 != rhs->joint1) {
    return false;
  }
  // joint2
  if (lhs->joint2 != rhs->joint2) {
    return false;
  }
  // joint3
  if (lhs->joint3 != rhs->joint3) {
    return false;
  }
  // joint4
  if (lhs->joint4 != rhs->joint4) {
    return false;
  }
  // joint5
  if (lhs->joint5 != rhs->joint5) {
    return false;
  }
  // joint6
  if (lhs->joint6 != rhs->joint6) {
    return false;
  }
  // time
  if (lhs->time != rhs->time) {
    return false;
  }
  return true;
}

bool
arm_msgs__msg__ArmJoints__copy(
  const arm_msgs__msg__ArmJoints * input,
  arm_msgs__msg__ArmJoints * output)
{
  if (!input || !output) {
    return false;
  }
  // joint1
  output->joint1 = input->joint1;
  // joint2
  output->joint2 = input->joint2;
  // joint3
  output->joint3 = input->joint3;
  // joint4
  output->joint4 = input->joint4;
  // joint5
  output->joint5 = input->joint5;
  // joint6
  output->joint6 = input->joint6;
  // time
  output->time = input->time;
  return true;
}

arm_msgs__msg__ArmJoints *
arm_msgs__msg__ArmJoints__create()
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  arm_msgs__msg__ArmJoints * msg = (arm_msgs__msg__ArmJoints *)allocator.allocate(sizeof(arm_msgs__msg__ArmJoints), allocator.state);
  if (!msg) {
    return NULL;
  }
  memset(msg, 0, sizeof(arm_msgs__msg__ArmJoints));
  bool success = arm_msgs__msg__ArmJoints__init(msg);
  if (!success) {
    allocator.deallocate(msg, allocator.state);
    return NULL;
  }
  return msg;
}

void
arm_msgs__msg__ArmJoints__destroy(arm_msgs__msg__ArmJoints * msg)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (msg) {
    arm_msgs__msg__ArmJoints__fini(msg);
  }
  allocator.deallocate(msg, allocator.state);
}


bool
arm_msgs__msg__ArmJoints__Sequence__init(arm_msgs__msg__ArmJoints__Sequence * array, size_t size)
{
  if (!array) {
    return false;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  arm_msgs__msg__ArmJoints * data = NULL;

  if (size) {
    data = (arm_msgs__msg__ArmJoints *)allocator.zero_allocate(size, sizeof(arm_msgs__msg__ArmJoints), allocator.state);
    if (!data) {
      return false;
    }
    // initialize all array elements
    size_t i;
    for (i = 0; i < size; ++i) {
      bool success = arm_msgs__msg__ArmJoints__init(&data[i]);
      if (!success) {
        break;
      }
    }
    if (i < size) {
      // if initialization failed finalize the already initialized array elements
      for (; i > 0; --i) {
        arm_msgs__msg__ArmJoints__fini(&data[i - 1]);
      }
      allocator.deallocate(data, allocator.state);
      return false;
    }
  }
  array->data = data;
  array->size = size;
  array->capacity = size;
  return true;
}

void
arm_msgs__msg__ArmJoints__Sequence__fini(arm_msgs__msg__ArmJoints__Sequence * array)
{
  if (!array) {
    return;
  }
  rcutils_allocator_t allocator = rcutils_get_default_allocator();

  if (array->data) {
    // ensure that data and capacity values are consistent
    assert(array->capacity > 0);
    // finalize all array elements
    for (size_t i = 0; i < array->capacity; ++i) {
      arm_msgs__msg__ArmJoints__fini(&array->data[i]);
    }
    allocator.deallocate(array->data, allocator.state);
    array->data = NULL;
    array->size = 0;
    array->capacity = 0;
  } else {
    // ensure that data, size, and capacity values are consistent
    assert(0 == array->size);
    assert(0 == array->capacity);
  }
}

arm_msgs__msg__ArmJoints__Sequence *
arm_msgs__msg__ArmJoints__Sequence__create(size_t size)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  arm_msgs__msg__ArmJoints__Sequence * array = (arm_msgs__msg__ArmJoints__Sequence *)allocator.allocate(sizeof(arm_msgs__msg__ArmJoints__Sequence), allocator.state);
  if (!array) {
    return NULL;
  }
  bool success = arm_msgs__msg__ArmJoints__Sequence__init(array, size);
  if (!success) {
    allocator.deallocate(array, allocator.state);
    return NULL;
  }
  return array;
}

void
arm_msgs__msg__ArmJoints__Sequence__destroy(arm_msgs__msg__ArmJoints__Sequence * array)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  if (array) {
    arm_msgs__msg__ArmJoints__Sequence__fini(array);
  }
  allocator.deallocate(array, allocator.state);
}

bool
arm_msgs__msg__ArmJoints__Sequence__are_equal(const arm_msgs__msg__ArmJoints__Sequence * lhs, const arm_msgs__msg__ArmJoints__Sequence * rhs)
{
  if (!lhs || !rhs) {
    return false;
  }
  if (lhs->size != rhs->size) {
    return false;
  }
  for (size_t i = 0; i < lhs->size; ++i) {
    if (!arm_msgs__msg__ArmJoints__are_equal(&(lhs->data[i]), &(rhs->data[i]))) {
      return false;
    }
  }
  return true;
}

bool
arm_msgs__msg__ArmJoints__Sequence__copy(
  const arm_msgs__msg__ArmJoints__Sequence * input,
  arm_msgs__msg__ArmJoints__Sequence * output)
{
  if (!input || !output) {
    return false;
  }
  if (output->capacity < input->size) {
    const size_t allocation_size =
      input->size * sizeof(arm_msgs__msg__ArmJoints);
    rcutils_allocator_t allocator = rcutils_get_default_allocator();
    arm_msgs__msg__ArmJoints * data =
      (arm_msgs__msg__ArmJoints *)allocator.reallocate(
      output->data, allocation_size, allocator.state);
    if (!data) {
      return false;
    }
    // If reallocation succeeded, memory may or may not have been moved
    // to fulfill the allocation request, invalidating output->data.
    output->data = data;
    for (size_t i = output->capacity; i < input->size; ++i) {
      if (!arm_msgs__msg__ArmJoints__init(&output->data[i])) {
        // If initialization of any new item fails, roll back
        // all previously initialized items. Existing items
        // in output are to be left unmodified.
        for (; i-- > output->capacity; ) {
          arm_msgs__msg__ArmJoints__fini(&output->data[i]);
        }
        return false;
      }
    }
    output->capacity = input->size;
  }
  output->size = input->size;
  for (size_t i = 0; i < input->size; ++i) {
    if (!arm_msgs__msg__ArmJoints__copy(
        &(input->data[i]), &(output->data[i])))
    {
      return false;
    }
  }
  return true;
}
