# ① 휠체어 PC (`wheelchair/`)

휠체어에 실린 컴퓨터에서 도는 부분입니다. 카메라 영상과 바퀴의 움직임을 받아, 사용자 명령을 위험할 때만 바꿔 바퀴에 냅니다.

```
카메라 영상 ─▶ looming/detector.py ─▶ connectome/brain.py ─▶ shared_control.py ─▶ 바퀴 명령
 + 회전·전진 속도   ① 루밍 연산            ② 커넥톰 회피 반사        ③ 공유 제어
                   (칸별 확장률)          (기기 안, 또는            (안전·감속·회피·정지)
                                          brain_client.py → ② 연산 서버)
사용자 명령(조이스틱) ─────────────────────────────────────────────▶ ┘
                                    reflex_log.py: 반사가 개입한 구간을 logs/reflex_*.jsonl에 기록
```

| 파일 | 하는 일 | 자세히 |
|---|---|---|
| `looming/detector.py` | **루밍 연산.** 영상을 칸(세로 9 × 가로 11)으로 나눠 칸마다 다가오는 정도(확장률)를 배율 찾기로 잰다. 회전·바닥이 퍼지는 몫을 빼고, 휠체어 통로(옆 몸 폭, 위 머리 높이) 밖으로 지나갈 것은 거른다. 속도를 줄였을 때·감속 한계가 있을 때 보정(`at_intended_speed`, `for_braking`) | [커넥톰 로직](../docs/connectome.md) |
| `range_sensor.py` | **거리 센서 → 커넥톰 입력**: 거리 측정으로 카메라 루밍과 같은 꼴(칸별 1/충돌까지 시간, 옆 흐름)을 만든다 (`config.reflex_sensor = "range"`) | [거리 센서 → 커넥톰](../docs/range-sensor.md) |
| `shared_control.py` | **공유 제어** (`ReflexModule`). 커넥톰 판단(위험도·회피 방향·옆벽 가까움)만으로 감속·회피·정지·방향 복귀·옆벽 피하기를 정한다. 판단이 없으면 안전 정지 | [공유 제어](../docs/shared-control.md) |
| `body.py` | 휠체어 탄 사람 모양. 카메라에 늘 보이는 무릎·팔걸이 화소를 루밍에서 뺀다(실제 카메라는 `body_mask_file`로 찍은 마스크) | [전체 구조](../docs/architecture.md#카메라와-몸) |
| `reflex_log.py` | 작동 기록: 반사가 켜지고 꺼지는 사건과 그때의 흐름(루밍 → 커넥톰 → 공유 제어)을 한 줄 JSON으로 | [작동 기록](../docs/logging.md) |
| `pipeline.py` | **휠체어 PC 한 사이클** (`WheelchairPipeline`): 새 영상 → 루밍 → 보정 → 커넥톰(`see`, 거리 센서면 `sense_range`), 스텝마다 공유 제어(`decide`), 바퀴가 알려 준 움직임(`moved`). Webots 컨트롤러·도착 시험·단독 실행이 모두 이것을 쓴다 | |
| `run.py` | **단독 실행**: Webots 없이 영상 파일·웹캠·녹화 파일 → 위험도를 겹친 결과 영상과 프레임별 기록 (`python -m wheelchair.run`) | [실행과 조작](../docs/usage.md#webots-없이-영상으로-돌리기-휠체어-pc-단독-실행) |
| `brain_client.py` | 커넥톰을 ② 연산 서버에서 계산할 때 쓰는 뇌. 늦거나 끊기면 판단 없음 → 안전 정지 | [커넥톰 연산 서버](../docs/brain-server.md) |

함께 쓰는 것: `connectome/`(커넥톰 회로), `common/`(설정값·제어 명령·관제 연결).
Webots에 의존하지 않고 numpy만 씁니다.

## 지금 어디서 도나

실제 휠체어에는 아직 올리지 않았습니다. 영상 파일·웹캠으로는 `python -m wheelchair.run --video 주행.mp4 --speed 1.2 --out 결과.mp4`로 바로 돌립니다. 시뮬레이션에서는 Webots 컨트롤러
[`simulation/webots/controllers/reflexguard/reflexguard.py`](../simulation/webots/controllers/reflexguard/reflexguard.py)가
휠체어 PC 역할을 합니다. Webots 카메라 영상을 `WheelchairPipeline`에 넣고, 나온 명령으로 시뮬레이션 속 휠체어를 움직입니다.

## 실제 휠체어로 옮길 때

`WheelchairPipeline`에 넣는 것을 그 기기의 장치로 바꾸면 됩니다(`wheelchair/run.py`가 가장 짧은 예).

| 입력 | 시뮬레이션 | 실제 휠체어 |
|---|---|---|
| 카메라 영상 (H×W×3 RGB) | Webots 카메라 | 카메라 드라이버 |
| 직전 영상 이후 돈 각도·앞으로 간 속도 | 시뮬레이션 속 휠체어 | 바퀴 엔코더·IMU |
| 사용자 명령 | 웹 조작 화면 | 조이스틱 |
| 바퀴 명령 (출력) | 시뮬레이션 속 휠체어(`simulation/agent.py`) | 모터 드라이버 |

카메라마다 다른 값(해상도·화각·숙인 각·높이·몸 마스크)은 `common/config.py`의 카메라 항목에 있습니다.
실제 카메라로 주행을 녹화하면 `tools/looming_replay.py`로 루밍이 제대로 나오는지 볼 수 있습니다([테스트와 평가](../docs/testing.md#루밍-보정-녹화와-재생)).

## 서버에 붙기 (선택)

| 환경 변수 | 뜻 |
|---|---|
| `REFLEXGUARD_BRAIN_SERVER=주소:8765` | 커넥톰을 ② 연산 서버에서 계산 (없으면 기기 안에서) |
| `REFLEXGUARD_DASHBOARD=주소:8766` | ③ 관제 서버에 상태·사건을 보내고 명령을 받음 |
| `REFLEXGUARD_DASHBOARD_TOKEN=비밀값` | 관제 서버에 토큰이 걸려 있을 때 |
| `REFLEXGUARD_CAMERA_MS=64` | 카메라·판단 간격(ms). 느린 기기에서 |
| `REFLEXGUARD_SENSOR=range` | 위험 인식을 거리 센서로 (판단·개입은 커넥톰). 기본 `camera` |

권장 사양은 [설치 — 권장 사양](../docs/install.md#권장-사양)에 있습니다. 가장 무거운 것은 루밍 연산입니다(영상 한 장에 약 15 ms — 코어 하나, i9-14900HX).
