import pytest

from tools import crop

# One line sits at y=10..40 and is split into two horizontally adjacent blocks;
# the next line sits at y=60..90.
LINE_ONE_LEFT = {"box": [[10, 10], [80, 10], [80, 40], [10, 40]], "text": "左边"}
LINE_ONE_RIGHT = {"box": [[90, 12], [160, 12], [160, 38], [90, 38]], "text": "右边"}
LINE_TWO = {"box": [[10, 60], [80, 60], [80, 90], [10, 90]], "text": "下一行"}


def test_block_box_is_the_bounding_rectangle():
    assert crop.block_box(LINE_ONE_LEFT) == (10, 10, 80, 40)


def test_same_line_detects_horizontal_neighbours():
    assert crop.same_line(LINE_ONE_LEFT, LINE_ONE_RIGHT)
    assert not crop.same_line(LINE_ONE_LEFT, LINE_TWO)


def test_crop_box_pads_the_block_and_its_line():
    box = crop.crop_box([LINE_ONE_LEFT, LINE_ONE_RIGHT], 0, pad_x=10, pad_y=5)
    assert box == (0, 5, 170, 45)


def test_crop_box_context_reaches_an_adjacent_line():
    box = crop.crop_box([LINE_ONE_LEFT, LINE_TWO], 0, context=1, pad_x=10, pad_y=5)
    assert box == (0, 5, 90, 95)


def test_crop_box_context_stops_at_a_page_gap():
    far_below = {"box": [[10, 300], [80, 300], [80, 330], [10, 330]], "text": "很远"}
    box = crop.crop_box([LINE_ONE_LEFT, far_below], 0, context=1, pad_x=10, pad_y=5)
    assert box == (0, 5, 90, 45)


def test_crop_box_never_leaves_the_image_on_the_negative_side():
    corner = {"box": [[2, 2], [20, 2], [20, 20], [2, 20]], "text": "角"}
    left, top, _, _ = crop.crop_box([corner], 0, pad_x=40, pad_y=12)
    assert (left, top) == (0, 0)


def test_crop_box_rejects_an_unknown_block():
    with pytest.raises(IndexError):
        crop.crop_box([LINE_ONE_LEFT], 1)
