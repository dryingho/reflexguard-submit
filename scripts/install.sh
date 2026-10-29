#!/usr/bin/env bash
#
# ReflexGuard — 전체 설치 (예전 명령 — 이제 setup.sh가 맡는다: bash setup.sh install <파트>)
#
# 사용법
#   bash scripts/install.sh                          # = bash setup.sh install sim tools   (Webots 포함 전부)
#   bash scripts/install.sh --no-webots              # = bash setup.sh install wheelchair brain dashboard portal tools
#   bash scripts/install.sh --webots-method tarball  # Webots를 ~/webots 에 풀기
#   bash scripts/install.sh --run-tests              # 설치 후 시험 (bash setup.sh test)
#
# 파트별 설치·확인·관리는 bash setup.sh --help

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARTS=(sim tools)
OPTIONS=()
RUN_TESTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-webots) PARTS=(wheelchair brain dashboard portal tools); shift ;;
    --run-tests) RUN_TESTS=1; shift ;;
    --webots-method) OPTIONS+=(--webots-method "${2:?--webots-method 뒤에 deb 또는 tarball}"); shift 2 ;;
    --webots-method=*) OPTIONS+=("$1"); shift ;;
    --yes|-y|--dry-run|--no-system|--minimal) OPTIONS+=("$1"); shift ;;  # setup.sh 옵션은 그대로 넘긴다
    --skip-numpy) shift ;;  # 컨트롤러는 이제 .venv의 numpy를 쓴다 (시스템 numpy가 필요 없다)
    -h|--help) sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done
bash "${ROOT}/setup.sh" install "${PARTS[@]}" "${OPTIONS[@]+"${OPTIONS[@]}"}"
[ "${RUN_TESTS}" -eq 1 ] && bash "${ROOT}/setup.sh" test
exit 0
