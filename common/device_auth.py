"""장치별 자격증명과 명령 서명 — 휠체어를 움직이는 경로(관제 → 장치 명령)를 지킨다.

KISA 개발보안 가이드: 설계 SR1-9(보안기능 입력값 검증), 구현 2-1·2-2(인증·인가), 1-15(보안기능 결정에 쓰는 입력값).

**장치 열쇠** = HMAC-SHA256(서버 토큰, "reflexguard:device:<역할>:<이름>:<세대>").
  - 휠체어 PC에는 서버 토큰 대신 자기 장치 열쇠만 둔다 (REFLEXGUARD_DEVICE_NAME·REFLEXGUARD_DEVICE_KEY). 열쇠는 그 이름에만
    통하므로 한 장치가 털려도 다른 장치·서버인 척할 수 없다. 서버는 토큰으로 다시 계산해 확인한다 (열쇠 목록이 필요 없다).
  - 폐기: 서버의 세대 번호를 올린다 (REFLEXGUARD_DEVICE_EPOCHS="wheelchair-01=2,…") → 예전 열쇠는 그때부터 통하지 않는다.
  - 서버 토큰을 가진 같은 PC의 프로그램(run.sh가 켠 것)은 예전처럼 토큰으로 붙는다.

**명령 서명**: 관제가 장치에 보내는 명령마다
    {"type": "command", "command", "value", "target": 장치 이름, "conn": 이번 접속의 nonce, "seq": 1, 2, …, "mac": HMAC}
  장치는 서명이 맞고 · 대상이 자기이며 · 이번 접속에서 자기가 정한 nonce이고 · 순번이 앞보다 클 때만 따른다.
  다른 접속에서 가로챈 명령을 다시 보내거나(nonce 다름), 같은 접속에서 다시 보내거나 순서를 바꾸면(순번) 거부한다.
  시계를 쓰지 않는다 — PC마다 시간이 달라도 된다.

    python -m common.device_auth <이름> [--role webots] [--rotate]   # 서버 PC에서: 그 장치의 열쇠 (setup.sh config로 장치에 넣는다)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any

DEVICE_ROLE = "webots"
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,79}")


def epochs() -> dict[str, int]:
    """서버의 장치 세대 번호 (REFLEXGUARD_DEVICE_EPOCHS="이름=세대,…" — 없으면 1)."""
    found: dict[str, int] = {}
    for item in os.environ.get("REFLEXGUARD_DEVICE_EPOCHS", "").split(","):
        name, _, number = item.strip().partition("=")
        if name and number.isdigit():
            found[name] = int(number)
    return found


def device_key(master: str, role: str, name: str, epoch: int | None = None) -> str:
    epoch = epochs().get(name, 1) if epoch is None else epoch
    return hmac.new(master.encode(), f"reflexguard:device:{role}:{name}:{epoch}".encode(), hashlib.sha256).hexdigest()


def check_device(master: str, role: str, name: str, provided: str) -> bool:
    """장치가 낸 열쇠가 그 역할·이름(지금 세대)의 열쇠인가."""
    if not master or not provided or not NAME_PATTERN.fullmatch(name):
        return False
    return hmac.compare_digest(provided.encode(), device_key(master, role, name).encode())


def own_device() -> tuple[str, str]:
    """이 PC가 장치 열쇠로 붙는다면 (이름, 열쇠). 아니면 ("", "")."""
    name, key = os.environ.get("REFLEXGUARD_DEVICE_NAME", "").strip(), os.environ.get("REFLEXGUARD_DEVICE_KEY", "").strip()
    return (name, key) if name and key else ("", "")


def _canonical(message: dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in message.items() if k != "mac"}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def sign_command(key: str, message: dict[str, Any]) -> dict[str, Any]:
    signed = dict(message)
    signed["mac"] = hmac.new(key.encode(), _canonical(signed), hashlib.sha256).hexdigest()
    return signed


class CommandGuard:
    """장치 쪽: 이번 접속의 nonce를 정하고, 받은 명령을 확인한다 (서명·대상·nonce·순번)."""

    def __init__(self, key: str, name: str) -> None:
        self.key, self.name = key, name
        self.conn = secrets.token_hex(12)
        self.last_seq = 0
        self.rejected = 0

    def new_connection(self) -> str:
        """다시 접속할 때마다 새 nonce — 앞 접속에서 쓰인 명령은 통하지 않는다."""
        self.conn, self.last_seq = secrets.token_hex(12), 0
        return self.conn

    def check(self, message: dict[str, Any]) -> str:
        """따를 명령이면 빈 문자열, 아니면 거부한 까닭."""
        mac = message.get("mac")
        if not isinstance(mac, str):
            why = "unsigned"
        elif not hmac.compare_digest(mac.encode(), sign_command(self.key, message)["mac"].encode()):
            why = "bad signature"
        elif message.get("target") != self.name:
            why = "wrong target"
        elif message.get("conn") != self.conn:
            why = "old connection"
        elif not isinstance(message.get("seq"), int) or message["seq"] <= self.last_seq:
            why = "replayed"
        else:
            self.last_seq = message["seq"]
            return ""
        self.rejected += 1
        return why


def main(argv: list[str] | None = None) -> None:
    import sys
    from common.auth import WeakToken, load_token
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0].startswith("-"):
        raise SystemExit("사용법: python -m common.device_auth <장치 이름> [--role webots] [--rotate]")
    name, role = args[0], DEVICE_ROLE
    if "--role" in args:
        role = args[args.index("--role") + 1]
    if not NAME_PATTERN.fullmatch(name):
        raise SystemExit("장치 이름은 영문·숫자·._@- 로 80자까지")
    try:
        master = load_token()
    except WeakToken as error:
        raise SystemExit(str(error)) from error
    if not master:
        raise SystemExit("이 PC에 서버 토큰이 없습니다 — 서버 PC(관제·연산 서버)에서 실행하세요")
    epoch = epochs().get(name, 1)
    if "--rotate" in args:  # 예전 열쇠를 폐기한다: 세대를 올려 이 PC의 설정 파일에 적는다
        from common import local_env
        epoch += 1
        current = epochs()
        current[name] = epoch
        local_env.write({"REFLEXGUARD_DEVICE_EPOCHS": ",".join(f"{k}={v}" for k, v in sorted(current.items()))},
                        local_env.env_file() or local_env.DEFAULT_FILE)
        print(f"{name}의 세대를 {epoch}로 올렸습니다 — 서버를 다시 켜면 예전 열쇠는 통하지 않습니다.")
    print(f"장치 이름: {name} (역할 {role}, 세대 {epoch})")
    print(f"장치 열쇠: {device_key(master, role, name, epoch)}")
    print("장치 PC에서: bash setup.sh config --device-name " + name + "  (열쇠는 물어볼 때 붙여 넣는다 — 화면에 보이지 않음)")


if __name__ == "__main__":
    main()
