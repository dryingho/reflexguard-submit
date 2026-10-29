# 어택랩 시연 대본 — 명령어 포함

[← 어택랩 README](README.md) · 실습 문서: [연습 1 — 인증 없음](exercises/01-no-auth.md) · [연습 2 — 명령 위조](exercises/02-command-forgery.md)

시연 영상(`attacklab_demo.mp4`, 약 90초)에서 한 것을 **그대로 다시 할 수 있게** 명령어와 결과를 순서대로 적었습니다. 같은 요청을 **보안 조치 전 고정본**(`LAB_VERSION=before`)과 **조치 후 본편**(`LAB_VERSION=after`)에 보내 결과를 비교합니다.

> **범위·안전**: 내 PC의 도커 컨테이너 안에서, `127.0.0.1`에만 열어 돌리는 내 랩에만 보냅니다. 다른 사람의 시스템이나 사내망·인터넷에는 쓰지 않습니다.

한눈에 보기:

| # | 같은 요청 | 조치 전 | 조치 후 | KISA |
|---|---|---|---|---|
| ① | 토큰 없이 로봇 목록 읽기 | `200` 다 읽힘 | `401` | 구현 2-1 |
| ② | 토큰 없이 비상 정지 | `200` 로봇이 섬 | `401` | 구현 2-1 |
| ③ | 다른 사이트의 폼처럼(`text/plain` + 다른 `Origin`) 커넥톰 끄기 | `200` 보호가 꺼짐 | `403` | 구현 1-11 |
| ④ | 보기 전용 토큰으로 비상 정지 | — | `403` | 설계 SR1-6 |
| ⑤ | 조작 토큰(정당한 관제자)으로 비상 정지 | — | `200` 로봇이 섬 | — |

## 준비

도커 설치와 본편 끄기(포트 비우기)는 [설치·실행 안내](INSTALL.md)를 따릅니다. 창을 셋 엽니다.

| 창 | 하는 일 |
|---|---|
| 브라우저 | 관제 화면 <http://127.0.0.1:8080/live> — 로봇 카드와 사건 기록을 봅니다 |
| 터미널 1 | 요청을 보냅니다 (아래 명령) |
| 터미널 2 | 로그를 봅니다 (감사 프록시 · 로봇) |

터미널 2 — 저장소의 `attacklab` 폴더에서:

```bash
tail -f logs/audit-*.jsonl | grep --line-buffered '/api/command'
```

```bash
docker compose logs -f client | grep --line-buffered "\[robot\] {"
```

(둘 중 하나씩, 또는 창을 하나 더 열어 둘 다)

## 1부 — 조치 전 (`LAB_VERSION=before`)

```bash
cd attacklab
docker compose up --build -d          # 기본이 조치 전 고정본
B=http://127.0.0.1:8080
```

브라우저로 <http://127.0.0.1:8080/live>를 엽니다. 로그인 없이 바로 열리고, 로봇 카드 `wheelchair-01@attacklab`이 보입니다.

### ① 토큰 없이 로봇 목록 읽기

```bash
curl -s -w '\n%{http_code}\n' $B/api/agents | tail -c 300
```

- **결과**: `200`. 로봇 이름·위치·상태가 그대로 읽힙니다.

로봇 id를 받아 둡니다 (접속할 때마다 `webots-N`으로 바뀝니다):

```bash
RID=$(curl -s $B/api/agents | grep -o '"id": *"webots-[0-9]*"' | head -1 | grep -o 'webots-[0-9]*'); echo $RID
```

### ② 토큰 없이 비상 정지

```bash
curl -s -w '\n%{http_code}\n' -X POST $B/api/command \
  -H 'Content-Type: application/json' \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
```

- **결과**: `200` `{"ok": true, "message": "ok"}`.
- **관제 화면**: 로봇 카드에 `비상 정지 중`. '사용자 → 적용'이 `전진 1.00 → 전진 0.00`이 됩니다.
- **로봇 로그**: `[robot] {'type': 'estop', ..., 'on': True}` — 인증 없는 명령을 따라 섰습니다.
- **감사 로그** (시연 때 실제로 남은 줄, 줄임):

```json
{"ts":"…21:41:59.509","kind":"http","method":"POST","path":"/api/command","headers":{"Content-Type":"application/json",…},"body":"{\"agent\": \"webots-1\", \"command\": \"estop\", \"value\": \"on\"}"}
{"ts":"…21:41:59.510","kind":"http-reply","path":"/api/command","upstream":"HTTP/1.0 200 OK"}
```

### ③ 다른 사이트의 폼인 척 커넥톰 끄기 (CSRF)

다른 사이트의 `<form>`이 관제 PC의 브라우저를 빌려 보내는 요청과 같은 모양(`text/plain` 본문 + 다른 `Origin`)입니다.

```bash
curl -s -w '\n%{http_code}\n' -X POST $B/api/command \
  -H 'Content-Type: text/plain' -H 'Origin: http://other-site.example' \
  -d "{\"agent\":\"$RID\",\"command\":\"connectome\",\"value\":\"off\"}"
```

- **결과**: `200`. 조치 전 관제 서버는 출처와 형식을 보지 않습니다.
- **관제 화면**: 스위치 줄이 `커넥톰 꺼짐`으로 바뀝니다. 휠체어의 회피 반사(보호)가 꺼진 것입니다.
- **로봇 로그**: `[robot] {'type': 'connectome', ..., 'on': False}`
- **감사 로그**: 헤더에 `"Origin":"http://other-site.example"`, `"Content-Type":"text/plain"`이 남고 답은 `HTTP/1.0 200 OK`입니다.

되돌리기:

```bash
for c in '"estop","value":"off"' '"connectome","value":"on"'; do
  curl -s -X POST $B/api/command -H 'Content-Type: application/json' -d "{\"agent\":\"$RID\",\"command\":$c}"; echo
done
```

## 2부 — 조치 후 (`LAB_VERSION=after`)

```bash
docker compose down
LAB_VERSION=after docker compose up --build -d
```

- 브라우저로 <http://127.0.0.1:8080/live>를 열면 로그인 화면입니다. 조작 토큰 `lab-secret`(compose의 `REFLEXGUARD_TOKEN` 기본값)으로 로그인합니다.
- 보기 전용 토큰과 로봇 id를 받아 둡니다. 이제 목록을 읽을 때도 토큰이 필요합니다.

```bash
VIEW=$(docker compose exec -T server python -m common.auth | sed -n 's/^보기 전용 토큰: *//p')
RID=$(curl -s -H 'X-Token: lab-secret' $B/api/agents | grep -o '"id": *"webots-[0-9]*"' | head -1 | grep -o 'webots-[0-9]*'); echo $RID
```

### ① 토큰 없이 로봇 목록 읽기

```bash
curl -s -w '\n%{http_code}\n' $B/api/agents
```

- **결과**: `401` `{"error": "login required"}`. 인증이 언제나 켜져 있습니다.

### ② 토큰 없이 비상 정지

```bash
curl -s -w '\n%{http_code}\n' -X POST $B/api/command \
  -H 'Content-Type: application/json' \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
```

- **결과**: `401` `{"error": "login required"}`. 형식이 맞아도 토큰이 없으면 막힙니다.
- **감사 로그**: `"upstream":"HTTP/1.0 401 Unauthorized"`. 로봇 로그에는 아무것도 없습니다(로봇까지 가지 않음).

### ③ 다른 사이트의 폼인 척 커넥톰 끄기

```bash
curl -s -w '\n%{http_code}\n' -X POST $B/api/command \
  -H 'Content-Type: text/plain' -H 'Origin: http://other-site.example' \
  -d "{\"agent\":\"$RID\",\"command\":\"connectome\",\"value\":\"off\"}"
```

- **결과**: `403` `{"error": "cross-site request"}`. 다른 출처에서 온 명령은 받지 않습니다.
- `Origin`을 빼고 토큰을 넣어도 `text/plain`이면 `415` `{"error": "json only"}`입니다 (JSON 본문만 받음).
- 로그인한 브라우저의 쿠키로 보내더라도 화면이 받은 CSRF 값(`X-CSRF-Token`)이 없으면 `403` `csrf`입니다.

### ④ 보기 전용 토큰으로 비상 정지

```bash
curl -s -w '\n%{http_code}\n' -X POST $B/api/command \
  -H "X-Token: $VIEW" -H 'Content-Type: application/json' \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
```

- **결과**: `403` `{"error": "view only"}`. 볼 수만 있는 권한은 명령을 보낼 수 없습니다.
- **감사 로그**: `"X-Token":"<가림>"`. 감사 로그에도 토큰 값은 남기지 않습니다.

### ⑤ 조작 토큰(정당한 관제자)으로 비상 정지

```bash
curl -s -w '\n%{http_code}\n' -X POST $B/api/command \
  -H 'X-Token: lab-secret' -H 'Content-Type: application/json' \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
```

- **결과**: `200` `{"ok": true, "message": "ok"}`.
- **관제 화면**: 로봇 카드에 `비상 정지 중`. 사건 기록에 `명령: 비상 정지 켜기` → `비상 정지 걸림`.
- **로봇 로그**: `[robot] {'type': 'estop', ..., 'on': True}`
  - 조치 후 본편은 관제가 이 명령에 서명(`mac`)·순번(`seq`)을 붙여 보냅니다.
  - 로봇은 서명을 확인한 뒤에만 따릅니다(위조·재전송이면 `command_rejected`로 알림).

되돌리고 끝내기:

```bash
curl -s -X POST $B/api/command -H 'X-Token: lab-secret' -H 'Content-Type: application/json' \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"off\"}"; echo
docker compose down
```

## 감사 로그를 나중에 보기

감사 프록시는 막힌 요청과 통한 요청을 **모두** 같은 꼴로 남깁니다 (`attacklab/logs/audit-*.jsonl`, 한 줄에 하나).

```bash
# 명령 요청과 그 답만
grep -h '"/api/command"' logs/audit-*.jsonl | grep -E '"kind":"(http|http-reply|blocked)"' | cut -c1-220
# 막힌 답만 (4xx)
grep -h '"kind":"http-reply"' logs/audit-*.jsonl | grep -E 'HTTP/1\.[01] 4[0-9]{2}'
```

| 로그 종류 `kind` | 뜻 |
|---|---|
| `http` | 받은 요청 (방법·주소·헤더·본문 앞부분 — 토큰·쿠키는 `<가림>`) |
| `http-reply` | 뒤의 관제 서버가 돌려준 답의 첫 줄 (`HTTP/1.0 401 Unauthorized` 등) |
| `blocked` | 프록시의 앞단 패치(`PATCH_*`)가 막은 것 — 이 대본에서는 쓰지 않음 ([연습 1](exercises/01-no-auth.md)의 패치) |

## 영상은 어떻게 찍었나

- 이 PC에는 도커가 없어, 저장소를 임시 폴더에 복사해 랩과 같은 구성을 직접 띄웠습니다: 관제 서버 ← 감사 프록시 ← 연산 서버·로봇 시뮬레이션(`client/robot_sim.py`).
- 모두 `127.0.0.1`에만 열었고, 포트는 쓰고 있던 본편과 겹치지 않게 바꿨습니다(관제 `28080`). 위 명령의 `B=http://127.0.0.1:8080`이 영상에서는 `28080`입니다.
- 왼쪽은 실제 관제 화면을 머리 없는 Edge로 찍은 것입니다. 오른쪽에는 위 `curl`을 실제로 보낸 응답과 감사 로그·로봇 로그를 보였습니다.
- 응답 코드는 위 표와 같았습니다: 조치 전 `200 · 200 · 200`, 조치 후 `401 · 401 · 403 · 403 · 200`.
