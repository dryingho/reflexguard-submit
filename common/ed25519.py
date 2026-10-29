"""Ed25519 서명·확인 — RFC 8032 6절의 참조 구현을 그대로 옮긴 것 (외부 패키지 없이; 데이터 서명 확인용).

커넥톰 데이터 목록(connectome/data/MANIFEST.json)의 서명을 확인하는 데 쓴다 (connectome/integrity.py). 속도보다 검증 가능성을
택했다: RFC 8032의 시험 벡터와 openssl의 Ed25519로 교차 확인한다 (tests/test_data_signing.py). 상수 시간 구현이 아니므로
**서명(비밀 열쇠를 쓰는 쪽)은 개발 PC에서만** 하고, 장비에서는 공개 열쇠로 확인만 한다.
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512_modq(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _L


def _add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s: int, p: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    q = (0, 1, 1, 0)  # 중립점
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> bool:
    if (p[0] * q[2] - q[0] * p[2]) % _P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
if _GX is None:  # 일어나지 않는다 — 기준점 계산이 틀리면 바로 멈춘다
    raise RuntimeError("Ed25519 기준점을 계산하지 못함")
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(p: tuple[int, int, int, int]) -> bytes:
    zinv = pow(p[2], _P - 2, _P)
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes) -> tuple[int, int, int, int] | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return x, y, 1, x * y % _P


def _secret_expand(secret: bytes) -> tuple[int, bytes]:
    if len(secret) != 32:
        raise ValueError("Ed25519 비밀 열쇠는 32바이트")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _secret_expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _secret_expand(secret)
    a_point = _compress(_mul(a, _G))
    r = _sha512_modq(prefix + message)
    r_point = _compress(_mul(r, _G))
    h = _sha512_modq(r_point + a_point + message)
    s = (r + h * a) % _L
    return r_point + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    if a is None:
        return False
    r_bytes = signature[:32]
    r = _decompress(r_bytes)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _sha512_modq(r_bytes + public + message)
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
