"""Render a Shark map to PNG: floor by room, walls, doors, the cleaned path, dock and robot."""
from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageFont

from sharklocal.models import MapGrid, MapPoint, VacuumMap

SCALE = 4  # pixels per grid cell (the basement grid is ~191 x 105 cells)

BACKGROUND = (22, 22, 30, 0)
FLOOR = (120, 130, 150, 255)
WALL = (190, 66, 66, 255)
ROOM_COLOURS = [
    (150, 200, 235, 255),
    (120, 200, 140, 255),
    (230, 180, 90, 255),
    (190, 140, 220, 255),
    (240, 120, 120, 255),
    (120, 220, 220, 255),
]
PATH = (255, 215, 40, 190)
EDGE = (255, 255, 255, 230)
DOOR = (255, 80, 80, 255)
DOCK = (80, 160, 255, 255)
ROBOT = (255, 255, 255, 255)
LABEL_BG = (0, 0, 0, 160)


def render_map(vacuum_map: VacuumMap, rooms_from: VacuumMap | None = None) -> bytes:
    """Draw *vacuum_map* and return PNG bytes.

    Live frames carry no rooms, so room names and outlines come from
    *rooms_from* (the latest persisted map) when given. Everything is placed by
    world coordinates, so the two need not share a grid.
    """
    grid = vacuum_map.grid
    width, height = grid.width * SCALE, grid.height * SCALE
    image = Image.new("RGBA", (width, height), BACKGROUND)
    _draw_grid(image, grid)

    draw = ImageDraw.Draw(image, "RGBA")

    def px(point: MapPoint | tuple[float, float]) -> tuple[float, float]:
        x, y = (point.x, point.y) if isinstance(point, MapPoint) else point
        col = (x - grid.origin.x) / grid.resolution
        row = (y - grid.origin.y) / grid.resolution
        # Row 0 is the bottom of the map; image row 0 is the top.
        return (col * SCALE, (grid.height - row) * SCALE)

    source = rooms_from or vacuum_map
    for feature in source.features:
        if len(feature.points) >= 2:
            door = feature.kind == "door"
            draw.line(
                [px(p) for p in feature.points],
                fill=DOOR if door else EDGE,
                width=4 if door else 2,
            )

    if len(vacuum_map.path) >= 2:
        draw.line([px(p) for p in vacuum_map.path], fill=PATH, width=2, joint="curve")

    if (dock := source.dock) is not None:
        _disc(draw, px((dock.x, dock.y)), SCALE * 1.3, DOCK)

    if (robot := vacuum_map.robot) is not None:
        centre = px((robot.x, robot.y))
        radius = SCALE * 1.6
        _disc(draw, centre, radius, ROBOT)
        # Heading: radians anticlockwise from +x; image y grows downwards.
        tip = (
            centre[0] + math.cos(robot.heading) * radius * 2,
            centre[1] - math.sin(robot.heading) * radius * 2,
        )
        draw.line([centre, tip], fill=ROBOT, width=3)

    font = ImageFont.load_default(size=SCALE * 3)
    for room in source.rooms:
        if not room.polygon:
            continue
        points = [px(p) for p in room.polygon]
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        name = room.name.strip()
        left, top, right, bottom = draw.textbbox((cx, cy), name, font=font, anchor="mm")
        draw.rectangle((left - 3, top - 2, right + 3, bottom + 2), fill=LABEL_BG)
        draw.text((cx, cy), name, font=font, fill=(255, 255, 255, 255), anchor="mm")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _draw_grid(image: Image.Image, grid: MapGrid) -> None:
    """Paint one cell per SCALE x SCALE block: walls, floor coloured by room."""
    cells = Image.new("RGBA", (grid.width, grid.height), BACKGROUND)
    pixels = cells.load()
    for row in range(grid.height):
        y = grid.height - 1 - row
        for col in range(grid.width):
            value = grid.cell(col, row)
            if MapGrid.is_wall(value):
                pixels[col, y] = WALL
            elif MapGrid.is_floor(value):
                room = grid.room_id(col, row)
                pixels[col, y] = (
                    ROOM_COLOURS[room % len(ROOM_COLOURS)] if room else FLOOR
                )
    image.paste(cells.resize(image.size, Image.Resampling.NEAREST))


def _disc(draw: ImageDraw.ImageDraw, centre: tuple[float, float], radius: float, fill) -> None:
    x, y = centre
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=fill,
        outline=(255, 255, 255, 255),
        width=2,
    )
