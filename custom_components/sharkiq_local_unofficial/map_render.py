"""Render a Shark map to PNG: floor by room, what the job cleaned and was sent to clean,
walls, doors, path, dock and robot."""
from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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
# Cleaned floor is the room colour blended this far towards white.
CLEANED_LIFT = 0.55
PATH = (255, 215, 40, 190)
EDGE = (255, 255, 255, 230)
DOOR = (255, 80, 80, 255)
DOCK = (80, 160, 255, 255)
ROBOT = (35, 35, 45, 255)
OUTLINE = (255, 255, 255, 255)
LABEL_BG = (0, 0, 0, 160)
# The job's target: a spot zone, or the rooms chosen for a room clean.
ZONE_FILL = (80, 160, 255, 70)
ZONE_EDGE = (80, 160, 255, 255)
TARGET_EDGE = (255, 255, 255, 255)


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
            _dashed_polygon(draw, [px(p) for p in room.polygon], TARGET_EDGE, width=3)
    if len(zone) >= 3:
        points = [px(p) for p in zone]
        draw.polygon(points, fill=ZONE_FILL)
        _dashed_polygon(draw, points, ZONE_EDGE, width=3)

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
        # A ring wider than the robot, so it still shows with the robot parked on it.
        x, y = px((dock.x, dock.y))
        r = SCALE * 2.6
        draw.ellipse((x - r, y - r, x + r, y + r), outline=DOCK, width=3)

    if (robot := vacuum_map.robot) is not None:
        centre = px((robot.x, robot.y))
        radius = SCALE * 1.6
        _disc(draw, centre, radius, ROBOT)
        # Heading: radians anticlockwise from +x; image y grows downwards.
        tip = (
            centre[0] + math.cos(robot.heading) * radius * 2,
            centre[1] - math.sin(robot.heading) * radius * 2,
        )
        draw.line([centre, tip], fill=OUTLINE, width=3)

    font = ImageFont.load_default(size=SCALE * 3)
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
        draw.rectangle((left - 3, top - 2, right + 3, bottom + 2), fill=LABEL_BG)
        draw.text((cx, cy), name, font=font, fill=(255, 255, 255, 255), anchor="mm")

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
                room = grid.room_id(col, row)
                colour = ROOM_COLOURS[room % len(ROOM_COLOURS)] if room else FLOOR
                if MapGrid.is_cleaned(value):
                    colour = _lift(colour)
                pixels[col, y] = colour
    size = (grid.width * SCALE, grid.height * SCALE)
    image.paste(cells.resize(size, Image.Resampling.NEAREST), offset)


def _lift(colour: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    r, g, b, a = colour
    return (
        round(r + (255 - r) * CLEANED_LIFT),
        round(g + (255 - g) * CLEANED_LIFT),
        round(b + (255 - b) * CLEANED_LIFT),
        a,
    )


def _disc(draw: ImageDraw.ImageDraw, centre: tuple[float, float], radius: float, fill) -> None:
    x, y = centre
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=fill,
        outline=OUTLINE,
        width=2,
    )
