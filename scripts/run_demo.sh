#!/usr/bin/env bash
# ReflexGuard — 체험·조작 포털만 켜기 (demo/server.py) — Webots 없이 카메라 체험만 할 때
#
# 보통은 bash scripts/run.sh가 관제 서버·연산 서버·Webots와 함께 포털을 켠다(조작 화면 하나에서 시뮬레이션 조작과
# '휴대폰 카메라' 체험을 스위치로 오간다). 이 스크립트는 Webots 없이 카메라 체험만 하고 싶을 때 쓴다.
#
# 사용법
#   bash scripts/run_demo.sh                 # 이 PC·휴대폰 모두 https://<이 PC 주소>:8443 (자체 인증서, 경고 한 번 넘기기)
#   bash scripts/run_demo.sh --local         # 이 PC에서만 http://localhost:8443 (PC 웹캠으로 시험)
#   bash scripts/run_demo.sh --port 9443     # HTTPS 포트
#   bash scripts/run_demo.sh --no-dashboard  # 관제 대시보드에 보내지 않기
#   bash scripts/run_demo.sh --webots-port 1234   # 따로 켠 Webots도 이 포털로 조작하기
#
# 휴대폰 브라우저는 HTTPS가 아니면 카메라를 열지 않는다 → 기본은 --https --public. 인증서는 logs/certs/ (openssl 필요, 시작할 때 지문을 보여 준다).
# 관제 대시보드: REFLEXGUARD_DASHBOARD 주소가 있으면 그리로, 없으면 이 PC에 관제 서버(접속 포트 8766)가 켜져 있을 때 거기로
# 카메라를 쓰는 기기마다 상태·사건을 보낸다 (관제 화면의 '휴대폰 체험'). 영상은 보내지 않는다.
# 영상은 저장하지 않는다. 처음 열면 토큰을 묻는다(시작할 때 터미널에 나온다). 인터넷에 열지 말 것. 끄려면 Ctrl+C.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_python
cd "${PROJECT_DIR}"
MODE=(--https --public)
ARGS=()
DASHBOARD="${REFLEXGUARD_DASHBOARD:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE=(); shift ;;
    --no-dashboard) DASHBOARD="none"; shift ;;
    -h|--help) print_help "${BASH_SOURCE[0]}"; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
if [[ -z "${DASHBOARD}" ]] && port_in_use 127.0.0.1 "${DEFAULT_DASHBOARD_AGENT_PORT}"; then
  DASHBOARD="127.0.0.1:${DEFAULT_DASHBOARD_AGENT_PORT}"
fi
[[ "${DASHBOARD}" == "none" ]] && DASHBOARD=""
show_tokens
exec "${PYTHON}" -m demo.server "${MODE[@]+"${MODE[@]}"}" --dashboard "${DASHBOARD}" "${ARGS[@]+"${ARGS[@]}"}"
