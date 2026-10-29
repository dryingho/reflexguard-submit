"""Webots 월드의 실제 3D 에셋 배치 — 격자 맵(simulation/world.py)의 막힌 칸·바닥 종류를 Webots 공식 에셋으로 그린다.

에셋은 저장소에 넣지 않고 Webots 공식 저장소 주소(github.com/cyberbotics/webots, R2025a 태그)로 참조한다. Webots가 월드를 처음 열 때 받아서
캐시하므로 첫 실행에만 인터넷이 필요하다. 에셋 라이선스: Cyberbotics, Webots에서만 사용 (https://cyberbotics.com/webots_assets_license).

부딪힘 판정은 지금처럼 격자 칸으로 한다 — 모델은 그 칸 안에 들어가게 놓는다(건물은 블록과 똑같이, 차·소품은 자리보다 조금 작게).

    칸 종류      Webots에서
    B 건물       SimpleBuilding (블록을 직사각형으로 나눠 바닥 모양을 그대로 준다, 층수·외벽은 맵마다)
    T 차         승용차(2×5칸)·승합차(2×7칸) 모델. 긴 쪽 방향으로, 차도에서는 달리는 방향을 보고 선다
    Y 나무       SimpleTree + 가로수 보호틀(인도) / 나무 둘레 흙(공원)
    K 소품       2×2 작은 매점, 1×3 벤치(병원에서는 침대), 1×2 차양 노점
    O 기둥       콘크리트 기둥 (광장의 2×2는 대리석 받침 위 조형물)
    # 벽         실내: 회벽 + 걸레받이·손잡이, 바깥: 콘크리트 담
    바닥          아스팔트·보도블록·잔디 재질 + 흰 도색(중앙선·횡단보도), 실내는 타일 + 천장 조명판
사람은 Webots 보행자(Pedestrian)의 몸 부위 모델로 직접 조립해 어른(키 1.7 m)과 아이(1.15 m, 줄임)를 만들고,
팔·다리 관절에 DEF를 붙여 컨트롤러가 걷는 동작을 준다 (actor_joint_names, simulation/gait.py).
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

from simulation.world import (ASPHALT, BUILDING, CROSS_X, CROSS_Y, FLOOR, FURNITURE, GRASS, LANE_X, LANE_Y, PILLAR,
                              SIDEWALK, TREE, TRUCK, WALL, World)

WEBOTS = "https://raw.githubusercontent.com/cyberbotics/webots/R2025a/projects"
PROTOS = {
    "TexturedBackground": f"{WEBOTS}/objects/backgrounds/protos/TexturedBackground.proto",
    "TexturedBackgroundLight": f"{WEBOTS}/objects/backgrounds/protos/TexturedBackgroundLight.proto",
    "Asphalt": f"{WEBOTS}/appearances/protos/Asphalt.proto",
    "Pavement": f"{WEBOTS}/appearances/protos/Pavement.proto",
    "Grass": f"{WEBOTS}/appearances/protos/Grass.proto",
    "Roughcast": f"{WEBOTS}/appearances/protos/Roughcast.proto",
    "RoughConcrete": f"{WEBOTS}/appearances/protos/RoughConcrete.proto",
    "RedBricks": f"{WEBOTS}/appearances/protos/RedBricks.proto",
    "Marble": f"{WEBOTS}/appearances/protos/Marble.proto",
    "MarbleTiles": f"{WEBOTS}/appearances/protos/MarbleTiles.proto",
    "CementTiles": f"{WEBOTS}/appearances/protos/CementTiles.proto",
    "Soil": f"{WEBOTS}/appearances/protos/Soil.proto",
    "PaintedWood": f"{WEBOTS}/appearances/protos/PaintedWood.proto",
    "SimpleBuilding": f"{WEBOTS}/objects/buildings/protos/SimpleBuilding.proto",
    "SimpleTree": f"{WEBOTS}/objects/trees/protos/SimpleTree.proto",
    "SmallKiosk": f"{WEBOTS}/objects/street_furniture/protos/SmallKiosk.proto",
    "Bench": f"{WEBOTS}/objects/street_furniture/protos/Bench.proto",
    "HospitalBed": f"{WEBOTS}/objects/hospital/protos/HospitalBed.proto",
    "ToyotaPriusSimple": f"{WEBOTS}/vehicles/protos/toyota/ToyotaPriusSimple.proto",
    "BmwX5Simple": f"{WEBOTS}/vehicles/protos/bmw/BmwX5Simple.proto",
    "CitroenCZeroSimple": f"{WEBOTS}/vehicles/protos/citroen/CitroenCZeroSimple.proto",
    "LincolnMKZSimple": f"{WEBOTS}/vehicles/protos/lincoln/LincolnMKZSimple.proto",
    "TeslaModel3Simple": f"{WEBOTS}/vehicles/protos/tesla/TeslaModel3Simple.proto",
    "RangeRoverSportSVRSimple": f"{WEBOTS}/vehicles/protos/range_rover/RangeRoverSportSVRSimple.proto",
    "MercedesBenzSprinterSimple": f"{WEBOTS}/vehicles/protos/mercedes_benz/MercedesBenzSprinterSimple.proto",
}
_PEDESTRIAN_PARTS = ("Torso", "Neck", "Head", "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm",
                     "RightHand", "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot")
for _part in _PEDESTRIAN_PARTS:
    PROTOS[f"Pedestrian{_part}"] = f"{WEBOTS}/humans/pedestrian/protos/Pedestrian{_part}.proto"

# 차 모델: 이름 → (뒤 차축에서 앞 끝·뒤 끝까지 거리 m). 모델의 원점은 뒤 차축이라 자리 가운데에 맞추려면 이만큼 옮긴다
CARS = {
    "ToyotaPriusSimple": (3.635, -0.85), "BmwX5Simple": (3.85, -1.0), "CitroenCZeroSimple": (3.075, -0.425),
    "LincolnMKZSimple": (3.944, -1.06), "TeslaModel3Simple": (3.79, -1.05), "RangeRoverSportSVRSimple": (3.5, -1.06),
}
VANS = {"MercedesBenzSprinterSimple": (5.31, -1.62)}
CAR_COLORS = ((0.75, 0.76, 0.78), (0.08, 0.09, 0.1), (0.62, 0.08, 0.07), (0.92, 0.92, 0.9), (0.15, 0.25, 0.5),
              (0.35, 0.36, 0.38), (0.55, 0.5, 0.42), (0.1, 0.3, 0.2))

# 맵마다 건물 외벽·층수, 인도 재질, 나무 종류
SCENE_STYLE = {
    "road": {"walls": ("windowed building", "classic building", "orange building", "gray glass building", "brick building",
                       "old building"), "floors": (3, 6), "pavement": "braun square stone", "trees": ("cherry tree",)},
    "plaza": {"walls": ("glass building", "classic building", "blue glass building", "windowed building"),
              "floors": (4, 7), "pavement": "slate", "trees": ("oak tree", "hackberry tree")},
    "alley": {"walls": ("residential building", "old house", "brick building", "old brick building", "stone brick"),
              "floors": (2, 3), "pavement": "braun stone", "trees": ("hazel tree",)},
    "parking": {"walls": ("windowed building", "gray glass building"), "floors": (3, 4), "pavement": "tiles",
                "trees": ("birch tree",)},
    "park": {"walls": ("classic building",), "floors": (3, 4), "pavement": "braun stone",
             "trees": ("oak tree", "hackberry tree", "crab apple tree", "birch tree")},
}
DEFAULT_STYLE = SCENE_STYLE["road"]

# 걷는 사람: 보행자 모델의 관절 (이름, 어느 관절 안에 있는지, 회전 중심 — 엉덩이 높이 1.27 m 기준 좌표)
HIP_HEIGHT = 1.27      # 보행자 모델의 원점(엉덩이) 높이 — 모델 키는 약 1.8 m
MODEL_HEIGHT = 1.8
JOINTS = (
    ("LA", None, (-0.01, 0.28, 0.19)), ("LLA", "LA", (-0.04, 0.25, -0.09)), ("LH", "LLA", (-0.035, 0.25, -0.37)),
    ("RA", None, (-0.01, -0.28, 0.19)), ("RLA", "RA", (-0.04, -0.25, -0.09)), ("RH", "RLA", (-0.035, -0.25, -0.37)),
    ("LL", None, (-0.04, 0.17, -0.3)), ("LLL", "LL", (-0.03, 0.17, -0.76)), ("LF", "LLL", (-0.092, 0.17, -1.165)),
    ("RL", None, (-0.04, -0.17, -0.3)), ("RLL", "RL", (-0.03, -0.17, -0.76)), ("RF", "RLL", (-0.092, -0.17, -1.165)),
)
_JOINT_PARTS = {"LA": ("LeftUpperArm", "shirt"), "LLA": ("LeftLowerArm", "skin"), "LH": ("LeftHand", "skin"),
                "RA": ("RightUpperArm", "shirt"), "RLA": ("RightLowerArm", "skin"), "RH": ("RightHand", "skin"),
                "LL": ("LeftUpperLeg", "pants"), "LLL": ("LeftLowerLeg", "pants"), "LF": ("LeftFoot", "shoes"),
                "RL": ("RightUpperLeg", "pants"), "RLL": ("RightLowerLeg", "pants"), "RF": ("RightFoot", "shoes")}


def actor_joint_names() -> tuple[str, ...]:
    """컨트롤러가 돌리는 관절 이름 (노드 DEF는 ACTOR_<장애물>_<관절>)."""
    return tuple(name for name, _, _ in JOINTS)


def _f(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _v(*values: float) -> str:
    return " ".join(_f(v) for v in values)


class Builder:
    """한 월드의 에셋 노드를 모은다. 쓰인 에셋만 EXTERNPROTO로 선언한다."""

    def __init__(self, world: World, scene_name: str, wall_height: float) -> None:
        self.world, self.scene, self.wall_h = world, scene_name, wall_height
        self.height = world.height
        self.used: list[str] = []
        self.lines: list[str] = []
        self.defined: set[str] = set()
        self.style = SCENE_STYLE.get(scene_name, DEFAULT_STYLE)
        self.counter = 0

    # --- 공통 ---
    def use(self, proto: str) -> str:
        if proto not in self.used:
            self.used.append(proto)
        return proto

    def externprotos(self) -> list[str]:
        return [f'EXTERNPROTO "{PROTOS[name]}"' for name in self.used]

    def wxy(self, x: float, y: float) -> tuple[float, float]:
        """격자 좌표 → Webots 평면 좌표 (y를 뒤집는다)."""
        return x, self.height - y

    def name(self, base: str) -> str:
        self.counter += 1
        return f"{base} {self.counter}"

    def shape_once(self, key: str, text: str) -> str:
        """같은 모양은 처음 한 번만 정의(DEF)하고 그다음부터는 USE."""
        if key in self.defined:
            return f"USE {key}"
        self.defined.add(key)
        return f"DEF {key} {text}"

    # --- 배경·빛 ---
    def background(self) -> None:
        if self.world.outdoor:
            self.lines += [f'{self.use("TexturedBackground")} {{ texture "noon_cloudy_countryside" }}',
                           f'{self.use("TexturedBackgroundLight")} {{ texture "noon_cloudy_countryside" castShadows FALSE }}']
        else:  # 실내: 하늘은 보이지 않게(반사·주변광만), 위에서 비추는 빛
            self.lines += [f'{self.use("TexturedBackground")} {{ texture "empty_office" skybox FALSE skyColor [ 0.55 0.57 0.6 ] }}',
                           f'{self.use("TexturedBackgroundLight")} {{ texture "empty_office" luminosity 0.8 castShadows FALSE }}',
                           "DirectionalLight { direction -0.2 -0.3 -1 intensity 1.2 castShadows FALSE }"]

    # --- 바닥 ---
    def floors(self) -> None:
        world = self.world
        if not world.outdoor:
            tiles = "MarbleTiles" if self.scene == "station" else "CementTiles"
            w, h = world.width, world.height
            self.lines.append(f"Solid {{ translation {_v(w / 2, h / 2, 0)} children [ Shape {{ appearance {self.use(tiles)} "
                              f"{{ textureTransform TextureTransform {{ scale {_v(w / 2, h / 2)} }} }} geometry DEF FLOOR_PLANE "
                              f'Plane {{ size {_v(w, h)} }} }} ] name "floor" boundingObject USE FLOOR_PLANE }}')
            self.ceiling()
            return
        kinds = world.floor.copy()
        kinds[world.grid != FLOOR] = 255
        groups = {"asphalt": (ASPHALT, LANE_X, LANE_Y, CROSS_X, CROSS_Y), "sidewalk": (SIDEWALK,), "grass": (GRASS,)}
        for label, members in groups.items():
            mask = np.isin(kinds, members)
            for x0, y0, x1, y1 in rectangles(mask):
                appearance = self.ground_appearance(label, x1 - x0, y1 - y0)
                cx, cy = self.wxy((x0 + x1) / 2, (y0 + y1) / 2)
                self.lines.append(f"Pose {{ translation {_v(cx, cy, 0)} children [ Shape {{ appearance {appearance} "
                                  f"geometry Plane {{ size {_v(x1 - x0, y1 - y0)} }} }} ] }}")
        # 막힌 칸 아래도 바닥을 깐다 (차·나무·소품 아래가 비어 보이지 않게): 차 아래는 아스팔트, 나머지는 둘레에 가장 많은 바닥
        for mask, fixed in ((world.grid == TRUCK, "asphalt"), ((world.grid == FURNITURE) | (world.grid == TREE), None)):
            for x0, y0, x1, y1 in rectangles(mask):
                kind = fixed or self.under_kind(x0, y0, x1, y1)
                cx, cy = self.wxy((x0 + x1) / 2, (y0 + y1) / 2)
                self.lines.append(f"Pose {{ translation {_v(cx, cy, 0)} children [ Shape {{ appearance "
                                  f"{self.ground_appearance(kind, x1 - x0, y1 - y0)} geometry Plane {{ size {_v(x1 - x0, y1 - y0)} }} }} ] }}")
        self.markings()

    def under_kind(self, x0: int, y0: int, x1: int, y1: int) -> str:
        """막힌 칸 아래 바닥: 이웃 바닥 중 가장 많은 종류."""
        world, around = self.world, []
        for cy in range(y0 - 1, y1 + 1):
            for cx in range(x0 - 1, x1 + 1):
                if 0 <= cx < world.width and 0 <= cy < world.height and world.grid[cy, cx] == FLOOR:
                    around.append(int(world.floor[cy, cx]))
        kind = max(set(around), key=around.count) if around else ASPHALT
        return "grass" if kind == GRASS else "sidewalk" if kind == SIDEWALK else "asphalt"

    def ground_appearance(self, label: str, w: float, h: float) -> str:
        if label == "asphalt":
            return f'{self.use("Asphalt")} {{ textureTransform TextureTransform {{ scale {_v(w / 4, h / 4)} }} }}'
        if label == "grass":
            return f'{self.use("Grass")} {{ type "maintained" textureTransform TextureTransform {{ scale {_v(w / 2, h / 2)} }} }}'
        return (f'{self.use("Pavement")} {{ type "{self.style["pavement"]}" '
                f'textureTransform TextureTransform {{ scale {_v(w / 1.5, h / 1.5)} }} }}')

    def paint(self) -> str:
        return self.shape_once("ROAD_PAINT", "PBRAppearance { baseColor 0.93 0.93 0.88 roughness 0.55 metalness 0 }")

    def markings(self) -> None:
        """흰 도색: 중앙선(점선, 칸 위쪽·왼쪽 가장자리)과 횡단보도(0.5 m 띠)."""
        world = self.world
        z = 0.003
        for cy in range(world.height):
            for cx in range(world.width):
                if world.grid[cy, cx] != FLOOR:
                    continue
                kind = world.floor[cy, cx]
                if kind == LANE_X:
                    x, y = self.wxy(cx + 0.3, cy)
                    self.lines.append(f"Pose {{ translation {_v(x, y, z)} children [ Shape {{ appearance {self.paint()} "
                                      "geometry Plane { size 0.6 0.12 } } ] }")
                elif kind == LANE_Y:
                    x, y = self.wxy(cx, cy + 0.3)
                    self.lines.append(f"Pose {{ translation {_v(x, y, z)} children [ Shape {{ appearance {self.paint()} "
                                      "geometry Plane { size 0.12 0.6 } } ] }")
        for kind, horizontal in ((CROSS_X, True), (CROSS_Y, False)):
            mask = (world.floor == kind) & (world.grid == FLOOR)
            for x0, y0, x1, y1 in rectangles(mask):
                if horizontal:  # 칸마다 위쪽 0.5 m가 흰 띠 (차도 방향과 나란한 줄)
                    for cy in range(y0, y1):
                        x, y = self.wxy((x0 + x1) / 2, cy + 0.25)
                        self.lines.append(f"Pose {{ translation {_v(x, y, z)} children [ Shape {{ appearance {self.paint()} "
                                          f"geometry Plane {{ size {_v(x1 - x0, 0.5)} }} }} ] }}")
                else:
                    for cx in range(x0, x1):
                        x, y = self.wxy(cx + 0.25, (y0 + y1) / 2)
                        self.lines.append(f"Pose {{ translation {_v(x, y, z)} children [ Shape {{ appearance {self.paint()} "
                                          f"geometry Plane {{ size {_v(0.5, y1 - y0)} }} }} ] }}")

    def ceiling(self) -> None:
        """실내 천장: 아래를 보는 판 + 3칸마다 조명판(빛나는 판, 실제 광원은 아님)."""
        world, w, h, top = self.world, self.world.width, self.world.height, self.wall_h
        self.lines.append(f"Solid {{ translation {_v(w / 2, h / 2, top)} rotation 1 0 0 {_f(math.pi)} children [ Shape {{ "
                          f"appearance PBRAppearance {{ baseColor 0.9 0.9 0.88 roughness 0.9 metalness 0 }} "
                          f'geometry Plane {{ size {_v(w, h)} }} }} ] name "ceiling" }}')
        lamp = ("Shape { appearance PBRAppearance { baseColor 1 1 1 emissiveColor 1 0.98 0.92 emissiveIntensity 1.5 "
                "roughness 1 metalness 0 } geometry Box { size 0.6 1.2 0.02 } }")
        for cy in range(1, world.height, 3):
            for cx in range(1, world.width, 3):
                if world.grid[cy, cx] == FLOOR:
                    x, y = self.wxy(cx + 0.5, cy + 0.5)
                    self.lines.append(f"Pose {{ translation {_v(x, y, top - 0.012)} children [ {self.shape_once('CEILING_LAMP', lamp)} ] }}")

    # --- 막힌 칸 ---
    def blocked(self) -> None:
        world = self.world
        grid = world.grid
        for x0, y0, x1, y1 in rectangles(grid == BUILDING):
            self.building(x0, y0, x1, y1)
        for x0, y0, x1, y1 in blocks(grid == TRUCK):
            self.vehicle(x0, y0, x1, y1)
        for x0, y0, x1, y1 in blocks(grid == FURNITURE):
            self.furniture(x0, y0, x1, y1)
        for cy, cx in np.argwhere(grid == TREE):
            self.tree(int(cx), int(cy))
        pillars = blocks(grid == PILLAR)
        for x0, y0, x1, y1 in pillars:
            self.pillar(x0, y0, x1, y1)
        self.walls()

    def faces_floor(self, cx: int, cy: int) -> list[tuple[int, int]]:
        world = self.world
        return [(dx, dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
                if 0 <= cx + dx < world.width and 0 <= cy + dy < world.height and world.grid[cy + dy, cx + dx] == FLOOR]

    def building(self, x0: int, y0: int, x1: int, y1: int) -> None:
        index = (x0 * 7 + y0 * 13) % 997
        walls, (low, high) = self.style["walls"], self.style["floors"]
        wall = walls[index % len(walls)]
        floors = low + index % (high - low + 1)
        corners = [self.wxy(x0, y1), self.wxy(x1, y1), self.wxy(x1, y0), self.wxy(x0, y0)]
        corner_text = ", ".join(_v(*c) for c in corners)
        self.lines.append(f'{self.use("SimpleBuilding")} {{ name "{self.name("building")}" floorHeight 3 floorNumber {floors} '
                          f'corners [ {corner_text} ] wallType "{wall}" roofType "gravel" roofShape "flat roof" roofHeight 1 '
                          "enableBoundingObject FALSE }")

    def vehicle(self, x0: int, y0: int, x1: int, y1: int) -> None:
        w, h = x1 - x0, y1 - y0
        long_side = max(w, h)
        if long_side >= 7:
            models = VANS
        elif long_side >= 4:
            models = CARS
        else:  # 자리가 작으면 소품처럼 (예전 맵 호환)
            self.furniture(x0, y0, x1, y1)
            return
        index = self.counter  # 차마다 모델·색을 차례로 돌린다 (같은 맵에서는 늘 같은 배치)
        model = list(models)[index % len(models)]
        front, rear = models[model]
        cx, cy = self.wxy((x0 + x1) / 2, (y0 + y1) / 2)
        if w >= h:   # 가로로 긴 자리: 차도 위쪽 절반은 서쪽, 아래쪽 절반은 동쪽을 보고 선다 (우측 통행)
            angle = math.pi if (y0 + y1) / 2 < self.road_middle() else 0.0
        else:        # 세로로 긴 자리(주차): 통로를 보고 선다
            angle = -math.pi / 2 if (y0 + y1) / 2 < self.height / 2 else math.pi / 2
        offset = (front + rear) / 2
        ox, oy = cx - math.cos(angle) * offset, cy - math.sin(angle) * offset
        color = CAR_COLORS[(index * 3) % len(CAR_COLORS)]
        self.lines.append(f'{self.use(model)} {{ translation {_v(ox, oy, 0.4)} rotation 0 0 1 {_f(angle)} '
                          f'color {_v(*color)} name "{self.name("vehicle")}" }}')

    def road_middle(self) -> float:
        """차도의 가운데 줄 (중앙선 '-' 줄의 위쪽 가장자리). 없으면 맵 가운데."""
        rows = np.argwhere((self.world.floor == LANE_X) & (self.world.grid == FLOOR))
        return float(np.median(rows[:, 0])) if len(rows) else self.height / 2

    def furniture(self, x0: int, y0: int, x1: int, y1: int) -> None:
        w, h = x1 - x0, y1 - y0
        cx, cy = self.wxy((x0 + x1) / 2, (y0 + y1) / 2)
        long_x = w >= h
        if w == 2 and h == 2:
            self.lines.append(f'{self.use("SmallKiosk")} {{ translation {_v(cx, cy, 0)} rotation 0 0 1 {_f(self.facing(x0, y0, x1, y1))} '
                              f'name "{self.name("kiosk")}" }}')
        elif max(w, h) == 3 and self.scene.startswith("hospital"):
            angle = 0.0 if not long_x else math.pi / 2   # 침대는 모델의 y축이 긴 쪽
            self.lines.append(f'{self.use("HospitalBed")} {{ translation {_v(cx, cy, 0.28)} rotation 0 0 1 {_f(angle)} '
                              f'name "{self.name("bed")}" }}')
        elif max(w, h) == 3:
            angle = (0.0 if long_x else math.pi / 2) + (math.pi if self.facing(x0, y0, x1, y1) > 0 else 0.0)
            self.lines.append(f'{self.use("Bench")} {{ translation {_v(cx, cy, 0)} rotation 0 0 1 {_f(angle)} '
                              f'woodColor 0.55 0.38 0.22 metalColor 0.2 0.2 0.22 name "{self.name("bench")}" }}')
        else:
            self.stall(cx, cy, w, h)

    def facing(self, x0: int, y0: int, x1: int, y1: int) -> float:
        """소품이 바라볼 방향(Webots 각): 바닥 칸이 더 많이 열린 쪽."""
        world = self.world
        best, angle = -1, 0.0
        for dx, dy, a in ((0, 1, -math.pi / 2), (0, -1, math.pi / 2), (1, 0, 0.0), (-1, 0, math.pi)):
            count = 0
            for k in range(1, 3):
                for cx in range(x0, x1):
                    for cy in range(y0, y1):
                        x, y = cx + dx * k * (x1 - x0), cy + dy * k * (y1 - y0)
                        if 0 <= x < world.width and 0 <= y < world.height and world.grid[y, x] == FLOOR:
                            count += 1
            if count > best:
                best, angle = count, a
        return angle

    def stall(self, cx: float, cy: float, w: int, h: int) -> None:
        """차양 달린 노점 (1×2칸에 꼭 맞게): 나무 판매대, 기둥 넷, 줄무늬 차양."""
        sx, sy = w - 0.1, h - 0.1
        self.lines.append(
            f'Pose {{ translation {_v(cx, cy, 0)} children [ '
            f'Pose {{ translation 0 0 0.45 children [ Shape {{ appearance {self.use("PaintedWood")} {{ colorOverride 0.55 0.38 0.25 }} '
            f'geometry Box {{ size {_v(sx, sy, 0.9)} }} }} ] }} '
            + " ".join(f'Pose {{ translation {_v(px * (sx / 2 - 0.04), py * (sy / 2 - 0.04), 1.2)} children [ Shape {{ '
                       'appearance PBRAppearance { baseColor 0.3 0.3 0.32 roughness 0.4 metalness 0.8 } '
                       'geometry Cylinder { height 2.4 radius 0.03 } } ] }' for px in (-1, 1) for py in (-1, 1))
            + f' Pose {{ translation 0 0 2.35 children [ Shape {{ appearance PBRAppearance {{ baseColor 0.85 0.2 0.18 '
            f'roughness 0.9 metalness 0 }} geometry Box {{ size {_v(sx + 0.2, sy + 0.2, 0.1)} }} }} ] }} ] }}')

    def tree(self, cx: int, cy: int) -> None:
        trees = self.style["trees"]
        kind = trees[(cx * 3 + cy) % len(trees)]
        x, y = self.wxy(cx + 0.5, cy + 0.5)
        height = 5.0 + (cx + cy) % 3 * 0.6
        self.lines.append(f'{self.use("SimpleTree")} {{ translation {_v(x, y, 0)} name "{self.name("tree")}" type "{kind}" '
                          f"height {_f(height)} radius 1.2 enableBoundingObject FALSE }}")
        if self.world.floor[cy, cx] == GRASS or self.under_kind(cx, cy, cx + 1, cy + 1) == "grass":
            self.lines.append(f'Pose {{ translation {_v(x, y, 0.004)} children [ Shape {{ appearance {self.use("Soil")} {{ }} '
                              "geometry Cylinder { height 0.01 radius 0.5 } } ] }")
        else:  # 가로수 보호틀: 칸 둘레의 낮은 연석 + 흙
            curb = self.shape_once("TREE_CURB", f'Shape {{ appearance {self.use("RoughConcrete")} {{ }} geometry Box {{ size 1 0.1 0.12 }} }}')
            soil = f'Shape {{ appearance {self.use("Soil")} {{ }} geometry Box {{ size 0.8 0.8 0.04 }} }}'
            parts = [f"Pose {{ translation 0 {_f(s * 0.45)} 0.06 children [ {curb} ] }}" for s in (-1, 1)]
            parts += [f"Pose {{ translation {_f(s * 0.45)} 0 0.06 rotation 0 0 1 {_f(math.pi / 2)} children [ USE TREE_CURB ] }}"
                      for s in (-1, 1)]
            parts.append(f"Pose {{ translation 0 0 0.02 children [ {soil} ] }}")
            self.lines.append(f"Pose {{ translation {_v(x, y, 0)} children [ {' '.join(parts)} ] }}")

    def pillar(self, x0: int, y0: int, x1: int, y1: int) -> None:
        cx, cy = self.wxy((x0 + x1) / 2, (y0 + y1) / 2)
        w, h = x1 - x0, y1 - y0
        if self.world.outdoor and w >= 2 and h >= 2:  # 광장 조형물: 대리석 받침 + 비튼 기둥
            marble = self.use("Marble")
            self.lines.append(
                f'Solid {{ translation {_v(cx, cy, 0)} children [ '
                f'Pose {{ translation 0 0 0.45 children [ Shape {{ appearance {marble} {{ }} geometry Box {{ size {_v(w, h, 0.9)} }} }} ] }} '
                f'Pose {{ translation 0 0 2.2 rotation 0 0 1 0.785 children [ Shape {{ appearance {marble} {{ }} '
                'geometry Box { size 0.7 0.7 2.6 } } ] } '
                f'Pose {{ translation 0 0 3.7 children [ Shape {{ appearance PBRAppearance {{ baseColor 0.72 0.55 0.2 '
                'roughness 0.3 metalness 1 } geometry Sphere { radius 0.45 subdivision 3 } } ] } ] '
                f'name "pillar_{x0}_{y0}" }}')
            return
        for cy_ in range(y0, y1):
            for cx_ in range(x0, x1):
                x, y = self.wxy(cx_ + 0.5, cy_ + 0.5)
                shape = self.shape_once("PILLAR_SHAPE", f'Shape {{ appearance {self.use("RoughConcrete")} {{ textureTransform '
                                        f"TextureTransform {{ scale 1 {_f(self.wall_h)} }} }} geometry DEF PILLAR_BOX Box {{ size 1 1 {_f(self.wall_h)} }} }}")
                self.lines.append(f'Solid {{ translation {_v(x, y, self.wall_h / 2)} children [ {shape} ] '
                                  f'name "pillar_{cx_}_{cy_}" boundingObject USE PILLAR_BOX }}')

    def walls(self) -> None:
        """벽('#'): 바닥에 닿은 칸만 그린다. 실내는 회벽 + 바닥 쪽 면마다 걸레받이·손잡이, 바깥은 벽돌 담."""
        world, outdoor = self.world, self.world.outdoor
        for cy, cx in np.argwhere(world.grid == WALL):
            cx, cy = int(cx), int(cy)
            sides = self.faces_floor(cx, cy)
            if not sides:
                continue
            x, y = self.wxy(cx + 0.5, cy + 0.5)
            if outdoor:
                # 벽돌 담: 무늬 없는 콘크리트 담은 다가가도 밝기 변화가 거의 없어 루밍으로 잡히지 않았다
                shape = self.shape_once("WALL_SHAPE", f'Shape {{ appearance {self.use("RedBricks")} {{ textureTransform '
                                        f"TextureTransform {{ scale 1 {_f(self.wall_h / 2)} }} }} geometry DEF WALL_BOX Box {{ size 1 1 {_f(self.wall_h)} }} }}")
            else:
                shape = self.shape_once("WALL_SHAPE", f'Shape {{ appearance {self.use("Roughcast")} {{ colorOverride 0.93 0.92 0.88 '
                                        f"textureTransform TextureTransform {{ scale 1 {_f(self.wall_h)} }} }} geometry DEF WALL_BOX "
                                        f"Box {{ size 1 1 {_f(self.wall_h)} }} }}")
            self.lines.append(f'Solid {{ translation {_v(x, y, self.wall_h / 2)} children [ {shape} ] name "wall_{cx}_{cy}" '
                              "boundingObject USE WALL_BOX }")
            if outdoor:
                continue
            for dx, dy in sides:  # 바닥 쪽 면: 걸레받이(어두운 띠)와 손잡이(나무 봉)
                angle = math.atan2(-dy, dx)  # 격자 → Webots (y 뒤집기)
                base = self.shape_once("BASEBOARD", "Shape { appearance PBRAppearance { baseColor 0.32 0.34 0.36 roughness 0.6 "
                                       "metalness 0 } geometry Box { size 0.02 1 0.12 } }")
                rail = self.shape_once("HANDRAIL", "Shape { appearance PBRAppearance { baseColor 0.62 0.45 0.28 roughness 0.5 "
                                       "metalness 0 } geometry Box { size 0.05 1 0.06 } }")
                px, py = x + math.cos(angle) * 0.51, y + math.sin(angle) * 0.51
                self.lines.append(f"Pose {{ translation {_v(px, py, 0)} rotation 0 0 1 {_f(angle)} children [ "
                                  f"Pose {{ translation 0 0 0.06 children [ {base} ] }} Pose {{ translation 0.015 0 0.9 "
                                  f"children [ {rail} ] }} ] }}")

    # --- 움직이는 장애물 ---
    def actor(self, spec: Any, kinds: dict[str, tuple[float, float]], ball_texture: str) -> None:
        radius, tall = kinds[spec.kind]
        x, y = self.wxy(*spec.path[0])
        head = f"ACTOR_{spec.name}"
        if spec.kind == "ball":  # 굴러가는 공: 안쪽 ROLL 노드를 컨트롤러가 굴린다
            self.lines.append(
                f"DEF {head} Pose {{ translation {_v(x, y, 0)} children [ DEF {head}_ROLL Pose {{ translation 0 0 {_f(radius)} "
                f'children [ Shape {{ appearance PBRAppearance {{ baseColorMap ImageTexture {{ url [ "textures/{ball_texture}.png" ] }} '
                f"roughness 0.5 metalness 0 }} geometry Sphere {{ radius {_f(radius)} subdivision 3 }} }} ] }} ] }}")
            return
        scale = tall / MODEL_HEIGHT
        shirt = tuple(c / 255 for c in spec.color)
        pants = (0.18, 0.2, 0.28) if spec.kind == "adult" else (0.35, 0.3, 0.45)
        colors = {"shirt": shirt, "pants": pants, "shoes": (0.12, 0.1, 0.09), "skin": (0.87, 0.71, 0.59)}
        looks: set[str] = set()

        def look(part: str) -> str:
            key = f"{head}_{part.upper()}"
            if key in looks:
                return f"USE {key}"
            looks.add(key)
            return f"DEF {key} PBRAppearance {{ baseColor {_v(*colors[part])} roughness 1 metalness 0 }}"

        def shape(part: str, color: str) -> str:
            return f"Shape {{ appearance {look(color)} geometry {self.use('Pedestrian' + part)} {{ }} }}"

        trunk = [shape("Torso", "shirt"), shape("Neck", "skin"), shape("Head", "skin")]  # 재질 DEF가 먼저 나오게 몸통부터
        children = {name: [] for name, _, _ in JOINTS}
        roots = []
        for name, parent, anchor in JOINTS:
            part, color = _JOINT_PARTS[name]
            ax, ay, az = anchor
            node = (f"Pose {{ translation {_v(ax, ay, az)} children [ DEF {head}_{name} Pose {{ children [ "
                    f"Pose {{ translation {_v(-ax, -ay, -az)} children [ {shape(part, color)} __{name}__ ] }} ] }} ] }}")
            (children[parent] if parent else roots).append((name, node))
        def expand(name: str, node: str) -> str:
            inner = " ".join(expand(child, text) for child, text in children[name])
            return node.replace(f"__{name}__", inner)
        body = " ".join(trunk + [expand(name, node) for name, node in roots])
        self.lines.append(f"DEF {head} Pose {{ translation {_v(x, y, 0)} children [ Transform {{ scale {_v(scale, scale, scale)} "
                          f"children [ Pose {{ translation 0 0 {_f(HIP_HEIGHT)} children [ {body} ] }} ] }} ] }}")


def rectangles(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """참인 칸들을 겹치지 않는 직사각형 (x0, y0, x1, y1)로 나눈다 (줄마다 이어진 칸을 묶고, 같은 가로 범위면 아래로 합친다)."""
    open_runs: dict[tuple[int, int], int] = {}
    result = []
    height, width = mask.shape
    for cy in range(height + 1):
        runs = set()
        if cy < height:
            cx = 0
            while cx < width:
                if mask[cy, cx]:
                    start = cx
                    while cx < width and mask[cy, cx]:
                        cx += 1
                    runs.add((start, cx))
                else:
                    cx += 1
        for run in list(open_runs):
            if run not in runs:
                result.append((run[0], open_runs.pop(run), run[1], cy))
        for run in runs:
            open_runs.setdefault(run, cy)
    return sorted(result, key=lambda r: (r[1], r[0]))


def blocks(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """이어진 칸 덩어리마다 둘러싼 직사각형 (차·소품 한 대 = 덩어리 하나)."""
    seen = np.zeros_like(mask, dtype=bool)
    height, width = mask.shape
    result = []
    for cy in range(height):
        for cx in range(width):
            if not mask[cy, cx] or seen[cy, cx]:
                continue
            stack, cells = [(cx, cy)], []
            seen[cy, cx] = True
            while stack:
                x, y = stack.pop()
                cells.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < width and 0 <= ny < height and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((nx, ny))
            xs, ys = [c[0] for c in cells], [c[1] for c in cells]
            result.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    return result


def ordered(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
