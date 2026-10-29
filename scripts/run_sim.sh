#!/usr/bin/env bash
# ReflexGuard — Webots PC: Webots + 커넥톰 연산 서버(이 PC) + 시뮬레이션 조작 포털 (관제·휴대폰 체험은 서버 PC에서)
#
# 하는 일
#   1) 시뮬레이션 조작 포털(demo/server.py — Webots 조작만, 휴대폰 카메라는 받지 않는다)을 켠다. 다른 기기에서도
#      https://<이 PC 주소>:8443 으로 Webots를 조작할 수 있다 (--public일 때).
#   2) 커넥톰 연산 서버(brain_server/server.py)를 이 PC에서만 받게(127.0.0.1) 켠다. 설정에 다른 PC의 연산 서버가 있어도
#      쓰지 않고 이 PC의 것으로 계산한다. 이미 그 포트에서 돌고 있으면 그대로 쓴다.
#   3) Webots를 열고, 컨트롤러를 2)의 연산 서버와 서버 PC(run_server.sh)의 관제 서버에 붙인다. 관제 주소·인증서 지문·장치 열쇠는
#      이 PC의 설정(reflexguard.local.env — bash setup.sh config). 설정에 없고 이 PC에 관제 서버가 켜져 있으면 그것에 붙는다.
#   4) Webots를 닫으면 1)·2)를 끈다.
#   이 PC에서 켠 연산 서버·포털은 이 PC의 관제 서버에만 붙는다 — 서버 PC의 관제에는 Webots 컨트롤러만 붙는다.
#
# 사용법
#   bash scripts/run_sim.sh                       # 병원 복도
#   bash scripts/run_sim.sh road                  # 맵: hospital hospital_busy road plaza alley station parking park
#   bash scripts/run_sim.sh road --public         # 조작 포털을 다른 기기에도 연다 (TLS, 0.0.0.0) — setup.sh config --serve-public이면 기본
#   bash scripts/run_sim.sh road --webots-port 1240   # Webots 웹 서버 포트 (기본: 1234부터 빈 번호)
#   bash scripts/run_sim.sh road --port 9000      # 이 PC의 커넥톰 연산 서버 포트 (기본 8765)
#   bash scripts/run_sim.sh road --camera-ms 32   # 카메라를 32 ms마다. 기본은 64 — 약 2배 빠르고 감지는 거의 같다 (docs/usage.md 느릴 때).
#                                                 #  환경 변수 REFLEXGUARD_CAMERA_MS로도 정할 수 있다
#   bash scripts/run_sim.sh road --sensor range   # 위험 인식을 거리 센서로 (판단·개입은 커넥톰). 기본 camera
#   bash scripts/run_sim.sh road --no-portal      # 조작 포털 없이 (조작은 Webots 주소로)
#
# 서버 PC는 bash scripts/run_server.sh 로 켠다. 한 PC에서 전부 켜려면 bash scripts/run.sh

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

MAP="hospital"
PORT="${DEFAULT_PORT}"
WEBOTS_ARGS=()
WITH_PORTAL=1
WEBOTS_PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --webots-port) WEBOTS_PORT="${2:?--webots-port 뒤에 번호}"; shift 2 ;;
    --port) PORT="${2:?--port 뒤에 번호}"; shift 2 ;;
    --public) SERVE_PUBLIC=1; shift ;;
    --no-portal) WITH_PORTAL=0; shift ;;
    --sensor) WEBOTS_ARGS+=(--sensor "${2:?--sensor 뒤에 camera 또는 range}"); shift 2 ;;
    --camera-ms) export REFLEXGUARD_CAMERA_MS="${2:?--camera-ms 뒤에 32 또는 64}"; shift 2 ;;
    -h|--help) print_help "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "알 수 없는 인자: $1 (--help 참고)" >&2; exit 2 ;;
    *) MAP="$1"; shift ;;
  esac
done
export REFLEXGUARD_CAMERA_MS="${REFLEXGUARD_CAMERA_MS:-64}"   # 기본 64 ms — 연산 서버·컨트롤러가 같은 값을 쓰게 켜기 전에 정한다

require_python
require_webots
world_for_map "${MAP}" >/dev/null   # 맵 이름을 먼저 확인한다
cd "${PROJECT_DIR}"
mkdir -p logs
"${PYTHON}" -m common.auth --create > /dev/null || exit 1   # 조작 포털의 로그인 토큰
trap stop_started EXIT INT TERM
apply_serve_public
BRAIN_HOST="127.0.0.1"  # 연산 서버는 이 PC의 Webots만 쓴다 — --public이어도 다른 기기에 열지 않는다

# 붙을 관제 서버: 설정의 주소(다른 PC면 그쪽) → 없으면 이 PC에 켜져 있는 것 → 없으면 붙지 않는다
DASHBOARD="${REFLEXGUARD_DASHBOARD:-}"
LOCAL_DASHBOARD=""  # 이 PC에서 켠 연산 서버·포털이 붙을 관제 서버 (이 PC의 것만)
if is_remote "${DASHBOARD}"; then
  :
elif port_in_use 127.0.0.1 "${DEFAULT_DASHBOARD_AGENT_PORT}"; then
  DASHBOARD="127.0.0.1:${DEFAULT_DASHBOARD_AGENT_PORT}"
  LOCAL_DASHBOARD="${DASHBOARD}"
else
  DASHBOARD=""
fi
echo "==> 관제 서버: ${DASHBOARD:-없음 (붙지 않습니다 — 서버 PC 주소는 bash setup.sh config)}"

# 커넥톰 연산 서버: 이 PC에 켜고 거기에 붙는다
start_local_brain "${PORT}" "${LOCAL_DASHBOARD}"
SERVER="127.0.0.1:${PORT}"
echo "==> 커넥톰 연산 서버: 이 PC ${SERVER} (Webots를 닫으면 끕니다)"

[[ -n "${WEBOTS_PORT}" ]] || WEBOTS_PORT="$(free_port 1234)"
WEBOTS_ARGS+=(--webots-port "${WEBOTS_PORT}")
[[ "${WITH_PORTAL}" == 1 ]] && start_portal "${LOCAL_DASHBOARD}" --webots-port "${WEBOTS_PORT}" --no-camera   # Webots 조작만
show_tokens

# exec를 쓰지 않는다: Webots가 끝난 뒤 trap이 연산 서버·포털을 끌 수 있게
bash "${SCRIPT_DIR}/run_webots.sh" "${MAP}" --server "${SERVER}" --dashboard "${DASHBOARD:-none}" \
  "${WEBOTS_ARGS[@]+"${WEBOTS_ARGS[@]}"}"
