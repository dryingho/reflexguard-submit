"""기록 무결성 — 관제 기록(logs/dashboard_*.jsonl) 줄마다 앞 줄과 이어지는 HMAC 사슬을 단다.

KISA 개발보안 가이드 설계 SR2-7(중요정보 저장)·SW 공급망 체크리스트 17(무결성 데이터). 한 줄을 고치거나, 빼거나, 순서를
바꾸거나, 다른 파일의 줄을 끼우면 그 줄부터 사슬이 맞지 않는다. 파일을 정상으로 닫으면(새 파일로 넘어갈 때·서버를 끌 때)
마지막에 'seal' 줄을 남겨, 뒤를 잘라 낸 것도 드러난다(봉인이 없으면 '쓰는 중이었거나 잘림').

- 열쇠: `logs/log.key` (32바이트 무작위, 이 계정만 읽기 0600). 열쇠가 없는 사람(다른 계정, 기록 사본만 가진 사람)은 사슬을
  다시 계산할 수 없다. 같은 계정으로 열쇠까지 읽을 수 있는 사람은 다시 계산할 수 있다 — 그 경우는 백업·외부 보관으로 막는다.
- 줄 형식: 원래 JSON 끝에 "mac"(HMAC-SHA256 앞 32자). mac = HMAC(열쇠, 앞 줄 mac + 이 줄의 mac 뺀 JSON).
  파일의 첫 사슬값은 HMAC(열쇠, "start:" + 파일 이름) — 파일 이름을 바꿔도 드러난다.

    python tools/verify_logs.py      # 모든 관제 기록 확인
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Iterable

from common.auth import ROOT, private_dir

KEY_FILE = ROOT / "logs" / "log.key"
MAC_CHARS = 32


def load_key(path: Path = KEY_FILE, create: bool = False) -> bytes | None:
    try:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        if not create:
            return None
    private_dir(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    key = secrets.token_bytes(32)
    with os.fdopen(fd, "w", encoding="ascii") as file:
        file.write(key.hex() + "\n")
    return key


def _mac(key: bytes, previous: str, body: str) -> str:
    return hmac.new(key, (previous + body).encode("utf-8"), hashlib.sha256).hexdigest()[:MAC_CHARS]


def _dump(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def start(key: bytes, file_name: str) -> str:
    return _mac(key, "", "start:" + file_name)


class Chain:
    """한 파일의 사슬. line(record)은 mac을 단 한 줄(개행 없이)을 돌려준다."""

    def __init__(self, key: bytes, file_name: str) -> None:
        self.key = key
        self.previous = start(key, file_name)
        self.count = 0

    def line(self, record: dict[str, Any]) -> str:
        body = _dump(record)
        mac = _mac(self.key, self.previous, body)
        self.previous = mac
        self.count += 1
        return body[:-1] + f',"mac":"{mac}"}}'

    def seal(self, time: str) -> str:
        return self.line({"time": time, "kind": "seal", "lines": self.count})


def verify(key: bytes, file_name: str, lines: Iterable[str]) -> tuple[str, int]:
    """(결과, 줄 번호). 결과: 'ok'(봉인까지 맞음) · 'open'(맞지만 봉인 없음 — 쓰는 중이었거나 뒤가 잘림) ·
    'broken'(그 줄에서 사슬이 끊김 — 고침·뺌·끼움) · 'unchained'(사슬이 없는 예전 기록) · 'empty'."""
    previous, sealed, number = start(key, file_name), False, 0
    for number, raw in enumerate(lines, 1):
        raw = raw.rstrip("\n")
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            return "broken", number
        if not isinstance(record, dict) or "mac" not in record:
            return ("unchained", number) if number == 1 else ("broken", number)
        if sealed:
            return "broken", number  # 봉인 뒤에 줄이 붙었다
        mac = str(record.pop("mac"))
        if not hmac.compare_digest(mac, _mac(key, previous, _dump(record))):
            return "broken", number
        previous = mac
        if record.get("kind") == "seal":
            if record.get("lines") != number - 1:
                return "broken", number
            sealed = True
    if number == 0:
        return "empty", 0
    return ("ok" if sealed else "open"), number
