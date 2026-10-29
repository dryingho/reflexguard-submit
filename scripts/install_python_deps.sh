#!/usr/bin/env bash
#
# ReflexGuard — 파이썬 의존성 설치 (예전 명령 — 이제 setup.sh가 맡는다)
#
# 사용법
#   bash scripts/install_python_deps.sh              # = bash setup.sh install brain tools  (.venv + numpy·pytest, 해시 확인)
#   bash scripts/install_python_deps.sh --run-tests  # 설치 후 시험
#
# 파트별로는 bash setup.sh install <파트> (서버 PC에는 시험 도구 없이 brain·dashboard 등만)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_TESTS=0
for arg in "$@"; do
  case "${arg}" in
    --run-tests) RUN_TESTS=1 ;;
    -h|--help) sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 인자: ${arg}" >&2; exit 2 ;;
  esac
done
bash "${ROOT}/setup.sh" install brain tools
[ "${RUN_TESTS}" -eq 1 ] && bash "${ROOT}/setup.sh" test
exit 0
