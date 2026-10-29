# ② 커넥톰 연산 서버 (`brain_server/`) — 선택

커넥톰 계산을 휠체어 PC 밖의 다른 프로세스·PC에서 합니다. 휠체어 PC는 영상마다 칸별 루밍을 보내고 판단을 받습니다.
없어도 됩니다(휠체어 PC가 직접 계산). 커넥톰 계산이 가벼워(영상 한 장에 약 1 ms) 성능 때문에 필요한 것은 아니고,
여러 휠체어의 판단을 한곳에서 관리·교체하고 싶을 때 씁니다.

| 파일 | 하는 일 |
|---|---|
| `server.py` | TCP 서버(기본 127.0.0.1:8765). 접속한 휠체어마다 따로 `connectome.brain.MaleCNSBrain`을 두고 한 줄 JSON으로 주고받는다. 받은 값의 범위를 확인한다. 관제 서버에 1초마다 상태를 보낼 수 있다 |

휠체어 쪽 클라이언트는 `wheelchair/brain_client.py`입니다.

## 실행

```bash
bash scripts/run_brain_server.sh                   # 같은 PC에서만 (127.0.0.1:8765)
bash scripts/run_brain_server.sh --public          # 다른 PC에서도 (0.0.0.0) — 암호화 없음, 믿을 수 있는 네트워크에서만
bash scripts/run_brain_server.sh --dashboard 127.0.0.1:8766   # ③ 관제 서버에 상태 보내기
python -m brain_server.server --help               # 프로젝트 폴더에서 직접
```

휠체어 PC(시뮬레이션)를 이 서버에 붙이기: `bash scripts/run_webots.sh road --server 127.0.0.1:8765`

- 필요한 것: Python 3.10, numpy, 그리고 `connectome/`·`common/` 폴더
- 통신 형식·지연·끊김 처리: [커넥톰 연산 서버](../docs/brain-server.md)
