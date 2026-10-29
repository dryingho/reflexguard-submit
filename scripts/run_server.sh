#!/usr/bin/env bash
# ReflexGuard — 서버 PC: 관제 대시보드 + 커넥톰 연산 서버 + 휴대폰 체험 포털 (Webots 없이)
#
# 하는 일
#   1) 관제 대시보드(dashboard/server.py)를 뒤에서 켠다 (화면 http://localhost:8080/live). 이 스크립트를 끝내도 계속 돈다
#      — 끄려면 bash scripts/stop_dashboard.sh
#   2) 커넥톰 연산 서버(brain_server/server.py)를 켜고 관제 서버에 붙인다. Webots PC(run_sim.sh)의 휠체어가 여기서 계산한다.
#   3) 휴대폰 카메라 체험 포털(demo/server.py — Webots 없이 카메라만)을 켜고 관제 서버에 붙인다. 휴대폰은
#      https://<이 PC 주소>:8443 을 열면 바로 카메라로 체험한다 (6대까지 함께, 계산은 이 PC에서).
#   4) Ctrl+C로 끝내면 2)·3)을 끈다. 이미 그 포트에서 돌고 있던 것은 그대로 쓰고 끄지 않는다.
#
# 사용법
#   bash scripts/run_server.sh                  # 이 PC에서만 (시험)
#   bash scripts/run_server.sh --public         # 다른 PC·휴대폰에도 연다 (TLS, 0.0.0.0) — setup.sh config --serve-public이면 기본
#   bash scripts/run_server.sh --port 9000      # 커넥톰 연산 서버 포트
#   bash scripts/run_server.sh --no-portal      # 휴대폰 체험 포털 없이 (관제·연산 서버만)
#   bash scripts/run_server.sh --no-dashboard   # 관제 서버 없이 (연산 서버·포털만)
#
# Webots PC는 bash scripts/run_sim.sh <맵> 으로 켜서 이 PC의 관제·연산 서버에 붙인다 (주소는 그 PC의 setup.sh config).
# 한 PC에서 Webots까지 전부 켜려면 bash scripts/run.sh

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

PORT="${DEFAULT_PORT}"
WITH_DASHBOARD=1
WITH_PORTAL=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?--port 뒤에 번호}"; shift 2 ;;
    --public) SERVE_PUBLIC=1; shift ;;
    --no-portal) WITH_PORTAL=0; shift ;;
    --no-dashboard) WITH_DASHBOARD=0; shift ;;
    -h|--help) print_help "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "알 수 없는 인자: $1 (--help 참고)" >&2; exit 2 ;;
  esac
done

require_python
cd "${PROJECT_DIR}"
mkdir -p logs
"${PYTHON}" -m common.auth --create > /dev/null || exit 1   # 토큰을 먼저 준비한다 (약한 REFLEXGUARD_TOKEN이면 여기서 멈춘다)
trap stop_started EXIT INT TERM
apply_serve_public

LOCAL_DASHBOARD=""
[[ "${WITH_DASHBOARD}" == 1 ]] && start_local_dashboard
start_local_brain "${PORT}" "${LOCAL_DASHBOARD}"
[[ "${WITH_PORTAL}" == 1 ]] && start_portal "${LOCAL_DASHBOARD}"   # Webots 없이 — 카메라 체험만
show_tokens

if [[ ${#STARTED_PIDS[@]} -eq 0 ]]; then
  echo "==> 모두 이미 돌고 있어 새로 켠 것이 없습니다."
  exit 0
fi
echo "==> 서버 PC로 켜 두었습니다. 끄려면 Ctrl+C (관제 서버는 계속 돕니다)."
wait
