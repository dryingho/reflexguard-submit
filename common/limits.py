"""자원 한도 — 동시 접속 수, IP별 요청 속도, 인증 실패 잠금. 관제·체험·조작·연산 서버가 같이 쓴다.

KISA 개발보안 가이드: 구현 2-16(반복된 인증시도 제한 기능 부재), 5-2(자원 고갈), 설계 SR2-2(인증 수행 제한).
"""

from __future__ import annotations

import threading
import time
from typing import Any

AUTH_FAILURES = 5        # 이만큼 틀리면
AUTH_WINDOW_S = 60.0     # (이 시간 안에)
AUTH_LOCK_S = 60.0       # 그 주소를 이만큼 잠근다


class ConnectionLimit:
    """socketserver에 섞어 쓴다 (`class S(ConnectionLimit, ThreadingTCPServer)`): 동시 접속이 모두 합쳐 max_connections,
    한 주소에서 max_per_ip를 넘으면 새 접속은 받자마자 닫는다. 접속마다 스레드가 하나씩이라, 접속을 쏟아붓거나 느리게 붙잡아
    (slowloris) 스레드·메모리를 고갈시키지 못하게. 한 주소가 자리를 다 차지해 다른 사람을 막지도 못하게."""

    max_connections = 64
    max_per_ip = 16
    refused_connections = 0
    _slots: threading.BoundedSemaphore | None = None
    _per_ip: dict[str, int] | None = None
    _per_ip_lock = threading.Lock()

    def process_request(self, request: Any, client_address: Any) -> None:
        if self._slots is None:  # serve_forever 스레드 하나에서만 불린다
            self._slots = threading.BoundedSemaphore(self.max_connections)
            self._per_ip = {}
        ip = str(client_address[0]) if client_address else ""
        with self._per_ip_lock:
            crowded = self._per_ip.get(ip, 0) >= self.max_per_ip  # type: ignore[union-attr]
            if not crowded and self._slots.acquire(blocking=False):
                self._per_ip[ip] = self._per_ip.get(ip, 0) + 1  # type: ignore[index, union-attr]
            else:
                crowded = True
        if crowded:
            self.refused_connections += 1
            self.shutdown_request(request)  # type: ignore[attr-defined]
            return
        try:
            super().process_request(request, client_address)  # type: ignore[misc]
        except Exception:
            self._release(ip)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)  # type: ignore[misc]
        finally:
            self._release(str(client_address[0]) if client_address else "")

    def _release(self, ip: str) -> None:
        with self._per_ip_lock:
            if self._per_ip is not None and ip in self._per_ip:
                self._per_ip[ip] -= 1
                if not self._per_ip[ip]:
                    del self._per_ip[ip]
        if self._slots is not None:
            self._slots.release()


class RateLimiter:
    """키(IP)마다 토큰 버킷: 초당 rate개, 한 번에 burst개까지. 넘으면 allow()가 거짓."""

    def __init__(self, rate: float, burst: float) -> None:
        self.rate, self.burst = rate, burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            if len(self._buckets) > 4096:  # 오래 조용한 키는 버린다 (가득 찬 버킷과 같으므로)
                idle = self.burst / self.rate
                self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < idle}
            return allowed


class FailureLock:
    """인증 실패를 키(IP)마다 센다: window_s 안에 limit번 틀리면 lock_s 동안 잠근다. 맞히면 지운다."""

    def __init__(self, limit: int = AUTH_FAILURES, window_s: float = AUTH_WINDOW_S, lock_s: float = AUTH_LOCK_S) -> None:
        self.limit, self.window_s, self.lock_s = limit, window_s, lock_s
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            if until and until <= now:
                del self._locked_until[key]
            return until > now

    def failed(self, key: str, now: float | None = None) -> bool:
        """실패를 하나 센다. 이번에 잠겼으면 참."""
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window_s] + [now]
            if len(recent) >= self.limit:
                self._failures.pop(key, None)
                self._locked_until[key] = now + self.lock_s
                return True
            self._failures[key] = recent
            if len(self._failures) > 4096:
                self._failures = {k: v for k, v in self._failures.items() if now - v[-1] < self.window_s}
            return False

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
