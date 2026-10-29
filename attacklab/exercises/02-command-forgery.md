# 연습 2 — 명령 위조

[← 연습 1 — 인증 없음](01-no-auth.md) · 다음: [연습 3 — 요청 폭주](03-flooding.md)

조치 전 관제 서버에 인증 없이 비상 정지 명령을 보내 보고, 다른 사이트의 폼이 보낸 것처럼 꾸민 명령도 통하는지 봅니다. 그다음 토큰·읽기 전용·조치 후 코드로 막히는 것을 확인합니다.

KISA 개발보안 가이드: 구현 1-11 크로스사이트 요청 위조 · 2-1 적절한 인증 없는 중요 기능 허용 · 설계 SR1-6

## 약점
관제 화면은 `POST /api/command`로 로봇에 명령을 보냅니다(커넥톰 끄기, 비상 정지, 맵 바꾸기 등).

- 토큰이 없으면 **아무나** 이 명령을 보낼 수 있습니다.
- 공격자가 비상 정지를 걸면 휠체어가 멈추고, 커넥톰을 끄면 보호가 사라집니다.
- 안전에 직접 영향을 주는, 가장 위험한 빈틈입니다.

게다가 조치 전 버전은 본문 형식(`Content-Type`)과 요청을 보낸 사이트(`Origin`, 출처)를 보지 않습니다. 그래서 관제 PC의 사용자가 **다른 사이트를 열어 두기만 해도**, 그 사이트의 폼(`text/plain`)이 사용자 브라우저를 빌려 명령을 보낼 수 있습니다. 이것을 크로스사이트 요청 위조(CSRF)라고 합니다.

## 관찰 (보안 테스트)
취약한 상태에서, 내 랩의 로봇에 명령을 보내 봅니다. 로봇 id는 재접속마다 `webots-N`으로 바뀌므로 먼저 읽어 옵니다.

```bash
RID=$(curl -s http://127.0.0.1:8080/api/agents | grep -o '"id": *"webots-[0-9]*"' | head -1 | grep -o 'webots-[0-9]*')
echo "로봇 id = $RID"
curl -s -X POST http://127.0.0.1:8080/api/command \
  -H "Content-Type: application/json" \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
```

- 응답은 `{"ok": true, "message": "ok"}`입니다.
- client 티어의 로그(`docker compose logs client`)에 `[robot] 대시보드 명령 수신: {...estop...}`이 찍힙니다. 인증 없이 명령이 로봇까지 닿았다는 뜻입니다.
- 로봇은 실제 휠체어처럼 그 명령을 따릅니다. 바로 뒤에 `[robot] {'type': 'estop', ... 'on': True}`가 찍히고 그 자리에 섭니다.
  - 관제 화면의 로봇 카드에 '비상 정지 중'이 뜹니다.
  - '사용자 → 적용'이 `전진 1.00 → 전진 0.00`이 됩니다.
  - 풀려면 같은 명령을 `"value":"off"`로 보냅니다. `connectome`(off → 보호가 꺼짐)·`speed_limit`·`restart`도 같은 식으로 통합니다.
- 감사 로그에는 `http`로 그 POST가 남습니다.
- 존재하지 않는 id로 보내면 `{"ok": false, "message": "no such agent"}`가 옵니다. 그러니 위에서 읽은 `$RID`를 그대로 씁니다.

다른 사이트의 폼이 보낸 것처럼 흉내 내 봅니다 (`text/plain` 본문 + 다른 `Origin`).

```bash
curl -s -o /dev/null -w "다른 사이트의 폼: %{http_code}\n" -X POST http://127.0.0.1:8080/api/command \
  -H "Content-Type: text/plain" -H "Origin: http://other-site.example" \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
```

조치 전(`LAB_VERSION=before`)에는 `200`입니다. 폼으로도 명령이 로봇까지 닿습니다. 감사 로그의 `http` 줄에서 `Origin`과 `Content-Type`을 확인합니다.

## 패치
명령을 막는 방법은 셋입니다. 상황에 맞게 켭니다.

```bash
docker compose down
# (가) 토큰이 있어야 명령을 받는다 (정상 관제자는 통과)
PATCH_REQUIRE_TOKEN=1 PATCH_TOKEN=lab-secret REFLEXGUARD_DASHBOARD_TOKEN=lab-secret \
  docker compose up --build
# (나) 이 랩을 '보기 전용'으로 — 어떤 명령도 받지 않는다
# PATCH_READ_ONLY=1 docker compose up --build
# (다) 코드 조치 — 관제가 JSON 본문·같은 출처의 명령만 받는다 (다른 사이트의 폼을 막음)
# LAB_VERSION=after docker compose up --build
```

## 검증
```bash
RID=$(curl -s -H "X-Token: lab-secret" http://127.0.0.1:8080/api/agents | grep -o '"id": *"webots-[0-9]*"' | head -1 | grep -o 'webots-[0-9]*')
# (가) 토큰 없이 명령 → 막힘
curl -s -o /dev/null -w "토큰 없이 POST: %{http_code}\n" -X POST http://127.0.0.1:8080/api/command \
  -H "Content-Type: application/json" -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"on\"}"
# 토큰 넣고 → 통과
curl -s -o /dev/null -w "토큰 넣고 POST: %{http_code}\n" -X POST http://127.0.0.1:8080/api/command \
  -H "X-Token: lab-secret" -H "Content-Type: application/json" \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"off\"}"
```

- 토큰 없이 → `401`, 로그 `blocked / require_token`.
- `PATCH_READ_ONLY=1`로 켰다면 토큰이 있어도 `403`, 로그 `blocked / read_only`.
- `LAB_VERSION=after`라면:
  - 관찰의 '다른 사이트의 폼' 요청이 `403`(`cross-site request`)입니다.
  - 로그인한 브라우저의 쿠키로 보내더라도, 화면이 받은 CSRF 값(`X-CSRF-Token`)이 없으면 `403`(`csrf`)입니다.
  - `Origin`만 빼고 보내도 `text/plain`이라 `415`(`json only`)입니다.
  - 관제 화면의 버튼(같은 출처의 JSON)은 그대로 됩니다.
- 단, `Origin`도 `Content-Type`도 브라우저 밖 도구(curl)에서는 마음대로 적을 수 있습니다. 그래서 인증과 함께 씁니다. 조치 후 본편은 인증도 언제나 켜져 있습니다(3단계).
  - 토큰 없는 명령은 형식이 맞아도 `401`입니다.
  - 보기 전용 토큰이면 `403`(`view only`)입니다.
  - 조작 토큰으로 보낼 때만 통과합니다:

```bash
curl -s -o /dev/null -w "조작 토큰: %{http_code}\n" -X POST http://127.0.0.1:8080/api/command \
  -H "X-Token: lab-secret" -H "Content-Type: application/json" \
  -d "{\"agent\":\"$RID\",\"command\":\"estop\",\"value\":\"off\"}"
```

  (`LAB_VERSION=after`에서 `$RID`를 읽을 때도 `-H "X-Token: lab-secret"`을 붙입니다.)

## 실제 반영
- 명령 경로는 반드시 인증 뒤에 둡니다. 관제 서버는 이미 토큰을 확인합니다.
- 더해, 공개망에 놓이지 않게 접속 범위를 `127.0.0.1`·내부망으로 좁힙니다.
- 꼭 필요하면 앞단에서 토큰·출처를 한 번 더 확인합니다.
