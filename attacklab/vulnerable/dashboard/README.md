# ③ 관제 서버 (`dashboard/`) — 선택

여러 휠체어·휴대폰 체험과 커넥톰 연산 서버의 상태를 한 화면에 모으고, 화면에서 명령(커넥톰 켜기·끄기, 안전 스위치, 비상 정지, 처음부터, 맵 바꾸기)을 보냅니다.
받는 모든 것을 기록 파일로 남기고, 지난 기록과 도착 시험 결과를 그림으로 보여 줍니다.

| 파일 | 하는 일 |
|---|---|
| `server.py` | 휠체어·연산 서버 접속(TCP 8766), 관제 화면(웹 8080), 실시간 전송(SSE), 명령 중계, 기록(`logs/dashboard_*.jsonl` — 상태는 바뀐 칸만, 다 쓴 파일은 `.jsonl.gz`로 압축), 분석 API |
| `web/index.html` | 관제 화면. 탭 셋: **실시간**(로봇 카드·휴대폰 카드·미니맵·위험도 그래프·사건 기록) · **기록 분석**(날짜별 작동·맵별 개입·개입 위치 지도) · **시험 결과**(도착 시험 방식별 비교) |

휠체어·연산 서버 쪽 연결은 `common/dashboard_link.py`입니다.

## 실행

```bash
bash scripts/run_dashboard.sh                 # http://localhost:8080/live (기록 분석 /analysis · 시험 결과 /trials), 접속 127.0.0.1:8766
bash scripts/run_dashboard.sh --public        # 다른 PC에서도 — 암호화 없음
bash scripts/stop_dashboard.sh                # scripts/run.sh가 뒤에 켜 둔 관제 서버 끄기
python -m dashboard.server --help             # 프로젝트 폴더에서 직접
```

- 필요한 것: Python 3.10 표준 라이브러리, 그리고 `simulation/scenes.py`(기록 분석의 맵 모양). 화면은 외부 파일(CDN·글꼴)을 쓰지 않아 인터넷 없이 열린다
- 토큰: `REFLEXGUARD_DASHBOARD_TOKEN`을 같은 값으로 주면 그 값이 있는 접속·명령만 받는다
- 기록은 상태를 바뀐 칸만 쓰고 다 쓴 파일을 gzip으로 압축한다. 휠체어 하나·연산 서버 하나로 약 하루 쌓인 135 MB 기록이 압축 뒤 6 MB였다 (지금 쓰는 파일은 64 MB까지 압축 전)
- 화면 구성·API·기록 형식: [관제 대시보드](../docs/dashboard.md)
