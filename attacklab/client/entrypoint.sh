#!/usr/bin/env bash
# 클라이언트 티어: 커넥톰 연산 서버 + 사용자(휠체어) 시뮬레이션.
# 커넥톰 통신도 감사 프록시로 한 번 거치게 해서 로그로 남긴다 (연산 서버는 컨테이너 안에서만 열고, 바깥 8765는 탭만 노출).
# LAB_VERSION=before|after 로 보안 조치 전 고정본/조치 후 본편 중 무엇을 돌릴지 고른다 (attacklab/lab_version.sh).
set -euo pipefail
source "${LAB_APP:-/app}/attacklab/lab_version.sh"
cd "${CODE}"

# 연산 서버·사용자 시뮬레이션이 붙을 관제 대시보드 (서버 티어, 기본 server:8766 — 감사 프록시 경유)
export REFLEXGUARD_DASHBOARD="${REFLEXGUARD_DASHBOARD:-server:8766}"

# 커넥톰 연산 서버 (컨테이너 안 18765). 관제에도 붙어 접속 상태와 뉴런 발화율(관제 화면의 '루밍 동작 보기' ④)을 보낸다
python -m brain_server.server --host 127.0.0.1 --port 18765 &
BRAIN_PID=$!

for _ in $(seq 1 30); do
  if python -c "import socket;socket.create_connection(('127.0.0.1',18765),0.3).close()" 2>/dev/null; then break; fi
  sleep 0.3
done

# 커넥톰 통신 감사 탭 (바깥 8765 → 안 18765). 웹 쪽은 끈다.
LAB_PROXY_ROLE="client(커넥톰) 감사 프록시" LAB_AUDIT_NAME="connectome" \
LAB_LISTEN_WEB="off" \
LAB_LISTEN_AGENT="0.0.0.0:8765" LAB_UPSTREAM_AGENT="127.0.0.1:18765" \
LAB_AGENT_LABEL="커넥톰 통신(TCP)" \
  python "${APP}/attacklab/server/audit_proxy.py" &
PROXY_PID=$!
sleep 1

# 사용자 시뮬레이션 → 대시보드·연산 서버(이 탭 경유 127.0.0.1:8765)
# 로봇도 고른 버전의 코드(LAB_CODE_ROOT)로 돈다 — 접속 방식이 버전마다 다를 수 있으므로.
export REFLEXGUARD_BRAIN_SERVER="127.0.0.1:8765"
export LAB_CODE_ROOT="${CODE}"

trap 'kill ${BRAIN_PID} ${PROXY_PID} 2>/dev/null || true' EXIT
exec python "${APP}/attacklab/client/robot_sim.py"
