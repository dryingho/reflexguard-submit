# 연습 1 — 인증 없음

[← 연습 개요](00-overview.md) · 다음: [연습 2 — 명령 위조](02-command-forgery.md)

조치 전 관제 서버에 토큰(로그인용 비밀 문자열) 없이 화면과 데이터를 읽어 보고, 토큰을 켜거나 조치 후 코드로 바꿔 막히는 것을 확인합니다.

KISA 개발보안 가이드: 구현 2-1 적절한 인증 없는 중요 기능 허용 · 2-2 부적절한 인가 · 2-12(주소에 남는 토큰) · 설계 SR2-1

## 약점
- 조치 전 관제 서버는 기본으로 토큰을 요구하지 않습니다.
- 그래서 같은 네트워크의 누구나 관제 화면과 실시간 데이터(로봇 위치·상태·사건 기록)를 읽을 수 있습니다.
- 운영 중인 휠체어의 위치가 그대로 노출됩니다.

## 관찰 (보안 테스트)
취약한 상태(패치 없이 `docker compose up`)에서, 토큰 없이 내 랩에 요청해 봅니다.

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/live        # 화면
curl -s http://127.0.0.1:8080/api/agents | head -c 300; echo               # 접속한 로봇 목록(데이터)
```

둘 다 `200`과 데이터가 그대로 옵니다. 감사 로그를 보면 `kind:"http"`로 내 요청이, `http-reply`로 `200`이 남습니다. 누가 언제 무엇을 읽어 갔는지 기록은 되지만, **막지는 않습니다**.

## 패치
(가)·(나)는 조치 전 버전에서 토큰을 켜는 방법, (다)는 코드 조치입니다.

(가)+(나) 앞단과 앱 모두 토큰을 요구하게 켜고 다시 올립니다.

```bash
docker compose down
PATCH_REQUIRE_TOKEN=1 PATCH_TOKEN=lab-secret REFLEXGUARD_DASHBOARD_TOKEN=lab-secret \
  docker compose up --build
```

- `PATCH_REQUIRE_TOKEN`은 프록시(앞단)에서 막고, `REFLEXGUARD_DASHBOARD_TOKEN`은 관제 서버 자체(앱단)가 확인합니다. 두 값을 같게 둡니다.
- 조치 전 관제 서버는 토큰을 켜도 **화면과 GET API(`/api/agents`·`/api/stream`)는 열려 있고** 명령만 막습니다. 앞단(가)이 없으면 데이터는 여전히 읽힙니다.

(다) 코드 조치 — 조치 후 본편은 인증이 언제나 켜져 있습니다 (토큰은 compose의 `REFLEXGUARD_TOKEN`, 기본 `lab-secret`).

```bash
docker compose down
LAB_VERSION=after docker compose up --build -d
```

## 검증
```bash
curl -s -o /dev/null -w "토큰 없이: %{http_code}\n" http://127.0.0.1:8080/api/agents
curl -s -o /dev/null -w "토큰 넣고: %{http_code}\n" -H "X-Token: lab-secret" http://127.0.0.1:8080/api/agents
```

- 토큰 없이 → `401`, 로그에 `kind:"blocked"`, `by:"require_token"`.
- 토큰 넣고 → `200`.
- client 티어(사용자 시뮬레이션)는 같은 토큰을 쓰므로 계속 접속합니다 — 화면에 로봇이 그대로 보이는지 확인하세요.
- (다) `LAB_VERSION=after`:
  - 토큰 없이 열면 화면은 로그인 화면(`401`), API도 `401`입니다.
  - 브라우저로 <http://127.0.0.1:8080/live>를 열어 `lab-secret`으로 로그인하면, 그 뒤로는 쿠키(로그인 상태를 브라우저가 기억하는 값)로 다닙니다.
  - 보기 전용 토큰(`docker compose exec server python -m common.auth`)으로 들어오면 명령 버튼이 꺼지고 `POST /api/command`는 `403`입니다.
  - `?token=`으로 열어도 서버가 곧바로 쿠키로 바꾸고 주소에서 토큰을 지웁니다 (`303`). 감사 로그의 주소·본문의 토큰 값은 `<가림>`으로 남습니다.

## 실제 반영
본편은 [3단계](../README.md#본편의-보안-조치-단계) 조치로 인증을 기본으로 켰습니다.

- 토큰이 없으면 서버가 무작위로 만들어 `logs/token`(0600, 나만 읽는 파일)에 둡니다.
- 약한 토큰은 받지 않습니다.
- 브라우저는 로그인 쿠키(HttpOnly·SameSite=Strict·만료+서명)로 다닙니다.
- 보기 전용 토큰으로 권한을 나눕니다.
- 연산 서버(8765)·체험·조작 포털(8443)도 같은 토큰을 요구합니다.
