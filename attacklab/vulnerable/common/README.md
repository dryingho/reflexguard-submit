# 공용 (`common/`)

모든 파트(① 휠체어 PC, ② 커넥톰 연산 서버, ③ 관제 서버, ④ 시뮬레이션, ⑤ 도구)가 같이 쓰는 것입니다. 어느 컴퓨터에 올리든 이 폴더는 함께 갑니다.

| 파일 | 하는 일 |
|---|---|
| `config.py` | 모든 설정값(`Config` 하나, 기본값은 `CONFIG`). 카메라, 루밍, 휠체어 통로 거르기, 커넥톰, 공유 제어, 서버 주소, 기록. 바꾼 사본은 `dataclasses.replace(CONFIG, …)` — [설정값](../docs/config.md) |
| `control.py` | `ControlCommand(forward, turn, strafe)`: 사용자 명령과 바퀴에 내는 명령의 공통 형식 (각 값 −1~1) |
| `dashboard_link.py` | ③ 관제 서버에 붙는 연결. 휠체어 PC와 연산 서버가 상태·사건을 보내고 명령을 받는다. 끊겨도 본 동작은 멈추지 않고 뒤에서 다시 붙는다 |
