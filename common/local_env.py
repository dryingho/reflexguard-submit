"""이 PC의 설정 파일 — reflexguard.local.env (setup.sh가 만든다, 이 계정만 읽기 0600, git에 넣지 않는다).

PC마다 다른 것(다른 PC의 주소, 토큰, 인증서 지문, 이 PC를 부를 이름)을 한 곳에 둔다. 이 모듈은 `common` 패키지를 불러올 때
한 번 읽어 환경 변수로 넣는다 — 휠체어 PC·Webots 컨트롤러·연산 서버·관제·포털·도구가 모두 같은 값을 쓴다.
직접 준 환경 변수가 먼저다(파일은 기본값). REFLEXGUARD_로 시작하는 줄만 받는다.

    REFLEXGUARD_DASHBOARD=tls://control-pc.local:8766
    REFLEXGUARD_BRAIN_SERVER=tls://brain-pc.local:8765
    REFLEXGUARD_TOKEN=…
    REFLEXGUARD_TLS_PIN=sha256:…            # 서버들의 공개키 지문 (쉼표로 여럿)
    REFLEXGUARD_PUBLIC_NAMES=control-pc.local 192.168.13.2   # 이 PC를 부를 이름·주소 (인증서에 넣는다)
    REFLEXGUARD_SERVE_PUBLIC=1              # 이 PC의 서버를 다른 PC에도 연다 (TLS)

다른 파일을 쓰려면 REFLEXGUARD_ENV_FILE=<경로> (빈 값이면 읽지 않는다 — 시험).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "reflexguard.local.env"
PREFIX = "REFLEXGUARD_"


def env_file() -> Path | None:
    chosen = os.environ.get("REFLEXGUARD_ENV_FILE")
    if chosen is None:
        return DEFAULT_FILE
    return Path(chosen) if chosen.strip() else None


def parse(text: str) -> dict[str, str]:
    """KEY=VALUE 줄들 (# 주석, 빈 줄, 앞의 export, 감싼 따옴표는 무시). REFLEXGUARD_로 시작하는 것만."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.startswith(PREFIX) or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load(path: Path | None = None) -> dict[str, str]:
    """파일을 읽어 아직 없는 환경 변수만 채운다. 채운 것을 돌려준다."""
    path = env_file() if path is None else path
    if path is None:
        return {}
    try:
        values = parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}
    added = {key: value for key, value in values.items() if key not in os.environ}
    os.environ.update(added)
    return added


def write(updates: dict[str, str], path: Path = DEFAULT_FILE) -> Path:
    """설정을 고치거나 더한다 (값이 빈 문자열이면 그 줄을 지운다). 이 계정만 읽게 0600으로 쓴다."""
    try:
        current = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        current = ["# ReflexGuard — 이 PC의 설정 (setup.sh config). 토큰이 들어 있으니 남에게 보내지 마세요."]
    seen, lines = set(), []
    for raw in current:
        key = raw.strip().removeprefix("export ").partition("=")[0].strip()
        if key in updates:
            seen.add(key)
            if updates[key] != "":
                lines.append(f"{key}={updates[key]}")
            continue
        lines.append(raw)
    lines += [f"{key}={value}" for key, value in updates.items() if key not in seen and value != ""]
    partial = path.with_name(path.name + ".new")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    partial.replace(path)
    return path


def main(argv: list[str] | None = None) -> None:
    """main(["set", "KEY=VALUE", …]) · set-stdin (줄마다 KEY=VALUE — 토큰을 명령줄에 남기지 않게) · get KEY · show
    — setup.sh가 쓴다."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "show"
    path = env_file() or DEFAULT_FILE
    if command == "set":
        write(dict(item.partition("=")[::2] for item in args[1:]), path)
    elif command == "set-stdin":
        write(dict(line.strip().partition("=")[::2] for line in sys.stdin if "=" in line), path)
    elif command == "get":
        print(parse(path.read_text(encoding="utf-8")).get(args[1], "") if path.exists() else "")
    else:
        values = parse(path.read_text(encoding="utf-8")) if path.exists() else {}
        for key, value in values.items():
            print(f"{key}={'<가림>' if 'TOKEN' in key or key.endswith('_KEY') else value}")


if __name__ == "__main__":
    main()
