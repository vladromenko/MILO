"""Screen gaze uses the verified RC13 mirror convention, independent of motors."""
import math


def camera_to_screen(center):
    x, y = center
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (x, y)):
        raise ValueError("invalid gaze center")
    # Camera and face look at the user from opposite sides of the image plane.
    return [max(-1., min(1., -(x - .5) * 2)),
            max(-1., min(1., (y - .5) * 2))]
