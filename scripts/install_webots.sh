#!/usr/bin/env bash
#
# ReflexGuard — Webots R2025a 설치 스크립트 (Ubuntu/Debian)
#
# 하는 일
#   1) Webots R2025a를 설치한다.
#        - deb 방식(기본, 권장): 공식 .deb를 받아 apt로 설치 → `webots` 명령 생성.
#        - tarball 방식: 홈 폴더(~/webots)에 풀기만 한다(시스템 변경 없음, sudo 불필요).
#   2) Webots 컨트롤러가 쓰는 Python(Linux 기본 `python3`)에 numpy를 깔아 둔다.
#      (컨트롤러 reflexguard.py가 numpy를 import 하기 때문. docs/install.md 문제 해결의 "No module named numpy" 대비)
#
# 공식 자산 (R2025a, Ubuntu 22.04 & 24.04)
#   deb     : webots_2025a_amd64.deb
#   tarball : webots-R2025a-x86-64.tar.bz2
#
# 사용법
#   bash scripts/install_webots.sh                  # deb 방식(권장, sudo 필요)
#   bash scripts/install_webots.sh --method tarball # tarball 방식(~/webots)
#   bash scripts/install_webots.sh --skip-numpy     # Webots용 numpy 설치 건너뛰기
#
# 주의
#   - amd64(x86-64) 전용입니다. ARM에서는 동작하지 않습니다.
#   - WSL에서 GUI로 열려면 WSLg(윈도우 11) 또는 X 서버가 필요합니다.

set -euo pipefail

VERSION="R2025a"
DEB_URL="https://github.com/cyberbotics/webots/releases/download/${VERSION}/webots_2025a_amd64.deb"
TARBALL_URL="https://github.com/cyberbotics/webots/releases/download/${VERSION}/webots-${VERSION}-x86-64.tar.bz2"
# 받은 파일의 SHA-256 (KISA 구현 2-15 무결성 검사 없는 코드 다운로드). Cyberbotics가 R2025a에는 공식 해시를 내지 않아
# 비어 있으면 계산한 값을 보여 주기만 한다 — 믿을 수 있는 곳에서 한 번 받아 확인한 값을 넣어 두면 다음부터는 다르면 멈춘다.
# 환경 변수 WEBOTS_DEB_SHA256 / WEBOTS_TARBALL_SHA256로도 줄 수 있다.
DEB_SHA256="${WEBOTS_DEB_SHA256:-}"
TARBALL_SHA256="${WEBOTS_TARBALL_SHA256:-}"

METHOD="deb"
INSTALL_NUMPY=1
TARBALL_DEST="${HOME}"

while [ $# -gt 0 ]; do
  case "$1" in
    --method) METHOD="${2:-}"; shift 2 ;;
    --method=*) METHOD="${1#*=}"; shift ;;
    --skip-numpy) INSTALL_NUMPY=0; shift ;;
    --dest) TARBALL_DEST="${2:-}"; shift 2 ;;
    --dest=*) TARBALL_DEST="${1#*=}"; shift ;;
    -h|--help)
      sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

if [ "${METHOD}" != "deb" ] && [ "${METHOD}" != "tarball" ]; then
  echo "오류: --method 는 deb 또는 tarball 이어야 합니다 (받은 값: ${METHOD})" >&2
  exit 2
fi

# --- 아키텍처 확인 (amd64 전용) ---
ARCH="$(uname -m)"
if [ "${ARCH}" != "x86_64" ] && [ "${ARCH}" != "amd64" ]; then
  echo "오류: Webots ${VERSION} 리눅스 빌드는 x86-64 전용입니다 (현재: ${ARCH})." >&2
  exit 1
fi

# --- 다운로드 도구 ---
download() {  # download <url> <out>
  local url="$1" out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --progress-bar -o "${out}" "${url}"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "${out}" "${url}"
  else
    echo "오류: curl 또는 wget이 필요합니다." >&2
    exit 1
  fi
}

verify() {  # verify <file> <기대 sha256 또는 빈 값>
  local file="$1" expected="$2" actual
  actual="$(sha256sum "${file}" | awk '{print $1}')"
  echo "    SHA-256: ${actual}"
  if [ -z "${expected}" ]; then
    echo "    (고정한 해시가 없어 비교하지 않았습니다 — 위 값을 기록해 두면 다음 설치 때 확인합니다)"
  elif [ "${actual}" != "${expected}" ]; then
    echo "오류: 받은 파일의 해시가 고정한 값과 다릅니다. 설치하지 않습니다 (변조·손상 의심)." >&2
    exit 1
  else
    echo "    고정한 해시와 같습니다."
  fi
}

# 이미 설치돼 있으면 알려주고 끝낸다 (deb 방식 한정)
if [ "${METHOD}" = "deb" ] && command -v webots >/dev/null 2>&1; then
  echo "==> 이미 webots 명령이 있습니다: $(command -v webots)"
  echo "    다시 설치하려면 'sudo apt-get remove webots' 후 재실행하세요."
else
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "${TMP_DIR}"' EXIT

  if [ "${METHOD}" = "deb" ]; then
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "오류: deb 방식은 apt가 있는 Debian/Ubuntu에서만 됩니다. --method tarball 을 쓰세요." >&2
      exit 1
    fi
    DEB_FILE="${TMP_DIR}/webots_2025a_amd64.deb"
    echo "==> Webots ${VERSION} .deb 내려받기"
    download "${DEB_URL}" "${DEB_FILE}"
    verify "${DEB_FILE}" "${DEB_SHA256}"
    echo "==> apt로 설치 (의존성 자동 해결, sudo 필요)"
    sudo apt-get update
    sudo apt-get install -y "${DEB_FILE}"
    echo "==> 설치 완료: $(command -v webots || echo webots)"
  else
    TARBALL_FILE="${TMP_DIR}/webots-${VERSION}-x86-64.tar.bz2"
    echo "==> Webots ${VERSION} tarball 내려받기"
    download "${TARBALL_URL}" "${TARBALL_FILE}"
    verify "${TARBALL_FILE}" "${TARBALL_SHA256}"
    echo "==> ${TARBALL_DEST} 에 풀기"
    mkdir -p "${TARBALL_DEST}"
    tar -xjf "${TARBALL_FILE}" -C "${TARBALL_DEST}"
    echo "==> 설치 위치: ${TARBALL_DEST}/webots"
    echo "    실행 파일: ${TARBALL_DEST}/webots/webots"
    echo "    PATH에 넣으려면 ~/.bashrc 에 다음 줄을 추가하세요:"
    echo "        export WEBOTS_HOME=${TARBALL_DEST}/webots"
    echo "        export PATH=\$WEBOTS_HOME:\$PATH"
  fi
fi

# --- Webots 런타임 시스템 라이브러리 (libsndio) ---
# Webots 바이너리는 sndio(오디오) 라이브러리에 링크돼 있어, 없으면 화면 없이(xvfb) 돌려도
# "libsndio.so.7: cannot open shared object file"로 시작조차 못 한다.
# deb 방식은 보통 apt가 함께 끌어오지만, tarball 방식·최소 구성(헤드리스) 시스템·일부 배포판에서는
# 빠질 수 있어 여기서 확인해 둔다. (Qt6·ode·assimp·OIS·pico 등은 Webots가 품고 있어 따로 깔지 않는다.)
echo ""
echo "==> Webots 런타임 라이브러리 확인 (libsndio)"
if ldconfig -p 2>/dev/null | grep -q 'libsndio\.so\.7'; then
  echo "    이미 있음: libsndio.so.7"
elif command -v apt-get >/dev/null 2>&1; then
  echo "    apt로 libsndio7.0 설치 (sudo 필요)"
  sudo apt-get update && sudo apt-get install -y libsndio7.0
else
  echo "    libsndio.so.7 이 없습니다 — 배포판 패키지로 sndio 런타임을 설치하세요 (Debian/Ubuntu: libsndio7.0)." >&2
fi

# --- Webots 컨트롤러용 numpy ---
# Webots는 리눅스에서 기본으로 `python3` 명령을 쓴다. 그 python3에 numpy가 있어야
# simulation/webots/controllers/reflexguard/reflexguard.py 가 돈다. (venv를 쓰려면 Webots Preferences의
# Python command 에 .venv/bin/python 경로를 넣으면 된다.)
if [ "${INSTALL_NUMPY}" -eq 1 ]; then
  echo ""
  echo "==> Webots 컨트롤러용 numpy 확인/설치 (시스템 python3)"
  if python3 -c 'import numpy' >/dev/null 2>&1; then
    echo "    이미 numpy 있음: $(python3 -c 'import numpy; print(numpy.__version__)')"
  elif command -v apt-get >/dev/null 2>&1; then
    echo "    apt로 python3-numpy 설치 (sudo 필요)"
    sudo apt-get update && sudo apt-get install -y python3-numpy
  else
    echo "    python3 -m pip install numpy 로 설치 시도"
    python3 -m pip install --user numpy
  fi
else
  echo ""
  echo "==> --skip-numpy: Webots용 numpy 설치를 건너뜁니다."
  echo "    Webots에서 'No module named numpy'가 나오면 Preferences > Python command 를"
  echo "    numpy가 있는 python(예: 프로젝트 .venv/bin/python)으로 바꾸세요."
fi

echo ""
echo "완료. 월드 열기:"
echo "    webots simulation/webots/worlds/reflexguard.wbt   (또는 bash scripts/run.sh)"
