"""Rendering real captured maps."""
from __future__ import annotations

import io

from PIL import Image

from custom_components.sharkiq_local_unofficial.map_render import SCALE, render_map

from .conftest import decode_frame


def test_renders_the_persisted_map():
    vacuum_map = decode_frame("sharkiq_persisted_map_frame.b64")
    image = Image.open(io.BytesIO(render_map(vacuum_map)))
    assert image.size == (vacuum_map.grid.width * SCALE, vacuum_map.grid.height * SCALE)


def test_renders_a_live_frame_with_rooms_from_the_persisted_map():
    live = decode_frame("sharkiq_live_map_frame.b64")
    persisted = decode_frame("sharkiq_persisted_map_frame.b64")
    assert live.rooms == []
    assert render_map(live) != render_map(live, persisted)
