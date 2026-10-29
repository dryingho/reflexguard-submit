# 연습 5 — 다른 사이트의 웹소켓 (체험·조작 포털)

[← 연습 4 — 평문 통신](04-plaintext-and-connectome.md) · [연습 개요](00-overview.md)

다른 사이트가 연 웹소켓(브라우저와 서버가 계속 이어진 채 주고받는 통로)이 체험·조작 포털을 거쳐 Webots까지 닿는지 확인하고, 출처 확인으로 막아 봅니다.

KISA 개발보안 가이드: 구현 1-11 크로스사이트 요청 위조 · 설계 SR1-6 웹 기반 중요 기능 수행 요청 유효성 검증

## 약점
- 체험·조작 포털(8443)은 조작 화면의 웹소켓을 Webots로 그대로 이어 줍니다.
- 이때 **그 웹소켓을 어느 사이트가 열었는지(`Origin` 헤더)를 보지 않습니다.**
- 브라우저의 같은 출처 정책(SOP — 다른 사이트의 응답을 읽지 못하게 막는 규칙)은 웹소켓을 막아 주지 않습니다.
- 그래서 사용자가 다른 사이트를 열어 두기만 해도, 그 사이트의 스크립트가 사용자 브라우저를 통해 `ws://<조작 서버>/`에 붙어 조작 명령(커넥톰 끄기, 키 입력 등)을 보낼 수 있습니다.

랩에는 Webots 대신 **Webots 대역**(`server/webots_stub.py`, Webots 흉내만 내는 가짜 프로그램)이 서 있어, 중계된 웹소켓의 `Origin`과 받은 명령을 모두 기록합니다.

## 관찰 (보안 테스트)
`curl`로 웹소켓 연결 요청만 보내 봅니다. `Origin`을 다른 사이트로 적습니다.

```bash
curl -s -i -m 3 http://127.0.0.1:8443/robot_windows/reflexguard/ \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  -H "Origin: http://other-site.example" | head -1
```

- 결과 `HTTP/1.1 101 Switching Protocols` — 다른 사이트의 연결이 받아들여졌다는 뜻입니다.
- `grep webots-connect logs/audit-webots-*.jsonl | tail -1` — `"origin":"http://other-site.example"`인 연결이 Webots(대역)까지 닿은 기록.
- `grep '"path":"/robot_windows' logs/audit-portal-*.jsonl | tail -1` — 앞단 프록시에 남은 같은 요청(헤더의 `Origin` 포함).

> 실제 조작 화면을 열어(<http://127.0.0.1:8443/>) 키를 눌러 보면, 정상 화면이 보낸 명령도 `webots-command`로 남습니다. 정상 연결은 `origin`이 `http://127.0.0.1:8443`입니다 — 둘을 비교해 보세요.

## 패치
(가) **앞단 완화** — 프록시가 `Origin`이 목록에 없는 요청을 막습니다.

```bash
docker compose down
PATCH_ALLOWED_HOSTS=127.0.0.1,localhost docker compose up --build -d
```

(나) **코드 조치** — 보안 조치를 마친 본편으로 띄웁니다. 체험·조작 포털이 직접 `Origin`을 확인합니다 (프록시를 우회해도 막힘).

```bash
docker compose down
LAB_VERSION=after docker compose up --build -d
```

## 검증
관찰의 `curl`을 그대로 다시 보냅니다.

- (가): `403`, `audit-portal-*.jsonl`에 `blocked / allowed_hosts`. Webots 대역에는 아무것도 남지 않습니다.
- (나):
  - 로그인 없이는 `401`([3단계](../README.md#본편의-보안-조치-단계) 인증)입니다.
  - 로그인한 사용자의 브라우저라도 다른 사이트가 연 것이면 `403`(출처 확인)입니다. 확인하려면 관찰의 `curl`에 `-H "X-Token: lab-secret"`을 더해 보세요. 그래도 `403`입니다.
  - Webots 대역에 `webots-connect`가 생기지 않습니다.
- 두 경우 모두 정상 조작 화면(<http://127.0.0.1:8443/>)은 그대로 연결돼야 합니다.

## 실제 반영
- 웹소켓·명령 API는 `Origin`이 내 화면의 출처일 때만 받습니다.
- 출처 확인은 인증을 대신하지 않습니다. 브라우저 밖(스크립트·도구)에서는 `Origin`을 마음대로 적을 수 있으므로, 인증(토큰)과 함께 씁니다.
