#!/usr/bin/env bash
# ReflexGuard — 관제 대시보드 + 커넥톰 연산 서버 + 체험·조작 포털 + Webots 한 번에 실행
#
# 하는 일
#   1) 관제 대시보드(dashboard/server.py)를 뒤에서 켠다 (화면 http://localhost:8080/live, 기록 logs/dashboard_*.jsonl).
#   2) 커넥톰 연산 서버(brain_server/server.py)를 뒤에서 켜고 대시보드에 붙인다 (로그 logs/brain_server.log).
#   3) 체험·조작 포털(demo/server.py)을 뒤에서 켠다 (로그 logs/portal.log). 조작 화면 하나로 Webots 시뮬레이션을 조작하고,
#      '휴대폰 카메라' 스위치를 켜면 그 기기의 카메라로 체험한다. 기본은 이 PC에서만: http://localhost:8443
#      --public이면 다른 기기(휴대폰·다른 PC·리버스 프록시)에도 연다: https://<이 PC 주소>:8443
#      (주소 하나 — 자체 인증서 경고를 한 번 넘긴다). 처음 열면 토큰을 묻는다 (터미널에 나온다).
#   4) 셋이 준비되면 서버·대시보드 주소를 넣어 Webots를 연다.
#   5) Webots를 닫으면 2)·3)에서 켠 연산 서버·포털은 끈다. 관제 대시보드는 계속 켜 둔다 — 다음 세션도 같은 기록에 이어 쌓이고
#      화면에서 지난 기록을 볼 수 있다. 끄려면 bash scripts/stop_dashboard.sh
#      이미 그 포트에서 돌고 있던 것은 그대로 쓰고 끄지 않는다.
#
# 사용법
#   bash scripts/run.sh                 # 병원 복도
#   bash scripts/run.sh road            # 맵: hospital hospital_busy road plaza alley station parking park
#   bash scripts/run.sh road --no-dashboard        # 대시보드 없이 (연산 서버 + Webots)
#   bash scripts/run.sh road --port 9000           # 커넥톰 연산 서버 포트
#   bash scripts/run.sh road --webots-port 1240    # Webots 웹 서버 포트 (기본: 1234부터 빈 번호)
#   bash scripts/run.sh road --camera-ms 64        # 카메라를 64 ms마다 (약 2배 빠름 — docs/usage.md)
#   bash scripts/run.sh road --sensor range        # 위험 인식을 거리 센서로 (판단·개입은 커넥톰). 기본 camera
#   bash scripts/run.sh road --public              # 관제·연산 서버·포털을 다른 기기에도 연다 (TLS, 0.0.0.0 — 예전 이름 --phone)
#                                                  # setup.sh config --serve-public으로 정한 PC는 옵션 없이도 이렇게 연다
#   bash scripts/run.sh road --no-portal           # 포털 없이 (조작은 Webots 주소로, 휴대폰 카메라 체험 없음)
#
# 한 PC에서 전부 켤 때 쓴다. PC마다 역할을 나누면: 서버 PC는 bash scripts/run_server.sh (관제·연산 서버·휴대폰 체험),
# Webots PC는 bash scripts/run_sim.sh <맵> (Webots + 시뮬레이션 조작 포털 — 서버 PC의 관제·연산 서버에 붙는다)
#
# setup.sh config에서 다른 PC의 관제·연산 서버(tls://…)를 정한 PC(휠체어·Webots PC)는 그 서버를 이 PC에 켜지 않고
# Webots를 그쪽에 붙인다 (장치 열쇠·등록한 인증서로). 포털과 이 PC에서 켠 연산 서버는 이 PC의 관제 서버에만 붙는다.
#
# 맵은 웹 조작 화면이나 관제 대시보드에서도 바꿀 수 있다 (서버·대시보드는 그대로 이어서 쓴다).

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

MAP="hospital"
PORT="${DEFAULT_PORT}"
WEBOTS_ARGS=()
WITH_DASHBOARD=1
WITH_PORTAL=1
WEBOTS_PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?--port 뒤에 번호}"; shift 2 ;;
    --webots-port) WEBOTS_PORT="${2:?--webots-port 뒤에 번호}"; shift 2 ;;
    --no-portal) WITH_PORTAL=0; shift ;;
    --public|--phone) SERVE_PUBLIC=1; shift ;;  # --phone: 예전 이름
    --portal-local) shift ;;                    # 예전 이름 (이제 기본)
    --sensor) WEBOTS_ARGS+=(--sensor "${2:?--sensor 뒤에 camera 또는 range}"); shift 2 ;;
    --no-dashboard) WITH_DASHBOARD=0; shift ;;
    --camera-ms) export REFLEXGUARD_CAMERA_MS="${2:?--camera-ms 뒤에 32 또는 64}"; shift 2 ;;
    -h|--help) print_help "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "알 수 없는 인자: $1 (--help 참고)" >&2; exit 2 ;;
    *) MAP="$1"; shift ;;
  esac
done

require_python
require_webots
world_for_map "${MAP}" >/dev/null   # 맵 이름을 먼저 확인한다
cd "${PROJECT_DIR}"
mkdir -p logs
"${PYTHON}" -m common.auth --create > /dev/null || exit 1   # 토큰을 먼저 준비한다 (약한 REFLEXGUARD_TOKEN이면 여기서 멈춘다)
trap stop_started EXIT INT TERM
apply_serve_public
# setup.sh config에서 정한 관제·연산 서버가 다른 PC면(휠체어·Webots PC) 이 PC에는 켜지 않고 그쪽에 붙는다
find_remote_servers

DASH_ARGS=(--dashboard none)  # Webots가 붙을 관제 서버
LOCAL_DASHBOARD=""            # 이 PC에서 켠 연산 서버·포털이 붙을 관제 서버 (이 PC의 것만 — 다른 PC에는 서버 토큰이 필요하다)
if [[ "${WITH_DASHBOARD}" == 1 && -n "${REMOTE_DASHBOARD}" ]]; then
  DASH_ARGS=(--dashboard "${REMOTE_DASHBOARD}")
  echo "==> 관제 서버: 다른 PC ${REMOTE_DASHBOARD} (setup.sh config — 이 PC에는 켜지 않습니다)"
elif [[ "${WITH_DASHBOARD}" == 1 ]]; then
  start_local_dashboard
  DASH_ARGS=(--dashboard "${LOCAL_DASHBOARD}")
fi
if [[ -n "${REMOTE_BRAIN}" ]]; then
  BRAIN_ADDRESS="${REMOTE_BRAIN}"
  echo "==> 커넥톰 연산 서버: 다른 PC ${REMOTE_BRAIN} (setup.sh config — 이 PC에는 켜지 않습니다)"
else
  BRAIN_ADDRESS="127.0.0.1:${PORT}"
  start_local_brain "${PORT}" "${LOCAL_DASHBOARD}"
fi

# Webots 웹 서버 포트를 여기서 정해 Webots와 포털에 같이 넘긴다
[[ -n "${WEBOTS_PORT}" ]] || WEBOTS_PORT="$(free_port 1234)"
WEBOTS_ARGS+=(--webots-port "${WEBOTS_PORT}")
[[ "${WITH_PORTAL}" == 1 ]] && start_portal "${LOCAL_DASHBOARD}" --webots-port "${WEBOTS_PORT}"
show_tokens

# exec를 쓰지 않는다: Webots가 끝난 뒤 trap이 서버들을 끌 수 있게
bash "${SCRIPT_DIR}/run_webots.sh" "${MAP}" --server "${BRAIN_ADDRESS}" \
  "${DASH_ARGS[@]+"${DASH_ARGS[@]}"}" "${WEBOTS_ARGS[@]+"${WEBOTS_ARGS[@]}"}"
