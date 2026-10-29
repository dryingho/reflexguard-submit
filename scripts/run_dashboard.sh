#!/usr/bin/env bash
# ReflexGuard — 관제 대시보드만 켜기 (dashboard/server.py)
#
# 사용법
#   bash scripts/run_dashboard.sh                     # 관제 화면 http://localhost:8080/live , 로봇·연산 서버 접속 127.0.0.1:8766
#   bash scripts/run_dashboard.sh --public            # 다른 PC의 로봇·연산 서버·브라우저도 접속 (TLS — 화면 https://, 접속 tls://)
#                                                       설정 REFLEXGUARD_SERVE_PUBLIC=1(setup.sh config)이면 기본
#   bash scripts/run_dashboard.sh --web-port 9080 --agent-port 9766
#
# 로봇:      bash scripts/run_webots.sh <맵> --dashboard 127.0.0.1:8766
# 연산 서버: bash scripts/run_brain_server.sh --dashboard 127.0.0.1:8766
# 끄려면 Ctrl+C. 기록은 logs/dashboard_<날짜-시각>.jsonl

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_python
cd "${PROJECT_DIR}"
show_tokens
PUBLIC_ARGS=()
if [[ "${REFLEXGUARD_SERVE_PUBLIC:-}" == 1 && " $* " != *" --public "* && " $* " != *" --host "* ]]; then
  PUBLIC_ARGS=(--public)
  PIN="$("${PYTHON}" -c 'from common.tls import certificate, file_fingerprint; print(file_fingerprint(certificate()[0]))')"
  NAME="${REFLEXGUARD_PUBLIC_NAMES%% *}"
  echo "다른 PC에서 (한 번): bash setup.sh config --dashboard tls://${NAME:-<이 PC의 이름이나 주소>}:${DEFAULT_DASHBOARD_AGENT_PORT} --pin ${PIN}"
fi
exec "${PYTHON}" -m dashboard.server "${PUBLIC_ARGS[@]+"${PUBLIC_ARGS[@]}"}" "$@"
