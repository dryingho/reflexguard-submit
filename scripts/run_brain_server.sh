#!/usr/bin/env bash
# ReflexGuard — 커넥톰 연산 서버만 켜기 (brain_server/server.py)
#
# 사용법
#   bash scripts/run_brain_server.sh                  # 같은 PC에서만 접속 받기 (127.0.0.1:8765)
#   bash scripts/run_brain_server.sh --port 9000      # 다른 포트
#   bash scripts/run_brain_server.sh --public         # 다른 PC에서도 접속 받기 (0.0.0.0, TLS) — 설정 REFLEXGUARD_SERVE_PUBLIC=1이면 기본
#   bash scripts/run_brain_server.sh --dashboard 127.0.0.1:8766   # 관제 대시보드에 상태 보내기
#
# 끄려면 Ctrl+C. Webots 쪽은 bash scripts/run_webots.sh <맵> --server <이 PC 주소>:<포트> 로 붙는다.
# --public이면 TLS로 받는다(같은 PC의 평문은 그대로) — 다른 PC는 tls://<주소>:<포트>와 인증서 지문(REFLEXGUARD_TLS_PIN)·토큰으로.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

HOST="127.0.0.1"
[[ "${REFLEXGUARD_SERVE_PUBLIC:-}" == 1 ]] && HOST="0.0.0.0"   # setup.sh config에서 '다른 PC에도 연다'로 정한 PC
PORT="${DEFAULT_PORT}"
DASH_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard) DASH_ARGS=(--dashboard "${2:?--dashboard 뒤에 host:port}"); shift 2 ;;
    --port) PORT="${2:?--port 뒤에 번호}"; shift 2 ;;
    --public) HOST="0.0.0.0"; shift ;;
    --host) HOST="${2:?--host 뒤에 주소}"; shift 2 ;;
    -h|--help) print_help "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "알 수 없는 인자: $1 (--help 참고)" >&2; exit 2 ;;
  esac
done

require_python
if [[ "${HOST}" == "0.0.0.0" ]]; then
  echo "다른 PC에서도 접속을 받습니다 — TLS로 암호화하고 토큰으로 확인합니다."
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  PIN="$(cd "${PROJECT_DIR}" && "${PYTHON}" -c 'from common.tls import certificate, file_fingerprint; print(file_fingerprint(certificate()[0]))')"
  NAME="${REFLEXGUARD_PUBLIC_NAMES%% *}"
  echo "      휠체어·Webots PC에서 (한 번): bash setup.sh config --brain tls://${NAME:-${IP:-<이 PC의 IP>}}:${PORT} --pin ${PIN}"
  echo "      (접속이 안 되면 방화벽에서 포트를 허용하세요: sudo ufw allow ${PORT}/tcp)"
fi
cd "${PROJECT_DIR}"
show_tokens
exec "${PYTHON}" -m brain_server.server --host "${HOST}" --port "${PORT}" "${DASH_ARGS[@]+"${DASH_ARGS[@]}"}"
