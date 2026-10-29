#!/usr/bin/env bash
# run_*.sh가 같이 쓰는 것 (직접 실행하지 않는다): 경로, 파이썬, 맵 이름 → 월드 파일, 포트 대기,
# 서버 띄우기(관제·연산 서버·포털 — run.sh·run_server.sh·run_sim.sh), 다른 PC의 서버인지 판별

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
umask 077   # 스크립트가 만드는 기록(logs/*.log)·pid는 이 계정만 읽게 (KISA 구현 2-3)

# 이 PC의 설정 파일(reflexguard.local.env — setup.sh config)을 환경 변수 기본값으로. 직접 준 환경 변수가 먼저다.
# 다른 PC 주소·토큰·인증서 지문·이 PC를 부를 이름·서버를 다른 PC에 열지 (common/local_env.py와 같은 규칙)
load_local_env() {
  local file="${REFLEXGUARD_ENV_FILE-${PROJECT_DIR}/reflexguard.local.env}" line key value
  [[ -n "${file}" && -r "${file}" ]] || return 0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#export }"
    [[ "${line}" =~ ^(REFLEXGUARD_[A-Z0-9_]+)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [[ "${value}" =~ ^\"(.*)\"$ || "${value}" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
    [[ -n "${!key+set}" ]] || export "${key}=${value}"
  done < "${file}"
}
load_local_env
DEFAULT_PORT=8765             # 커넥톰 연산 서버
DEFAULT_DASHBOARD_WEB_PORT=8080
DEFAULT_DASHBOARD_AGENT_PORT=8766
DEFAULT_PORTAL_PORT=8443         # 체험·조작 포털 — 이 PC·휴대폰 같은 주소 (카메라는 HTTPS에서만)
MAPS="hospital hospital_busy road plaza alley station parking park"   # scenes.SCENES

# 프로젝트의 .venv가 있으면 그 파이썬, 없으면 python3 (numpy가 필요하다)
if [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
  PYTHON="${PROJECT_DIR}/.venv/bin/python"
else
  PYTHON="$(command -v python3 || true)"
fi

require_python() {
  if [[ -z "${PYTHON}" ]]; then
    echo "오류: python3를 찾지 못했습니다. 먼저 bash setup.sh install <파트> 를 실행하세요." >&2
    exit 1
  fi
}

# 맵 이름 → simulation/webots/worlds/ 안의 월드 파일 (기본 맵 hospital은 reflexguard.wbt)
world_for_map() {
  local map="$1"
  if [[ " ${MAPS} " != *" ${map} "* ]]; then
    echo "오류: 모르는 맵 '${map}'. 고를 수 있는 맵: ${MAPS}" >&2
    exit 2
  fi
  if [[ "${map}" == "hospital" ]]; then
    echo "${PROJECT_DIR}/simulation/webots/worlds/reflexguard.wbt"
  else
    echo "${PROJECT_DIR}/simulation/webots/worlds/${map}.wbt"
  fi
}

# Webots 실행 파일: PATH의 webots → $WEBOTS_HOME/webots → ~/webots/webots(tarball 설치) → /usr/local/webots/webots
require_webots() {
  local candidate
  WEBOTS_CMD=""
  for candidate in "$(command -v webots 2>/dev/null || true)" "${WEBOTS_HOME:+${WEBOTS_HOME}/webots}" \
                   "${HOME}/webots/webots" /usr/local/webots/webots; do
    if [[ -n "${candidate}" && -x "${candidate}" ]]; then WEBOTS_CMD="${candidate}"; return 0; fi
  done
  echo "오류: Webots를 찾지 못했습니다. 먼저 bash setup.sh install sim 을 실행하세요." >&2
  exit 1
}

# host:port가 접속을 받을 때까지 기다린다 (최대 $3초). 받으면 0, 아니면 1
wait_for_port() {
  "${PYTHON}" - "$1" "$2" "${3:-10}" <<'PY'
import socket, sys, time
host, port, limit = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
deadline = time.time() + limit
while time.time() < deadline:
    try:
        socket.create_connection((host, port), timeout=0.5).close()
        sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit(1)
PY
}

# 이미 무언가가 host:port에서 접속을 받고 있는지 (한 번만 확인)
port_in_use() {
  "${PYTHON}" - "$1" "$2" <<'PY'
import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=0.5).close()
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
}

# start부터 차례로 보며 아무도 쓰지 않는 포트 번호 (Webots 웹 서버용). Webots는 기본 1234를 쓰고, 이미 쓰이면 다른 번호로 연다
free_port() {
  local port="${1:-1234}"
  while port_in_use 127.0.0.1 "${port}"; do
    port=$((port + 1))
  done
  echo "${port}"
}

# 웹 조작 화면 주소를 눈에 띄게 보여 준다 (Webots 로봇 창: 로봇 이름 reflexguard)
print_console_url() {
  local port="$1"
  echo ""
  echo "================================================================================"
  echo "  웹 조작 화면 — Webots가 열리고 월드를 다 불러온 뒤 브라우저에서 여세요"
  echo ""
  echo "    http://localhost:${port}/robot_windows/reflexguard/reflexguard.html?name=reflexguard"
  echo ""
  echo "  (이 PC의 브라우저에서 여는 주소입니다)"
  echo "================================================================================"
  echo ""
}

# 체험·조작 포털 주소를 눈에 띄게 — 이 PC와 휴대폰이 같은 주소 하나 (인자: 포털에 넘긴 옵션들)
print_portal_urls() {
  local scheme="http" addresses="localhost" address
  [[ " $1 " == *" --https "* ]] && scheme="https"
  if [[ " $1 " == *" --public "* ]]; then
    addresses="$("${PYTHON}" -c 'import sys; sys.path.insert(0, sys.argv[1]); from demo.server import lan_addresses; print(" ".join(lan_addresses()) or "localhost")' "${PROJECT_DIR}")"
  fi
  echo ""
  echo "================================================================================"
  local after="Webots가 월드를 다 불러온 뒤 여세요"
  [[ " $1 " == *" --webots-port "* ]] || after="Webots 없이 카메라 체험만"
  [[ " $1 " == *" --no-camera "* ]] && after="${after} · 휴대폰 카메라 체험은 받지 않음"
  if [[ " $1 " == *" --public "* ]]; then
    echo "  조작 화면 — 이 PC와 휴대폰 모두 이 주소 하나 (${after})"
  else
    echo "  조작 화면 — 이 PC에서 (${after})"
  fi
  echo ""
  for address in ${addresses}; do
    echo "    ${scheme}://${address}:${DEFAULT_PORTAL_PORT}"
  done
  [[ "${scheme}" == "https" ]] && echo "" && echo "  자체 인증서라 처음 한 번 '안전하지 않음' 경고를 넘깁니다 (휴대폰은 같은 와이파이에서)"
  echo """"
  if [[ " $1 " == *" --no-camera "* ]]; then
    echo "  Webots 시뮬레이션 조작만 합니다 (휴대폰 카메라 체험은 서버 PC의 포털 — run_server.sh)"
  elif [[ " $1 " == *" --webots-port "* ]]; then
    echo "  머리줄의 '휴대폰 카메라' 스위치: 끄면 Webots 시뮬레이션 조작, 켜면 그 기기의 카메라로 체험"
  else
    echo "  열면 바로 그 기기의 카메라로 체험합니다 (휴대폰 6대까지 함께)"
  fi
  echo "================================================================================"
  echo ""
}

# 로그인 토큰을 (없으면 logs/token에 만들어) 터미널에만 보여 준다. 기록 파일에는 남기지 않는다 (common/auth.py)
show_tokens() {
  echo ""
  ( cd "${PROJECT_DIR}" && "${PYTHON}" -m common.auth --create ) | sed 's/^/  /' || { echo "토큰을 준비하지 못했습니다." >&2; exit 1; }
  echo "  관제·조작 화면을 처음 열면 이 토큰을 묻습니다 (다시 보기: python -m common.auth)."
  echo ""
}

# Webots를 이 PC(루프백)에서만 열게 하는 LD_PRELOAD 라이브러리 (simulation/webots/loopback_only.c — KISA 설계 SR2-4).
# Webots는 받는 주소를 고르는 옵션이 없어 웹 서버(1234)가 모든 주소로 열린다. 처음 한 번 gcc로 만들고 경로를 찍는다.
# 만들 수 없으면(gcc 없음) 아무것도 찍지 않는다.
webots_loopback_lib() {
  local src="${PROJECT_DIR}/simulation/webots/loopback_only.c"
  local out="${PROJECT_DIR}/simulation/webots/build/loopback_only.so"
  if [[ ! -f "${out}" || "${src}" -nt "${out}" ]]; then
    command -v gcc >/dev/null 2>&1 || return 0
    mkdir -p "$(dirname "${out}")"
    gcc -shared -fPIC -O2 -o "${out}" "${src}" -ldl 2>/dev/null || return 0
  fi
  echo "${out}"
}

# ----- 서버 띄우기 (run.sh · run_server.sh · run_sim.sh) -----

# 이 스크립트가 켠 것들의 PID — 끝날 때(Webots를 닫음·Ctrl+C) stop_started가 끈다. 관제 서버(start_detached)는 넣지 않는다
STARTED_PIDS=()
stop_started() {
  for pid in "${STARTED_PIDS[@]+"${STARTED_PIDS[@]}"}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
}

# 뒤에서 켜고 포트가 열릴 때까지 기다린다: start_background <이름> <포트> <로그> <명령...>
start_background() {
  local label="$1" port="$2" log="$3"; shift 3
  if port_in_use 127.0.0.1 "${port}"; then
    echo "==> ${label}: 127.0.0.1:${port}에서 이미 돌고 있어 그대로 씁니다."
    return 0
  fi
  "$@" >> "${log}" 2>&1 &
  STARTED_PIDS+=($!)
  if ! wait_for_port 127.0.0.1 "${port}" 15; then
    echo "오류: ${label}가 15초 안에 켜지지 않았습니다. ${log}를 확인하세요." >&2
    tail -n 20 "${log}" >&2 || true
    exit 1
  fi
}

# 세션과 따로 켜 둔다: 스크립트가 끝나도 끄지 않는다. PID는 <pidfile> (stop_dashboard.sh가 쓴다)
start_detached() {
  local label="$1" port="$2" log="$3" pidfile="$4" web_port="$5"; shift 5
  if port_in_use 127.0.0.1 "${port}"; then
    echo "==> ${label}: 127.0.0.1:${port}에서 이미 돌고 있어 그대로 씁니다."
    return 0
  fi
  if port_in_use 127.0.0.1 "${web_port}"; then  # 접속 포트는 비었는데 화면 포트를 다른 것이 쓴다 — 관제 서버가 잠깐 기다려 본다
    echo "주의: 포트 ${web_port}를 다른 프로그램이 쓰고 있습니다. 관제 서버가 20초까지 기다려 봅니다 (WSL 미러 네트워크면 Windows 쪽 프로그램도 확인)."
  fi
  setsid nohup "$@" >> "${log}" 2>&1 < /dev/null &
  echo $! > "${pidfile}"
  if ! wait_for_port 127.0.0.1 "${web_port}" 40; then
    echo "오류: ${label}가 40초 안에 켜지지 않았습니다. ${log}를 확인하세요." >&2
    tail -n 20 "${log}" >&2 || true
    exit 1
  fi
  echo "==> ${label}: 이 스크립트가 끝나도 계속 돕니다 (끄려면 bash scripts/stop_dashboard.sh)"
}

# 다른 기기에도 열지: SERVE_PUBLIC(1이면 연다 — --public 또는 setup.sh config --serve-public) → SERVE_PUBLIC_ARGS·BRAIN_HOST
SERVE_PUBLIC="${REFLEXGUARD_SERVE_PUBLIC:-0}"  # 기본은 이 PC에서만 (KISA 공급망 체크리스트 7: 기본 설정은 닫힌 쪽으로)
SERVE_PUBLIC_ARGS=()
BRAIN_HOST="127.0.0.1"
apply_serve_public() {
  SERVE_PUBLIC_ARGS=()
  BRAIN_HOST="127.0.0.1"
  if [[ "${SERVE_PUBLIC}" == 1 ]]; then
    SERVE_PUBLIC_ARGS=(--public)
    BRAIN_HOST="0.0.0.0"
  fi
}

# 설정(setup.sh config)의 관제·연산 서버가 다른 PC인지 → REMOTE_DASHBOARD·REMOTE_BRAIN (이 PC면 빈 값).
# 이 PC 자신의 이름·주소를 적어 둔 경우(서버 PC)는 이 PC로 본다
is_remote() {  # is_remote <주소> — 다른 PC의 서버인가
  local host="${1#tls://}" own
  case "$1" in ""|none) return 1 ;; esac
  [[ "${host}" == \[* ]] && host="${host%%]*}]" || host="${host%%:*}"
  case "${host}" in 127.*|localhost|"[::1]") return 1 ;; esac
  own=" localhost $(hostname 2>/dev/null) $(hostname 2>/dev/null).local ${REFLEXGUARD_PUBLIC_NAMES:-} $(
    "${PYTHON}" -c 'from common.tls import lan_addresses; print(" ".join(lan_addresses()))' 2>/dev/null || true) "
  own="${own//,/ }"
  case "${own}" in *" ${host} "*) return 1 ;; esac
  return 0
}
find_remote_servers() {
  REMOTE_DASHBOARD=""
  REMOTE_BRAIN=""
  if is_remote "${REFLEXGUARD_DASHBOARD:-}"; then REMOTE_DASHBOARD="${REFLEXGUARD_DASHBOARD}"; fi
  if is_remote "${REFLEXGUARD_BRAIN_SERVER:-}"; then REMOTE_BRAIN="${REFLEXGUARD_BRAIN_SERVER}"; fi
}

# 이미 이 PC에서만 받는 서버가 돌고 있으면 그대로 쓰게 되므로 다른 기기에 열리지 않는다 — 미리 알린다
warn_if_local_only() {  # warn_if_local_only <이름> <포트> <끄는 법>
  [[ "${SERVE_PUBLIC}" == 1 ]] && command -v ss >/dev/null 2>&1 || return 0
  local listen
  listen="$(ss -ltn "sport = :$2" 2>/dev/null | awk 'NR>1 {print $4}' | tr '\n' ' ')"
  if [[ -n "${listen}" ]] && ! grep -qE '(^| )(0\.0\.0\.0|\*|\[::\]):' <<< "${listen}"; then
    echo "주의: ${1} — 이미 이 PC에서만 받는 채로 돌고 있습니다(${listen% }) — 다른 기기에서는 닿지 않습니다. ${3} 뒤에 다시 실행하세요." >&2
  fi
}

# 이 PC에 관제 서버를 켠다 (스크립트가 끝나도 계속) → LOCAL_DASHBOARD = 이 PC의 접속 주소
start_local_dashboard() {
  warn_if_local_only "관제 서버" "${DEFAULT_DASHBOARD_WEB_PORT}" "bash scripts/stop_dashboard.sh"
  start_detached "관제 대시보드" "${DEFAULT_DASHBOARD_AGENT_PORT}" logs/dashboard_server.log logs/dashboard.pid \
    "${DEFAULT_DASHBOARD_WEB_PORT}" \
    "${PYTHON}" -m dashboard.server --web-port "${DEFAULT_DASHBOARD_WEB_PORT}" --agent-port "${DEFAULT_DASHBOARD_AGENT_PORT}" \
    "${SERVE_PUBLIC_ARGS[@]+"${SERVE_PUBLIC_ARGS[@]}"}"
  LOCAL_DASHBOARD="127.0.0.1:${DEFAULT_DASHBOARD_AGENT_PORT}"
  echo "==> 관제 대시보드: http://localhost:${DEFAULT_DASHBOARD_WEB_PORT}"
}

# 이 PC에 커넥톰 연산 서버를 켠다 (스크립트가 끝나면 끈다): start_local_brain <포트> [관제 서버 주소]
#   관제 서버 주소가 없으면 관제에 붙지 않는다 — 설정의 다른 PC 관제 서버로 가지 않게 (이 PC의 장치 열쇠는 로봇 역할에만 통해,
#   연산 서버로 붙으면 인증이 거듭 실패해 그 PC가 이 주소를 잠글 수 있다)
start_local_brain() {
  local port="$1" dashboard="${2:-}"
  warn_if_local_only "커넥톰 연산 서버" "${port}" "그 연산 서버를 끈"
  if [[ -n "${dashboard}" ]]; then
    start_background "커넥톰 연산 서버" "${port}" logs/brain_server.log \
      "${PYTHON}" -m brain_server.server --host "${BRAIN_HOST}" --port "${port}" --dashboard "${dashboard}"
  else
    start_background "커넥톰 연산 서버" "${port}" logs/brain_server.log \
      env REFLEXGUARD_DASHBOARD= "${PYTHON}" -m brain_server.server --host "${BRAIN_HOST}" --port "${port}"
  fi
}

# 이 PC에 체험·조작 포털을 켠다 (스크립트가 끝나면 끈다): start_portal [관제 서버 주소] [포털 옵션...]
#   --webots-port N이면 Webots로 이어 주고, --no-camera면 카메라 체험을 받지 않는다. 다른 기기에 열면 HTTPS로
start_portal() {
  local dashboard="${1:-}"; shift || true
  PORTAL_ARGS=(--port "${DEFAULT_PORTAL_PORT}" "$@")
  if [[ "${SERVE_PUBLIC}" == 1 ]]; then
    if command -v openssl >/dev/null 2>&1; then
      PORTAL_ARGS+=(--https --public)
    else
      echo "주의: openssl이 없어 HTTPS를 못 엽니다 — 포털은 이 PC에서만 엽니다(http://localhost:${DEFAULT_PORTAL_PORT}). bash setup.sh install portal"
    fi
  fi
  warn_if_local_only "체험·조작 포털" "${DEFAULT_PORTAL_PORT}" "그 포털을 끈"
  start_background "체험·조작 포털" "${DEFAULT_PORTAL_PORT}" logs/portal.log \
    "${PYTHON}" -m demo.server "${PORTAL_ARGS[@]}" --dashboard "${dashboard}"   # 빈 값이면 관제에 붙지 않는다 (연산 서버와 같은 까닭)
  print_portal_urls "${PORTAL_ARGS[*]}"
  [[ "${SERVE_PUBLIC}" == 1 ]] || echo "  (이 PC에서만 열었습니다. 휴대폰·다른 PC에서 쓰려면 --public)"
  export REFLEXGUARD_PORTAL_SHOWN=1
}

print_help() {  # 호출한 스크립트 맨 위의 주석을 사용법으로 보여 준다
  sed -n '2,/^$/p' "$1" | sed 's/^# \{0,1\}//'
}
