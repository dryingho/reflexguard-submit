"""커넥톰 데이터 무결성 — 서명된 목록으로 확인한 뒤에만 읽는다.

KISA 개발보안 가이드: 구현 2-10(부적절한 전자서명 확인)·2-15(무결성 검사 없는 코드 다운로드)·5-5(신뢰할 수 없는 데이터),
SW 공급망 체크리스트 17·22(무결성 데이터 전달·확인).

    connectome/data/MANIFEST.json   {"version": 1, "key": "<열쇠 id>", "files": {"malecns-circuit.json": "<sha256>", …}}
    connectome/data/MANIFEST.sig    MANIFEST.json 바이트의 Ed25519 서명 (base64)

읽을 때: 목록의 서명을 믿는 공개 열쇠(connectome/trusted_keys.py — 데이터 폴더 밖, 코드에)로 확인 → 파일을 심볼릭 링크·특수 파일
없이(O_NOFOLLOW) 크기 상한 안에서 읽어 → SHA-256이 목록과 같을 때만 그 바이트를 돌려준다. 하나라도 어긋나면 IntegrityError —
커넥톰을 만들지 않는다 (휠체어는 판단이 없어 움직이지 않는다).

데이터를 다시 뽑았으면 개발 PC에서 다시 서명한다: python tools/sign_data.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from common import ed25519
from connectome.trusted_keys import TRUSTED_KEYS

DATA_DIR = Path(__file__).resolve().parent / "data"
MANIFEST_NAME, SIGNATURE_NAME = "MANIFEST.json", "MANIFEST.sig"
MAX_BYTES = 16 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.json")


class IntegrityError(RuntimeError):
    """데이터가 서명된 목록과 다르다 (고쳐졌거나, 서명하지 않은 열쇠이거나, 링크·특수 파일)."""


def key_id(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()[:16]


def read_regular(path: Path, limit: int = MAX_BYTES) -> bytes:
    """심볼릭 링크를 따라가지 않고, 일반 파일만, limit 바이트까지 읽는다."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise IntegrityError(f"{path.name}: 열 수 없음 ({error.strerror})") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise IntegrityError(f"{path.name}: 일반 파일이 아님")
        if info.st_size > limit:
            raise IntegrityError(f"{path.name}: 너무 큼 ({info.st_size} 바이트)")
        with os.fdopen(fd, "rb", closefd=False) as file:
            data = file.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise IntegrityError(f"{path.name}: 너무 큼")
    return data


def manifest(data_dir: Path = DATA_DIR) -> dict[str, str]:
    """서명을 확인한 목록 {파일 이름: sha256}."""
    body = read_regular(data_dir / MANIFEST_NAME, 1024 * 1024)
    try:
        signature = base64.b64decode(read_regular(data_dir / SIGNATURE_NAME, 1024).strip(), validate=True)
        parsed = json.loads(body)
    except ValueError as error:
        raise IntegrityError("목록·서명 형식이 틀림") from error
    if not isinstance(parsed, dict) or parsed.get("version") != 1 or not isinstance(parsed.get("files"), dict):
        raise IntegrityError("목록 형식이 틀림")
    public_hex = TRUSTED_KEYS.get(str(parsed.get("key", "")))
    if public_hex is None:
        raise IntegrityError(f"믿지 않는 열쇠로 서명됨 ({parsed.get('key')!r}) — connectome/trusted_keys.py")
    if not ed25519.verify(bytes.fromhex(public_hex), body, signature):
        raise IntegrityError("목록의 서명이 맞지 않음 — 목록이 고쳐졌거나 다른 열쇠로 서명됨")
    files = parsed["files"]
    if not all(isinstance(k, str) and NAME.fullmatch(k) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for k, v in files.items()):
        raise IntegrityError("목록의 항목 형식이 틀림")
    return dict(files)


def read_verified(name: str, data_dir: Path = DATA_DIR) -> bytes:
    """서명된 목록에 있는 파일을, 해시가 맞을 때만 돌려준다."""
    expected = manifest(data_dir).get(name)
    if expected is None:
        raise IntegrityError(f"{name}: 서명된 목록에 없음")
    data = read_regular(data_dir / name)
    if hashlib.sha256(data).hexdigest() != expected:
        raise IntegrityError(f"{name}: 내용이 서명된 목록과 다름 — 고쳐졌거나 다시 뽑고 서명하지 않음")
    return data


def load_json(name: str, data_dir: Path = DATA_DIR) -> object:
    return json.loads(read_verified(name, data_dir))
