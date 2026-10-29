"""simulation/scenes.py의 장면(격자 맵 + 움직이는 장애물)으로 Webots 월드 파일과 그림(텍스처)을 만든다.

맵(world.DEFAULT_MAP, simulation/scenes.py의 *_MAP·장면)이나 설정(common/config.py)을 바꾼 뒤에는 이 스크립트를 다시 실행해야 Webots 쪽에 반영된다.

실행:  python simulation/build_webots_world.py
결과:  simulation/webots/worlds/reflexguard.wbt (기본 장면 hospital)과 장면마다 <이름>.wbt, simulation/webots/worlds/textures/*.png,
       simulation/webots/worlds/.reflexguard.wbproj · .<이름>.wbproj (없을 때만)
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 프로젝트 폴더의 패키지(common, simulation, …)를 불러오기 위해

import numpy as np

from simulation.agent import Agent  # noqa: E402
from wheelchair.body import LOOKS, wheelchair_parts  # noqa: E402
from simulation.world_assets import Builder  # noqa: E402
from common.config import CONFIG, Config  # noqa: E402
from simulation.webots_bridge import webots_pose  # noqa: E402
from simulation.actors import KINDS  # noqa: E402
from simulation.scenes import DEFAULT_SCENE, SCENES, Scene  # noqa: E402
from simulation.textures import ball_texture, goal_image  # noqa: E402
from simulation.world import World  # noqa: E402

WORLDS_DIR = ROOT / "simulation" / "webots" / "worlds"
WORLD_FILE = WORLDS_DIR / "reflexguard.wbt"
LAYOUT_FILE = WORLDS_DIR / ".reflexguard.wbproj"
TEXTURE_DIR = WORLDS_DIR / "textures"


def world_file(scene: Scene) -> Path:
    """장면의 월드 파일: 기본 장면은 reflexguard.wbt, 나머지는 <이름>.wbt (scenes.Scene.world_file)."""
    return WORLD_FILE if scene.name == DEFAULT_SCENE else WORLDS_DIR / scene.world_file


def layout_text(scene: Scene) -> str:
    """처음 만드는 화면 배치 파일 내용. 웹 조작 화면을 브라우저에 자동으로 여는 줄(robotWindow)은 기본 맵에만 둔다 —
    웹 화면에서 맵을 고르면 컨트롤러가 다른 월드를 불러오는데, 그때마다 새 브라우저 탭이 열리지 않게(열려 있던 화면이
    다시 연결된다)."""
    if scene.name == DEFAULT_SCENE:
        return LAYOUT_TEXT
    return "".join(line for line in LAYOUT_TEXT.splitlines(keepends=True) if not line.startswith("robotWindow:"))


def layout_file(scene: Scene) -> Path:
    return LAYOUT_FILE if scene.name == DEFAULT_SCENE else WORLDS_DIR / f".{scene.name}.wbproj"
ROBOT_NAME = "reflexguard"  # 로봇·컨트롤러·웹 조작 화면(로봇 창)이 모두 이 이름을 쓴다

# Webots 화면 배치 파일의 내용: 월드를 열면 웹 조작 화면이 브라우저에 자동으로 뜨고,
# 3D 화면 위에 겹쳐 그리는 카메라 미리보기는 끈다 (값 순서: 표시 여부;크기;가로 위치;세로 위치).
# 3D 화면 그리기도 꺼 둔다(renderingDisabled): 조작은 웹 화면의 카메라 영상으로 하고, 내장 그래픽(WSL)에서는
# 3D 화면을 그리는 데 한 스텝(32ms)보다 오래 걸려 시뮬레이션이 느려진다. Webots에서 Ctrl+4로 켜고 끌 수 있다.
LAYOUT_TEXT = f"""Webots Project File version R2025a
userInteractions: renderingDisabled
robotWindow: {ROBOT_NAME}
renderingDevicePerspectives: {ROBOT_NAME}:camera;0;0.5;0;0
"""


# ---------------------------------------------------------------------------
# 텍스처 PNG 저장 (그림 자체는 simulation/textures.py)
# ---------------------------------------------------------------------------

def write_png(path: Path, rgb: np.ndarray) -> None:
    """(H, W, 3) uint8 배열을 PNG 파일로 저장한다 (표준 라이브러리만 사용)."""
    height, width, _ = rgb.shape
    # PNG는 각 줄 맨 앞에 '필터 종류' 1바이트가 붙는다 (0 = 필터 없음)
    rows = np.hstack([np.zeros((height, 1), dtype=np.uint8), rgb.reshape(height, width * 3)])

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8비트 RGB
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 9))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# 월드 파일
# ---------------------------------------------------------------------------

def _fmt(value: float) -> str:
    """숫자를 월드 파일에 쓸 짧은 문자열로 바꾼다 (불필요한 0 제거)."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def actor_texture_name(scene: Scene, spec: Any) -> str:
    """장애물 몸통 그림 이름: 맵마다 같은 이름의 장애물이 있을 수 있어 맵 이름을 붙인다."""
    return f"actor_{scene.name}_{spec.name}"


# ---------------------------------------------------------------------------
# 로봇 모습: 휠체어 탄 사람 (wheelchair/body.py — 보이기만 하는 모형, 부딪힘은 simulation/agent.py의 원(agent_radius)으로 판정한다)
# ---------------------------------------------------------------------------

def _wheelchair_nodes(cfg: Config = CONFIG) -> list[str]:
    """휠체어 탄 사람 모습 (body.wheelchair_parts — 루밍 감지의 몸 마스크와 같은 정의). 재질은 처음 쓸 때 DEF, 그다음은 USE."""
    used: set[str] = set()

    def look(name: str) -> str:
        key = f"WC_{name.upper()}"
        if key in used:
            return f"USE {key}"
        used.add(key)
        color = " ".join(_fmt(c) for c in LOOKS[name])
        rough = "0.35 metalness 0.8" if name == "frame" else "0.85 metalness 0"
        return f"DEF {key} PBRAppearance {{ baseColor {color} roughness {rough} }}"

    lines = []
    for part in wheelchair_parts(cfg):
        if part.kind == "box":
            geometry = f"Box {{ size {' '.join(_fmt(v) for v in part.size)} }}"
        elif part.kind == "sphere":
            geometry = f"Sphere {{ radius {_fmt(part.size[0])} subdivision 3 }}"
        else:
            name = "Capsule" if part.kind == "capsule" else "Cylinder"
            geometry = f"{name} {{ height {_fmt(part.size[0])} radius {_fmt(part.size[1])} subdivision 16 }}"
        turn = "" if part.rotation[3] == 0.0 else f" rotation {' '.join(_fmt(v) for v in part.rotation)}"
        lines.append(f"        Pose {{ translation {' '.join(_fmt(v) for v in part.center)}{turn} children [ Shape {{"
                     f" appearance {look(part.look)} geometry {geometry} }} ] }}")
    return ["    DEF WHEELCHAIR Pose {", "      children ["] + lines + ["      ]", "    }"]


def build_world_text(world: World, cfg: Config = CONFIG, scene: Scene | None = None) -> str:
    """월드 파일(.wbt)의 내용을 문자열로 만든다. scene을 주면 그 장면의 바닥·하늘·장애물·controllerArgs를 넣는다."""
    scene = scene or SCENES[DEFAULT_SCENE]
    width, height, wall_h = world.width, world.height, cfg.wall_height
    center = f"{_fmt(width / 2)} {_fmt(height / 2)}"
    start = Agent(*world.start, angle_deg=cfg.start_angle_deg)
    translation, rotation = webots_pose(start, world)

    assets = Builder(world, scene.name, wall_h)
    assets.background()
    assets.floors()
    # 도착지('G' 칸): 바닥 위에 초록 바둑판 판을 깐다 (textures.goal_image). 세워 둔 표지판은
    # 다가갈수록 화면에서 커져 커넥톰이 장애물로 보므로 두지 않는다. 부딪힘 판정이 없는 그림이다.
    for index, (cy, cx) in enumerate(np.argwhere(world.goal_mask)):
        shape = ("DEF GOAL_SHAPE Shape { appearance PBRAppearance { baseColorMap ImageTexture"
                 ' { url [ "textures/goal.png" ] } roughness 1 metalness 0 } geometry Plane { size 1 1 } }'
                 if index == 0 else "USE GOAL_SHAPE")
        assets.lines.append(f"Pose {{ translation {_fmt(cx + 0.5)} {_fmt(height - cy - 0.5)} 0.004 children [ {shape} ] }}")
    assets.blocked()
    for spec in scene.actors:
        assets.actor(spec, KINDS, actor_texture_name(scene, spec))

    lines: list[str] = [
        "#VRML_SIM R2025a utf8",
        "# Generated by simulation/build_webots_world.py from the grid map in simulation/world.py - do not edit by hand.",
        "# 3D assets: Cyberbotics Webots assets (github.com/cyberbotics/webots, R2025a), licensed for use only with Webots.",
        *assets.externprotos(),
        "",
        "WorldInfo {",
        '  info [ "ReflexGuard stage 1. Generated from the grid map in simulation/world.py (1 cell = 1 m)." ]',
        '  title "ReflexGuard"' if scene.name == DEFAULT_SCENE else f'  title "ReflexGuard - {scene.name}"',
        f"  basicTimeStep {cfg.webots_time_step_ms}",
        "}",
        "Viewpoint {",  # 맵 전체를 위에서 내려다보는 시점 (화면의 위쪽 = 맵의 위쪽)
        "  orientation -0.5774 0.5774 0.5774 2.0944",
        f"  position {center} {_fmt(max(width, height) * 1.45)}",
        "}",
        *assets.lines,
    ]
    lines += [
        "Robot {",  # 휠체어 탄 사람: 컨트롤러가 위치·방향을 직접 정한다(물리 엔진을 쓰지 않음)
        f"  translation {' '.join(_fmt(v) for v in translation)}",
        f"  rotation {' '.join(_fmt(v) for v in rotation)}",
        "  children [",
        *_wheelchair_nodes(cfg),
        "    Camera {",  # 1인칭 카메라: 앉은 사람의 눈에서 앞(+x)을 camera_pitch_deg만큼 숙여 본다 — 무릎·팔걸이 끝이 보인다
        f"      translation 0 0 {_fmt(cfg.camera_height)}",
        f"      rotation 0 1 0 {_fmt(math.radians(cfg.camera_pitch_deg))}",
        '      name "camera"',
        f"      fieldOfView {_fmt(math.radians(cfg.fov_deg))}",
        f"      width {cfg.render_width}",
        f"      height {cfg.render_height}",
        "      near 0.05",
        "      far 30",
        "    }",
        "  ]",
        f'  name "{ROBOT_NAME}"',
        f'  controller "{ROBOT_NAME}"',
        *([f'  controllerArgs [ "{scene.name}" ]'] if scene.name != DEFAULT_SCENE else []),
        "  supervisor TRUE",
        f'  window "{ROBOT_NAME}"',
        "}",
        "",
    ]
    return "\n".join(lines)


def power_of_two(image: np.ndarray) -> np.ndarray:
    """가로·세로를 2의 거듭제곱으로 늘린다(가까운 화소 반복). Webots가 그렇지 않은 그림을 경고와 함께 늘리기 때문."""
    height, width = image.shape[:2]
    new_h, new_w = 1 << (height - 1).bit_length(), 1 << (width - 1).bit_length()
    rows = np.arange(new_h) * height // new_h
    cols = np.arange(new_w) * width // new_w
    return image[rows][:, cols]


def scene_textures(scene: Scene) -> dict[str, np.ndarray]:
    """장면에만 쓰는 그림: 공의 줄무늬 그림 (2의 거듭제곱 크기). 사람·건물·바닥은 Webots 에셋을 쓴다."""
    return {actor_texture_name(scene, spec): power_of_two(ball_texture(spec.color))
            for spec in scene.actors if spec.kind == "ball"}


def main() -> None:
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    write_png(TEXTURE_DIR / "goal.png", goal_image())
    written = {"goal.png"}
    for scene in SCENES.values():
        for name, image in scene_textures(scene).items():
            write_png(TEXTURE_DIR / f"{name}.png", image)
            written.add(f"{name}.png")
        path = world_file(scene)
        path.write_text(build_world_text(scene.world(), scene=scene), encoding="utf-8", newline="\n")
        layout = layout_file(scene)
        if not layout.exists():  # Webots가 사용자의 창 배치를 이 파일에 저장하므로, 이미 있으면 건드리지 않는다
            layout.write_text(layout_text(scene), encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)}")
    # 이번에 만들지 않은 그림은 지운다 (장애물 이름을 바꾸거나, 예전에 쓰던 칸·바닥 그림 — 지금은 Webots 에셋을 쓴다)
    for stale in sorted(set(path.name for path in TEXTURE_DIR.glob("*.png")) - written):
        (TEXTURE_DIR / stale).unlink()
        print(f"removed {TEXTURE_DIR.relative_to(ROOT)}/{stale} (not used any more)")
    print(f"wrote {TEXTURE_DIR.relative_to(ROOT)}/*.png")


if __name__ == "__main__":
    main()
