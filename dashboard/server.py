"""관제 대시보드 서버: Webots 로봇(컨트롤러)과 커넥톰 연산 서버의 상태·사건을 모아 보여 주고, 명령을 내려보낸다.

    python dashboard/server.py                 # 화면 http://localhost:8080 , 로봇·서버 접속 127.0.0.1:8766
    python dashboard/server.py --public        # 다른 PC에서도 접속 (0.0.0.0) — 화면은 HTTPS, 로봇·서버 접속은 TLS(등록된 클라이언트 인증서)
    REFLEXGUARD_TOKEN=비밀값 python dashboard/server.py   # 토큰 (안 주면 logs/token을 만든다): 로봇·서버는 hello에(또는 장치 열쇠), 브라우저는 로그인에 같은 값

로봇·서버 쪽: REFLEXGUARD_DASHBOARD=127.0.0.1:8766 (common/dashboard_link.py)

로봇·서버 ↔ 대시보드 (접속 포트 8766, TCP 위에 한 줄에 JSON 하나)
    → {"type": "hello", "role": "webots" | "brain" | "phone", "name": "...", ...}  접속하자마자. 로봇은 맵·맵 목록·맵 모양도 싣는다
                                                                             (phone: 휴대폰 체험 서버가 휴대폰마다 — demo/server.py, 명령은 받지 않는다)
    → {"type": "state", ...}                                                 초당 몇 번. 루밍 동작 보기(loom: 로봇·휴대폰의 칸별 값,
                                                                             neurons: 연산 서버의 접속마다 LPLC2·DNp01 발화율)는 화면에만
    → {"type": "event", "event": {...}}                                      회피 반사 작동·충돌·스위치·접속 등 (기록에 남는다)
    ← {"type": "command", "command": "...", "value": "..."}                  화면에서 누른 명령
       로봇: connectome on|off, safety on|off, estop on|off, restart, map <이름>, sensor camera|range (위험 인식 센서), speed_limit 100|80|60|40|20 (%)
       연산 서버: reset_all
브라우저 ↔ 대시보드 (화면 포트 8080, HTTP)
    GET  /live · /analysis · /trials · /trials/<시험>   관제 화면 (dashboard/web/index.html — / 도 같은 화면, 실시간)
    GET  /api/stream       실시간 갱신 (Server-Sent Events): snapshot(처음 한 번) · agent · event · removed(끊긴 지 오래돼 치운 것)
    GET  /api/me           이 로그인의 권한(control|view)과 CSRF 값 (명령의 X-CSRF-Token)
    GET  /api/agents       접속 중(이었던) 로봇·서버 목록
    GET  /api/events       최근 사건
    GET  /api/analysis     기록 분석 탭: 모든 기록 파일의 회피 반사 작동·충돌·도착(맵·위치 포함)과 맵 모양
    GET  /api/trials       시험 결과 탭: 도착 시험(tools/goal_trials.py)마다 조건·방식·모든 판 (logs/trials/trials_*.json)
    POST /api/command      {"agent": "<id>", "command": "...", "value": "..."}
기록: logs/dashboard_<날짜-시각>.jsonl — 접속·사건·명령 전부, 상태는 로봇·서버마다 1초에 한 번 (바뀐 칸만 — 아래)
     켤 때마다 새 파일에 쓰고, 지난 파일들의 사건(최근 HISTORY_EVENTS개)을 읽어 화면의 사건 기록에 '지난 기록'으로 이어 보여 준다.
     용량 줄이기: 상태 줄에는 직전 상태 줄에서 바뀐 칸만 쓴다(t·map은 늘, FULL_STATE_EVERY_S마다 한 번은 전부 — "full": true).
     바뀐 것이 없으면 쓰지 않는다. 다 쓴 기록 파일은 gzip으로 압축한다(.jsonl.gz — 켤 때 지난 파일들, 지금 파일이
     ROTATE_BYTES를 넘으면 새 파일로 넘어가며 앞 파일). 지난 기록 읽기·기록 분석은 압축 파일도 읽는다.
     Webots를 닫아도 기록이 이어지게 하려면 관제 서버를 계속 켜 둔다 (scripts/run.sh는 Webots를 닫아도 끄지 않는다).
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import errno
import gzip
import hmac
import json
import os
import queue
import re
import signal
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]  # 프로젝트 폴더
sys.path.insert(0, str(ROOT))  # python dashboard/server.py로 바로 실행해도 프로젝트 패키지(simulation 맵 모양)를 찾게

from common.auth import CONTROL, WeakToken, load_token, private_dir  # noqa: E402
from common.control import SPEED_LIMITS  # noqa: E402
from common.device_auth import check_device, device_key, sign_command  # noqa: E402
from common.mtls import check_peer, enable as enable_mtls, handle_enroll  # noqa: E402
from common.limits import ConnectionLimit, FailureLock, RateLimiter  # noqa: E402
from common.log_chain import Chain, load_key  # noqa: E402
from common.tls import TLSServerMixin, certificate, file_fingerprint, lan_addresses, server_context  # noqa: E402
from common.web_auth import WebAuthMixin  # noqa: E402
from common.web_guard import is_json, page_csp, same_origin  # noqa: E402
PAGE = ROOT / "dashboard" / "web" / "index.html"
LOG_DIR = ROOT / "logs"
DEFAULT_WEB_PORT = 8080
DEFAULT_AGENT_PORT = 8766
MAX_LINE = 512 * 1024
MAX_STREAMS = 32          # 동시에 여는 실시간 화면(SSE) 수 상한 — 연결마다 스레드 하나 (KISA 구현 5-2)
ANALYSIS_CACHE_S = 10.0   # 기록 분석(모든 기록 파일을 읽는다)은 이 동안 같은 결과를 돌려준다 — 거듭 불러 CPU를 태우지 못하게
_MISSING = object()
HISTORY_EVENTS = 2000   # 켤 때 지난 기록 파일에서 읽어 오는 사건 수 (최근 것부터)
FULL_STATE_EVERY_S = 60.0   # 상태 줄은 바뀐 칸만 쓰되, 이 간격(초)마다 한 번은 전부 쓴다 (중간부터 읽어도 되살릴 수 있게)
ALWAYS_STATE_KEYS = ("t", "map")  # 상태 줄에 늘 쓰는 칸 (기록 분석이 상태 줄의 맵을 읽는다)
# 화면에만 보내고 기록 파일에는 쓰지 않는 칸: 루밍 동작 보기(로봇·휴대폰의 칸별 값 loom, 연산 서버의 뉴런 발화율 neurons) —
# 늘 바뀌고 커서(약 5 KB) 매초 쓰면 기록이 하루 수백 MB가 된다
SCREEN_ONLY_STATE_KEYS = ("loom", "neurons")
ROTATE_BYTES = 64 * 1024 * 1024   # 지금 기록 파일이 이보다 커지면 새 파일로 넘어가고 앞 파일은 압축한다
PAGE_PATHS = ("/", "/index.html", "/live", "/analysis", "/trials")  # 관제 화면 주소 (/trials/<시험>도)
SNAPSHOT_EVENTS = 1000  # 화면을 열 때 한 번에 보내는 사건 수
DISCONNECTED_KEEP_S = 300.0  # 끊긴 로봇·서버를 '끊김' 카드로 이만큼(초) 보여 준 뒤 목록에서 뺀다 (사건 기록에는 남는다)
PRUNE_EVERY_S = 5.0         # 끊긴 것을 치우는 간격(초)
COMMANDS = {  # 역할별로 받아들이는 명령과 값
    "webots": {"connectome": ("on", "off"), "safety": ("on", "off"), "estop": ("on", "off"), "restart": None, "map": "map",
               "sensor": ("camera", "range"), "speed_limit": SPEED_LIMITS},
    "brain": {"reset_all": None},
}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def log_files(log_dir: Path) -> list[Path]:
    """관제 기록 파일들(dashboard_*.jsonl, 압축한 것은 .jsonl.gz)을 이름(=시작 시각) 순서로. 압축하는 도중이라 둘 다
    있으면 압축 전 파일을 쓴다."""
    plain = {path.name: path for path in log_dir.glob("dashboard_*.jsonl")}
    packed = {path.name[:-3]: path for path in log_dir.glob("dashboard_*.jsonl.gz")}
    return [plain.get(name) or packed[name] for name in sorted(set(plain) | set(packed), key=_log_order)]


def _log_order(name: str) -> tuple[str, int]:
    """dashboard_<시각>[-n].jsonl → (시각, n). 같은 초에 넘어간 파일(-2, -3 …)도 만든 순서대로 (이름 그대로면 -2가 앞에 온다)."""
    match = re.fullmatch(r"dashboard_(\d{8}-\d{6})(?:-(\d+))?\.jsonl", name)
    return (match.group(1), int(match.group(2) or 1)) if match else (name, 0)


def open_log(path: Path):
    """기록 파일을 글자로 연다 (.gz면 풀면서)."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def open_private_log(path: Path):
    """기록 파일을 이 계정만 읽고 쓰게(0600) 열어 이어 쓴다 — 위치·명령·휴대폰 정보가 담긴다 (KISA 구현 2-3)."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.chmod(path, 0o600)  # 예전에 만든 파일이어도
    return os.fdopen(fd, "a", encoding="utf-8")


def remove_expired_logs(log_dir: Path, keep_days: float, now: float | None = None) -> int:
    """보존 기한(keep_days일)이 지난 압축 기록(dashboard_*.jsonl.gz)을 지운다. 0 이하면 지우지 않는다. 지운 수를 돌려준다."""
    if keep_days <= 0:
        return 0
    limit = (time.time() if now is None else now) - keep_days * 86400
    count = 0
    for path in log_dir.glob("dashboard_*.jsonl.gz"):
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
                count += 1
        except OSError:
            pass
    return count


def compress_log(path: Path) -> Path | None:
    """다 쓴 기록 파일을 gzip으로 압축한다 (.jsonl → .jsonl.gz, 수정 시각 유지). 임시 파일에 쓴 뒤 바꿔 달아, 도중에 꺼져도
    원래 파일은 남는다. 압축한 파일을 돌려준다 (읽을 수 없으면 None)."""
    target = path.with_name(path.name + ".gz")
    partial = path.with_name(path.name + ".gz.part")
    try:
        stat = path.stat()
        with path.open("rb") as source, gzip.open(partial, "wb", compresslevel=6) as sink:
            while chunk := source.read(1 << 20):
                sink.write(chunk)
        os.utime(partial, (stat.st_atime, stat.st_mtime))
        partial.chmod(0o600)
        partial.replace(target)
        path.unlink()
        return target
    except OSError:
        partial.unlink(missing_ok=True)
        return None


def compress_old_logs(log_dir: Path, keep: Path | None = None) -> int:
    """압축하지 않은 지난 기록 파일(keep 빼고)을 모두 압축한다. 압축한 수를 돌려준다."""
    count = 0
    for path in sorted(log_dir.glob("dashboard_*.jsonl")):
        if keep is not None and path.resolve() == keep.resolve():
            continue
        count += compress_log(path) is not None
    return count


HELLO_PRIVATE = ("type", "token", "device_key", "conn")  # 화면·기록에 내보내지 않는 hello 칸 (비밀값·접속 nonce)
AGENT_ROLES = ("webots", "brain", "phone")
HELLO_FIELDS = {"type", "role", "name", "token", "device_key", "conn",          # 접속·인증
                "map", "maps", "rows", "brain_server", "goal",               # 로봇 (Webots 컨트롤러·어택랩)
                "model", "listen",                                           # 연산 서버
                "client", "fov", "pitch", "height"}                          # 휴대폰 체험
AGENT_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,80}")
HELLO_WAIT_S = 10.0   # 붙고 나서 이 안에 hello가 와야 한다
AGENT_IDLE_S = 120.0  # 이만큼 아무 줄도 없으면 닫는다 (장치는 한가할 때 ping을 보낸다 — common/dashboard_link.py)


def bad_hello(hello: dict[str, Any]) -> str:
    """hello가 약속한 모양이 아니면 그 까닭 (KISA 구현 1-15 — 보안 결정에 쓰는 역할·이름을 그대로 믿지 않는다)."""
    if hello.get("role") not in AGENT_ROLES:
        return "unknown role"
    if not isinstance(hello.get("name"), str) or not AGENT_NAME.fullmatch(hello["name"]):
        return "bad name"
    unknown = set(hello) - HELLO_FIELDS
    return f"unknown field {sorted(unknown)[0]!r}"[:60] if unknown else ""


class Agent:
    def __init__(self, agent_id: str, role: str, name: str, peer: str, hello: dict[str, Any], send: Any) -> None:
        self.id, self.role, self.name, self.peer = agent_id, role, name, peer
        self.hello = hello
        self.send = send                 # 명령 보내기 (접속이 끊기면 None)
        self.connected = True
        self.duplicate = False  # 같은 이름이 동시에 붙어 있었다 (Hub.register)
        self.connected_at = _now()
        self.disconnected_at: float | None = None  # 끊긴 시각 (time.time) — DISCONNECTED_KEEP_S 뒤 목록에서 뺀다
        self.last_seen = time.time()
        self.state: dict[str, Any] | None = None
        self.logged_state_at = 0.0
        self.logged_state: dict[str, Any] = {}  # 마지막으로 기록한 상태 (바뀐 칸만 쓰려고)
        self.full_state_at = 0.0                # 마지막으로 상태를 전부 쓴 시각
        self.auth = ""                          # "device"(장치 열쇠) · "server"(서버 토큰) · ""(인증 없음 — 시험)
        self.command_key = ""                   # 이 장치에 보낼 명령을 서명할 열쇠 (common/device_auth.py)
        self.conn = str(hello.get("conn", ""))  # 장치가 정한 이번 접속의 nonce — 명령에 그대로 싣는다
        self.seq = 0                            # 이 접속에서 보낸 명령 순번

    def public(self, with_hello: bool = True) -> dict[str, Any]:
        """화면에 보낼 모양. with_hello=False면 자주 바뀌지 않는 hello(맵 모양 등)는 뺀다 (상태 갱신용)."""
        data = {"id": self.id, "role": self.role, "name": self.name, "peer": self.peer, "connected": self.connected,
                "connected_at": self.connected_at, "age_s": round(time.time() - self.last_seen, 1), "state": self.state,
                "keep_s": DISCONNECTED_KEEP_S, "duplicate": self.duplicate, "auth": self.auth}
        if with_hello:
            data["hello"] = {k: v for k, v in self.hello.items() if k not in HELLO_PRIVATE}
        return data


class Hub:
    """모든 접속(로봇·서버)과 화면(SSE 구독자)을 잇는다."""

    def __init__(self, token: str = "", log_path: Path | None = None, log_key: bytes | None = None) -> None:
        self.token = token
        self.lock = threading.Lock()
        self.agents: dict[str, Agent] = {}
        self.counter = collections.Counter()
        self.events: collections.deque[dict[str, Any]] = collections.deque(maxlen=HISTORY_EVENTS + 1000)
        self.subscribers: list[queue.Queue] = []
        self.log_file = None
        self.log_key = log_key  # 있으면 줄마다 HMAC 사슬 (common/log_chain.py — 기록 무결성)
        self.chain: Chain | None = None
        if log_path is not None:
            private_dir(log_path.parent)
            self.log_file = open_private_log(log_path)
            self.log_path = log_path
            self.chain = Chain(log_key, log_path.name) if log_key else None
        self.rotate_bytes = ROTATE_BYTES

    # --- 기록·화면으로 퍼뜨리기 ---
    def _log(self, record: dict[str, Any]) -> None:
        if self.log_file is not None:
            line = self.chain.line(record) if self.chain else json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            self.log_file.write(line + "\n")
            self.log_file.flush()
            if self.log_file.tell() > self.rotate_bytes:
                self._rotate()

    def close(self) -> None:
        """기록을 봉인하고 닫는다 (서버를 끌 때)."""
        with self.lock:
            if self.log_file is None:
                return
            if self.chain is not None:
                self.log_file.write(self.chain.seal(_now()) + "\n")
            self.log_file.close()
            self.log_file = None

    def _rotate(self) -> None:
        """지금 기록 파일이 커졌다 → 새 파일로 넘어가고, 앞 파일은 뒤에서 압축한다 (잠금 안에서 부른다)."""
        old = self.log_path
        if self.chain is not None:  # 봉인: 이 파일이 여기서 끝났다 (뒤를 잘라 내면 드러난다)
            self.log_file.write(self.chain.seal(_now()) + "\n")
        self.log_file.close()
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = old.with_name(f"dashboard_{stamp}.jsonl")
        n = 2
        while path.exists() or path.with_name(path.name + ".gz").exists():
            path = old.with_name(f"dashboard_{stamp}-{n}.jsonl")
            n += 1
        self.log_path = path
        self.log_file = open_private_log(path)
        self.chain = Chain(self.log_key, path.name) if self.log_key else None
        threading.Thread(target=compress_log, args=(old,), name="compress-log", daemon=True).start()

    def _publish(self, kind: str, data: dict[str, Any]) -> None:
        message = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for subscriber in list(self.subscribers):
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                pass  # 느린 화면은 몇 개 건너뛴다

    def add_event(self, agent: Agent | None, event: dict[str, Any], kind: str = "event") -> None:
        record = {"time": _now(), "agent": agent.id if agent else None, "name": agent.name if agent else "dashboard",
                  "role": agent.role if agent else "dashboard", "kind": kind, "event": event}
        with self.lock:
            self.events.append(record)
            self._log(record)
        self._publish("event", record)

    # --- 로봇·서버 접속 ---
    def register(self, hello: dict[str, Any], peer: str, send: Any) -> Agent:
        role = str(hello.get("role", "unknown"))[:20]
        name = str(hello.get("name", role))[:80]
        with self.lock:
            self.counter[role] += 1
            agent_id = f"{role}-{self.counter[role]}"
            agent = Agent(agent_id, role, name, peer, hello, send)
            # 같은 이름으로 다시 접속했다(컨트롤러를 다시 켰거나 맵을 바꿈, 서버 재시작) → 끊겨 남아 있던 앞 카드는 바로 뺀다
            replaced = [a.id for a in self.agents.values() if not a.connected and a.role == role and a.name == name]
            for old in replaced:
                del self.agents[old]
            # 같은 이름이 이미 붙어 있다 → 둘 다 '이름 겹침'으로 표시한다 (토큰을 가진 쪽의 사칭이나 설정 실수 — KISA 설계 SR1-9).
            # 끊지는 않는다: 컨트롤러를 다시 켜 앞 접속이 아직 정리되지 않은 정상 경우도 있어서.
            twins = [a for a in self.agents.values() if a.connected and a.role == role and a.name == name]
            for twin in twins:
                twin.duplicate = agent.duplicate = True
            self.agents[agent_id] = agent
        for old in replaced:
            self._publish("removed", {"id": old})
        self.add_event(agent, {"type": "connected", "peer": peer}, kind="connection")
        if twins:
            self.add_event(agent, {"type": "duplicate_name", "name": name, "peers": [peer] + [t.peer for t in twins]},
                           kind="connection")
            for twin in twins:
                self._publish("agent", twin.public())
        self._publish("agent", agent.public())
        return agent

    def prune(self, now: float | None = None) -> list[str]:
        """끊긴 지 DISCONNECTED_KEEP_S가 지난 로봇·서버를 목록에서 빼고 화면에 알린다. 뺀 id들을 돌려준다."""
        now = time.time() if now is None else now
        with self.lock:
            gone = [a.id for a in self.agents.values()
                    if not a.connected and a.disconnected_at is not None and now - a.disconnected_at >= DISCONNECTED_KEEP_S]
            for old in gone:
                del self.agents[old]
        for old in gone:
            self._publish("removed", {"id": old})
        return gone

    def update_state(self, agent: Agent, state: dict[str, Any]) -> None:
        agent.state = state
        agent.last_seen = time.time()
        state = {key: value for key, value in state.items() if key not in SCREEN_ONLY_STATE_KEYS}
        if agent.last_seen - agent.logged_state_at >= 1.0:
            agent.logged_state_at = agent.last_seen
            full = agent.last_seen - agent.full_state_at >= FULL_STATE_EVERY_S
            if full:
                changed = dict(state)
                agent.full_state_at = agent.last_seen
            else:  # 직전에 기록한 상태에서 바뀐 칸만 (판단 기준값·서버 정보처럼 거의 안 바뀌는 칸이 매초 쌓이지 않게)
                changed = {key: value for key, value in state.items()
                           if key in ALWAYS_STATE_KEYS or agent.logged_state.get(key, _MISSING) != value}
            if full or any(key not in ALWAYS_STATE_KEYS for key in changed):
                agent.logged_state = dict(state)
                record = {"time": _now(), "agent": agent.id, "name": agent.name, "role": agent.role, "kind": "state",
                          "state": changed}
                if full:
                    record["full"] = True
                with self.lock:
                    self._log(record)
        self._publish("agent", agent.public(with_hello=False))

    def disconnect(self, agent: Agent) -> None:
        agent.connected = False
        agent.send = None
        agent.disconnected_at = time.time()
        self.add_event(agent, {"type": "disconnected"}, kind="connection")
        self._publish("agent", agent.public())

    # --- 화면에서 온 명령 ---
    def command(self, agent_id: str, command: str, value: Any) -> tuple[int, str]:
        agent = self.agents.get(agent_id)
        if agent is None:
            return 404, "no such agent"
        allowed = COMMANDS.get(agent.role, {})
        if command not in allowed:
            return 400, f"{agent.role} does not take {command!r}"
        values = allowed[command]
        if values == "map":
            maps = [m[0] for m in agent.hello.get("maps", []) if isinstance(m, list) and m]
            if value not in maps:
                return 400, "unknown map"
        elif values is not None and value not in values:
            return 400, f"value must be one of {values}"
        send = agent.send
        if send is None or not agent.connected:
            return 409, "agent is not connected"
        message: dict[str, Any] = {"type": "command", "command": command, "value": value}
        if agent.command_key:  # 서명·대상·접속 nonce·순번 — 장치가 위조·재전송을 거부한다 (common/device_auth.py)
            with self.lock:
                agent.seq += 1
                message.update(target=agent.name, conn=agent.conn, seq=agent.seq)
            message = sign_command(agent.command_key, message)
        try:
            send(message)
        except OSError:
            return 409, "send failed"
        self.add_event(agent, {"type": "command", "command": command, "value": value}, kind="command")
        return 200, "ok"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"agents": [a.public() for a in self.agents.values()], "events": list(self.events)[-SNAPSHOT_EVENTS:]}

    # --- 지난 기록 ---
    def load_history(self, log_dir: Path, limit: int | None = None) -> int:
        """지난 기록 파일(logs/dashboard_*.jsonl, 지금 쓰는 파일 빼고)의 사건·접속·명령을 시간순으로 읽어 사건 목록 앞에 넣는다.
        최근 limit개만 (상태 줄은 읽지 않는다). 넣은 수를 돌려준다. 읽을 수 없는 줄·파일은 건너뛴다."""
        limit = HISTORY_EVENTS if limit is None else limit
        current = getattr(self, "log_path", None)
        files = list(reversed(log_files(log_dir)))  # 이름에 시각이 있어 새 것부터 (압축한 것도)
        collected: list[list[dict[str, Any]]] = []
        total = 0
        for path in files:
            if current is not None and path.resolve() == current.resolve():
                continue
            records = []
            try:
                with open_log(path) as stream:
                    for line in stream:
                        if '"kind":"state"' in line:  # 상태 줄은 많고 화면 사건 기록에는 쓰지 않는다 — 파싱하지 않고 건너뛴다
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and record.get("kind") in ("event", "connection", "command"):
                            record.pop("mac", None)  # 무결성 사슬 값은 화면에 쓰지 않는다
                            record["past"] = True
                            records.append(record)
            except (OSError, EOFError):  # 압축이 덜 된(끊긴) 파일은 읽은 데까지
                pass
            collected.append(records)
            total += len(records)
            if total >= limit:
                break
        history = [record for records in reversed(collected) for record in records][-limit:]
        with self.lock:
            past_and_now = history + list(self.events)
            self.events.clear()
            self.events.extend(past_and_now[-self.events.maxlen:])
        return len(history)


# ---------------------------------------------------------------------------
# 기록 분석 (화면의 '기록 분석' 탭): 모든 기록 파일에서 로봇의 회피 반사 작동·충돌·도착을 뽑는다
# ---------------------------------------------------------------------------

_STAGE_ORDER = ("stop", "avoid", "veer", "slow")   # 한 작동이 거친 단계 중 가장 센 것 (goal.SEVERITY와 같은 순서)
_MAP_IN_STATE = re.compile(r'"map":"([A-Za-z0-9_]+)"')
_AGENT_IN_LINE = re.compile(r'"agent":"([^"]+)"')
_analysis_cache: dict[str, tuple[float, int, dict[str, list]]] = {}


def _strongest(stages: list[str]) -> str:
    for stage in _STAGE_ORDER:
        if stage in stages:
            return stage
    return "avoid" if "return" in stages else (stages[0] if stages else "slow")


def _round(value: Any, digits: int = 2) -> Any:
    return round(value, digits) if isinstance(value, (int, float)) else None


def _analyze_file(path: Path) -> dict[str, list]:
    """기록 파일 하나 → 작동(episodes)·충돌·도착. 사건에는 맵이 없어서, 그 로봇의 직전 상태 줄(1초마다)의 맵을 붙인다."""
    episodes, collisions, arrivals = [], [], []
    current_map: dict[str, str] = {}
    open_episodes: dict[tuple[str, int], dict[str, Any]] = {}
    with open_log(path) as stream:
        for line in stream:
            if '"kind":"state"' in line:  # 상태 줄은 맵만 본다 (파싱하지 않고 — 기록이 수백 MB일 수 있다)
                agent, found = _AGENT_IN_LINE.search(line), _MAP_IN_STATE.search(line)
                if agent and found:
                    current_map[agent.group(1)] = found.group(1)
                continue
            if '"role":"webots"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            event, agent = record.get("event") or {}, str(record.get("agent"))
            kind, when = event.get("type"), record.get("time", "")
            game_map = current_map.get(agent, "")
            pose = event.get("pose") or {}
            if kind == "map":
                current_map[agent] = str(event.get("to", ""))
            elif kind == "start":
                open_episodes[(agent, event.get("episode"))] = {
                    "time": when, "map": game_map, "x": _round(pose.get("x")), "y": _round(pose.get("y")),
                    "first": (event.get("reflex") or {}).get("stage", "")}
            elif kind == "end":
                start = open_episodes.pop((agent, event.get("episode")), None)
                if start is None:
                    continue
                stages = [str(s) for s in event.get("stages") or []] or [start.pop("first")]
                start.pop("first", None)
                start.update(stage=_strongest(stages), peak=_round(event.get("peak_escape")),
                             duration=_round(event.get("duration")), collision=bool(event.get("collision")),
                             no_brain=bool(event.get("no_brain")))
                episodes.append(start)
            elif kind == "collision":
                collisions.append({"time": when, "map": game_map, "x": _round(pose.get("x")), "y": _round(pose.get("y")),
                                   "with": event.get("with") or "wall", "object": event.get("object") or ""})
            elif kind == "arrival":
                arrivals.append({"time": when, "map": game_map, "elapsed": _round(event.get("elapsed"), 1),
                                 "interventions": event.get("interventions"), "by_stage": event.get("by_stage") or {}})
    return {"episodes": episodes, "collisions": collisions, "arrivals": arrivals}


def analyze_logs(log_dir: Path) -> dict[str, Any]:
    """모든 기록 파일(logs/dashboard_*.jsonl)을 모아 분석한다. 바뀌지 않은 파일은 다시 읽지 않는다(크기·수정 시각으로)."""
    result: dict[str, Any] = {"episodes": [], "collisions": [], "arrivals": [], "maps": {}}
    for path in log_files(log_dir):
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = _analysis_cache.get(str(path))
        if cached is None or cached[0] != stat.st_mtime or cached[1] != stat.st_size:
            try:
                cached = (stat.st_mtime, stat.st_size, _analyze_file(path))
            except (OSError, EOFError):
                continue
            _analysis_cache[str(path)] = cached
        for key in ("episodes", "collisions", "arrivals"):
            result[key].extend(cached[2][key])
    try:  # 맵 모양 (개입 위치 지도) — 맵 문자 줄 그대로
        from simulation.scenes import SCENES
        result["maps"] = {name: {"title": scene.title, "rows": list(scene.rows)} for name, scene in SCENES.items()}
    except Exception:  # noqa: BLE001 — 맵을 못 읽어도 다른 분석은 보여 준다
        pass
    return result


def load_trials(trials_dir: Path) -> dict[str, Any]:
    """도착 시험 결과 파일(logs/trials/trials_*.json)을 새것부터. 읽지 못한 파일은 건너뛴다. 맵 이름표도 붙인다."""
    runs = []
    for path in sorted(trials_dir.glob("trials_*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("outcomes"), list):
            runs.append(dict(record, file=path.name))
    titles: dict[str, str] = {}
    try:
        from simulation.scenes import SCENES
        titles = {name: scene.title for name, scene in SCENES.items()}
    except Exception:  # noqa: BLE001 — 이름표가 없어도 결과는 보여 준다
        pass
    return {"runs": runs, "titles": titles}


class AgentHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        hub: Hub = self.server.hub  # type: ignore[attr-defined]
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        failures = getattr(self.server, "auth_failures", None)
        if failures is not None and failures.locked(self.client_address[0]):  # 토큰을 거듭 틀린 주소 (KISA 구현 2-16)
            return
        send_lock = threading.Lock()

        def send(message: dict[str, Any]) -> None:
            with send_lock:
                self.wfile.write((json.dumps(message) + "\n").encode())
                self.wfile.flush()

        agent: Agent | None = None
        try:
            self.connection.settimeout(HELLO_WAIT_S)
            first = self.rfile.readline(MAX_LINE + 1)
            hello = json.loads(first) if first else None
            if isinstance(hello, dict) and hello.get("type") == "enroll" and hub.token:  # 클라이언트 인증서 등록 (common/mtls.py)
                send(handle_enroll(hello, hub.token))
                return
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                return
            problem = bad_hello(hello)
            if problem:
                send({"type": "error", "message": problem})
                return
            role, name = str(hello.get("role", ""))[:20], str(hello.get("name", ""))[:80]
            server_token = bool(hub.token) and hmac.compare_digest(str(hello.get("token", "")), hub.token)
            device = bool(hub.token) and check_device(hub.token, role, name, str(hello.get("device_key", "")))
            if hub.token and not (server_token or device):
                failures = getattr(self.server, "auth_failures", None)
                if failures is not None:
                    failures.failed(self.client_address[0])
                send({"type": "error", "message": "bad token"})
                return
            problem = check_peer(self.connection, self.client_address, name, device) if hub.token else ""
            if problem:  # 다른 PC의 TLS 접속은 등록된 클라이언트 인증서까지 (mTLS — KISA 구현 2-11)
                send({"type": "error", "message": problem})
                return
            agent = hub.register(hello, peer, send)
            if hub.token:  # 명령은 이 장치의 열쇠로 서명한다 (서버 토큰으로 붙었어도 같은 계산 — 장치가 확인할 수 있게)
                agent.auth = "device" if device else "server"
                agent.command_key = device_key(hub.token, agent.role, agent.name)
            hub_failures = getattr(self.server, "auth_failures", None)
            if hub_failures is not None:
                hub_failures.succeeded(self.client_address[0])
            self.connection.settimeout(AGENT_IDLE_S)
            while line := self.rfile.readline(MAX_LINE + 1):  # 줄 길이를 읽으면서 잰다 (끝까지 읽고 재면 메모리를 다 쓸 수 있다)
                if len(line) > MAX_LINE:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "state":
                    hub.update_state(agent, {k: v for k, v in message.items() if k != "type"})
                elif message.get("type") == "event" and isinstance(message.get("event"), dict) \
                        and isinstance(message["event"].get("type"), str) and len(message["event"]["type"]) <= 40:
                    agent.last_seen = time.time()
                    hub.add_event(agent, message["event"])
                elif message.get("type") == "hello" and not set(message) - HELLO_FIELDS:  # 맵 등이 바뀌었다 (모르는 칸이면 무시)
                    agent.hello.update({k: v for k, v in message.items() if k not in HELLO_PRIVATE and k not in ("name", "role")})
                    hub._publish("agent", agent.public())
        except (OSError, ValueError):
            pass
        finally:
            if agent is not None:
                hub.disconnect(agent)


class AgentServer(TLSServerMixin, ConnectionLimit, socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    max_connections = 64

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.auth_failures = FailureLock()


class WebHandler(WebAuthMixin, BaseHTTPRequestHandler):
    server_version = "ReflexGuardDashboard/1"
    auth_title = "ReflexGuard 관제"
    timeout = 30  # 한 번 받기를 이만큼 기다린다 (요청 전체 시한은 web_auth.REQUEST_DEADLINE_S 15초 — 실시간 화면은 _stream에서 푼다)

    def log_message(self, format: str, *args: Any) -> None:  # 접속 기록은 찍지 않는다
        pass

    @property
    def hub(self) -> Hub:
        return self.server.hub  # type: ignore[attr-defined]

    def auth_token(self) -> str:
        return self.hub.token

    def _json(self, code: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.redirect_plain() or not self.guard():  # https로 넘기기(TLS일 때), 요청 속도·인증 실패 잠금 (KISA 구현 2-16)
            return
        path = urlparse(self.path).path
        if self.login_from_query():  # ?token= → 로그인 쿠키로 바꾸고 주소에서 지운다
            return
        role = self.role()
        is_page = path in PAGE_PATHS or path.startswith("/trials/")
        if role is None:  # 로그인 전: 화면은 로그인 화면, API는 401 (KISA 구현 2-1)
            if is_page:
                self.send_login()
            else:
                self._json(401 if path.startswith("/api/") else 404, {"error": "login required"})
            return
        if is_page:  # 화면 주소는 모두 같은 화면 (어느 화면인지는 화면이 주소로 정한다)
            body = PAGE.read_bytes()
            self.csp = page_csp(body)  # 이 화면의 인라인 스크립트만 허용 (KISA 설계 SR1-5)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/me":
            self._json(200, {"role": role, "csrf": self.csrf_value()})
        elif path == "/api/agents":
            self._json(200, self.hub.snapshot()["agents"])
        elif path == "/api/analysis":
            self._json(200, self.server.analysis())  # type: ignore[attr-defined]
        elif path == "/api/trials":
            self._json(200, load_trials(LOG_DIR / "trials"))
        elif path == "/api/events":
            try:
                limit = int(parse_qs(urlparse(self.path).query).get("limit", ["200"])[0] or 200)
            except ValueError:  # 숫자가 아니면 기본값 (KISA 구현 4-2)
                limit = 200
            self._json(200, self.hub.snapshot()["events"][-max(1, min(limit, 1000)):])
        elif path == "/api/stream":
            if len(self.hub.subscribers) >= MAX_STREAMS:
                self._json(503, {"error": "too many live screens"})
                return
            self._stream()
        else:
            self._json(404, {"error": "not found"})

    def _reject(self, code: int, error: str) -> None:
        """거절: 작은 본문은 읽어 버리고 답한다 (안 읽고 닫으면 상대가 답을 받기 전에 연결이 끊길 수 있다)."""
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = -1
        if 0 < length <= 4096:
            self.rfile.read(length)
        else:
            self.close_connection = True
        self._json(code, {"error": error})

    def do_POST(self) -> None:
        if self.redirect_plain() or not self.guard():
            return
        path = urlparse(self.path).path
        if path == "/login":
            self.handle_login()
            return
        if path == "/logout":
            self.handle_logout()
            return
        if path != "/api/command":
            self._reject(404, "not found")
            return
        if not same_origin(self.headers):  # 다른 사이트가 사용자 브라우저를 빌려 보낸 명령 (KISA 구현 1-11)
            self._reject(403, "cross-site request")
            return
        if not is_json(self.headers):  # 다른 사이트의 폼(text/plain 등)으로는 보낼 수 없게
            self._reject(415, "json only")
            return
        role = self.role()
        if role != CONTROL:  # 로그인 안 함 401, 보기 전용 403 (KISA 구현 2-1·2-2)
            self._reject(401 if role is None else 403, "login required" if role is None else "view only")
            return
        if not self.csrf_ok():  # 화면이 받은 CSRF 값이 있어야 (도구는 X-Token) — KISA 구현 1-11
            self._reject(403, "csrf")
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:  # 숫자가 아닌 길이 (KISA 구현 4-2)
            length = -1
        if not 0 < length <= 4096:
            self._reject(400, "bad body")
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) - {"agent", "command", "value"}:  # 정한 칸만 (KISA 구현 1-15)
                raise ValueError("unexpected fields")
            code, message = self.hub.command(str(body["agent"]), str(body["command"]), body.get("value"))
        except (KeyError, TypeError, ValueError):
            code, message = 400, "bad request"
        self._json(code, {"ok": code == 200, "message": message})

    def _stream(self) -> None:
        self.release_deadline()  # 실시간 화면은 오래 간다
        self.connection.settimeout(60)  # 실시간 화면: 받을 것은 없고, 보내기가 1분 넘게 막히면(화면이 안 읽음) 끊는다
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # 앞에 nginx(리버스 프록시)가 있어도 모아 두지 말고 바로 넘기게
        self.end_headers()
        inbox: queue.Queue = queue.Queue(maxsize=500)
        self.hub.subscribers.append(inbox)
        try:
            snapshot = json.dumps(self.hub.snapshot(), ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: snapshot\ndata: {snapshot}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    message = inbox.get(timeout=15)
                except queue.Empty:
                    message = ": keepalive\n\n"  # 연결 유지
                self.wfile.write(message.encode())
                self.wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            if inbox in self.hub.subscribers:
                self.hub.subscribers.remove(inbox)


class WebServer(TLSServerMixin, ConnectionLimit, ThreadingHTTPServer):
    daemon_threads = True
    plain_allowed = True  # 다른 기기의 http://는 처리기가 https://로 넘긴다
    allow_reuse_address = True
    max_connections = 64  # 실시간 화면(MAX_STREAMS)이 다 열려도 나머지 요청이 들어올 자리가 남게
    max_per_ip = 24       # 한 PC의 탭 여러 개(탭마다 실시간 화면 하나 + 요청)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rate = RateLimiter(rate=20.0, burst=60.0)  # IP마다 초당 20개, 한 번에 60개까지
        self.auth_failures = FailureLock()
        self._analysis: tuple[float, Any] | None = None
        self._analysis_lock = threading.Lock()

    def analysis(self) -> Any:
        with self._analysis_lock:  # 여러 화면이 한꺼번에 불러도 한 번만 계산한다
            if self._analysis is None or time.monotonic() - self._analysis[0] > ANALYSIS_CACHE_S:
                self._analysis = (time.monotonic(), analyze_logs(LOG_DIR))
            return self._analysis[1]


BIND_RETRY_S = 20.0  # 포트가 잠깐 막혀 있으면 이만큼 다시 시도한다 (WSL을 막 다시 켰을 때 등)


def bind_with_retry(server_class: Any, host: str, port: int, handler: Any, label: str, wait_s: float | None = None) -> Any:
    """포트를 연다. 이미 쓰이고 있으면 wait_s(기본 BIND_RETRY_S)초까지 1초마다 다시 시도하고, 끝내 못 열면 알기 쉽게 끝낸다.
    WSL 미러 네트워크(networkingMode=mirrored)에서는 Windows 프로그램과 포트를 함께 쓰므로, WSL을 다시 켠 직후 잠깐 막히기도 했다."""
    deadline = time.monotonic() + (BIND_RETRY_S if wait_s is None else wait_s)
    warned = False
    while True:
        try:
            return server_class((host, port), handler)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            if time.monotonic() >= deadline:
                raise SystemExit(f"[dashboard] 오류: {label} 포트 {port}를 다른 프로그램이 쓰고 있습니다. 이미 켜 둔 관제 서버가 있으면 "
                                 f"bash scripts/stop_dashboard.sh, WSL 미러 네트워크면 Windows 쪽 프로그램도 확인하세요 "
                                 f"(다른 포트: --web-port / --agent-port).") from error
            if not warned:
                print(f"[dashboard] {label} 포트 {port}가 쓰이고 있어 다시 시도합니다…", flush=True)
                warned = True
            time.sleep(1.0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ReflexGuard monitoring & control dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="받을 주소 (기본 127.0.0.1: 같은 PC만)")
    parser.add_argument("--public", action="store_true", help="다른 PC에서도 접속 (0.0.0.0) — 그러면 TLS도 켠다")
    parser.add_argument("--tls", action=argparse.BooleanOptionalAction, default=None,
                        help="화면은 HTTPS, 접속은 TLS (기본: --public일 때 켬). 같은 PC의 평문은 그대로 받는다 (common/tls.py)")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="관제 화면 포트 (기본 8080)")
    parser.add_argument("--agent-port", type=int, default=DEFAULT_AGENT_PORT, help="로봇·연산 서버 접속 포트 (기본 8766)")
    parser.add_argument("--keep-days", type=float, default=float(os.environ.get("REFLEXGUARD_LOG_KEEP_DAYS", "0") or 0),
                        help="이 날수가 지난 압축 기록을 켤 때 지운다 (기본 0 = 지우지 않음, 환경 변수 REFLEXGUARD_LOG_KEEP_DAYS)")
    args = parser.parse_args(argv)
    host = "0.0.0.0" if args.public else args.host  # nosec B104  # --public을 줄 때만 (기본 127.0.0.1)
    log_path = LOG_DIR / f"dashboard_{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    private_dir(LOG_DIR)  # 기록 폴더는 이 계정만 (KISA 구현 2-3)
    removed = remove_expired_logs(LOG_DIR, args.keep_days)
    if removed:
        print(f"[dashboard] 보존 기한({args.keep_days:g}일)이 지난 기록 {removed}개를 지웠습니다", flush=True)
    if LOG_DIR.is_dir() and any(LOG_DIR.glob("dashboard_*.jsonl")):
        print("[dashboard] 지난 기록 파일을 압축하는 중…", flush=True)
        print(f"[dashboard] {compress_old_logs(LOG_DIR)}개 압축함 (.jsonl.gz)", flush=True)
    # 포트를 먼저 연다 (못 열면 기록 파일을 만들지 않고 끝낸다 — 빈 기록이 남지 않게)
    agents = bind_with_retry(AgentServer, host, args.agent_port, AgentHandler, "로봇·연산 서버 접속")
    web = bind_with_retry(WebServer, host, args.web_port, WebHandler, "관제 화면")
    use_tls = args.public if args.tls is None else args.tls
    if use_tls:  # 다른 PC와는 암호화해서 (KISA 설계 SR2-8)
        cert, key = certificate(lan_addresses())
        web.tls = server_context(cert, key)                       # 화면(브라우저): 클라이언트 인증서를 청하지 않는다
        agents.tls = enable_mtls(server_context(cert, key))       # 기계 접속: 등록된 클라이언트 인증서 (mTLS)
    try:
        token = load_token(create=True)  # 환경 변수 또는 logs/token (없으면 만든다) — 인증은 언제나 켜져 있다
    except WeakToken as error:
        raise SystemExit(f"[dashboard] 오류: {error}") from error
    hub = Hub(token, log_path, load_key(create=True))  # 기록 무결성 사슬 열쇠 logs/log.key (0600)
    loaded = hub.load_history(LOG_DIR)
    hub.add_event(None, {"type": "dashboard", "action": "started", "history": loaded})
    agents.hub = hub  # type: ignore[attr-defined]
    web.hub = hub  # type: ignore[attr-defined]
    threading.Thread(target=agents.serve_forever, name="agents", daemon=True).start()

    def prune_forever() -> None:
        while True:
            time.sleep(PRUNE_EVERY_S)
            hub.prune()

    threading.Thread(target=prune_forever, name="prune", daemon=True).start()
    print(f"[dashboard] 관제 화면: http://localhost:{args.web_port}/live  (기록 분석 /analysis · 시험 결과 /trials)", flush=True)
    print(f"[dashboard] 로봇·연산 서버 접속: {host}:{args.agent_port} (REFLEXGUARD_DASHBOARD=127.0.0.1:{args.agent_port})", flush=True)
    if use_tls:
        for address in lan_addresses():
            print(f"[dashboard] 다른 PC: 화면 https://{address}:{args.web_port}/live · 접속 tls://{address}:{args.agent_port}", flush=True)
        print(f"[dashboard] 인증서 지문 — 다른 PC에 REFLEXGUARD_TLS_PIN={file_fingerprint(cert)}", flush=True)
    print(f"[dashboard] 기록: {log_path.relative_to(ROOT)} (지난 기록 {loaded}개를 이어 보여 줌)", flush=True)
    print("[dashboard] 로그인 토큰: REFLEXGUARD_TOKEN 또는 logs/token 파일 (보기 전용 토큰: python -m common.auth)", flush=True)

    def stop(*_: Any) -> None:  # stop_dashboard.sh(SIGTERM)로 꺼도 기록을 봉인하고 끄게
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        web.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        agents.shutdown()
        hub.close()


if __name__ == "__main__":
    main(sys.argv[1:])
