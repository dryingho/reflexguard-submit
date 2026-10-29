"""Webots 연결용 순수 로직.

Webots 컨트롤러(simulation/webots/controllers/reflexguard/reflexguard.py)가 쓰는 계산 부분만 모았다.
Webots API를 직접 부르지 않으므로 Webots가 없어도 불러와서 테스트할 수 있다.

좌표 변환
    격자 좌표(simulation/world.py)   x 오른쪽 +, y 아래쪽 +, 각도는 위에서 볼 때 시계 방향 +
    Webots 좌표           X 동쪽 +,   Y 북쪽 +,   Z 위쪽 +, 회전은 Z축 기준 반시계 방향 +
    → X = x,  Y = (맵의 세로 칸 수) - y,  회전각 = -각도

웹 조작 화면과 주고받는 메시지 (모두 ASCII 글자만 쓴다)
    화면 → 컨트롤러    "keys:<화면 이름>:WD"   그 화면에서 지금 눌린 키들(없으면 "keys:<화면 이름>:").
                                             누르는 동안 0.2초마다, 아니면 1초마다 다시 보낸다.
                                             화면 이름은 화면(탭)마다 다른 임의의 영문·숫자다.
                       "keys:<화면 이름>:W;0.50,-0.25"
                                             조이스틱을 잡고 있으면 ';' 뒤에 '전진,회전' 값(각 -1~1)을 붙인다.
                                             키와 같은 메시지라 신호가 끊기면 조이스틱 입력도 같이 풀린다.
    화면 → 컨트롤러    "connectome:on" / "connectome:off"
                                             커넥톰 연결을 켜고 끈다. 끄면 커넥톰 판단이 아예 없는 상태(brain=None)가 되어
                                             공유 제어가 안전 정지(fail-safe)한다 — 전진·후진·회전 모두 막는다.
                       "safety:on" / "safety:off"
                                             안전 스위치. 켜면 커넥톰 판단이 없을 때 막지 않고 수동 주행을 허용한다
                                             (ReflexModule.manual_without_brain).
                                             스위치는 누를 때 한 번만 보낸다. 키와 달리 신호가 끊겨도 그대로 유지하고,
                                             컨트롤러를 다시 켜면 커넥톰은 켜진 상태, 안전 스위치는 꺼진 상태로 시작한다.
                       "sensor:camera" / "sensor:range"
                                             커넥톰 입력 센서(위험 인식): 카메라 루밍 / 거리 센서 (wheelchair/range_sensor.py).
                                             판단·개입은 둘 다 같은 커넥톰과 공유 제어. 컨트롤러를 다시 켜면(맵을 바꿔도)
                                             시작 값(config.reflex_sensor — 실행 스크립트의 --sensor)으로 돌아간다.
                       "map:<맵 이름>"         그 맵의 월드로 바꾼다 (scenes.SCENES의 이름만 받는다). 컨트롤러가 Webots에
                                             월드를 불러오게 하고 끝나며, 화면은 다시 연결된다.
    컨트롤러 → 화면    "frame:<base64>"        카메라 영상 한 장(JPEG를 base64로 바꾼 것)
    컨트롤러 → 화면    "cells:<JSON>"          영상마다 함께: 칸별 커넥톰 입력·재는 칸·거른 칸 (위험도 겹쳐 보기, cells_message)
    컨트롤러 → 화면    "ready"                 컨트롤러가 돌고 있다는 신호. 0.5초마다 보낸다.
    컨트롤러 → 화면    "state:<JSON>"          루밍 → 커넥톰 → 공유 제어 흐름 한 스텝 (reflex_log.flow_snapshot).
                                             지금 맵("map")과 고를 수 있는 맵 목록("maps": [[이름, 제목], …])도 싣는다.
                                             화면이 열려 있을 때만, 초당 web_state_hz번까지.
    컨트롤러 → 화면    "reflex:<JSON>"         회피 반사 작동 사건(start/stage/end, reflex_log.ReflexMonitor).
                                             화면이 열려 있을 때만, 생길 때마다 바로 보낸다.
    컨트롤러 → 화면    "collision:<JSON>"      벽·장애물 충돌(type "collision", 부딪힌 것은 "with"), 처음부터 다시 시작(type "restart", 도착 뒤면 "after":
                                             "arrival"), 도착지 도착(type "arrival" — 걸린 시간·커넥톰 개입 수, simulation/goal.py).
                                             화면이 열려 있을 때만, 생길 때마다 바로 보낸다.

화면은 컨트롤러의 신호("ready"나 "frame:")를 받은 뒤에만 키를 보낸다.
Webots R2025a는 월드를 다 열기 전에 로봇 창 메시지를 받으면 비정상 종료하기 때문이다
(조작 화면을 열어 둔 채 Webots를 다시 켜는 경우). 컨트롤러가 돌고 있다면 월드는 이미 열려 있다.
"""

from __future__ import annotations

import json
import math
import zlib
from typing import Any

import numpy as np

from common.control import ControlCommand
from simulation.agent import Agent
from simulation.world import World

KEYS_PREFIX = "keys:"
MAP_PREFIX = "map:"
SENSOR_PREFIX = "sensor:"
SENSORS = ("camera", "range")  # 커넥톰 입력 센서 (common.config.SENSORS와 같다)
SWITCHES = ("connectome", "safety")  # 화면이 켜고 끌 수 있는 스위치: "<이름>:on" / "<이름>:off"
STICK_SEPARATOR = ";"
FRAME_PREFIX = "frame:"
CELLS_PREFIX = "cells:"  # 영상과 함께: 칸별 커넥톰 입력(위험도 겹쳐 보기) — cells_message()
STATE_PREFIX = "state:"
COLLISION_PREFIX = "collision:"
REFLEX_PREFIX = "reflex:"
READY_MESSAGE = "ready"


def _clamp_unit(value: float) -> float:
    """값을 -1 ~ +1 범위로 자른다."""
    return max(-1.0, min(1.0, value))


def parse_stick(text: str) -> tuple[float, float]:
    """조이스틱 값 "<전진>,<회전>"(각 -1~1)을 (forward, turn)으로 읽는다.

    네트워크에서 온 값이므로 믿지 않는다: 비었거나 형식이 틀리거나 무한대·NaN이면 (0, 0), 범위 밖이면 자른다.
    """
    forward_text, separator, turn_text = text.partition(",")
    if not separator:
        return 0.0, 0.0
    try:
        forward, turn = float(forward_text), float(turn_text)
    except ValueError:
        return 0.0, 0.0
    if not (math.isfinite(forward) and math.isfinite(turn)):
        return 0.0, 0.0
    return _clamp_unit(forward), _clamp_unit(turn)


def command_from_key_letters(letters: str) -> ControlCommand:
    """눌린 키 글자들(예: "WD")을 제어 명령으로 바꾼다 (W/S 전진·후진, A/D 회전, Q/E 옆으로, 반대 키는 상쇄)."""
    held = set(letters.upper())
    return ControlCommand(
        forward=float("W" in held) - float("S" in held),
        turn=float("D" in held) - float("A" in held),
        strafe=float("E" in held) - float("Q" in held),
    )


def webots_pose(agent: Agent, world: World) -> tuple[list[float], list[float]]:
    """에이전트의 격자 좌표를 Webots 로봇의 (translation, rotation) 필드 값으로 바꾼다."""
    translation = [agent.x, world.height - agent.y, 0.0]
    rotation = [0.0, 0.0, 1.0, -math.radians(agent.angle_deg)]  # (회전축 x, y, z, 각도)
    return translation, rotation


class NewFrames:
    """카메라가 새 영상을 찍었는지 알아본다 (영상 내용이 바뀌었으면 새 영상).

    카메라 간격이 스텝보다 길면 Webots는 '카메라를 켠 시각 + 간격의 배수'에 찍고, 그 사이 스텝에는 직전 영상을 그대로 준다.
    찍는 스텝을 셈으로 짐작해 읽으면 한 스텝 묵은 영상을 읽을 수 있고, 그러면 그 사이 돈 각도를 잘못 되돌려 돌 때마다 가짜
    루밍이 생긴다(64 ms에서 실제로 났다). 그래서 매 스텝 영상을 받아 내용이 바뀌었을 때만 새 영상으로 본다. 아무것도
    움직이지 않아 같은 영상이 찍혔다면 새로 잴 것도 없다. 카메라 간격이 스텝과 같으면 매 스텝 새 영상이다.
    """

    def __init__(self, every_step: bool) -> None:
        self.every_step = every_step
        self._last: int | None = None

    def is_new(self, image: bytes | None) -> bool:
        if not image:
            return False
        signature = zlib.crc32(image)
        new = self.every_step or signature != self._last
        self._last = signature
        return new


def frame_from_bgra(data: bytes, width: int, height: int) -> np.ndarray:
    """Webots Camera.getImage()의 바이트열(BGRA)을 루밍 감지기가 받는 RGB 배열로 바꾼다.

    반환: shape (height, width, 3), dtype uint8, 색 순서 RGB — 루밍 감지기(LoomingDetector.update)가 받는 형식.
    """
    bgra = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)
    return np.ascontiguousarray(bgra[:, :, 2::-1])  # B, G, R, A → R, G, B


class WebInput:
    """웹 조작 화면에서 온 메시지를 모아 '지금 눌린 키'와 '화면이 열려 있는지'를 알려 준다.

    화면은 자기가 누르고 있는 키를 주기적으로 다시 보낸다(생존 신호: 누르는 동안 0.2초마다, 아니면 1초마다).
    어떤 화면의 신호가 끊기고
    - key_timeout이 지나면: 그 화면의 키를 뗀 것으로 본다 → 브라우저가 닫히거나 연결이 끊겨도 계속 달리지 않는다.
    - viewer_timeout이 지나면: 그 화면이 닫힌 것으로 본다 → 열린 화면이 하나도 없으면 영상 전송을 멈춘다.

    화면이 여러 개 열려 있으면(다른 PC·휴대전화 등) 각 화면이 누르고 있는 키를 모두 합쳐서 적용한다.
    그래서 보기만 하는 화면이 있어도 운전하는 화면의 조작이 끊기지 않는다.
    조이스틱 값도 화면별로 더한 뒤 -1~1로 자른다 (키 신호와 같은 시간 제한을 받는다).
    """

    def __init__(self, key_timeout: float, viewer_timeout: float) -> None:
        self.key_timeout = key_timeout
        self.viewer_timeout = viewer_timeout
        # 화면 이름 → (눌린 키 글자들, 조이스틱 (전진, 회전), 마지막으로 신호가 온 시각)
        self._screens: dict[str, tuple[str, tuple[float, float], float]] = {}
        self._switch_requests: dict[str, bool] = {}  # 화면이 요청한 스위치 상태 (아직 반영 전)
        self._map_request: str | None = None           # 화면이 요청한 맵 이름 (아직 반영 전)
        self._sensor_request: str | None = None        # 화면이 요청한 커넥톰 입력 센서 (아직 반영 전)

    def handle_message(self, message: str, now: float) -> None:
        """화면에서 온 메시지 하나를 반영한다. now: 현재 시각(초)."""
        name, _, value = message.partition(":")
        if name in SWITCHES:
            if value in ("on", "off"):  # 그 밖의 값은 무시한다
                self._switch_requests[name] = value == "on"
            return
        if message.startswith(SENSOR_PREFIX):
            if value in SENSORS:  # 그 밖의 값은 무시한다
                self._sensor_request = value
            return
        if message.startswith(MAP_PREFIX):
            # 네트워크에서 온 값이므로 영문 소문자·숫자·밑줄의 짧은 이름만 받는다 (파일 경로를 만들 수 없게)
            if 0 < len(value) <= 32 and all(c.isascii() and (c.islower() or c.isdigit() or c == "_") for c in value):
                self._map_request = value
            return
        if not message.startswith(KEYS_PREFIX):
            return
        screen, _, payload = message[len(KEYS_PREFIX):].partition(":")
        letters, _, stick = payload.partition(STICK_SEPARATOR)
        self._screens[screen] = (letters, parse_stick(stick), now)
        # 닫힌 지 오래된 화면은 잊는다 (새로고침할 때마다 화면 이름이 바뀌므로 그냥 두면 계속 쌓인다)
        self._screens = {
            name: state for name, state in self._screens.items() if now - state[2] <= self.viewer_timeout
        }

    def held_letters(self, now: float) -> str:
        """지금 눌려 있다고 볼 키 글자들(알파벳순). 신호가 key_timeout 넘게 끊긴 화면의 키는 뺀다."""
        held: set[str] = set()
        for letters, _, last_seen in self._screens.values():
            if now - last_seen <= self.key_timeout:
                held.update(letters)
        return "".join(sorted(held))

    def stick(self, now: float) -> tuple[float, float]:
        """지금 적용할 조이스틱 (전진, 회전). 신호가 key_timeout 넘게 끊긴 화면의 조이스틱은 뺀다."""
        forward = turn = 0.0
        for _, (stick_forward, stick_turn), last_seen in self._screens.values():
            if now - last_seen <= self.key_timeout:
                forward += stick_forward
                turn += stick_turn
        return _clamp_unit(forward), _clamp_unit(turn)

    def command(self, now: float) -> ControlCommand:
        """키와 조이스틱을 합친 사용자 명령. 같은 방향으로 겹쳐도 -1~1을 넘지 않는다."""
        keys = command_from_key_letters(self.held_letters(now))
        forward, turn = self.stick(now)
        return ControlCommand(
            forward=_clamp_unit(keys.forward + forward),
            turn=_clamp_unit(keys.turn + turn),
            strafe=keys.strafe,
        )

    def take_map_request(self) -> str | None:
        """화면이 요청한 맵 이름을 꺼내 준다. 요청이 없었으면 None. 한 번 꺼내면 비운다 (맵이 있는지는 컨트롤러가 본다)."""
        request, self._map_request = self._map_request, None
        return request

    def take_sensor_request(self) -> str | None:
        """화면이 요청한 커넥톰 입력 센서(camera / range)를 꺼내 준다. 요청이 없었으면 None. 한 번 꺼내면 비운다."""
        request, self._sensor_request = self._sensor_request, None
        return request

    def take_switch_request(self, name: str) -> bool | None:
        """화면이 요청한 스위치 상태(True=켜기, False=끄기)를 꺼내 준다. 요청이 없었으면 None. 한 번 꺼내면 비운다."""
        return self._switch_requests.pop(name, None)

    def viewer_connected(self, now: float) -> bool:
        """조작 화면이 하나라도 열려 있는지(= 최근 viewer_timeout 안에 신호가 왔는지)."""
        return any(now - last_seen <= self.viewer_timeout for *_, last_seen in self._screens.values())


def cells_message(inputs: Any, measured: Any, filtered: Any = None) -> str:
    """웹 조작 화면의 '위험도 겹쳐 보기'용: 칸별 LPLC2 입력(0~1, 행마다 왼쪽→오른쪽, 위→아래), 재는 칸 표시(1/0),
    통로 밖이라 거른 칸 표시(1/0 — 커지고는 있지만 휠체어가 지나갈 통로 밖이라 커넥톰에 넘기지 않은 칸).
    칸은 영상 전체를 looming_grid_rows × looming_grid_cols로 고르게 나눈 것이라, 화면도 같은 격자로 나눠 그리면 겹친다.
    inputs가 없으면(첫 영상 전) 빈 메시지."""
    if inputs is None:
        return CELLS_PREFIX + "{}"
    rows, cols = inputs.shape
    ones = [1] * (rows * cols)

    def flags(values: Any, default: list[int]) -> list[int]:
        return default if values is None else [int(v > 0) for v in np.asarray(values).ravel()]

    return CELLS_PREFIX + json.dumps({"rows": rows, "cols": cols, "input": [round(float(v), 2) for v in inputs.ravel()],
                                      "measured": flags(measured, ones), "filtered": flags(filtered, [0] * len(ones))},
                                     separators=(",", ":"))
