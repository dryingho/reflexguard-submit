# 연습 3 — 요청 폭주 (서비스 거부)와 토큰 추측

[← 연습 2 — 명령 위조](02-command-forgery.md) · 다음: [연습 4 — 평문 통신](04-plaintext-and-connectome.md)

조치 전 관제 서버에 요청을 연달아 보내 막는 장치가 없음을 확인하고, 토큰 추측·이상한 길이 값·끝없는 줄도 시험해 봅니다. 그다음 속도 제한과 조치 후 코드로 막히는 것을 확인합니다.

KISA 개발보안 가이드: 구현 2-16 반복된 인증시도 제한 기능 부재 · 4-2 오류상황 대응 부재 · 5-2 부적절한 자원 해제(자원 고갈) · 설계 SR2-2

## 약점
- 조치 전 관제 서버와 접속 포트에는 요청 속도·접속 수 제한이 없습니다.
- 한 출처가 요청을 쏟아부으면 관제가 느려지거나 멈출 수 있습니다(가용성 저하 — 서비스를 쓸 수 없게 됨).
- 안전 시스템의 관제가 멈추는 것 자체가 위험입니다.

## 관찰 (보안 테스트)
취약한 상태에서, 내 랩에 짧게 연속 요청을 보내 봅니다(폭주가 아니라, 제한이 없음을 확인하는 정도).

```bash
for i in $(seq 1 20); do curl -s -o /dev/null -w "%{http_code} " http://127.0.0.1:8080/live; done; echo
```

모두 `200`입니다. 몇 번을 연달아 보내든 서버가 전부 받습니다. 감사 로그에 같은 IP의 `http`가 20줄 남습니다. 막는 장치가 없다는 뜻입니다.

그 밖에 조치 전 버전에서 함께 볼 것 (모두 내 랩에만, 짧게):

```bash
# ① 토큰 추측: 틀린 토큰을 거듭 넣어도 막지 않는다 (토큰을 켠 경우 — 연습 1의 패치)
for i in $(seq 1 7); do curl -s -o /dev/null -w "%{http_code} " -H "X-Token: guess-$i" http://127.0.0.1:8080/api/agents; done; echo
# ② 숫자가 아닌 길이: 서버가 예외로 연결을 끊는다 (응답 없음 = 000)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/api/command -H "Content-Type: application/json" -H "Content-Length: abc"
# ③ 개행 없는 긴 줄: 접속 포트(8766)가 2MB를 다 받아 둔다 (줄 끝을 기다리며 메모리에 쌓음)
python3 -c "
import socket; s = socket.create_connection(('127.0.0.1', 8766))
s.sendall(b'{\"type\":\"hello\",\"role\":\"webots\",\"name\":\"big\"}\n')
sent = 0
try:
    while sent < 2_000_000: s.sendall(b'x' * 65536); sent += 65536
    print('끊기지 않고', sent, '바이트를 받아 줌')
except OSError: print('서버가', sent, '바이트쯤에서 끊음')"
```

## 패치
(가) 앞단 완화 — IP마다 초당 허용량을 둡니다(토큰 버킷: 일정한 속도로 채워지는 허용량을 요청마다 하나씩 꺼내 쓰는 방식).

```bash
docker compose down
PATCH_RATE_LIMIT_RPS=5 docker compose up --build
```

(나) 코드 조치 — 조치 후 본편은 서버 자체에 한도가 있습니다(4단계).

- IP마다 요청 속도 (관제 초당 20·한 번에 60)
- 토큰을 5번 틀리면 그 주소를 1분 잠금
- 동시 접속 수 상한 (관제 64·실시간 화면 32·연산 서버 16)
- 줄 길이는 읽으면서 재기
- 숫자가 아닌 값은 400

```bash
docker compose down
LAB_VERSION=after docker compose up --build -d
```

## 검증
```bash
for i in $(seq 1 20); do curl -s -o /dev/null -w "%{http_code} " http://127.0.0.1:8080/live; done; echo
```

- 처음 몇 개는 `200`, 그 뒤로 `429`가 섞입니다. 로그에 `blocked / rate_limit`.
- client 티어는 초당 몇 번만 상태를 보내므로 제한에 걸리지 않고 계속 접속합니다. 정상 동작이 유지되는지 확인하세요. 제한값을 너무 낮추면 정상 트래픽도 막히는 것을 함께 관찰해 보면 좋습니다.
- (나) `LAB_VERSION=after`에서 관찰의 ①②③을 다시 해 봅니다.
  - ① `401 401 401 401 401 429 429` — 5번 틀리면 맞는 토큰도 1분 동안 `429`.
  - ② `400`.
  - ③ hello에 토큰이 없어 바로 끊깁니다. 토큰(`\"token\":\"lab-secret\"`)을 넣어도 512KB쯤에서 끊습니다.
- (나)에서 요청 속도: `for i in $(seq 1 80); do curl -s -o /dev/null -w "%{http_code} " -H "X-Token: lab-secret" http://127.0.0.1:8080/api/agents; done` — 60개쯤부터 `429`.

## 실제 반영
- 운영에서는 앞단(리버스 프록시·로드밸런서 — 서버 앞에서 요청을 받아 나눠 주는 중계 서버)에서 속도·동시 접속을 제한합니다.
- 접속 로그로 비정상 패턴을 감시합니다. 이 랩의 감사 로그가 그 감시의 축소판입니다.
- 함께 `PATCH_MAX_BODY`로 과대 입력도 막아 메모리 고갈을 줄입니다.
