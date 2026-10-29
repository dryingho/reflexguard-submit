#!/usr/bin/env bash
# ReflexGuard — 계속 켜 둔 관제 대시보드 끄기
#
# scripts/run.sh는 Webots를 닫아도 관제 대시보드를 끄지 않는다 (기록이 세션을 넘어 이어지게).
# 이 스크립트는 run.sh가 켜 둔 대시보드(logs/dashboard.pid)를 끈다. 기록 파일(logs/dashboard_*.jsonl)은 그대로 남는다.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "${PROJECT_DIR}"

case "${1:-}" in
  "") ;;
  -h|--help) sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "모르는 옵션: $1 (사용법: bash scripts/stop_dashboard.sh)" >&2; exit 2 ;;
esac

PIDFILE=logs/dashboard.pid
if [[ ! -f "${PIDFILE}" ]]; then
  echo "켜 둔 관제 대시보드가 없습니다 (${PIDFILE} 없음)."
  exit 0
fi
PID="$(cat "${PIDFILE}")"
if [[ "${PID}" =~ ^[0-9]+$ ]] && kill -0 "${PID}" 2>/dev/null && grep -qa "dashboard.server\|dashboard_server.py" "/proc/${PID}/cmdline" 2>/dev/null; then
  kill "${PID}"
  echo "관제 대시보드(PID ${PID})를 껐습니다."
else
  echo "관제 대시보드가 이미 꺼져 있습니다."
fi
rm -f "${PIDFILE}"
