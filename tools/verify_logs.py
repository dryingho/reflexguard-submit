"""관제 기록의 무결성 확인 — 줄마다 단 HMAC 사슬(common/log_chain.py)이 처음부터 끝까지 맞는지 본다.

    python tools/verify_logs.py                 # logs/dashboard_*.jsonl(.gz) 모두
    python tools/verify_logs.py logs/dashboard_20261026-010000.jsonl.gz

결과: 정상(봉인까지) · 열림(맞지만 봉인 없음 — 지금 쓰는 파일이거나 뒤가 잘림) · 깨짐(그 줄을 고치거나 빼거나 끼움) ·
사슬 없음(이 기능 전의 기록). 깨진 것이 하나라도 있으면 종료 코드 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.log_chain import load_key, verify  # noqa: E402
from dashboard.server import LOG_DIR, log_files, open_log  # noqa: E402

LABELS = {"ok": "정상", "open": "열림 (봉인 없음 — 쓰는 중이었거나 뒤가 잘림)", "broken": "깨짐", "unchained": "사슬 없음 (예전 기록)",
          "empty": "빈 파일"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="관제 기록 무결성 확인 (HMAC 사슬)")
    parser.add_argument("files", nargs="*", type=Path, help="확인할 파일 (기본: logs/의 관제 기록 모두)")
    args = parser.parse_args(argv)
    key = load_key()
    if key is None:
        print("열쇠(logs/log.key)가 없습니다 — 관제 서버를 한 번 켜면 만들어집니다.")
        return 1
    broken = 0
    for path in args.files or log_files(LOG_DIR):
        name = path.name[:-3] if path.name.endswith(".gz") else path.name  # 사슬은 압축 전 이름으로 시작한다
        try:
            with open_log(path) as stream:
                result, line = verify(key, name, stream)
        except (OSError, EOFError) as error:
            result, line = "broken", 0
            print(f"{path.name}: 읽지 못함 ({error})")
            broken += 1
            continue
        broken += result == "broken"
        where = f" — {line}번째 줄" if result == "broken" else f" ({line}줄)" if line else ""
        print(f"{path.name}: {LABELS[result]}{where}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
