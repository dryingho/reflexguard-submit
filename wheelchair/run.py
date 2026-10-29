"""휠체어 PC 단독 실행: Webots 없이 영상 파일·웹캠·녹화 파일을 넣어 ① 루밍 → ② 커넥톰 → ③ 공유 제어를 돌린다.

    python -m wheelchair.run --video 주행.mp4 --out 결과.mp4                      # 영상 파일 (속도는 카메라로 짐작 — 바닥이 퍼지는 빠르기)
    python -m wheelchair.run --video 주행.mp4 --speed 1.2 --out 결과.mp4          # 속도를 정해 준다 (1.2 m/s로 갔다고 보고)
    python -m wheelchair.run --camera /dev/video0 --speed 1.0 --out 결과.mp4       # 웹캠 (리눅스 v4l2), Ctrl+C로 끝
    python -m wheelchair.run --recording recordings/road_actors.npz --out 결과.mp4 # 녹화 파일 (tools/looming_record.py 형식)

결과:
- `--out`: 위험도를 겹친 영상. 칸마다 커넥톰 입력(빨강 진하기), 커지지만 휠체어 통로 밖이라 거른 칸(점선), 위쪽 띠에
  커넥톰 판단(빨강 정지 · 파랑 회피 · 노랑 감속 · 회색 안전)과 위험도(흰 막대).
- `--log`: 영상마다 한 줄 JSON (시각, 위험도, 신호, 공유 제어 단계, 좌우 회피 세기, 가장 센 칸 입력).
- 끝나면 요약(정지 신호가 처음 나온 시각, 신호별 영상 수)을 찍는다.

영상·웹캠 읽기와 결과 영상 쓰기는 ffmpeg를 부른다 (sudo apt install ffmpeg — 파이썬 패키지는 numpy만).

바퀴의 움직임(엔코더·IMU)이 없는 영상은 앞으로 가는 속도를 카메라로 짐작한다(--speed auto, 기본 — 바닥이 퍼지는 빠르기와
카메라 높이로. 바닥에 무늬가 없어 잴 수 없으면 1 m/s). `--speed 1.2`처럼 정해 줄 수도 있다. 회전은 0으로 둔다. 영상 속에서 카메라가 돌면
회전을 되돌리지 못해 다가오는 것으로 잘못 볼 수 있다 — 실제 휠체어에서는 IMU 값을 넣는다(WheelchairPipeline.moved).
녹화 파일은 녹화 때의 회전·속도를 그대로 쓴다. 영상은 휠체어 명령을 따라 움직이지 않으므로(열린 고리), 공유 제어 단계는
참고용이다 — 정지한 뒤에는 사용자가 전진을 놓아야 풀리는데 영상 속 사용자는 계속 전진한다.

카메라 값(화각·숙인 각·높이)은 `--fov`·`--pitch`·`--camera-height`로 준다 (기본은 common/config.py — 시뮬레이션 카메라).
영상에 휠체어 몸(무릎·팔걸이)이 보이면 `--body-mask 마스크.npy`(H×W, 참 = 몸)를 준다. 없으면 몸을 빼지 않는다.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # python wheelchair/run.py로 바로 실행해도 프로젝트 패키지를 찾게

from common.config import CONFIG, Config, handheld_profile  # noqa: E402
from common.control import ControlCommand  # noqa: E402
from wheelchair.pipeline import WheelchairPipeline  # noqa: E402

# 위쪽 띠 색: 커넥톰 신호 (웹 조작 화면과 같은 뜻)
SIGNAL_COLORS = {"STOP": (229, 72, 60), "LEFT": (70, 140, 230), "RIGHT": (70, 140, 230), "NONE": (110, 116, 126)}
SLOW_COLOR = (225, 175, 50)
HEAT = np.array([255, 90, 60], dtype=np.float32)
BAR = 14  # 위쪽 띠 높이(px)


# ---------------------------------------------------------------------------
# 영상 읽기 (ffmpeg) — 영상 파일 · 웹캠 · 녹화 파일
# ---------------------------------------------------------------------------

def _probe(path: str) -> tuple[int, int, float]:
    """영상의 (너비, 높이, 초당 영상 수) — 보이는 방향 그대로. 휴대폰 세로 영상은 1440×1080으로 저장하고 '90도 돌려
    보라'는 회전 정보(태그 rotate 또는 side data의 rotation)를 붙인다. ffmpeg는 풀 때 그 회전을 적용하므로 너비·높이도
    바꿔 줘야 한다 (그러지 않으면 세로 영상을 가로 크기로 눌러 위아래가 찌그러진다)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    stream = json.loads(out)["streams"][0]
    num, _, den = stream["r_frame_rate"].partition("/")
    width, height = int(stream["width"]), int(stream["height"])
    if _rotation(stream) % 180 == 90:
        width, height = height, width
    return width, height, float(num) / float(den or 1)


def _rotation(stream: dict) -> int:
    """ffprobe 스트림 정보의 화면 회전(도, 0·90·180·270)."""
    angles = [stream.get("tags", {}).get("rotate")]
    angles += [side.get("rotation") for side in stream.get("side_data_list", []) if isinstance(side, dict)]
    for angle in angles:
        try:
            return int(round(float(angle))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def ffmpeg_frames(input_args: list[str], width: int, height: int, every: int = 1) -> Iterator[np.ndarray]:
    """ffmpeg가 해독한 영상을 width×height RGB(uint8) 영상으로 하나씩. every: 원본 장을 이만큼마다 하나씩만 (건너뛰기)."""
    pick = [f"select=not(mod(n\\,{every}))"] if every > 1 else []
    command = ["ffmpeg", "-v", "error", *input_args, "-vf", ",".join([*pick, f"scale={width}:{height}"]),
               *(["-vsync", "0"] if every > 1 else []), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
    finally:
        process.kill()
        process.wait()


def video_source(path: str, width: int | None, fps: float | None) -> tuple[Iterator[np.ndarray], float, tuple[int, int]]:
    """영상 파일 → (영상들, 영상 간격(초), (높이, 너비)). width가 있으면 가로를 그만큼으로(비율 그대로), 없으면 원래 크기.
    (원래 크기가 render_width의 1.5배를 넘으면 render_width로 줄인다 — 루밍 계산은 크기에 비례해 느려진다)
    fps가 원래보다 작으면 원본 장을 정수배로 건너뛴다(가장 가까운 배수 — 24장 영상에 15를 주면 2장마다 = 12장). 장 사이 시간이
    늘 같아야 한다: 24장을 ffmpeg로 15장에 맞추면 1장·2장 건너뛴 장이 섞여 실제 간격이 42·83 ms인데 67 ms로 재게 되어, 잰 빠르기가
    장마다 ±25% 튀었다 (실제 영상 10개 7 → 4개 맞음 — 실제 간격으로 넣으면 7개 그대로). 원래보다 크게 주면 원래 그대로 (같은 장을 늘리지 않는다)"""
    src_w, src_h, src_fps = _probe(path)
    if width is None:
        width = src_w if src_w <= 1.5 * CONFIG.render_width else CONFIG.render_width
    width = _even(width) if width != src_w or src_w % 2 else width
    height = src_h if width == src_w else _even(src_h * width / src_w)
    every = max(1, int(round(src_fps / fps))) if fps and fps < src_fps else 1
    return ffmpeg_frames(["-i", path], width, height, every), every / src_fps, (height, width)


def camera_source(device: str, width: int, height: int, fps: float) -> tuple[Iterator[np.ndarray], float, tuple[int, int]]:
    """웹캠(리눅스 v4l2) → (영상들, 영상 간격, (높이, 너비))."""
    args = ["-f", "v4l2", "-framerate", f"{fps:g}", "-i", device]
    return ffmpeg_frames(args, width, height), 1.0 / fps, (height, width)


def recording_source(path: str) -> dict[str, Any]:
    """녹화 파일(tools/looming_record.py 형식): 줄인 밝기 영상과 그때의 회전·속도·명령."""
    with np.load(path) as clip:
        return {key: clip[key] for key in clip.files}


# ---------------------------------------------------------------------------
# 결과 영상: 위험도 겹쳐 보기 (웹 조작 화면과 같은 그림)
# ---------------------------------------------------------------------------

def overlay(frame: np.ndarray, inputs: np.ndarray | None, filtered: np.ndarray | None, decision: Any,
            slowing: bool = False) -> np.ndarray:
    """영상 위에 칸별 커넥톰 입력(빨강), 통로 밖이라 거른 칸(점선), 위쪽 띠(판단 색 + 위험도 막대)를 그린다."""
    image = frame.astype(np.float32)
    height, width = image.shape[:2]
    if inputs is not None:
        rows, cols = inputs.shape
        row_edges = np.linspace(0, height, rows + 1).round().astype(int)
        col_edges = np.linspace(0, width, cols + 1).round().astype(int)
        for r in range(rows):
            for c in range(cols):
                y0, y1, x0, x1 = row_edges[r], row_edges[r + 1], col_edges[c], col_edges[c + 1]
                v = float(np.clip(inputs[r, c], 0.0, 1.0))
                if v > 0.02:
                    alpha = 0.08 + 0.67 * v
                    image[y0 + 1:y1 - 1, x0 + 1:x1 - 1] = (1 - alpha) * image[y0 + 1:y1 - 1, x0 + 1:x1 - 1] + alpha * HEAT
                elif filtered is not None and filtered[r, c]:
                    dash = (np.arange(max(x1 - x0 - 6, 0)) // 4) % 2 == 0
                    for y in (y0 + 3, y1 - 4):
                        image[y, x0 + 3:x1 - 3][dash] = 225
                    dash = (np.arange(max(y1 - y0 - 6, 0)) // 4) % 2 == 0
                    for x in (x0 + 3, x1 - 4):
                        image[y0 + 3:y1 - 3, x][dash] = 225
        for x in col_edges[1:-1]:  # 칸 경계
            image[:, x] = 0.85 * image[:, x] + 0.15 * 255
        for y in row_edges[1:-1]:
            image[y, :] = 0.85 * image[y, :] + 0.15 * 255
    signal = str(getattr(getattr(decision, "signal", None), "value", "NONE")) if decision is not None else "NONE"
    color = SLOW_COLOR if signal == "NONE" and slowing else SIGNAL_COLORS.get(signal, SIGNAL_COLORS["NONE"])
    image[:BAR] = color
    escape = float(getattr(decision, "escape", 0.0)) if decision is not None else 0.0
    image[BAR // 3:BAR - BAR // 3, :int(width * min(max(escape, 0.0), 1.0))] = 255  # 위험도 막대 (0~1 → 화면 폭)
    if decision is None:
        image[:BAR, ::6] = 40  # 판단 없음(연산 서버가 늦거나 끊김): 줄무늬
    return image.clip(0, 255).astype(np.uint8)


class VideoWriter:
    """RGB 영상들 → mp4 (ffmpeg). 첫 영상의 크기로 연다."""

    def __init__(self, path: str, fps: float) -> None:
        self.path, self.fps, self.process = path, fps, None

    def write(self, image: np.ndarray) -> None:
        pad_h, pad_w = image.shape[0] % 2, image.shape[1] % 2  # 영상 인코더는 짝수 크기만 받는다
        if pad_h or pad_w:
            image = np.pad(image, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        if self.process is None:
            height, width = image.shape[:2]
            self.process = subprocess.Popen(
                ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                 "-r", f"{self.fps:g}", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", self.path],
                stdin=subprocess.PIPE)
        self.process.stdin.write(np.ascontiguousarray(image).tobytes())

    def close(self) -> None:
        if self.process is not None:
            self.process.stdin.close()
            self.process.wait()


def load_motion(path: str) -> np.ndarray:
    """자이로·속도 기록 CSV → (N, 5) [시각(초), 좌우 회전 속도(도/초, +오른쪽 = 시계 방향), 앞으로 가는 속도(m/s),
    숙임 속도(도/초, +카메라가 더 아래로), 기울기 속도(도/초, +뒤에서 볼 때 시계 방향 = 오른쪽이 내려감)].
    첫 줄은 칸 이름 (time_s, yaw_rate_dps, speed_mps[, pitch_rate_dps, roll_rate_dps] — 뒤 둘은 없으면 0). 시각은 영상 시작이 0."""
    import csv
    with open(path, encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    table = np.array([[float(r["time_s"]), float(r["yaw_rate_dps"]), float(r["speed_mps"]),
                       float(r.get("pitch_rate_dps") or 0.0), float(r.get("roll_rate_dps") or 0.0)] for r in rows])
    if table.ndim != 2 or len(table) < 2 or np.any(np.diff(table[:, 0]) <= 0):
        raise ValueError(f"{path}: 시각이 늘어나는 두 줄 이상이 필요합니다")
    return table


def with_motion(images: Iterator[np.ndarray], table: np.ndarray, dt: float,
                cfg: Config) -> Iterator[tuple[np.ndarray, None, dict[str, Any]]]:
    """영상마다 직전 영상 이후 돈 각도(좌우·숙임·기울기)·평균 속도를 기록에서 (자이로처럼 영상과 다른 빈도여도 적분해 맞춘다)."""
    times = table[:, 0]
    steps = np.diff(times)
    totals = np.vstack([np.zeros(table.shape[1] - 1), np.cumsum(0.5 * (table[1:, 1:] + table[:-1, 1:]) * steps[:, None], axis=0)])
    for index, image in enumerate(images):
        start, end = max(0.0, (index - 1) * dt), index * dt
        moved = [float(np.interp(end, times, column) - np.interp(start, times, column)) if index else 0.0 for column in totals.T]
        turn, speed = moved[0], moved[1] / dt
        nod, roll = (moved[2], moved[3]) if len(moved) > 3 else (0.0, 0.0)
        user = ControlCommand(forward=min(1.0, max(0.0, speed / cfg.move_speed)))
        yield image, None, {"turn": turn, "speed": speed, "user": user, "applied": user, "nod": nod, "roll": roll}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def configure(args: argparse.Namespace, base: Config = CONFIG) -> Config:
    """명령줄의 카메라 값 → 설정. 영상·웹캠은 몸 마스크를 따로 주지 않으면 몸을 빼지 않는다.
    영상 파일은 들고 찍은 휴대폰 영상으로 보고 걸음 흔들림 보정을 켠다 (--handheld / --no-handheld로 바꾼다)."""
    changes: dict[str, Any] = {}
    if args.fov is not None:
        changes["fov_deg"] = args.fov
    if args.pitch is not None:
        changes["camera_pitch_deg"] = args.pitch
    if args.camera_height is not None:
        changes["camera_height"] = args.camera_height
    if args.body_mask:
        changes["body_mask_file"] = args.body_mask
    elif not args.recording:
        changes["body_mask_file"] = "none"
    cfg = dataclasses.replace(base, **changes)
    handheld = args.handheld if getattr(args, "handheld", None) is not None else bool(args.video)
    return handheld_profile(cfg) if handheld else cfg


def record_line(t: float, pipe: WheelchairPipeline) -> dict[str, Any]:
    decision = pipe.decision
    inputs = pipe.cell_inputs
    return {
        "t": round(t, 3),
        "escape": round(float(getattr(decision, "escape", 0.0)), 3) if decision is not None else None,
        "signal": str(getattr(getattr(decision, "signal", None), "value", "")) if decision is not None else None,
        "turn_left": round(float(getattr(decision, "turn_left", 0.0)), 3) if decision is not None else None,
        "turn_right": round(float(getattr(decision, "turn_right", 0.0)), 3) if decision is not None else None,
        "stage": pipe.reflex.stage,
        "max_input": round(float(inputs.max()), 3) if inputs is not None else 0.0,
        "speed": round(float(pipe.speed_used), 2),  # 루밍에 쓴 속도 (카메라로 짐작했으면 그 값)
    }


def run(frames: Iterator[tuple[np.ndarray | None, np.ndarray | None, dict[str, Any]]], pipe: WheelchairPipeline,
        dt: float, writer: VideoWriter | None, log: Any) -> list[dict[str, Any]]:
    """frames: (보여 줄 RGB 영상, 줄인 밝기 영상 또는 None, 그 영상 직전의 움직임) — 영상마다 휠체어 PC를 한 번 돌린다."""
    lines = []
    t = 0.0
    for image, gray, motion in frames:
        pipe.moved(motion["turn"], motion["speed"], dt, motion["user"], motion["applied"],
                   nod_deg=motion.get("nod", 0.0), roll_deg=motion.get("roll", 0.0))
        if gray is not None:
            pipe.see_gray(gray, motion["frame_shape"], dt)
        else:
            pipe.see(image, dt)
        pipe.decide(motion["user"], dt)
        line = record_line(t, pipe)
        lines.append(line)
        if log is not None:
            log.write(json.dumps(line, ensure_ascii=False) + "\n")
        if writer is not None and image is not None:
            writer.write(overlay(image, pipe.cell_inputs, pipe.cell_filtered, pipe.decision, pipe.reflex.stage == "slow"))
        t += dt
    return lines


def summary(lines: list[dict[str, Any]]) -> str:
    if not lines:
        return "영상이 없습니다."
    signals = Counter(line["signal"] or "판단 없음" for line in lines)
    first_stop = next((line["t"] for line in lines if line["signal"] == "STOP"), None)
    peak = max((line["escape"] or 0.0) for line in lines)
    parts = [f"영상 {len(lines)}장 ({lines[-1]['t']:.1f}초)", f"최대 위험도 {peak:.2f}",
             "처음 정지 신호 " + (f"{first_stop:.2f}초" if first_stop is not None else "없음"),
             "신호별 " + ", ".join(f"{k} {v}" for k, v in signals.most_common())]
    return " · ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--video", help="영상 파일 (ffmpeg가 읽는 것이면 무엇이든)")
    source.add_argument("--camera", help="웹캠 장치 (리눅스: /dev/video0)")
    source.add_argument("--recording", help="녹화 파일 .npz (tools/looming_record.py 형식)")
    parser.add_argument("--speed", default="auto",
                        help="영상·웹캠: 앞으로 간 속도(m/s, 바퀴 값이 없으므로). auto(기본)면 카메라로 짐작 (잴 수 없으면 1 m/s)")
    parser.add_argument("--motion", help="영상: 자이로·속도 기록 CSV (time_s, yaw_rate_dps(+오른쪽), speed_mps"
                        "[, pitch_rate_dps(+아래로), roll_rate_dps(+시계 방향)]) — 영상마다 돈 각도·속도로")
    parser.add_argument("--fps", type=float, default=None, help="영상: 원본 장을 건너뛰어 이 장수 가까이로 (정수배 — 24장에 15면 12장). 웹캠: 장수 (기본 30)")
    parser.add_argument("--width", type=int, default=None, help="영상 가로(px)로 바꾼다 (기본: 원래 크기, 960 넘으면 640)")
    parser.add_argument("--fov", type=float, default=None, help="카메라 가로 화각(도)")
    parser.add_argument("--pitch", type=float, default=None, help="카메라가 아래로 숙인 각(도)")
    parser.add_argument("--camera-height", type=float, default=None, help="카메라 높이(m)")
    parser.add_argument("--body-mask", default="", help="카메라에 보이는 내 몸 마스크 .npy (H×W, 참 = 몸)")
    parser.add_argument("--handheld", action=argparse.BooleanOptionalAction, default=None,
                        help="들고 찍은 카메라(휴대폰)용 걸음 흔들림 보정 (기본: 영상 파일이면 켬, 웹캠·녹화는 끔)")
    parser.add_argument("--out", help="위험도를 겹친 결과 영상 (.mp4)")
    parser.add_argument("--log", help="영상마다 한 줄 JSON 기록")
    args = parser.parse_args(argv)
    cfg = configure(args)
    width = args.width
    camera_speed = str(args.speed).strip().lower() == "auto"
    try:
        speed = 1.0 if camera_speed else float(args.speed)
    except ValueError:
        parser.error("--speed: 숫자(m/s) 또는 auto")
    user = ControlCommand(forward=min(1.0, max(0.0, speed / cfg.move_speed)))

    if args.recording:
        clip = recording_source(args.recording)
        dt = float(clip["dt"])
        frame_shape = tuple(int(v) for v in clip["frame_shape"])
        n = max(1, frame_shape[1] // clip["gray"].shape[2])

        def frames() -> Iterator[tuple[np.ndarray, np.ndarray, dict[str, Any]]]:
            for i, gray in enumerate(clip["gray"]):
                big = np.repeat(np.repeat(np.asarray(gray, dtype=np.float32), n, axis=0), n, axis=1)
                image = np.repeat(big.clip(0, 255).astype(np.uint8)[:, :, None], 3, axis=2)
                yield image, gray, {"turn": float(clip["turn"][i]), "speed": float(clip["speed"][i]),
                                    "user": ControlCommand(forward=float(clip["intended"][i])),
                                    "applied": ControlCommand(forward=float(clip["applied"][i])), "frame_shape": frame_shape}
        stream = frames()
        if "camera" in clip:  # 녹화 때의 카메라 값 (명령줄에서 따로 주지 않았으면)
            fov, pitch, cam_h = (float(v) for v in clip["camera"])
            cfg = dataclasses.replace(cfg, fov_deg=args.fov or fov, camera_pitch_deg=args.pitch if args.pitch is not None else pitch,
                                      camera_height=args.camera_height or cam_h)
    else:
        if args.video:
            images, dt, (height, width) = video_source(args.video, width, args.fps)
        else:
            width = width or cfg.render_width
            images, dt, (height, width) = camera_source(args.camera, width, _even(width * 9 / 16), args.fps or 30.0)
        cfg = dataclasses.replace(cfg, render_width=width, render_height=height)
        if args.motion and args.video:
            stream = with_motion(images, load_motion(args.motion), dt, cfg)
        else:
            motion = {"turn": 0.0, "speed": speed, "user": user, "applied": user}
            stream = ((image, None, motion) for image in images)

    pipe = WheelchairPipeline(cfg)
    pipe.camera_speed = camera_speed and not args.recording and not (args.motion and args.video)  # 기록된 속도가 없을 때만
    writer = VideoWriter(args.out, 1.0 / dt) if args.out else None
    log = open(args.log, "w", encoding="utf-8") if args.log else None
    started = time.perf_counter()
    lines: list[dict[str, Any]] = []
    try:
        lines = run(stream, pipe, dt, writer, log)
    except KeyboardInterrupt:
        print("멈췄습니다 (Ctrl+C).")
    finally:
        if writer is not None:
            writer.close()
        if log is not None:
            log.close()
    elapsed = time.perf_counter() - started
    print(summary(lines))
    if lines:
        print(f"계산: 영상 한 장에 {1000 * elapsed / len(lines):.1f} ms (그림·쓰기 포함)")
    if args.out:
        print(f"결과 영상: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
