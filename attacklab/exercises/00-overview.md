# 연습 개요

[← 어택랩](../README.md) · 다음: [연습 1 — 인증 없음](01-no-auth.md)

이 문서는 어택랩 연습 다섯 개의 공통 흐름과 준비, 순서를 정리합니다. 랩을 띄운 뒤([설치·실행 안내](../INSTALL.md)) 첫 연습을 시작하기 전에 읽으면 됩니다.

## 연습의 흐름

각 연습은 같은 흐름입니다. 모두 **내 랩(127.0.0.1)**을 대상으로 하며, 목적은 방어를 익히는 것입니다.

1. **약점** — ReflexGuard를 만들며 함께 찾은 설계상의 빈틈.
2. **관찰(보안 테스트)** — 취약한 상태에서 내 랩의 엔드포인트(요청을 받는 주소)에 요청을 보내 보고, `attacklab/logs/audit-*.jsonl`(감사 로그)에서 무슨 일이 일어났는지 봅니다.
3. **패치** — 두 가지 방법으로 막아 봅니다.
   - 앞단 완화: `PATCH_*` 토글(감사 프록시가 막음)
   - 코드 조치: `LAB_VERSION=after` — 보안 조치를 마친 본편 코드로 다시 띄움 (기본 `before`는 조치 전 고정본 `attacklab/vulnerable/`)
4. **검증** — 같은 요청을 다시 보내 막히는지, 로그에 `blocked`로 사유가 남는지 확인합니다.
5. **정상 트래픽은 그대로** — client 티어(사용자 시뮬레이션)는 패치 뒤에도 계속 통과해야 합니다(토큰을 맞춰 주면). 보호가 정상 동작까지 막지 않는지 함께 봅니다.

각 연습 문서 끝의 **실제 반영**은 같은 약점을 본편(실제 코드)에서는 어떻게 다루는지 적은 절입니다.

## 준비

```bash
cd attacklab
docker compose up --build           # 취약한 상태 (LAB_VERSION=before)
# 다른 터미널에서 로그를 띄워 둔다
tail -f logs/audit-*.jsonl
```

## 고치기 전/후 비교

같은 시험을 두 버전에 번갈아 돌리고, 응답 코드와 감사 로그를 나란히 봅니다.

```bash
docker compose down && LAB_VERSION=before docker compose up --build -d   # 조치 전
# … 시험 …
docker compose down && LAB_VERSION=after docker compose up --build -d    # 조치 후
# … 같은 시험 …
```

- 어떤 약점을 어떻게 고쳤는지는 [README의 보안 기능](../../README.md#보안-기능)에 정리돼 있습니다.
- 코드 차이는 `diff -ru attacklab/vulnerable/dashboard dashboard`처럼 바로 볼 수 있습니다.

- 로그를 보기 좋게 보려면: `tail -f logs/audit-*.jsonl | jq .` (jq가 있으면).

## 순서

1. [`01-no-auth.md`](01-no-auth.md) — 인증 없음: 아무나 관제 화면·데이터를 읽는다
2. [`02-command-forgery.md`](02-command-forgery.md) — 명령 위조: 아무나 로봇에 비상 정지·커넥톰 끄기를 보낸다
3. [`03-flooding.md`](03-flooding.md) — 요청 폭주(DoS): 접속·요청을 쏟아 붓는다
4. [`04-plaintext-and-connectome.md`](04-plaintext-and-connectome.md) — 평문 통신과 커넥톰 통신 관찰
5. [`05-cross-site-websocket.md`](05-cross-site-websocket.md) — 다른 사이트의 웹소켓: 체험·조작 포털(8443)을 거쳐 Webots까지 조작 명령이 닿는다

## 안전 수칙

- 이 랩은 내가 만든 시스템을, 내 PC에서, `127.0.0.1`에만 열어 돌립니다. 다른 사람의 시스템에 같은 방법을 쓰지 않습니다.
- 여기서 쓰는 `curl`은 '내 서버가 어떻게 반응하는지' 확인하는 정상적인 점검입니다. 끝나면 `docker compose down`으로 내립니다.
