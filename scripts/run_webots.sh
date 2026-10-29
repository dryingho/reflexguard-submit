#!/usr/bin/env bash
# ReflexGuard — Webots만 열기 (맵 선택, 커넥톰을 서버에서 계산할지 선택)
#
# 사용법
#   bash scripts/run_webots.sh                         # 병원 복도, 커넥톰은 컨트롤러 안에서 계산
#   bash scripts/run_webots.sh road                    # 맵 고르기: hospital hospital_busy road plaza alley station parking park
#   bash scripts/run_webots.sh road --server 127.0.0.1:8765     # 커넥톰은 연산 서버에서 (서버는 따로 켜 둔다)
#   bash scripts/run_webots.sh road --server tls://brain-pc.local:8765   # 다른 PC의 서버 (TLS — 지문·토큰은 setup.sh config로)
#   bash scripts/run_webots.sh road --server none               # 설정 파일에 서버가 있어도 이번에는 컨트롤러 안에서 계산
#   bash scripts/run_webots.sh road --webots-port 1240          # Webots 웹 서버 포트 지정 (기본: 1234부터 빈 번호)
#   bash scripts/run_webots.sh road --dashboard 127.0.0.1:8766  # 관제 대시보드에 상태·사건 보내고 명령 받기
#   bash scripts/run_webots.sh road --camera-ms 64              # 카메라를 64 ms마다 (약 2배 빠름 — docs/usage.md)
#   bash scripts/run_webots.sh road --sensor range              # 위험 인식을 거리 센서로 (판단·개입은 커넥톰). 기본 camera
#                                                               # 실행 중에도 웹 조작 화면·관제 대시보드에서 바꿀 수 있다
#
# 웹 조작 화면 주소는 실행할 때 터미널에 보여 준다:
#   http://localhost:<포트>/robot_windows/reflexguard/reflexguard.html?name=reflexguard   (포트는 보통 1234)
# --server·--dashboard를 안 주면 이 PC의 설정(reflexguard.local.env — bash setup.sh config)의 주소를 쓴다. none이면 쓰지 않는다.

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

MAP="hospital"
SERVER="${REFLEXGUARD_BRAIN_SERVER:-}"      # 설정 파일·환경 변수의 기본값 (인자가 먼저)
WEBOTS_PORT=""
DASHBOARD="${REFLEXGUARD_DASHBOARD:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard) DASHBOARD="${2:?--dashboard 뒤에 host:port}"; shift 2 ;;
    --server) SERVER="${2:?--server 뒤에 host:port}"; shift 2 ;;
    --webots-port) WEBOTS_PORT="${2:?--webots-port 뒤에 번호}"; shift 2 ;;
    --camera-ms) export REFLEXGUARD_CAMERA_MS="${2:?--camera-ms 뒤에 32 또는 64}"; shift 2 ;;
    --sensor)
      case "${2:-}" in
        camera|range) export REFLEXGUARD_SENSOR="$2"; shift 2 ;;
        *) echo "오류: --sensor 뒤에 camera 또는 range" >&2; exit 2 ;;
      esac ;;
    -h|--help) print_help "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "알 수 없는 인자: $1 (--help 참고)" >&2; exit 2 ;;
    *) MAP="$1"; shift ;;
  esac
done

[[ "${SERVER}" == none ]] && SERVER=""
[[ "${DASHBOARD}" == none ]] && DASHBOARD=""

require_webots
require_python
WORLD="$(world_for_map "${MAP}")"
cd "${PROJECT_DIR}/simulation/webots"   # Webots 프로젝트 폴더에서 연다 (worlds/와 같은 곳의 controllers/, plugins/를 쓴다)
# Webots 웹 서버 포트를 직접 정해 넘긴다 — 그래야 웹 조작 화면 주소를 정확히 알려 줄 수 있다
if [[ -z "${WEBOTS_PORT}" ]]; then
  WEBOTS_PORT="$(free_port 1234)"
elif port_in_use 127.0.0.1 "${WEBOTS_PORT}"; then
  echo "오류: 포트 ${WEBOTS_PORT}를 이미 다른 프로그램(다른 Webots 등)이 쓰고 있습니다. 다른 번호를 고르세요." >&2
  exit 1
fi
if [[ -n "${REFLEXGUARD_PORTAL_SHOWN:-}" ]]; then  # scripts/run.sh가 체험·조작 포털 주소를 이미 보여 줬다
  echo "==> Webots 웹 서버: ${WEBOTS_PORT} (조작 화면은 위의 포털 주소로 — 직접 열려면 http://localhost:${WEBOTS_PORT}/robot_windows/reflexguard/reflexguard.html?name=reflexguard)"
else
  print_console_url "${WEBOTS_PORT}"
fi
# 관제 대시보드 주소 (없으면 남아 있는 환경 변수도 지운다)
export REFLEXGUARD_DASHBOARD="${DASHBOARD}"   # 비어 있으면 붙지 않는다 (빈 값이라 설정 파일 값으로 다시 채워지지 않는다)

# Webots 웹 서버는 이 PC에서만 (다른 기기는 토큰 로그인을 하는 체험·조작 포털로). REFLEXGUARD_WEBOTS_PUBLIC=1이면 예전처럼 모든 주소
WEBOTS_ENV=()
# 컨트롤러는 Webots가 PATH의 python3로 돌린다 → .venv를 앞에 두면 시스템 numpy 없이 .venv의 numpy(해시 고정)를 쓴다
if [[ -x "${PROJECT_DIR}/.venv/bin/python3" ]]; then
  WEBOTS_ENV+=("PATH=${PROJECT_DIR}/.venv/bin:${PATH}")
fi
if [[ "${REFLEXGUARD_WEBOTS_PUBLIC:-}" != 1 ]]; then
  LOOPBACK_LIB="$(webots_loopback_lib)"
  if [[ -n "${LOOPBACK_LIB}" ]]; then
    WEBOTS_ENV+=("LD_PRELOAD=${LOOPBACK_LIB}${LD_PRELOAD:+:${LD_PRELOAD}}")
    echo "==> Webots 웹 서버(${WEBOTS_PORT})는 이 PC(127.0.0.1)에서만 받습니다"
  else
    echo "주의: gcc가 없어 Webots 웹 서버(${WEBOTS_PORT})가 모든 주소로 열립니다 — 방화벽으로 막거나 gcc를 설치하세요."
  fi
fi

# 화면이 없으면 가상 화면(xvfb)에서 Webots를 띄운다 — setup.sh가 약속한 대로. 웹 조작 화면·관제 대시보드는 그대로 쓴다
WEBOTS_LAUNCHER=()
WEBOTS_FLAGS=()
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    WEBOTS_LAUNCHER=(xvfb-run -a -s "-screen 0 1280x720x24")
    # 아무도 볼 수 없는 화면: 처음 실행 안내 창(가이드 투어·사용 통계 동의)이 시뮬레이션을 막지 않게 --batch,
    # 바로 실행 --mode=realtime, 컨트롤러 출력·오류는 이 터미널로 --stdout --stderr
    WEBOTS_FLAGS=(--batch --mode=realtime --stdout --stderr)
    echo "==> 화면이 없어 가상 화면(xvfb)에서 Webots를 띄웁니다 (웹 조작 화면·관제는 그대로)"
  else
    echo "주의: 화면도 xvfb도 없습니다 — Webots 창을 열 수 없습니다. 'bash setup.sh install tools'로 xvfb를 설치한 뒤 다시 실행하세요." >&2
  fi
fi

if [[ -n "${SERVER}" ]]; then
  echo "==> 맵 ${MAP} (${WORLD#${PROJECT_DIR}/}), 커넥톰 연산 서버 ${SERVER}"
  SERVER_HOSTPORT="${SERVER#tls://}"
  if ! port_in_use "${SERVER_HOSTPORT%:*}" "${SERVER_HOSTPORT##*:}" 2>/dev/null; then
    echo "주의: ${SERVER}에서 서버가 응답하지 않습니다. 서버가 켜질 때까지 커넥톰 판단이 없어 로봇이 안전 정지합니다."
  fi
  exec "${WEBOTS_LAUNCHER[@]+"${WEBOTS_LAUNCHER[@]}"}" env "${WEBOTS_ENV[@]+"${WEBOTS_ENV[@]}"}" REFLEXGUARD_BRAIN_SERVER="${SERVER}" "${WEBOTS_CMD}" "${WEBOTS_FLAGS[@]+"${WEBOTS_FLAGS[@]}"}" --port="${WEBOTS_PORT}" "${WORLD}"
else
  echo "==> 맵 ${MAP} (${WORLD#${PROJECT_DIR}/}), 커넥톰은 컨트롤러 안에서 계산"
  exec "${WEBOTS_LAUNCHER[@]+"${WEBOTS_LAUNCHER[@]}"}" env "${WEBOTS_ENV[@]+"${WEBOTS_ENV[@]}"}" REFLEXGUARD_BRAIN_SERVER= "${WEBOTS_CMD}" "${WEBOTS_FLAGS[@]+"${WEBOTS_FLAGS[@]}"}" --port="${WEBOTS_PORT}" "${WORLD}"
fi
