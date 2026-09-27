"""Rendering real captured maps."""
from __future__ import annotations

import io

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
    # A zone reaching past everything else grows the canvas to fit it.
    far = dataclasses.replace(spot, polygon=spot_polygon(8.0, 0.0))
    wide = dataclasses.replace(plain, rooms=[*rooms, far])
    assert Image.open(io.BytesIO(render_map(wide))).size[0] > Image.open(io.BytesIO(render_map(plain))).size[0]


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
