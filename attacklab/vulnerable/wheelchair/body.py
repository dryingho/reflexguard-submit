"""내 몸: 휠체어 탄 사람의 모양 (Webots 모형과 루밍 감지의 몸 마스크가 같은 정의를 쓴다).

로봇 좌표: +x 앞, +y 왼쪽, +z 위, 바닥 z = 0, 로봇 중심(충돌 원의 가운데)이 원점. 부딪힘 판정은 이 모양이 아니라
simulation/agent.py의 원(agent_radius)으로 한다 — 모양은 보이기만 한다.

카메라 (config.camera_height 높이, x = 0 — 앉은 사람의 눈)는 camera_pitch_deg만큼 아래를 본다. 무릎·허벅지 앞쪽과 좌우 팔걸이
끝이 영상 아래에 보여 운전하는 사람이 자기 폭을 가늠할 수 있다. 커넥톰도 같은 영상을 쓴다.
몸은 카메라에 붙어 같이 움직이므로 영상에서 늘 같은 자리에 있다 — 루밍 감지(wheelchair.looming.detector.LoomingDetector)는 그 화소(body_overlay의
mask)를 빼고 잰다. 빼지 않으면 돌 때 몸이 화면에서 밀린 것처럼 계산되어(회전 되돌리기) 다가오는 물체로 착각한다.

    parts = wheelchair_parts(cfg)            # 도형 목록 (Webots 월드 생성: simulation/build_webots_world.py)
    rgb, mask = body_overlay(cfg)            # 카메라에서 본 몸 그림과 그 화소 (몸은 카메라에 붙어 있어 한 번만 그린다)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from common.config import CONFIG, Config

# 재질 이름 → 색(0~1 RGB). Webots는 PBRAppearance baseColor로 쓴다
LOOKS: dict[str, tuple[float, float, float]] = {
    "frame": (0.55, 0.58, 0.62),   # 금속 틀
    "tire": (0.08, 0.08, 0.09),
    "seat": (0.12, 0.13, 0.16),    # 좌석·등받이·팔걸이 천
    "shirt": (0.16, 0.42, 0.86),
    "pants": (0.2, 0.22, 0.3),
    "shoe": (0.1, 0.08, 0.07),
    "skin": (222 / 255, 182 / 255, 150 / 255),
    "hair": (0.16, 0.11, 0.08),
}


@dataclass(frozen=True)
class Part:
    """도형 하나. kind: box(size = 가로·세로·높이) / capsule·cylinder(size = 길이, 반지름 — z축 방향) / sphere(size = 반지름)."""

    kind: str
    center: tuple[float, float, float]
    size: tuple[float, ...]
    look: str
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 0.0)  # 축 x·y·z, 각(라디안)


def _limb(start: tuple[float, float, float], end: tuple[float, float, float], radius: float, look: str) -> Part:
    """두 점을 잇는 캡슐 (팔·다리·관). 캡슐은 z축 방향이라 그 방향을 start → end로 돌린다."""
    d = np.subtract(end, start)
    length = float(np.linalg.norm(d))
    axis = np.cross((0.0, 0.0, 1.0), d / length)
    angle = math.acos(max(-1.0, min(1.0, d[2] / length)))
    norm = float(np.linalg.norm(axis))
    axis = axis / norm if norm > 1e-9 else np.array((1.0, 0.0, 0.0))
    middle = tuple(float(v) for v in (np.add(start, end) / 2.0))
    return Part("capsule", middle, (length, radius), look, (float(axis[0]), float(axis[1]), float(axis[2]), angle))


_SIDEWAYS = (1.0, 0.0, 0.0, math.pi / 2)  # 원기둥(바퀴)을 옆으로 눕힌다


def wheelchair_parts(cfg: Config = CONFIG) -> list[Part]:
    """휠체어(큰 바퀴 둘, 앞바퀴 둘, 좌석·등받이·팔걸이·발판·손잡이)와 앉은 사람(몸통·머리·팔·다리).

    큰 바퀴 반지름은 충돌 원 반지름과 같게 해서 크기를 맞춘다. 머리는 카메라 조금 뒤에 둔다(카메라가 머리 안에서 보지 않게).
    팔걸이는 앞 끝이 카메라에 보이도록 앞으로 길게 두고(긴 팔걸이 휠체어), 손은 팔걸이 가운데쯤에 얹는다.
    """
    wheel = cfg.agent_radius
    side = wheel * 0.97
    head_z = cfg.camera_height + 0.02
    parts: list[Part] = []
    for sign in (1.0, -1.0):
        y = side * sign
        parts += [
            Part("cylinder", (-0.08, y, wheel), (0.04, wheel), "tire", _SIDEWAYS),               # 큰 바퀴
            Part("cylinder", (-0.08, y * 1.08, wheel), (0.02, 0.08), "frame", _SIDEWAYS),          # 바퀴 축
            Part("cylinder", (0.3, y * 0.7, 0.07), (0.03, 0.07), "tire", _SIDEWAYS),               # 앞바퀴
            _limb((0.3, y * 0.7, 0.08), (0.28, y * 0.7, 0.46), 0.012, "frame"),                    # 앞바퀴 기둥
            _limb((-0.16, y * 0.72, 0.46), (-0.18, y * 0.72, 0.98), 0.014, "frame"),               # 등받이 기둥
            _limb((-0.18, y * 0.72, 0.98), (-0.3, y * 0.72, 0.98), 0.016, "seat"),                 # 미는 손잡이
            Part("box", (0.13, y * 0.8, 0.68), (0.46, 0.06, 0.04), "seat"),                        # 팔걸이
            _limb((0.34, y * 0.8, 0.66), (0.34, y * 0.8, 0.47), 0.014, "frame"),                   # 팔걸이 앞 기둥
            _limb((0.34, y * 0.8, 0.47), (0.24, y * 0.8, 0.47), 0.014, "frame"),                   # 팔걸이 아래 가로대
            _limb((0.28, y * 0.55, 0.46), (0.4, y * 0.55, 0.12), 0.012, "frame"),                  # 발판 기둥
        ]
    parts += [
        Part("box", (0.06, 0.0, 0.47), (0.44, 0.44, 0.06), "seat"),                                # 좌석
        Part("box", (-0.17, 0.0, 0.74), (0.04, 0.42, 0.44), "seat", (0.0, 1.0, 0.0, -0.08)),       # 등받이
        Part("box", (0.42, 0.0, 0.11), (0.14, 0.34, 0.02), "frame"),                               # 발판
    ]
    for y in (0.1, -0.1):  # 다리: 허벅지(좌석 위 앞으로), 정강이(무릎에서 발판으로), 신발
        parts += [
            _limb((-0.04, y, 0.56), (0.32, y, 0.56), 0.075, "pants"),
            _limb((0.34, y, 0.54), (0.38, y, 0.17), 0.06, "pants"),
            Part("box", (0.43, y, 0.15), (0.2, 0.09, 0.07), "shoe"),
        ]
    parts += [
        Part("capsule", (-0.07, 0.0, 0.8), (0.24, 0.15), "shirt"),                                 # 몸통
        _limb((-0.09, 0.0, 0.98), (-0.1, 0.0, head_z - 0.08), 0.045, "skin"),                      # 목
        Part("sphere", (-0.12, 0.0, head_z), (0.1,), "skin"),                                      # 머리 (카메라 뒤)
        Part("sphere", (-0.14, 0.0, head_z + 0.025), (0.098,), "hair"),                            # 머리카락
    ]
    for y in (0.19, -0.19):  # 팔: 어깨 → 팔꿈치 → 손 (팔걸이 앞쪽 위)
        parts += [
            _limb((-0.07, y, 0.97), (0.02, y * 1.2, 0.74), 0.045, "shirt"),
            _limb((0.02, y * 1.2, 0.74), (0.17, y * 1.2, 0.73), 0.04, "skin"),
        ]
    return parts


# ---------------------------------------------------------------------------
# 카메라에서 본 몸 그림과 마스크 (표면을 촘촘한 점으로 덮어 투영하고, 가까운 점이 이긴다)
# ---------------------------------------------------------------------------

def _rotation_matrix(axis_angle: tuple[float, float, float, float]) -> np.ndarray:
    x, y, z, angle = axis_angle
    k = np.array([x, y, z], dtype=np.float64)
    n = np.linalg.norm(k)
    if n < 1e-12 or abs(angle) < 1e-12:
        return np.eye(3)
    k /= n
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx @ kx


def _surface(part: Part, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """도형 겉면을 덮는 점들과 바깥 방향 법선 (도형 자신의 좌표, 가운데가 원점)."""
    def grid(n_u: int, n_v: int) -> tuple[np.ndarray, np.ndarray]:
        u, v = np.meshgrid((np.arange(n_u) + 0.5) / n_u, (np.arange(n_v) + 0.5) / n_v)
        return u.ravel(), v.ravel()

    def sphere_points(radius: float, z_min: float = -1.0, z_max: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        n = max(16, int(4 * math.pi * radius ** 2 * (z_max - z_min) / 2 / spacing ** 2))
        i = np.arange(n) + 0.5
        z = z_min + (z_max - z_min) * i / n               # 고르게 흩은 점 (해바라기 배치)
        phi = i * math.pi * (3 - math.sqrt(5))
        r = np.sqrt(np.maximum(0.0, 1 - z ** 2))
        normal = np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)
        return normal * radius, normal

    if part.kind == "sphere":
        return sphere_points(part.size[0])
    if part.kind in ("capsule", "cylinder"):
        length, radius = part.size
        u, v = grid(max(12, int(2 * math.pi * radius / spacing)), max(2, int(length / spacing)))
        theta = u * 2 * math.pi
        normal = np.stack([np.cos(theta), np.sin(theta), np.zeros_like(theta)], axis=1)
        points = normal * radius + np.stack([np.zeros_like(v), np.zeros_like(v), (v - 0.5) * length], axis=1)
        caps_p, caps_n = [], []
        for sign in (1.0, -1.0):
            if part.kind == "capsule":
                p, n = sphere_points(radius, 0.0, 1.0)
                p, n = p * [1, 1, sign], n * [1, 1, sign]
                p = p + [0, 0, sign * length / 2]
            else:
                cu, cv = grid(max(8, int(2 * radius / spacing)), max(8, int(2 * radius / spacing)))
                x, y = (cu - 0.5) * 2 * radius, (cv - 0.5) * 2 * radius
                keep = x ** 2 + y ** 2 <= radius ** 2
                p = np.stack([x[keep], y[keep], np.full(keep.sum(), sign * length / 2)], axis=1)
                n = np.tile([0.0, 0.0, sign], (len(p), 1))
            caps_p.append(p)
            caps_n.append(n)
        return np.concatenate([points] + caps_p), np.concatenate([normal] + caps_n)
    # box
    half = np.array(part.size) / 2.0
    points, normals = [], []
    for axis in range(3):
        a, b = [i for i in range(3) if i != axis]
        u, v = grid(max(2, int(part.size[a] / spacing)), max(2, int(part.size[b] / spacing)))
        for sign in (1.0, -1.0):
            p = np.zeros((len(u), 3))
            p[:, a], p[:, b], p[:, axis] = (u - 0.5) * part.size[a], (v - 0.5) * part.size[b], sign * half[axis]
            n = np.zeros_like(p)
            n[:, axis] = sign
            points.append(p)
            normals.append(n)
    return np.concatenate(points), np.concatenate(normals)


_OVERLAYS: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}


def body_overlay(cfg: Config = CONFIG) -> tuple[np.ndarray, np.ndarray]:
    """카메라(camera_pitch_deg만큼 숙임)에서 본 몸 그림: (RGB uint8 (H, W, 3), 몸이 있는 화소 bool (H, W)).

    몸은 카메라에 붙어 움직이므로 설정마다 한 번만 그린다. 빛은 위·앞에서 오는 한 방향 빛 + 바탕 밝기.
    """
    key = (cfg.render_width, cfg.render_height, cfg.fov_deg, cfg.camera_pitch_deg, cfg.camera_height, cfg.agent_radius)
    if key in _OVERLAYS:
        return _OVERLAYS[key]
    width, height = cfg.render_width, cfg.render_height
    focal = (width / 2.0) / math.tan(math.radians(cfg.fov_deg) / 2.0)
    pitch = math.radians(cfg.camera_pitch_deg)
    forward = np.array([math.cos(pitch), 0.0, -math.sin(pitch)])
    up = np.array([math.sin(pitch), 0.0, math.cos(pitch)])
    eye = np.array([0.0, 0.0, cfg.camera_height])
    light = np.array([0.35, 0.25, 1.0]) / np.linalg.norm([0.35, 0.25, 1.0])
    spacing = 0.6 * 0.3 / focal  # 0.3 m 앞에서 화소 0.6개 간격 — 가까운 무릎까지 빈틈 없이

    image = np.zeros((height, width, 3), dtype=np.uint8)
    mask = np.zeros((height, width), dtype=bool)
    depth_buffer = np.full((height, width), np.inf)
    for part in wheelchair_parts(cfg):
        local, normals = _surface(part, spacing)
        rot = _rotation_matrix(part.rotation)
        points = local @ rot.T + np.array(part.center)
        normals = normals @ rot.T
        rel = points - eye
        depth = rel @ forward
        facing = np.einsum("ij,ij->i", normals, -rel) > 0.0       # 카메라 쪽을 보는 면만
        keep = (depth > 0.05) & facing
        if not keep.any():
            continue
        rel, depth, normals = rel[keep], depth[keep], normals[keep]
        cols = width / 2.0 - focal * rel[:, 1] / depth
        rows = height / 2.0 - focal * (rel @ up) / depth
        shade = 0.38 + 0.62 * np.clip(normals @ light, 0.0, 1.0)
        color = np.clip(np.outer(shade, LOOKS[part.look]) * 255.0, 0, 255)
        for du in (0.0, 0.7):          # 점 하나를 2×2 화소로 찍어 틈을 메운다
            for dv in (0.0, 0.7):
                c = np.floor(cols + du).astype(np.int64)
                r = np.floor(rows + dv).astype(np.int64)
                inside = (c >= 0) & (c < width) & (r >= 0) & (r < height)
                c, r, d, col = c[inside], r[inside], depth[inside], color[inside]
                order = np.argsort(-d)            # 먼 것부터 써서 가까운 것이 남게
                c, r, d, col = c[order], r[order], d[order], col[order]
                nearer = d < depth_buffer[r, c]
                c, r, d, col = c[nearer], r[nearer], d[nearer], col[nearer]
                depth_buffer[r, c] = d
                image[r, c] = col.astype(np.uint8)
                mask[r, c] = True
    _OVERLAYS[key] = (image, mask)
    return image, mask
