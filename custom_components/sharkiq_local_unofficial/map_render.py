"""Render a Shark map to PNG in the SharkClean app's live-map style.

Floor, walls, what the job cleaned and was sent to clean, doors, path, dock and
robot. Colours, the robot marker and the dock marker are the app's own
(``MapPaints`` / ``res/values/colors.xml`` and its vector drawables, rasterised
into ``icons/``).
"""
from __future__ import annotations

import io
import math
from functools import cache
from pathlib import Path
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from sharklocal.models import MapGrid, MapPoint, VacuumMap

SCALE = 6  # pixels per grid cell (the basement grid is ~191 x 105 cells)
# Map cards shrink the picture to the card's width, so label size is set as a
# fraction of the picture's width rather than in pixels.
LABEL_WIDTH_FRACTION = 1 / 42


def _w(width: float) -> int:
    """A line width given at SCALE 4, at the current scale."""
    return max(1, round(width * SCALE / 4))

ICONS = Path(__file__).parent / "icons"

BACKGROUND = (0, 0, 0, 0)
FLOOR = (0xD0, 0xD0, 0xDA, 255)  # light_gray: floor and rooms
WALL = (0x8A, 0x8B, 0x9C, 255)  # medium_gray: room and floor outline
CLEANED = (0xBB, 0xE5, 0xEE, 255)  # cleaned_area
PATH = (0xF6, 0xF6, 0xF6, 255)  # grey01: the report map's cleaned track
EDGE = (0x8A, 0x8B, 0x9C, 255)  # medium_gray
DOOR = (0x9E, 0x9E, 0xAB, 255)  # gray: the app's dashed room line
PURPLE = (0x77, 0x00, 0xFF, 255)  # purple: selection and spot clean
SELECTED_FILL = (0x77, 0x00, 0xFF, 0x33)  # purple_translucent_33
ZONE_FILL = (0x77, 0x00, 0xFF, 51)  # spot clean at 20 %
DARKEST = (0x4A, 0x4A, 0x53, 255)  # darkest_gray
LABEL_FILL = (0xFC, 0xFC, 0xFF, 255)  # white
LABEL_BORDER = (0x19, 0x19, 0x23, 0x16)  # black_translucent
LABEL_TEXT = DARKEST
ROBOT_METRES = 0.33  # the robot marker's disc, drawn to scale
ROBOT_DISC_FRACTION = 32.1 / 104  # disc diameter within live_cleaning_robot


@cache
def _icon(name: str) -> Image.Image:
    return Image.open(ICONS / name).convert("RGBA")


def render_map(
    vacuum_map: VacuumMap,
    rooms_from: VacuumMap | None = None,
    target: Any = None,
) -> bytes:
    """Draw *vacuum_map* and return PNG bytes.

    Live frames carry no rooms, so room names and outlines come from
    *rooms_from* (the latest persisted map) when given. Everything is placed by
    world coordinates, so the two need not share a grid.

    *target* (anything with ``rooms`` and ``zone``) is what a running job was
    sent to clean; without one, the saved map's record of the last job is
    drawn instead — its spot zone, or the rooms it selected.
    """
    grid = vacuum_map.grid
    source = rooms_from or vacuum_map
    target_rooms, zone = _target(source, target)

    canvas = Canvas.fit(vacuum_map, rooms_from)
    width, height = canvas.width, canvas.height
    image = Image.new("RGBA", (width, height), BACKGROUND)
    _draw_grid(image, grid, canvas.grid_offset)

    draw = ImageDraw.Draw(image, "RGBA")
    px = canvas.px

    for room in source.named_rooms:
        if room.name in target_rooms and len(room.polygon) >= 3:
            points = [px(p) for p in room.polygon]
            draw.polygon(points, fill=SELECTED_FILL)
            draw.line(points + points[:1], fill=PURPLE, width=_w(2))
    if len(zone) >= 3:
        _spot_zone(draw, [px(p) for p in zone])

    for feature in source.features:
        if len(feature.points) >= 2:
            door = feature.kind == "door"
            draw.line(
                [px(p) for p in feature.points],
                fill=DOOR if door else EDGE,
                width=_w(3) if door else _w(2),
            )

    if len(vacuum_map.path) >= 2:
        draw.line([px(p) for p in vacuum_map.path], fill=PATH, width=_w(1.5), joint="curve")

    font = ImageFont.load_default(size=max(SCALE * 3, round(width * LABEL_WIDTH_FRACTION)))
    for room in source.named_rooms:
        if not room.polygon:
            continue
        points = [px(p) for p in room.polygon]
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        name = room.name.strip()
        left, top, right, bottom = draw.textbbox((cx, cy), name, font=font, anchor="mm")
        # Keep the label inside the picture: a room at the edge would clip it.
        cx += max(0, 4 - left) - max(0, right - (width - 4))
        cy += max(0, 3 - top) - max(0, bottom - (height - 3))
        left, top, right, bottom = draw.textbbox((cx, cy), name, font=font, anchor="mm")
        pad = max(3, font.size // 3)
        box = (left - pad, top - pad // 2, right + pad, bottom + pad // 2)
        selected = room.name in target_rooms
        # The app's label pill: purple for a selected room, white otherwise.
        draw.rounded_rectangle(
            box,
            radius=(box[3] - box[1]) / 2,
            fill=PURPLE if selected else LABEL_FILL,
            outline=None if selected else LABEL_BORDER,
            width=_w(1),
        )
        draw.text((cx, cy), name, font=font, fill=LABEL_FILL if selected else LABEL_TEXT, anchor="mm")

    # Markers last: the robot and its dock sit above everything, labels included.
    pixels_per_metre = SCALE / grid.resolution
    if (robot := vacuum_map.robot) is not None:
        size = round(ROBOT_METRES * pixels_per_metre / ROBOT_DISC_FRACTION)
        marker = _icon("robot.png").resize((size, size), Image.Resampling.LANCZOS)
        # The drawable faces -x; headings are anticlockwise from +x, and PIL
        # rotates anticlockwise too.
        marker = marker.rotate(math.degrees(robot.heading) - 180, resample=Image.Resampling.BICUBIC)
        _paste_centred(image, marker, px((robot.x, robot.y)))

    if (dock := source.dock) is not None:
        # Over the robot, so a parked robot does not hide its dock.
        size = round(0.24 * pixels_per_metre)
        marker = _icon("dock.png").resize((size, size), Image.Resampling.LANCZOS)
        _paste_centred(image, marker, px((dock.x, dock.y)))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@dataclass(frozen=True)
class Canvas:
    """Where the map sits in the picture: the world-to-pixel transform.

    It depends only on the map (the grid and the room outlines, which can reach
    past the explored grid), never on a job's target, so the calibration a map
    card reads stays true for the picture it is showing.
    """

    origin_x: float  # world x of the grid's cell (0, 0)
    origin_y: float
    resolution: float
    grid_height: int
    cols_left: int  # cells of margin left of the grid
    rows_above: int  # cells of margin above the grid
    width: int  # pixels
    height: int

    @classmethod
    def fit(cls, vacuum_map: VacuumMap, rooms_from: VacuumMap | None = None) -> Canvas:
        """The canvas for *vacuum_map* with *rooms_from*'s room outlines."""
        grid = vacuum_map.grid
        res = grid.resolution
        points = [p for room in (rooms_from or vacuum_map).named_rooms for p in room.polygon]
        grid_right = grid.origin.x + grid.width * res
        grid_top = grid.origin.y + grid.height * res
        cols_left = max(0, math.ceil((grid.origin.x - min([grid.origin.x, *(p.x for p in points)])) / res))
        cols_right = max(0, math.ceil((max([grid_right, *(p.x for p in points)]) - grid_right) / res))
        rows_above = max(0, math.ceil((max([grid_top, *(p.y for p in points)]) - grid_top) / res))
        rows_below = max(0, math.ceil((grid.origin.y - min([grid.origin.y, *(p.y for p in points)])) / res))
        return cls(
            origin_x=grid.origin.x,
            origin_y=grid.origin.y,
            resolution=res,
            grid_height=grid.height,
            cols_left=cols_left,
            rows_above=rows_above,
            width=(cols_left + grid.width + cols_right) * SCALE,
            height=(rows_above + grid.height + rows_below) * SCALE,
        )

    @property
    def grid_offset(self) -> tuple[int, int]:
        """Pixel position of the grid's top-left corner."""
        return (self.cols_left * SCALE, self.rows_above * SCALE)

    def px(self, point: MapPoint | tuple[float, float]) -> tuple[float, float]:
        """World metres to picture pixels."""
        x, y = (point.x, point.y) if isinstance(point, MapPoint) else point
        col = (x - self.origin_x) / self.resolution
        row = (y - self.origin_y) / self.resolution
        # Row 0 is the bottom of the map; image row 0 is the top.
        return (
            (self.cols_left + col) * SCALE,
            (self.rows_above + self.grid_height - row) * SCALE,
        )

    def calibration_points(self) -> list[dict[str, dict[str, float]]]:
        """Three world/pixel pairs, in the shape vacuum map cards read."""
        corners = [
            (self.origin_x, self.origin_y),
            (self.origin_x + 1.0, self.origin_y),
            (self.origin_x, self.origin_y + 1.0),
        ]
        points = []
        for x, y in corners:
            map_x, map_y = self.px((x, y))
            points.append(
                {
                    "vacuum": {"x": round(x, 4), "y": round(y, 4)},
                    "map": {"x": round(map_x, 2), "y": round(map_y, 2)},
                }
            )
        return points


def _target(source: VacuumMap, target: Any) -> tuple[set[str], Sequence[MapPoint]]:
    """The rooms and zone to highlight."""
    if target is not None:
        return set(target.rooms), target.zone
    spot = source.spot
    zone = spot.polygon if spot is not None and spot.selected else ()
    return {room.name for room in source.named_rooms if room.selected}, zone


def _dashed_polygon(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: tuple[int, int, int, int],
    width: int,
    dash: float = SCALE * 2.5,
) -> None:
    """Outline a polygon with dashes (PIL draws only solid lines)."""
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(length // dash))
        for i in range(0, steps, 2):
            a, b = i / steps, min(i + 1, steps) / steps
            draw.line(
                [(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a), (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)],
                fill=fill,
                width=width,
            )


def _draw_grid(image: Image.Image, grid: MapGrid, offset: tuple[int, int]) -> None:
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
                pixels[col, y] = CLEANED if MapGrid.is_cleaned(value) else FLOOR
    size = (grid.width * SCALE, grid.height * SCALE)
    image.paste(cells.resize(size, Image.Resampling.NEAREST), offset)


def _spot_zone(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]]) -> None:
    """The app's spot-clean area: translucent purple, dashed edge, a bull's-eye."""
    draw.polygon(points, fill=ZONE_FILL)
    _dashed_polygon(draw, points, PURPLE, width=_w(2))
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    ring = SCALE * 2.2
    draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=PURPLE, width=_w(2))
    dot = SCALE * 0.7
    draw.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=DARKEST)


def _paste_centred(image: Image.Image, marker: Image.Image, centre: tuple[float, float]) -> None:
    """Draw *marker* centred on *centre*, blended by its own alpha, clipped at the edges."""
    x = round(centre[0] - marker.width / 2)
    y = round(centre[1] - marker.height / 2)
    image.paste(marker, (x, y), marker)
