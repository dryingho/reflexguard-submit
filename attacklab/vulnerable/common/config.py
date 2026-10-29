"""ReflexGuard 1단계 프로토타입 — 설정값 모음.

조정할 값(속도, 회전속도, 반지름, FOV, 해상도 등)은 전부 이 파일에서 관리한다.

단위 규칙
- 거리: 격자 칸 (1칸 = 1 m 로 가정)
- 각도: 도(degree). 0도 = +x 방향(맵의 오른쪽), 값이 커질수록 위에서 볼 때 시계 방향(우회전)
- 시간: 초
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace



@dataclass(frozen=True)
class Config:
    """프로그램 전체 설정. 일부만 바꾼 사본은 dataclasses.replace(CONFIG, fov_deg=60) 처럼 만든다."""

    # --- 1인칭 카메라 ---
    # 카메라마다 다른 값(해상도·화각·숙임·높이·입력 이득)은 이 설정에 모여 있다. 기본값은 Webots 카메라.
    # 실제 카메라로 바꾸면 이 값들을 그 카메라에 맞추고, 그 카메라로 녹화해 입력 이득을 다시 맞춘다 (tools/looming_replay.py)
    render_width: int = 640       # 카메라 영상 가로(px) — 루밍 계산의 입력 해상도
    render_height: int = 360      # 카메라 영상 세로(px)
    fov_deg: float = 120.0        # 수평 시야각(도). 넓을수록 옆에서 튀어나오는 것을 일찍 본다 (90도에서 넓힘)
    body_mask_file: str = ""      # 카메라에 보이는 내 몸 화소 (.npy, H×W, 참 = 몸). 비어 있으면 wheelchair/body.py 모양으로 계산한다
                                  # (Webots). "none"이면 몸을 빼지 않는다 (몸이 안 보이는 카메라·실제 영상 — wheelchair/run.py)
                                  # 실제 카메라는 몸만 찍힌 영상에서 만든 마스크를 준다
    collision_restart_delay_s: float = 1.5  # 벽에 부딪히면 그 자리에서 이만큼(초) 멈춰 보여 준 뒤 처음 위치에서 다시 시작 (simulation/collision.py)
    goal_restart_delay_s: float = 6.0       # 도착지('G')에 닿으면 이만큼(초) 멈춰 결과(걸린 시간·커넥톰 개입 수)를 보여 준 뒤 처음부터 (simulation/goal.py)

    # --- 에이전트(임시 휠체어) ---
    agent_radius: float = 0.3     # 원형 충돌체 반지름(칸)
    move_speed: float = 2.0       # 전진·후진·평행이동 속도(칸/초)
    turn_speed_deg: float = 120.0  # 회전 속도(도/초)
    brake_decel: float = 0.0       # 줄일 때의 감속 한계(m/s²). 0이면 명령대로 바로 선다. 2.0이면 2 m/s에서 서는 데 1 m (시험의 현실 조건)
    start_angle_deg: float = 0.0  # 시작 방향(도)

    # --- Webots 버전 (월드 생성 + 웹 조작 화면) ---
    wall_height: float = 2.4          # 벽 높이(m)
    camera_height: float = 1.1        # 카메라 높이(m) — 휠체어에 앉은 사람의 눈높이 정도
    camera_pitch_deg: float = 15.0    # 카메라가 아래로 숙인 각(도). 무릎·팔걸이 끝이 영상 아래에 보여 내 폭을 가늠할 수 있다 (wheelchair/body.py).
                                      # 루밍 감지는 이 각만큼 수평선(확장 중심)·칸 각도를 보정하고, 몸이 보이는 화소는 빼고 잰다
    webots_time_step_ms: int = 32     # 시뮬레이션 한 스텝의 길이(ms) — 운전 제어는 이 간격으로 한다
    camera_period_ms: int = 32        # 카메라 영상 간격(ms, 스텝 길이의 배수). 루밍·커넥톰 판단은 새 영상이 올 때 갱신한다.
                                      # 64면 카메라 비용이 절반 — WSL에서 영상 한 장 읽기가 약 100 ms라 실행이 약 2배 빨라진다.
                                      # 환경 변수 REFLEXGUARD_CAMERA_MS로 고른다 (실행 스크립트의 --camera-ms)
    web_fps: float = 15.0             # 웹 조작 화면으로 보내는 영상의 초당 프레임 수(상한)
    web_jpeg_quality: int = 70        # 웹으로 보내는 영상의 JPEG 품질(1~100)
    web_key_timeout: float = 0.6      # 웹 화면에서 이 시간(초) 동안 소식이 없으면 키를 뗀 것으로 본다(안전장치)
    web_viewer_timeout: float = 3.0   # 웹 화면에서 이 시간(초) 동안 소식이 없으면 영상 전송을 멈춘다
    web_ready_interval: float = 0.5   # 컨트롤러가 웹 화면에 '돌고 있음' 신호를 보내는 간격(초)

    # --- 루밍 감지 (wheelchair/looming/detector.py, 기획서 ① 루밍 감지) ---
    looming_focus_x: float = 0.5   # 영상이 확장(루밍)하는 중심의 가로 위치(0=왼쪽, 1=오른쪽). 정면 주행이면 화면 중앙
    looming_focus_y: float = 0.5   # 확장 중심의 세로 위치(0=위, 1=아래)
    looming_roi_half_width_deg: float = 26.6   # 루밍을 재는 '중앙 영역'의 가로 반폭(도, 진행 방향 기준). 양옆으로 지나쳐 가는
                                               # 벽을 재지 않으려고 좁힌다 (90도 화면의 가운데 절반과 같은 범위)
    looming_roi_half_height_deg: float = 15.7  # 중앙 영역의 세로 반폭(도, 수평선 기준). 천장·바닥, 바로 위·아래로 스쳐 가는 벽을 뺀다
    looming_cell_max_azimuth_deg: float = 90.0  # 커넥톰(LPLC2)에 넘기는 칸을 정면에서 이 각도 안쪽으로 자른다 (90 = 자르지 않음).
                                                # 예전엔 45로 옆으로 스쳐 가는 벽을 뺐지만, 이제 충돌 경로 거르기(looming_course_*)가
                                                # 휠체어 통로 밖을 거르므로 자르지 않는다 (카메라·장소가 바뀌어도 같은 기준). 옆 흐름(HS)은 늘 전부
    looming_cell_min_elevation_deg: float = -90.0  # 커넥톰에 넘기는 칸은 수평선 아래 이 각도까지만 (-90 = 제한 없음). -30으로 막으면
                                                   # 예전 정면 카메라와 같은 창이 되어 빈 인도 헛반응은 0이 되지만, 숙인 카메라가 새로 보는
                                                   # 발밑을 잃어 굴러 나오는 공·뛰어나온 아이를 놓쳤다 (docs/connectome.md '보정 기록')
    looming_method: str = "zoom"      # 칸별 확장률 재는 방법: zoom(이전 영상을 키워 보며 가장 잘 맞는 배율을 찾는다,
                                      # wheelchair.looming.detector.LoomingDetector._zoom_cells) / gradient(밝기 기울기 최소제곱 — 예전 방식).
                                      # zoom은 참값 그대로의 확장률을 내서 프레임 간격·밝기·흐림이 달라도 같은 이득을 쓴다.
                                      # gradient는 한 프레임에 무늬가 많이 움직이면 크게 덜 잡아(배율 1.15에서 5%) 카메라·간격마다
                                      # 이득을 다시 맞춰야 했다 (docs/connectome.md '보정 기록')
    looming_zoom_step: float = 0.25   # zoom: 확장률 후보 간격(1/초)
    looming_zoom_max_rate: float = 5.0  # zoom: 찾는 확장률 상한(1/초) — 2 m/s로 0.4 m 앞
    looming_zoom_min_std: float = 6.0   # zoom: 칸 안 밝기 표준편차가 이보다 작으면(무늬 없음) 재지 않는다 (0~255 밝기, 줄인 영상).
                                        # 3이면 잡음 많은 카메라(줄인 영상에서 σ 3)에서 잡음끼리 맞는 배율을 골라 헛반응이 9번 났다
    looming_zoom_contrast: float = 0.25  # zoom: 가장 잘 맞는 후보가 후보들의 중간값보다 이 비율만큼 나아야 믿는다
    looming_ground_comp: float = 1.0  # 평평한 바닥이 다가오는 속도만큼 퍼져 보이는 확장률을 이 비율로 빼고 잰다 (0 = 빼지 않음).
                                      # 무늬 있는 바닥(Webots 타일·보도블록)은 가까운 아래쪽이 늘 퍼져 보여(바닥 칸 0.5~0.8/초)
                                      # 빈 복도에서도 멈췄다. 1이면 이론값을 그대로 빼고, 실제 Webots 영상에서 바닥 칸이 0이 된다
    looming_course_inner_m: float = 0.4   # 충돌 경로 거르기: 칸의 물체가 진행 방향에서 옆으로 이 거리(m) 안이면 그대로 넘기고,
    looming_course_outer_m: float = 0.6   # 이 거리 밖이면 넘기지 않는다 (사이는 비례). 거리 = 속도/확장률. outer 0이면 끈다.
                                          # 몸 반지름 0.3 m — 몸 가장자리에서 0.1~0.3 m. 0.5~0.8이면 0.3 m 옆으로 지나칠
                                          # 칸막이 모서리에도 멈췄다
                                          # 옆 건물·복도 벽, 0.4 m 옆으로 지나칠 칸막이 모서리도 다가가면 퍼져 보이지만 그대로 가도
                                          # 부딪히지 않는다 — 실제 Webots에서 이런 것에 멈추고 비켜 갔다 (몸 반지름 0.3 m)
    # 위아래도 같은 방법으로: 칸의 물체 높이 = 카메라 높이 + 거리 × tan(위 각도). 타고 있는 사람 머리보다 위로 지나갈 것
    # (천장·조명·간판)은 넘기지 않는다. 이 높이(m) 아래는 그대로, top_outer 위는 0 (사이는 비례). top_outer 0이면 끈다
    looming_course_top_m: float = 1.6
    looming_course_top_outer_m: float = 2.0
    # 흔들림 보정 (손떨림 방지): 직전 영상과 지금 영상이 화면 전체로 위아래·좌우로 밀린 만큼(줄인 영상에서 최대 이 화소)을
    # 확장 중심 둘레(가로·세로 stabilize_window 비율)에서 재어 되돌린 뒤 루밍을 잰다. 손에 든 휴대폰은 1°쯤 흔들리기만 해도
    # 한쪽 칸이 '퍼지는 것'으로 잡혀 위험이 떴다 (흔들림 3 px에 영상의 절반이 위험 신호). 0이면 끈다
    looming_stabilize_px: float = 6.0
    looming_stabilize_window: float = 0.5
    # 잡음 바닥 보정 (습관화 — 초파리 루밍 뉴런도 늘 있는 자극에는 무뎌진다): 칸마다 평소 루밍 수준(잡음 바닥)을 재 두고
    # 그 margin배를 뺀 만큼만 커넥톰에 넘긴다. 바닥은 floor_rise_s로 천천히 오르고 floor_fall_s로 빨리 내려간다.
    # 바닥의 floor_event_ratio배 + floor_event(1/초)를 넘는 값은 '실제로 다가오는 것'이라 바닥에 넣지 않는다.
    # 기본은 끔(margin 0): 손떨림은 짧은 튐이라 '다가오는 것'으로 걸러져 바닥에 들어가지 않아, 켜도(1.5) 흔들림 헛신호가
    # 52→53, 72→68 / 450장으로 거의 그대로였다(흔들림 보정이 그 일을 한다). 형광등 깜박임처럼 늘 깔린 잡음이 있는 카메라면 1.5쯤 켠다
    looming_floor_margin: float = 0.0
    looming_floor_rise_s: float = 8.0
    looming_floor_fall_s: float = 2.0
    looming_floor_event_ratio: float = 3.0
    looming_floor_event: float = 0.3
    looming_still_max_azimuth_deg: float = 20.0  # 거의 서 있을 때(0.2 m/s 이하)는 거리를 몰라 통로로 거르지 못하므로 각도로:
    looming_still_fade_deg: float = 15.0         # 정면 이 각도 안은 1, 그 밖으로 fade만큼 가며 0으로 (중앙일수록 크게)
                                                 # 제자리에서 돌 때 옆에서 움직이는 사람에 멈추지 않게
    looming_cell_max_elevation_deg: float = 90.0  # 커넥톰에 넘기는 칸을 수평선 위 이 각도까지로 자른다 (90 = 자르지 않음). 예전엔
                                                  # 14로 천장·조명을 뺐지만(빈 복도에서 멈췄다), 이제 위아래 통로 거르기(looming_course_top_*)가 뺀다
    looming_smooth_tau: float = 0.1  # 좌/우 루밍 지수 평활 시간상수(초). 프레임마다 값이 튀어 화면이 떨리는 것을 막는다
    looming_grid_cols: int = 11      # 화면 전체를 가로 이만큼 칸으로 나눠 칸마다 루밍을 잰다 (LPLC2 수용장 입력). 칸 하나가 약 11도 —
                                     # 시야 120도에서 8칸(15도)이면 진행 방향 근처 잡음 칸 하나가 수용장을 너무 많이 차지해 복도에서 튀었다
    looming_grid_rows: int = 9       # 세로 칸 수 (칸 하나가 약 10도)
    looming_speed_cap: float = 2.0   # 공유 제어가 속도를 줄였을 때 루밍을 '원래 속도였다면'으로 되돌리는 최대 배율. 잡음까지 키우므로
                                     # 5배였을 때 실제 Webots에서 '조금 감속 → 벽 무늬 잡음 4.5배 → 정지'로 번졌다
    looming_speed_turn_limit: float = 0.3  # 직전에 이보다 세게 돌았으면 되돌리지 않는다(회전 보정 잔여 잡음까지 키우지 않게)
    # 감속 한계(brake_decel)가 있을 때: 바로 서는 조건에서 커넥톰이 멈추는 '충돌까지 시간'(보정값, 초). 루밍을
    # 1 + v/(2·brake_decel·이 값)배(최대 looming_brake_cap) 키워, 서는 데 더 가는 만큼 일찍 같은 위험도가 나오게 한다
    looming_brake_ttc_s: float = 0.25   # 감속 4 m/s²·비키지 않는 사람 시험에서 고름 (1.0→18, 0.5→23, 0.25→32/48 도착, 헛개입 47%)
    looming_brake_cap: float = 3.0
    # 감속 한계가 있을 때 충돌 경로 거르기(looming_course_*)를 '옆 사람이 서기까지 걸어 들어올 거리'(이 속도 × v/a)만큼 넓힌다
    looming_brake_cross_mps: float = 0.0
    looming_downsample: int = 3      # 루밍을 재기 전에 영상을 이 배율로 줄인다(칸 평균). 벽에 가까우면 한 스텝 사이에 무늬가
                                     # 수십 픽셀씩 커져 밝기 기울기 방식이 크게 덜 잡는다(실제 Webots 영상에서 1칸 앞 2칸/초일 때
                                     # 이론값의 0.09배). 줄이면 움직임이 몇 픽셀로 줄고 잔무늬도 사라진다. 1이면 줄이지 않는다

    # --- 옆 흐름 → HS 세포 (LoomingDetector._cell_shifts, connectome.brain.HSCircuit) ---
    hs_max_shift_px: int = 16        # 칸마다 옆 흐름을 찾는 범위(줄인 영상의 픽셀, 한 프레임). 벽에 붙어 달리면 화면 가장자리가 10픽셀 넘게 흐른다
    hs_match_contrast: float = 0.12  # 가장 잘 맞는 밀기가 중간값보다 이 비율 이상 좋아야 믿는다 (무늬 없는 칸은 0)
    hs_flow_norm_deg: float = 60.0   # HS 입력 정규화: 수용장 안 앞→뒤 흐름(도/초)이 이 값이면 1
    hs_rf_sigma_deg: float = 7.0     # HS 수용장 지도(10도 칸)를 카메라 칸으로 옮길 때 퍼짐
    hs_turn_limit_deg: float = 2.5   # 한 프레임에 이보다 많이 돌면 옆 흐름을 새로 재지 않는다 (회피의 최대 회전 3.8도/프레임에서
                                     # 가짜 흐름이 났다. 옆벽 피하기는 최대 2.3도/프레임이라 걸리지 않는다)
    hs_hold_decay_s: float = 0.3     # 재지 않는 동안 직전 값이 줄어드는 시간상수(초)

    # --- 회피 반사 / 공유 제어 (wheelchair/shared_control.py, 기획서 ③ 공유 제어 · 표 7 위험 단계) ---
    # 거리 센서 없이 커넥톰 출력만으로 판단한다: 위험도 → 감속, 디코더 LEFT/RIGHT → 회피, STOP → 정지 (아래 decode_* 기준).
    reflex_avoid_turn: float = 1.0   # 비켜 갈 때 도는 세기(-1~1 중). 1이면 최대 회전 속도 (0.7보다 사람 옆 여유가 컸다)
    # 회피 중 속도(사용자 전진의 비율): 비켜 가면서 같이 감속한다. 위험도가 decode_stop_off일 때 1, reflex_hard_stop일 때 0인
    # 직선을 [reflex_avoid_min_speed, reflex_avoid_max_speed]로 자른다 → 평소엔 최대 30%, 위험할수록 10%까지 줄인다.
    # (최대 30%: 정지 대신 비켜 갈 때 60%면 골목에서 나온 사람과 부딪혔고, 30%가 사람 옆 여유가 가장 컸다)
    reflex_avoid_max_speed: float = 0.3
    reflex_avoid_min_speed: float = 0.1   # 회피 중에도 조금은 나아가야 비켜 갈 수 있다
    reflex_avoid_first: bool = True   # 정지 신호라도 위험이 한쪽으로 쏠렸으면 멈추지 않고 반대쪽으로 비켜 간다 (회피 우선, 아래 값들).
                                      # 한계: 옆벽에 붙은 채 막다른 벽으로 가면 붙은 벽 쪽으로 꺾을 수 있다 (옆벽은 커넥톰이 못 본다)
    reflex_avoid_side_ratio: float = 0.2   # 한쪽으로 쏠림 = |좌 − 우| / (좌 + 우) (DNp01 좌·우 출력)가 이 값 이상
    reflex_hard_stop: float = 0.65         # 위험도가 이 값 이상이면 쏠림과 상관없이 멈춘다 (비켜 가기엔 너무 가깝다)
    reflex_avoid_hold_deg: float = 25.0     # 한번 비켜 가기 시작하면 이만큼(도) 돌 때까지 이어서 꺾는다 (조금 꺾고 다시 벽으로 가지 않게)
    reflex_return_heading: bool = True  # 비켜 간 뒤 위험이 사라지면 원래 가던 방향으로 몸을 되돌린다 (반사가 꺾은 만큼만)
    reflex_return_delay_s: float = 1.0  # 위험이 이 시간(초) 동안 없으면 되돌리기 시작한다 (피한 것을 다 지나가도록 — 0.3초면
                                        # 다 지나가기 전에 돌아서 화물차 사이 아이·45도 벽에 부딪혔다)
    reflex_return_turn: float = 0.5     # 되돌릴 때 도는 세기(-1~1 중) — 회피보다 천천히
    # HS 회로(옆 흐름)로 옆벽 피하기 — 가까움(0~1)은 2 m/s로 달릴 때 옆벽 0.15 m ≈ 0.75, 0.5 m ≈ 0.47, 1 m ≈ 0.24
    reflex_near_on: float = 0.45       # 한쪽 가까움이 이 값 이상이면 반대쪽으로 떼어 놓기 시작 (stage "veer")
    reflex_near_off: float = 0.30      # 이 값 아래로 떨어지면 그만 (떨림 방지)
    reflex_near_full: float = 0.75     # 이 값이면 최대 세기로 꺾는다
    reflex_veer_turn: float = 0.6      # 옆벽 피하기 최대 회전 세기 (사용자 회전에 더한다)
    reflex_veer_speed: float = 0.8     # 옆벽 피하기 중 속도 비율
    reflex_veer_max_deg: float = 15.0  # 옆벽 피하기로 한 번에 꺾는 최대 각도 (그 뒤엔 방향을 유지하며 비스듬히 멀어진다)
    reflex_veer_return_delay_s: float = 0.3  # 옆벽에서 떨어지고 이 시간(초)이 지나면 원래 가던 방향으로 되돌린다 (회피보다 짧게 —
                                             # 그대로 두면 폭 2 m 복도에서 맞은편 벽으로 비스듬히 다가가 회피·정지가 줄줄이 났다)
    reflex_near_min: float = 0.15      # 회피 방향을 HS로 정하려면 가까운 쪽이 이 값 이상이고
    reflex_near_margin: float = 0.10   # 좌우 차이가 이 값 이상이어야 한다 (아니면 DNp01 좌우로 정한다)
    # 옆 흐름 속도 조절 (wheelchair/shared_control.ReflexModule._pace): HS 회로의 좌우 가까움 중 큰 쪽이 near부터 full까지 오르는
    # 동안 전진 상한을 1 → min_speed로 낮춘다. 가까운 기둥·모퉁이 옆을 미리 느리게 지나간다 (꿀벌·파리의 광학 흐름 속도 조절).
    # near 0이면 끈다(기본). 감속 한계가 있는 시험(tools/goal_trials.py --realistic)에서 고른다
    reflex_pace_near: float = 0.0
    reflex_pace_full: float = 0.6
    reflex_pace_min_speed: float = 0.5
    reflex_lock_until_safe: bool = True     # 정지한 뒤 커넥톰이 아직 위험하다고 하는 동안(신호 STOP·회피, 또는 위험도 decode_stop_off 이상)은
                                            # 조이스틱을 놓았다 다시 밀어도 전진·후진·회전을 모두 막는다. False면 예전처럼 W를 떼면 바로 푼다
    reflex_stop_release_turn_deg: float = 20.0  # 정지한 뒤 사용자가 W를 누른 채 이만큼(도) 방향을 돌리면 다시 출발한다 (0이면 W를 떼야 출발).
                                                # 비스듬히 벽 쪽으로 가다 멈췄을 때 살짝 꺾어 바로 이어 가게 — 정지 기준을 올리는 것보다 안전했다

    # --- 커넥톰 뇌(MaleCNS LPLC2→DNp01 LIF) : connectome/brain.py (기획서 ② 커넥톰 기반 회피 반사) ---
    # zip 본편의 brain_server/model.py(LIF)·decoder/decoder.py를 그대로 이식한 파라미터.
    looming_norm: float = 2.0         # 루밍 확장률(1/s)을 0~1로 정규화하는 기준(이 값이 1.0). LPLC2 자극 입력 범위 맞춤
    brain_input_gain: float = 24.0    # 루밍 → LPLC2 입력 전류 이득 (수용장 안 칸의 루밍 가중 합에 곱한다). 배율 찾기(zoom)로 잰
                                      # 참값 확장률 기준 — 실제 Webots 녹화 재생으로 맞춤 (docs/connectome.md '보정 기록')
    brain_synaptic_gain: float = 300.0  # LPLC2 → DNp01 시냅스 이득(행 정규화 후). LPLC2 사이의 상대 세기는 커넥톰 시냅스 수,
                                       # 전체 크기는 실제 Webots 영상으로 맞춤: 정면 벽엔 충돌 1.3~1.7초 전 STOP, 옆벽·회전엔 0
    brain_tau_ms: float = 10.0        # LIF 막전위 시간상수(ms)
    brain_refractory_ms: int = 2      # 스파이크 뒤 불응기(ms)
    brain_output_rate_hz: float = 200.0  # DNp01 발화율을 0~1로 나누는 기준
    lplc2_eye_overlap_deg: float = 10.0  # 겹눈 맨 앞 기둥이 정면에서 반대쪽으로 넘어가 보는 각도(두 눈이 앞에서 겹치는 폭)
    brain_rate_window_ms: float = 40.0   # 발화율을 재는 시간창(ms, 1ms마다 갱신하는 지수 이동 평균).
                                         # 프레임 간격(Webots 32ms, 다른 카메라는 다를 수 있음)과 무관하게 같은 발화율이 나오게 한다
    # 커넥톰 연산 서버 (brain_server/server.py · wheelchair/brain_client.py). 비어 있으면 기기 안에서 계산한다.
    brain_server: str = ""                 # "host:port" — 환경 변수 REFLEXGUARD_BRAIN_SERVER가 있으면 그쪽이 우선
    brain_server_wait_ms: float = 25.0     # 스텝마다 서버의 답을 이만큼(ms)까지 기다린다 (Webots 한 스텝 32ms 안)
    brain_server_stale_ms: float = 150.0   # 답이 늦으면 이보다 오래되지 않은 직전 답을 쓰고, 더 오래되면 '판단 없음'(안전 정지)
    brain_server_connect_timeout_s: float = 1.0  # 접속 시도 제한 시간(초)
    brain_server_retry_s: float = 0.5      # 끊기면 이 간격(초)으로 다시 접속한다
    # 관제 대시보드 (dashboard/server.py · common/dashboard_link.py). 비어 있으면 붙지 않는다.
    dashboard: str = ""                    # "host:port" (대시보드의 로봇·서버 접속 포트, 기본 8766) — 환경 변수 REFLEXGUARD_DASHBOARD 우선
    dashboard_state_hz: float = 5.0        # 로봇이 대시보드로 상태를 보내는 초당 횟수
    dashboard_connect_timeout_s: float = 1.0
    dashboard_retry_s: float = 1.0         # 끊기면 이 간격(초)으로 다시 접속한다
    # 디코더(히스테리시스): DNp01 출력 → STOP/LEFT/RIGHT/NONE (기획서 ③ 위험 신호)
    decode_stop_on: float = 0.3       # escape가 이 값 이상이면 STOP
    decode_stop_off: float = 0.12     # STOP은 이 값 밑으로 내려가야 풀린다(떨림 방지)
    decode_turn_on: float = 0.25      # 좌·우 출력차가 이 값 이상이면 그쪽으로 회피 신호
    decode_turn_off: float = 0.10     # 회피 신호는 이 값 밑으로 내려가야 풀린다

    # --- 위험 인식 센서: 커넥톰에 무엇을 넣나 (wheelchair/range_sensor.py · simulation/range_scan.py) ---
    # camera: 카메라 영상의 루밍(기본 — 커넥톰이 위험 인식과 판단을 다 한다)
    # range:  거리 센서(2D 라이다 같은 것)가 잰 거리로 '다가오는 빠르기 ÷ 거리'(= 1/충돌까지 시간, 카메라 루밍과 같은 양)를
    #         칸마다 만들어 같은 커넥톰에 넣는다 — 위험 인식은 거리 센서, 판단·개입은 커넥톰과 공유 제어.
    # 환경 변수 REFLEXGUARD_SENSOR(실행 스크립트의 --sensor)로 고르고, 실행 중에는 웹 조작 화면·관제 대시보드에서 바꾼다
    reflex_sensor: str = "camera"
    range_step_deg: float = 2.0          # 빛살 간격(도). 카메라와 같은 시야(fov_deg)를 훑는다
    range_max_m: float = 5.0             # 최대 측정 거리(m). 이보다 멀면 '맞은 것 없음'
    range_max_closing_mps: float = 6.0   # 한 빛살의 거리가 이보다 빨리 줄면 다가온 것이 아니라 다른 것이 빛살 앞으로 들어온 것
                                         # (지나가던 사람의 가장자리 등) — 그 빛살은 이번에 0으로 둔다
    # 거리 센서 입력의 세기: 커넥톰 이득(looming_norm·hs_flow_norm_deg)은 카메라로 맞춘 값이라, 거리 센서로 만든 입력은
    # 이 배율을 곱해 카메라 쪽 세기에 맞춘다 (카메라 값은 그대로 — docs/range-sensor.md '거리 센서 값 맞추기')
    # 같은 Webots 녹화(tests/clips.py)에 두 센서를 함께 대어 맞췄다:
    range_gain: float = 0.75             # 칸별 확장률(LPLC2 → DNp01 입력)에 곱한다. 1이면 카메라와 같은 때 — 2 m/s로 벽에 다가가면
                                         # 3.2 m 앞에서 섰다(너무 이르다). 0.75면 1.6 m 앞. 0.78~1은 3 m 안팎, 0.75 아래는 1.3~1.6 m로
                                         # 계단처럼 바뀐다(DNp01이 발화 기준을 넘는 때). 대신 서는 데 오래 걸리면(감속 2 m/s²) 늦어진다
    range_flow_gain: float = 0.5         # 옆 흐름(HS 입력)에 곱한다. 거리로 계산한 흐름은 카메라가 잰 흐름의 약 2배라(옆벽 가까움
                                         # 평균 0.43 대 0.22) 옆벽 피하기가 잦았다 — 0.5에서 세 녹화 모두 카메라와 같아졌다
    range_object_top_m: float = 1.6      # 거리 센서는 높이를 모른다: 맞은 것은 바닥부터 이 높이(m)까지 서 있다고 보고 칸에 펼친다
                                         # (휠체어 통로 위쪽 끝 looming_course_top_m과 같게 — 그 위는 어차피 거른다)

    # --- 회피 반사 작동 기록 · 화면 알림 (wheelchair/reflex_log.py) ---
    reflex_log_release_s: float = 0.3  # 개입이 이 시간(초) 동안 없으면 한 번의 작동이 끝난 것으로 본다(짧게 끊긴 것은 한 번으로 묶는다)
    reflex_log_sample_s: float = 0.1   # 작동 중 흐름(루밍·커넥톰·공유 제어)을 로그에 남기는 간격(초)
    web_state_hz: float = 10.0         # 웹 조작 화면으로 흐름 상태를 보내는 초당 횟수(상한)


def camera_profile(cfg: "Config", period_ms: int) -> "Config":
    """카메라 간격을 바꾼 설정 (스텝 길이의 배수). 확장률을 배율 찾기로 재므로 입력 이득은 그대로 쓴다."""
    if period_ms <= 0 or period_ms % cfg.webots_time_step_ms:
        raise ValueError(f"camera period {period_ms} ms must be a multiple of the {cfg.webots_time_step_ms} ms step")
    return replace(cfg, camera_period_ms=period_ms)


CONFIG = Config()  # 기본 설정 (Webots 카메라 영상 기준)
# 환경 변수 REFLEXGUARD_CAMERA_MS(예: 64)로 카메라 간격을 고른다 — 컨트롤러·연산 서버·도구가 같은 값을 쓰도록 여기서 한 번 정한다
if os.environ.get("REFLEXGUARD_CAMERA_MS"):
    CONFIG = camera_profile(CONFIG, int(os.environ["REFLEXGUARD_CAMERA_MS"]))
SENSORS = ("camera", "range")  # reflex_sensor로 고를 수 있는 것
# 환경 변수 REFLEXGUARD_SENSOR(camera / range)로 커넥톰 입력 센서를 고른다 (실행 스크립트의 --sensor)
if os.environ.get("REFLEXGUARD_SENSOR"):
    if os.environ["REFLEXGUARD_SENSOR"] not in SENSORS:
        raise ValueError(f"REFLEXGUARD_SENSOR must be one of {SENSORS}, got {os.environ['REFLEXGUARD_SENSOR']!r}")
    CONFIG = replace(CONFIG, reflex_sensor=os.environ["REFLEXGUARD_SENSOR"])
