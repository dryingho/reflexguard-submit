#!/usr/bin/env bash
# 서버 티어: 관제 대시보드 + 체험·조작 서버(+ Webots 대역). 모두 컨테이너 안에서만(127.0.0.1) 열고,
# 바깥으로는 감사 프록시만 노출한다. 그래서 들어오는 모든 요청이 감사 프록시의 로그를 거친다 (로그 수집 극대화).
# LAB_VERSION=before|after 로 보안 조치 전 고정본/조치 후 본편 중 무엇을 돌릴지 고른다 (attacklab/lab_version.sh).
set -euo pipefail
source "${LAB_APP:-/app}/attacklab/lab_version.sh"
cd "${CODE}"

wait_port() {  # 포트가 열릴 때까지 잠깐 기다린다
  for _ in $(seq 1 30); do
    if python -c "import socket;socket.create_connection(('127.0.0.1',$1),0.3).close()" 2>/dev/null; then return; fi
    sleep 0.3
  done
}

# 앱 자체 패치: REFLEXGUARD_DASHBOARD_TOKEN을 주면 대시보드가 직접 토큰을 확인한다 (프록시 토글과 별개의 '앱단 패치').
python -m dashboard.server --host 127.0.0.1 --web-port 18080 --agent-port 18766 &
PIDS=($!)

# Webots 대역 (안 11234) — 체험·조작 서버가 이어 주는 웹소켓이 어디까지 닿는지 기록한다
python "${APP}/attacklab/server/webots_stub.py" &
PIDS+=($!)

# 체험·조작 서버 (안 18443, 랩에서는 HTTP) — 조작 화면 + Webots 중계 + 카메라 체험
python -m demo.server --port 18443 --webots-port 11234 --dashboard 127.0.0.1:18766 &
PIDS+=($!)

wait_port 18080
wait_port 18443

# 체험·조작 서버 앞 감사 프록시 (바깥 8443 → 안 18443). 웹소켓 업그레이드도 그대로 이어 준다.
LAB_PROXY_ROLE="server(체험·조작 서버) 감사 프록시" LAB_AUDIT_NAME="portal" \
LAB_LISTEN_WEB="0.0.0.0:8443" LAB_UPSTREAM_WEB="127.0.0.1:18443" \
LAB_LISTEN_AGENT="off" \
  python "${APP}/attacklab/server/audit_proxy.py" &
PIDS+=($!)

export LAB_PROXY_ROLE="server(대시보드) 감사 프록시"
export LAB_LISTEN_WEB="0.0.0.0:8080"   LAB_UPSTREAM_WEB="127.0.0.1:18080"
export LAB_LISTEN_AGENT="0.0.0.0:8766" LAB_UPSTREAM_AGENT="127.0.0.1:18766"
export LAB_AGENT_LABEL="로봇·연산 서버 접속(TCP)"

# 프록시가 죽으면 나머지도 내린다 (컨테이너가 깔끔히 끝나게)
trap 'kill "${PIDS[@]}" 2>/dev/null || true' EXIT
python "${APP}/attacklab/server/audit_proxy.py"
