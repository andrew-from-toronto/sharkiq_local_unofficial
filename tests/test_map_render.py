"""Rendering real captured maps."""
from __future__ import annotations

import io

import pytest

from PIL import Image

from custom_components.sharkiq_local_unofficial.map_render import SCALE, render_map

from .conftest import decode_frame


def test_renders_the_persisted_map():
    vacuum_map = decode_frame("sharkiq_persisted_map_frame.b64")
    image = Image.open(io.BytesIO(render_map(vacuum_map)))
    # At least the grid; wider where room outlines reach past what was explored.
    assert image.size[0] >= vacuum_map.grid.width * SCALE
    assert image.size[1] >= vacuum_map.grid.height * SCALE


def test_renders_a_live_frame_with_rooms_from_the_persisted_map():
    live = decode_frame("sharkiq_live_map_frame.b64")
    persisted = decode_frame("sharkiq_persisted_map_frame.b64")
    assert live.rooms == []
    assert render_map(live) != render_map(live, persisted)


def test_draws_the_saved_spot_zone_and_not_as_a_room():
    import dataclasses

    from sharklocal.models import MapRoom
    from sharklocal.vacuum_map import SPOT_ROOM_NAME, spot_polygon

    persisted = decode_frame("sharkiq_persisted_map_frame.b64")
    rooms = [dataclasses.replace(r, selected=False) for r in persisted.rooms]
    plain = dataclasses.replace(persisted, rooms=rooms)
    spot = MapRoom(SPOT_ROOM_NAME, spot_polygon(2.74, 0.02), selected=True, coverage=1.0)
    with_zone = dataclasses.replace(plain, rooms=[*rooms, spot])

    assert render_map(with_zone) != render_map(plain)
    # A zone never resizes the picture (it would move everything under a
    # map card's calibration); one reaching past the map is clipped.
    far = dataclasses.replace(spot, polygon=spot_polygon(8.0, 0.0))
    wide = dataclasses.replace(plain, rooms=[*rooms, far])
    assert Image.open(io.BytesIO(render_map(wide))).size == Image.open(io.BytesIO(render_map(plain))).size


def test_calibration_points_match_the_drawing():
    from custom_components.sharkiq_local_unofficial.map_render import Canvas

    persisted = decode_frame("sharkiq_persisted_map_frame.b64")
    canvas = Canvas.fit(persisted)
    assert (canvas.width, canvas.height) == Image.open(io.BytesIO(render_map(persisted))).size

    # Solve the affine map a card derives from the three points, and check it
    # lands the dock exactly where the renderer draws it.
    (p0, p1, p2) = canvas.calibration_points()
    sx = p1["map"]["x"] - p0["map"]["x"]  # pixels per metre along x
    sy = p2["map"]["y"] - p0["map"]["y"]  # pixels per metre along y (negative: y up)
    dock = persisted.dock
    card_x = p0["map"]["x"] + (dock.x - p0["vacuum"]["x"]) * sx
    card_y = p0["map"]["y"] + (dock.y - p0["vacuum"]["y"]) * sy
    drawn_x, drawn_y = canvas.px((dock.x, dock.y))
    assert card_x == pytest.approx(drawn_x, abs=0.05)
    assert card_y == pytest.approx(drawn_y, abs=0.05)
    assert sy < 0 < sx


def test_draws_a_live_target():
    from types import SimpleNamespace

    from sharklocal.vacuum_map import spot_polygon

    live = decode_frame("sharkiq_live_map_frame.b64")
    persisted = decode_frame("sharkiq_persisted_map_frame.b64")
    nothing = SimpleNamespace(rooms=(), zone=())
    rooms = SimpleNamespace(rooms=("Hallway",), zone=())
    zone = SimpleNamespace(rooms=(), zone=tuple(spot_polygon(-0.8, 0.0)))

    pictures = {render_map(live, persisted, t) for t in (nothing, rooms, zone)}
    assert len(pictures) == 3
