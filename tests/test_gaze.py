import pytest
from milo_next.gaze import camera_to_screen


def test_horizontal_mirror_matches_golden_config():
    assert camera_to_screen((.75, .5)) == [-.5, 0]
    assert camera_to_screen((.25, .5)) == [.5, 0]


def test_vertical_is_not_mirrored():
    assert camera_to_screen((.5, .25)) == [0, -.5]
    assert camera_to_screen((.5, .75)) == [0, .5]


def test_center_bounds_and_nonfinite():
    assert camera_to_screen((.5, .5)) == [0, 0]
    assert camera_to_screen((-5, 5)) == [1, 1]
    with pytest.raises(ValueError):
        camera_to_screen((float("nan"), .5))
