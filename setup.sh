#!/usr/bin/env bash
#
# ReflexGuard — 설치·관리 (파트별). 이 PC의 환경(운영체제·배포판·패키지 관리자·WSL·CPU·sudo·화면)을 알아보고,
# 고른 파트에 필요한 것만 확인·설치한다. 다시 실행해도 이미 된 것은 건너뛴다.
#
# 사용법
#   bash setup.sh                          # 상태 보기 (= check, 기록해 둔 파트 또는 전부)
#   bash setup.sh check [파트...]           # 무엇이 있고 없는지 — 아무것도 바꾸지 않는다
#   bash setup.sh install <파트...>         # 필요한 것만 설치하고 이 PC의 파트로 기록 (.setup-parts) → 이어서 config
#   bash setup.sh config                   # 이 PC의 설정(reflexguard.local.env): 다른 PC 주소·토큰·지문, 이 PC 이름 → 인증서
#   bash setup.sh update                   # 받아 온 새 코드에 맞춰 기록한 파트를 다시 맞춘다 (코드는 직접 받는다 — git·꾸러미 등)
#   bash setup.sh test                     # 자동 시험 (tools 파트)
#   bash setup.sh security                 # 보안 점검: 파일 권한·열린 포트·기록 무결성·bandit·semgrep·pip-audit (점검 도구는 해시 잠금)
#   bash setup.sh status                   # 켜져 있는 ReflexGuard 서버·포트
#   bash setup.sh clean                    # 만든 실행 환경(.venv 등)을 지운다 — 기록·토큰·인증서는 그대로
#   bash setup.sh bundle [파트...]          # 파트별 꾸러미(tar.gz) — 그 PC에 필요한 파일만 (tools/bundle.py, dist/)
#
# 파트 (docs/architecture.md의 파트 나눔)
#   wheelchair  ① 휠체어 PC         파이썬 3.10+·numpy (녹화·영상 만들기: ffmpeg 선택)
#   brain       ② 커넥톰 연산 서버   파이썬·numpy, 다른 PC와 쓰면 openssl(TLS)
#   dashboard   ③ 관제 서버         파이썬·numpy, 다른 PC와 쓰면 openssl(TLS)
#   sim         ④ 시뮬레이션        Webots R2025a + gcc — 실제 휠체어(①) 대신 한 PC에서 전부 돌려 봄. run.sh가 ②③⑥도 켜므로 함께 깐다
#   tools       ⑤ 시험·보정 도구     파이썬·numpy·pytest (도착 시험: Webots·xvfb 선택)
#   portal      ⑥ 체험·조작 포털     파이썬·numpy·openssl(휴대폰 HTTPS)
#   lab         어택랩             Docker·Docker Compose — 확인만 (설치는 attacklab/INSTALL.md)
#   all         위 전부
#
# 옵션
#   --yes            묻지 않고 진행           --dry-run      할 일만 보여 주고 아무것도 바꾸지 않는다
#   --no-system      시스템 패키지(sudo)는 건드리지 않는다 (필요한 설치 명령만 알려 준다)
#   --minimal        선택 항목(ffmpeg·xvfb 등)은 빼고 꼭 필요한 것만
#   --webots-method deb|tarball               Webots 설치 방식 (기본: apt가 있으면 deb, 없으면 tarball → ~/webots)
#
# 여러 PC로 나눠 쓸 때 (config — install 끝에서도 묻는다. 묻지 않으려면 아래 옵션으로)
#   --serve-public / --local-only             이 PC의 서버(관제·연산)를 다른 PC에도 연다(TLS) / 이 PC에서만
#   --names "control-pc.local 192.168.0.10"  이 PC를 부를 이름·주소 (인증서에 넣는다 — 고정 IP나 .local 이름 권장)
#   --dashboard tls://host:8766               관제 서버 주소     --brain tls://host:8765   연산 서버 주소   (none = 안 씀)
#   --pin sha256:…                            서버 공개키 지문 (받아 온 값과 대조)
#   --device-name 이름 · --device-key-stdin   휠체어 PC: 장치 이름과 장치 열쇠(서버 PC에서 python -m common.device_auth 이름)
#   --token-stdin                             다른 서버 PC: 서버 토큰을 표준 입력으로
#
# 지원: Ubuntu 22.04·24.04, Debian 12, WSL2 위의 이들, Fedora·Arch·openSUSE·Alpine(apt 외 — 파이썬 파트), macOS(Homebrew —
#       파이썬 파트, Webots는 공식 설치 파일로). Webots(④)는 리눅스 x86-64·macOS만. Windows는 WSL2 안에서 쓴다.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
VENV="${ROOT}/.venv"
SECURITY_VENV="${ROOT}/.venv-security"
STATE_FILE="${ROOT}/.setup-parts"
ALL_PARTS="wheelchair brain dashboard sim tools portal lab"
[ -d "${ROOT}/tests" ] || ALL_PARTS="wheelchair brain dashboard sim portal lab"  # 제출본: 시험·보정 도구(⑤)는 들어 있지 않다
MIN_PY_MINOR=10
SECURITY_LOCK="tools/locks/security.txt"          # 점검 도구(bandit·pip-audit·semgrep)의 판·해시 잠금
AUDIT_EXCEPTIONS="tools/locks/audit-exceptions.txt"  # 점검 도구 의존성의 '경로 없음' 판정 (근거·다시 볼 조건과 함께)

ASSUME_YES=0
DRY_RUN=0
NO_SYSTEM=0
MINIMAL=0
WEBOTS_METHOD=""
CFG_SERVE_PUBLIC=""
CFG_NAMES=""
CFG_DASHBOARD=""
CFG_BRAIN=""
CFG_PINS=""
CFG_TOKEN_STDIN=0
CFG_DEVICE_NAME=""
CFG_DEVICE_KEY_STDIN=0
ENV_FILE="${REFLEXGUARD_ENV_FILE-${ROOT}/reflexguard.local.env}"   # 시험은 다른 파일로 (빈 값이면 설정 파일을 쓰지 않는다)

# ---------------------------------------------------------------- 출력
if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_END=$'\033[0m'
else
  C_OK=""; C_WARN=""; C_BAD=""; C_DIM=""; C_END=""
fi
say()   { printf '%s\n' "$*"; }
title() { printf '\n== %s ==\n' "$*"; }
mark()  {  # mark ok|warn|bad|skip <글>
  case "$1" in
    ok)   printf '  %s✓%s %s\n' "${C_OK}" "${C_END}" "$2" ;;
    warn) printf '  %s△%s %s\n' "${C_WARN}" "${C_END}" "$2" ;;
    bad)  printf '  %s✗%s %s\n' "${C_BAD}" "${C_END}" "$2" ;;
    *)    printf '  %s·%s %s\n' "${C_DIM}" "${C_END}" "$2" ;;
  esac
}
die() { printf '%s오류:%s %s\n' "${C_BAD}" "${C_END}" "$*" >&2; exit 1; }

confirm() {  # confirm <질문> — --yes면 참, 화면이 없으면(파이프) 거짓
  [ "${ASSUME_YES}" -eq 1 ] && return 0
  [ -t 0 ] || return 1
  printf '%s [y/N] ' "$1"
  local answer=""
  read -r answer || true
  case "${answer}" in y|Y|yes|YES|예|네) return 0 ;; *) return 1 ;; esac
}

run() {  # run <명령...> — --dry-run이면 보여 주기만
  if [ "${DRY_RUN}" -eq 1 ]; then
    say "  [할 일] $*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------- 환경 알아보기
# 시험용: SETUP_UNAME_S·SETUP_UNAME_M·SETUP_KERNEL·SETUP_OS_RELEASE·SETUP_PKG로 다른 환경인 척, SETUP_PRETEND_MISSING으로
# 항목이 없는 척할 수 있다 (tests/test_setup.py — --dry-run과 함께).
detect_env() {
  OS_NAME="${SETUP_UNAME_S:-$(uname -s)}"
  ARCH="${SETUP_UNAME_M:-$(uname -m)}"
  KERNEL="${SETUP_KERNEL:-$(uname -r)}"
  OS_RELEASE="${SETUP_OS_RELEASE:-/etc/os-release}"
  DISTRO_ID=""; DISTRO_LIKE=""; DISTRO_VERSION=""; DISTRO_NAME=""
  case "${OS_NAME}" in
    Linux) OS_KIND="linux" ;;
    Darwin) OS_KIND="macos"; DISTRO_ID="macos"; DISTRO_NAME="macOS" ;;
    MINGW*|MSYS*|CYGWIN*) OS_KIND="windows" ;;
    *) OS_KIND="other" ;;
  esac
  if [ "${OS_KIND}" = "linux" ] && [ -r "${OS_RELEASE}" ]; then
    DISTRO_ID="$(. "${OS_RELEASE}"; printf '%s' "${ID:-}")"
    DISTRO_LIKE="$(. "${OS_RELEASE}"; printf '%s' "${ID_LIKE:-}")"
    DISTRO_VERSION="$(. "${OS_RELEASE}"; printf '%s' "${VERSION_ID:-}")"
    DISTRO_NAME="$(. "${OS_RELEASE}"; printf '%s' "${PRETTY_NAME:-${ID:-linux}}")"
  fi
  IS_WSL=0
  case "${KERNEL}" in *microsoft*|*Microsoft*|*WSL*) IS_WSL=1 ;; esac

  PKG="none"
  if [ -n "${SETUP_PKG:-}" ]; then PKG="${SETUP_PKG}"
  elif [ "${OS_KIND}" = "macos" ]; then
    command -v brew >/dev/null 2>&1 && PKG="brew"
  elif [ -n "${SETUP_OS_RELEASE:-}" ]; then  # 시험: 배포판 이름으로 고른다
    PKG="$(pkg_for_distro "${DISTRO_ID} ${DISTRO_LIKE}")"
  else
    for candidate in apt-get:apt dnf:dnf yum:yum pacman:pacman zypper:zypper apk:apk; do
      if command -v "${candidate%%:*}" >/dev/null 2>&1; then PKG="${candidate##*:}"; break; fi
    done
  fi

  if [ "$(id -u)" -eq 0 ]; then SUDO=""; CAN_ROOT=1
  elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; CAN_ROOT=1
  else SUDO=""; CAN_ROOT=0
  fi
  [ "${PKG}" = "brew" ] && SUDO=""  # Homebrew는 sudo 없이

  HAS_GUI=0
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || [ "${OS_KIND}" = "macos" ]; then HAS_GUI=1; fi
}

pkg_for_distro() {  # "<ID> <ID_LIKE>" → 패키지 관리자
  case " $1 " in
    *" ubuntu "*|*" debian "*|*" linuxmint "*|*" pop "*) echo apt ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*) echo dnf ;;
    *" arch "*|*" manjaro "*|*" endeavouros "*) echo pacman ;;
    *" opensuse"*|*" suse "*|*" sles "*) echo zypper ;;
    *" alpine "*) echo apk ;;
    *) echo none ;;
  esac
}

webots_supported() {  # Webots R2025a를 이 환경에 깔 수 있는가
  case "${OS_KIND}:${ARCH}" in
    linux:x86_64|linux:amd64|macos:*) return 0 ;;
    *) return 1 ;;
  esac
}

show_env() {
  title "이 PC"
  local where="${DISTRO_NAME:-${OS_NAME}}"
  [ "${IS_WSL}" -eq 1 ] && where="${where} (WSL2)"
  mark skip "운영체제: ${where}, CPU ${ARCH}"
  mark skip "패키지 관리자: ${PKG}$([ "${CAN_ROOT}" -eq 1 ] || printf ' (sudo 없음 — 시스템 패키지는 직접)')"
  mark skip "화면: $([ "${HAS_GUI}" -eq 1 ] && printf '있음' || printf '없음 (Webots는 xvfb로)')"
  if [ "${OS_KIND}" = "windows" ]; then
    mark bad "Windows에서는 WSL2(Ubuntu) 안에서 실행하세요 — docs/install.md"
  fi
}

# ---------------------------------------------------------------- 파트 → 필요한 것
# 항목: python venv pydeps pydev openssl gcc ffmpeg xvfb webots docker token
#       (build — C++ 컴파일러·파이썬 헤더: 이 파이썬용 numpy wheel이 없을 때만 setup_venv가 찾아 깐다)
part_items() {  # part_items <파트> → "항목 req|opt" 줄들
  case "$1" in
    wheelchair) printf '%s\n' "python req" "venv req" "pydeps req" "ffmpeg opt" ;;
    brain)      printf '%s\n' "python req" "venv req" "pydeps req" "openssl opt" "token req" ;;
    dashboard)  printf '%s\n' "python req" "venv req" "pydeps req" "openssl opt" "token req" ;;
    portal)     printf '%s\n' "python req" "venv req" "pydeps req" "openssl req" "token req" ;;
    sim)        printf '%s\n' "python req" "venv req" "pydeps req" "webots req" "gcc req" "openssl opt" "xvfb opt" "token req" ;;
    tools)      printf '%s\n' "python req" "venv req" "pydeps req" "pydev req" "ffmpeg opt" "xvfb opt" "webots opt" ;;
    lab)        printf '%s\n' "docker req" ;;
  esac
}

item_label() {
  case "$1" in
    python)  echo "파이썬 3.${MIN_PY_MINOR} 이상" ;;
    venv)    echo "파이썬 가상환경(venv)" ;;
    pydeps)  echo "실행 패키지 (requirements.txt — numpy, 해시 확인)" ;;
    pydev)   echo "시험 패키지 (requirements-dev.txt — pytest)" ;;
    openssl) echo "openssl (TLS 인증서 — 다른 PC·휴대폰과 암호화)" ;;
    gcc)     echo "gcc (Webots 웹 서버를 이 PC에서만 열기 — simulation/webots/loopback_only.c)" ;;
    build)   echo "빌드 도구 (C++ 컴파일러·파이썬 헤더 — 이 파이썬용으로 미리 빌드된 numpy가 없을 때)" ;;
    ffmpeg)  echo "ffmpeg (녹화·결과 영상)" ;;
    xvfb)    echo "xvfb (화면 없이 Webots — 시험·서버)" ;;
    webots)  echo "Webots R2025a" ;;
    docker)  echo "Docker + Docker Compose" ;;
    token)   echo "로그인 토큰 (logs/token 0600 또는 REFLEXGUARD_TOKEN)" ;;
  esac
}

expand_parts() {  # 인자 → 중복 없는 파트 목록 (sim은 run.sh가 켜는 ②③⑥을 더한다)
  local out="" part
  for part in "$@"; do
    case "${part}" in
      all) out="${out} ${ALL_PARTS}" ;;
      sim) out="${out} sim brain dashboard portal" ;;
      tools) [ -d tests ] || die "이 제출본에는 시험·보정 도구(⑤ tools — tests/)가 들어 있지 않습니다"; out="${out} tools" ;;
      wheelchair|brain|dashboard|portal|lab) out="${out} ${part}" ;;
      *) die "모르는 파트 '${part}' — 고를 수 있는 파트: ${ALL_PARTS} all" ;;
    esac
  done
  local seen="" result=""
  for part in ${ALL_PARTS}; do
    case " ${out} " in *" ${part} "*) result="${result} ${part}" ;; esac
  done
  echo "${result# }"
}

saved_parts() { [ -f "${STATE_FILE}" ] && tr '\n' ' ' < "${STATE_FILE}" | sed 's/ *$//' || true; }

save_parts() {
  local merged
  merged="$(expand_parts $(saved_parts) "$@")"
  if [ "${DRY_RUN}" -eq 1 ]; then say "  [할 일] 이 PC의 파트 기록: ${merged}"; return; fi
  printf '%s\n' ${merged} > "${STATE_FILE}"
}

part_file() {  # part_file <파트> → 이 폴더에 그 파트가 있는지 볼 파일 (파트별 꾸러미로 받았을 때)
  case "$1" in
    wheelchair) echo "wheelchair/pipeline.py" ;; brain) echo "brain_server/server.py" ;; dashboard) echo "dashboard/server.py" ;;
    sim) echo "simulation/webots/worlds/reflexguard.wbt" ;; tools) echo "tests" ;; portal) echo "demo/server.py" ;;
    lab) echo "attacklab/docker-compose.yml" ;;
  esac
}

require_parts_present() {  # 꾸러미에 없는 파트를 설치하려 하면 멈춘다
  local part missing=""
  for part in "$@"; do [ -e "$(part_file "${part}")" ] || missing="${missing} ${part}"; done
  [ -z "${missing}" ] && return 0
  local have=""
  local py
  if [ -f BUNDLE.json ] && py="$(find_python)"; then
    have=" (이 꾸러미의 파트: $("${py}" -c 'import json; print(" ".join(json.load(open("BUNDLE.json"))["parts"]))' 2>/dev/null))"
  fi
  die "이 폴더에는 파트${missing}의 파일이 없습니다${have} — 그 파트의 꾸러미를 받거나 저장소 전체를 받으세요"
}

part_title() {
  case "$1" in
    wheelchair) echo "① 휠체어 PC" ;; brain) echo "② 커넥톰 연산 서버" ;; dashboard) echo "③ 관제 서버" ;;
    sim) echo "④ 시뮬레이션" ;; tools) echo "⑤ 시험·보정 도구" ;; portal) echo "⑥ 체험·조작 포털" ;; lab) echo "어택랩" ;;
  esac
}

# ---------------------------------------------------------------- 확인
find_python() {  # 3.10 이상인 파이썬 (3.10을 먼저 — CI와 같은 판)
  local candidate
  for candidate in python3.10 python3.11 python3.12 python3.13 python3; do
    if command -v "${candidate}" >/dev/null 2>&1 &&
       "${candidate}" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, ${MIN_PY_MINOR}) else 1)" 2>/dev/null; then
      command -v "${candidate}"
      return 0
    fi
  done
  return 1
}

pinned_version() {  # pinned_version <패키지> <파일> → .venv의 파이썬에 맞는 판 (줄 끝 '; python_version < "3.12"' 같은 조건을 따진다)
  "${VENV}/bin/python" - "$1" "$2" <<'PY'
import re, sys
name, path = sys.argv[1:3]
for line in open(path, encoding="utf-8"):
    m = re.match(rf'{re.escape(name)}==([^\s;\\]+)\s*(?:;\s*python_version\s*(<|>=)\s*"(\d+)\.(\d+)")?', line)
    if m and (not m[2] or (sys.version_info[:2] < (int(m[3]), int(m[4]))) == (m[2] == "<")):
        print(m[1])
        break
PY
}

webots_path() {
  local candidate
  for candidate in "$(command -v webots 2>/dev/null || true)" "${WEBOTS_HOME:+${WEBOTS_HOME}/webots}" \
                   "${HOME}/webots/webots" /usr/local/webots/webots /Applications/Webots.app/Contents/MacOS/webots; do
    if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then echo "${candidate}"; return 0; fi
  done
  return 1
}

DETAIL=""
check_item() {  # check_item <항목> → 0 있음 / 1 없음, DETAIL에 설명
  local py version
  DETAIL=""
  case " ${SETUP_PRETEND_MISSING:-} " in *" $1 "*) DETAIL="없음 (시험)"; return 1 ;; esac  # 시험: 없는 척
  case "$1" in
    python)
      if py="$(find_python)"; then DETAIL="${py} ($("${py}" --version 2>&1))"; return 0; fi
      DETAIL="없음"; return 1 ;;
    venv)
      py="$(find_python)" || { DETAIL="파이썬부터"; return 1; }
      if "${py}" -c "import venv, ensurepip" 2>/dev/null; then DETAIL="${py} -m venv"; return 0; fi
      DETAIL="venv·ensurepip 모듈이 없음"; return 1 ;;
    pydeps)
      [ -x "${VENV}/bin/python" ] || { DETAIL=".venv 없음"; return 1; }
      version="$(pinned_version numpy requirements.txt)"
      if "${VENV}/bin/python" -c "import numpy, sys; sys.exit(0 if numpy.__version__ == '${version}' else 1)" 2>/dev/null; then
        DETAIL=".venv numpy ${version}"; return 0
      fi
      DETAIL=".venv에 numpy ${version}이 없음"; return 1 ;;
    pydev)
      [ -x "${VENV}/bin/python" ] || { DETAIL=".venv 없음"; return 1; }
      version="$(pinned_version pytest requirements-dev.txt)"
      if "${VENV}/bin/python" -c "import pytest, sys; sys.exit(0 if pytest.__version__ == '${version}' else 1)" 2>/dev/null; then
        DETAIL=".venv pytest ${version}"; return 0
      fi
      DETAIL=".venv에 pytest ${version}이 없음"; return 1 ;;
    openssl|gcc|ffmpeg)
      if command -v "$1" >/dev/null 2>&1; then DETAIL="$(command -v "$1")"; return 0; fi
      DETAIL="없음"; return 1 ;;
    build)
      py="${VENV}/bin/python"; [ -x "${py}" ] || py="$(find_python)" || { DETAIL="파이썬부터"; return 1; }
      version="$(command -v c++ || command -v g++ || command -v clang++ || true)"
      [ -n "${version}" ] || { DETAIL="C++ 컴파일러가 없음"; return 1; }
      if "${py}" -c "import os, sys, sysconfig; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_paths()['include'], 'Python.h')) else 1)" 2>/dev/null; then
        DETAIL="${version}, Python.h"; return 0
      fi
      DETAIL="파이썬 헤더(Python.h)가 없음"; return 1 ;;
    xvfb)
      if command -v xvfb-run >/dev/null 2>&1; then DETAIL="$(command -v xvfb-run)"; return 0; fi
      if [ "${HAS_GUI}" -eq 1 ]; then DETAIL="없음 (화면이 있어 없어도 됨)"; else DETAIL="없음"; fi
      return 1 ;;
    webots)
      if py="$(webots_path)"; then
        version="$(cat "$(dirname "$(readlink -f "${py}" 2>/dev/null || echo "${py}")")/resources/version.txt" 2>/dev/null || true)"
        DETAIL="${py}${version:+ (${version})}"; return 0
      fi
      if webots_supported; then DETAIL="없음"; else DETAIL="이 환경(${OS_KIND} ${ARCH})에는 Webots R2025a가 없음 — 다른 파트만"; fi
      return 1 ;;
    docker)
      if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        DETAIL="$(docker --version 2>/dev/null | head -1)"; return 0
      fi
      DETAIL="없음 — attacklab/INSTALL.md"; return 1 ;;
    token)
      if [ -n "${REFLEXGUARD_TOKEN:-}${REFLEXGUARD_DASHBOARD_TOKEN:-}" ]; then DETAIL="환경 변수"; return 0; fi
      if [ -f "${ROOT}/logs/token" ]; then DETAIL="logs/token"; return 0; fi
      DETAIL="아직 없음 (서버를 처음 켤 때 만든다)"; return 1 ;;
  esac
}

do_check() {  # do_check <파트...> → 꼭 필요한 것이 빠졌으면 1
  local parts part line item level missing=0
  parts="$(expand_parts "$@")"
  for part in ${parts}; do
    title "$(part_title "${part}")"
    while read -r item level; do
      [ -n "${item}" ] || continue
      if check_item "${item}"; then
        mark ok "$(item_label "${item}"): ${DETAIL}"
      elif [ "${level}" = "req" ] && [ "${item}" != "token" ]; then
        mark bad "$(item_label "${item}"): ${DETAIL}"
        missing=1
      else
        mark warn "$(item_label "${item}"): ${DETAIL}$([ "${level}" = "opt" ] && printf ' (선택)')"
      fi
    done <<EOF
$(part_items "${part}")
EOF
  done
  echo
  if [ "${missing}" -eq 1 ]; then
    say "빠진 것이 있습니다 → bash setup.sh install ${parts}"
    return 1
  fi
  say "필요한 것이 모두 있습니다."
}

# ---------------------------------------------------------------- 설치
sys_packages() {  # sys_packages <항목> → 이 패키지 관리자에서의 패키지 이름들 (없으면 빈 줄)
  local ver=""
  case "${PKG}:$1" in
    apt:python)   echo "python3" ;;
    apt:venv)     ver="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)"
                  echo "python3-venv${ver:+ python${ver}-venv}" ;;
    apt:openssl)  echo "openssl" ;;
    apt:gcc)      echo "gcc libc6-dev" ;;
    apt:build)    ver="$("${VENV}/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)"
                  echo "g++ python3-dev${ver:+ python${ver}-dev}" ;;
    apt:ffmpeg)   echo "ffmpeg" ;;
    apt:xvfb)     echo "xvfb xauth libxcb-cursor0" ;;   # libxcb-cursor0: Webots R2025a(Qt 6.5+)가 xvfb 안에서도 xcb 플러그인에 필요
    dnf:python|yum:python) echo "python3" ;;
    dnf:openssl|yum:openssl) echo "openssl" ;;
    dnf:gcc|yum:gcc) echo "gcc glibc-devel" ;;
    dnf:build|yum:build) echo "gcc-c++ python3-devel" ;;
    dnf:ffmpeg)   echo "ffmpeg-free" ;;
    dnf:xvfb|yum:xvfb) echo "xorg-x11-server-Xvfb xcb-util-cursor" ;;
    pacman:python) echo "python" ;;
    pacman:openssl) echo "openssl" ;;
    pacman:gcc)   echo "gcc" ;;
    pacman:build) echo "gcc" ;;   # g++는 gcc에, 파이썬 헤더는 python에 들어 있다
    pacman:ffmpeg) echo "ffmpeg" ;;
    pacman:xvfb)  echo "xorg-server-xvfb xcb-util-cursor" ;;
    zypper:python) echo "python3" ;;
    zypper:openssl) echo "openssl" ;;
    zypper:gcc)   echo "gcc" ;;
    zypper:build) echo "gcc-c++ python3-devel" ;;
    zypper:ffmpeg) echo "ffmpeg" ;;
    zypper:xvfb)  echo "xvfb-run libxcb-cursor0" ;;
    apk:python)   echo "python3" ;;
    apk:openssl)  echo "openssl" ;;
    apk:gcc)      echo "gcc musl-dev" ;;
    apk:build)    echo "g++ musl-dev python3-dev" ;;
    apk:ffmpeg)   echo "ffmpeg" ;;
    apk:xvfb)     echo "xvfb-run xcb-util-cursor" ;;
    brew:python)  echo "python@3.12" ;;
    brew:openssl) echo "openssl@3" ;;
    brew:ffmpeg)  echo "ffmpeg" ;;
    *) echo "" ;;
  esac
}

install_command() {  # install_command <패키지...> → 한 줄 명령
  case "${PKG}" in
    apt)    echo "${SUDO:+${SUDO} }apt-get update && ${SUDO:+${SUDO} }apt-get install -y $*" ;;
    dnf)    echo "${SUDO:+${SUDO} }dnf install -y $*" ;;
    yum)    echo "${SUDO:+${SUDO} }yum install -y $*" ;;
    pacman) echo "${SUDO:+${SUDO} }pacman -S --needed --noconfirm $*" ;;
    zypper) echo "${SUDO:+${SUDO} }zypper --non-interactive install $*" ;;
    apk)    echo "${SUDO:+${SUDO} }apk add $*" ;;
    brew)   echo "brew install $*" ;;
    *)      echo "" ;;
  esac
}

install_system() {  # 빠진 시스템 패키지를 한 번에
  local parts="$1" part item level wanted="" packages="" names command
  for part in ${parts}; do
    while read -r item level; do
      [ -n "${item}" ] || continue
      case "${item}" in python|venv|openssl|gcc|ffmpeg|xvfb) ;; *) continue ;; esac
      [ "${level}" = "opt" ] && [ "${MINIMAL}" -eq 1 ] && continue
      [ "${item}" = "xvfb" ] && [ "${OS_KIND}" = "macos" ] && continue
      [ "${item}" = "gcc" ] && [ "${OS_KIND}" = "macos" ] && continue  # macOS는 Webots 루프백 라이브러리를 쓰지 않는다
      check_item "${item}" && continue
      case " ${wanted} " in *" ${item} "*) continue ;; esac
      wanted="${wanted} ${item}"
    done <<EOF
$(part_items "${part}")
EOF
  done
  title "시스템 패키지"
  if [ -z "${wanted}" ]; then mark ok "더 깔 것이 없습니다"; return 0; fi
  install_items ${wanted} || true
}

install_items() {  # install_items <항목...> → 그 항목들의 시스템 패키지를 한 번에 (물어보고). 깔지 못했으면 1
  local item names packages="" command
  for item in "$@"; do
    names="$(sys_packages "${item}")"
    if [ -n "${names}" ]; then packages="${packages} ${names}"; else mark warn "$(item_label "${item}"): ${PKG}에서 패키지 이름을 모름 — 직접 설치"; fi
  done
  packages="${packages# }"
  [ -n "${packages}" ] || return 1
  command="$(install_command ${packages})"
  if [ -z "${command}" ]; then
    mark warn "패키지 관리자를 모릅니다 — 직접 설치하세요: ${packages}"
    return 1
  fi
  mark skip "필요: ${packages}"
  if [ "${NO_SYSTEM}" -eq 1 ] || { [ "${CAN_ROOT}" -eq 0 ] && [ "${PKG}" != "brew" ]; }; then
    mark warn "시스템 패키지는 직접 설치하세요: ${command}"
    return 1
  fi
  if [ "${DRY_RUN}" -eq 1 ]; then say "  [할 일] ${command}"; return 0; fi
  confirm "  위 패키지를 설치할까요? (${command})" || { mark warn "건너뜀 — 직접: ${command}"; return 1; }
  sh -c "${command}"
}

setup_venv() {  # setup_venv dev?
  local py requirements="requirements.txt"
  [ "${1:-}" = "dev" ] && requirements="requirements-dev.txt"
  title "파이썬 가상환경 (.venv, ${requirements})"
  if ! py="$(find_python)"; then
    mark bad "파이썬 3.${MIN_PY_MINOR} 이상이 없습니다 — 위 시스템 패키지를 깔고 다시 실행하세요"
    [ "${DRY_RUN}" -eq 1 ] && return 0
    return 1
  fi
  if [ -x "${VENV}/bin/python" ] && ! "${VENV}/bin/python" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, ${MIN_PY_MINOR}) else 1)" 2>/dev/null; then
    mark warn ".venv의 파이썬이 너무 오래됐거나 깨졌습니다 — 새로 만듭니다"
    run rm -rf "${VENV}"
  fi
  if [ ! -x "${VENV}/bin/python" ]; then
    run "${py}" -m venv "${VENV}" || die "가상환경을 만들지 못했습니다 — $(item_label venv)이 필요합니다 (bash setup.sh check)"
  fi
  # 해시를 확인하며 설치한다 — 목록에 없거나 해시가 다른 파일은 설치하지 않는다.
  # 먼저 미리 빌드된 파일(wheel)로만 깐다. 이 파이썬용 wheel이 없으면(requirements.txt가 아는 것보다 새 파이썬)
  # 빌드 도구(C++ 컴파일러·파이썬 헤더)를 갖춘 뒤 소스로 빌드한다 — 빌드 도구가 없어 pip이 중간에 멈추지 않게
  local pip=("${VENV}/bin/python" -m pip install --disable-pip-version-check --require-hashes -q -r "${requirements}") log
  if [ "${DRY_RUN}" -eq 1 ]; then
    run "${pip[@]}" --only-binary :all:
  else
    log="$(mktemp)"
    if ! "${pip[@]}" --only-binary :all: 2> "${log}"; then
      if ! grep -q -e "No matching distribution" -e "Could not find a version" "${log}"; then
        cat "${log}" >&2; rm -f "${log}"
        die "파이썬 패키지를 설치하지 못했습니다 (위 메시지 — 인터넷 연결·해시를 확인하세요)"
      fi
      rm -f "${log}"
      mark warn "$("${VENV}/bin/python" --version 2>&1)용으로 미리 빌드된 numpy가 없어 소스로 빌드합니다 (몇 분 걸립니다)"
      if check_item build; then
        mark ok "$(item_label build): ${DETAIL}"
      else
        mark warn "$(item_label build): ${DETAIL}"
        { install_items build && check_item build; } ||
          die "빌드 도구가 없어 numpy를 빌드할 수 없습니다 — 위 패키지를 깔고 다시 실행하거나, 파이썬 3.10~3.15로 .venv를 만드세요 (bash setup.sh clean)"
      fi
      "${pip[@]}" || die "numpy를 소스로 빌드하지 못했습니다 (위 메시지)"
    else
      rm -f "${log}"
    fi
  fi
  [ "${DRY_RUN}" -eq 1 ] || mark ok "$("${VENV}/bin/python" --version 2>&1), $("${VENV}/bin/python" -c "import importlib.metadata as m, sys; print(', '.join(f'{p} {m.version(p)}' for p in sys.argv[1:]))" numpy ${1:+pytest} 2>/dev/null)"
}

setup_webots() {
  title "Webots R2025a"
  if check_item webots; then mark ok "${DETAIL}"; return 0; fi
  if ! webots_supported; then
    mark warn "이 환경(${OS_KIND} ${ARCH})에는 Webots R2025a가 없습니다 — ④는 리눅스 x86-64·macOS에서. ①②③⑥은 여기서 됩니다"
    return 0
  fi
  if [ "${OS_KIND}" = "macos" ]; then
    mark warn "macOS는 공식 설치 파일로: https://github.com/cyberbotics/webots/releases/tag/R2025a (webots-R2025a.dmg)"
    return 0
  fi
  local method="${WEBOTS_METHOD}"
  if [ -z "${method}" ]; then
    if [ "${PKG}" = "apt" ] && [ "${CAN_ROOT}" -eq 1 ] && [ "${NO_SYSTEM}" -eq 0 ]; then method="deb"; else method="tarball"; fi
  fi
  # 컨트롤러는 .venv의 파이썬(numpy)으로 돈다 (run_webots.sh가 PATH 앞에 둔다) → 시스템 numpy는 깔지 않는다
  if [ "${DRY_RUN}" -eq 1 ]; then say "  [할 일] bash scripts/install_webots.sh --method ${method} --skip-numpy"; return 0; fi
  confirm "  Webots R2025a를 ${method} 방식으로 설치할까요? (약 160MB 내려받기)" || { mark warn "건너뜀 — bash scripts/install_webots.sh"; return 0; }
  bash scripts/install_webots.sh --method "${method}" --skip-numpy
}

setup_loopback() {
  title "Webots를 이 PC에서만 (loopback_only.so)"
  if [ "${OS_KIND}" != "linux" ]; then mark skip "리눅스만 — macOS에서는 방화벽으로 1234를 막으세요"; return 0; fi
  if [ "${DRY_RUN}" -eq 1 ]; then say "  [할 일] gcc로 simulation/webots/build/loopback_only.so 빌드"; return 0; fi
  local built
  built="$(bash -c 'source scripts/_common.sh; webots_loopback_lib')"
  if [ -n "${built}" ]; then mark ok "${built#${ROOT}/}"; else mark bad "빌드하지 못했습니다 (gcc 확인) — Webots 웹 서버가 모든 주소로 열립니다"; fi
}

setup_token() {
  title "로그인 토큰"
  if [ "${DRY_RUN}" -eq 1 ]; then say "  [할 일] ${VENV}/bin/python -m common.auth --create"; return 0; fi
  [ -x "${VENV}/bin/python" ] || { mark warn "가상환경이 없어 건너뜀"; return 0; }
  "${VENV}/bin/python" -m common.auth --create | sed 's/^/  /'
}

next_steps() {
  local part
  title "다음"
  for part in $1; do
    case "${part}" in
      wheelchair) mark skip "① 휠체어 PC:  .venv/bin/python -m wheelchair.run --help   (녹화·영상으로 단독 실행)" ;;
      brain)      mark skip "② 연산 서버:  bash scripts/run_brain_server.sh   (다른 PC와: --public → tls:// + 지문)" ;;
      dashboard)  mark skip "③ 관제 서버:  bash scripts/run_dashboard.sh  → http://localhost:8080/live" ;;
      sim)        mark skip "④ 시뮬레이션: bash scripts/run.sh   (한 PC에서 전부 — 휴대폰·다른 PC에서도: --public)"
                  mark skip "             역할을 나누면 서버 PC는 bash scripts/run_server.sh, Webots PC는 bash scripts/run_sim.sh" ;;
      tools)      mark skip "⑤ 시험:       bash setup.sh test" ;;
      portal)     mark skip "⑥ 체험 포털:  bash scripts/run_demo.sh" ;;
      lab)        mark skip "어택랩:       cd attacklab && docker compose up --build   (attacklab/README.md)" ;;
    esac
  done
}

do_install() {
  [ $# -gt 0 ] || die "설치할 파트를 고르세요: ${ALL_PARTS} all   (예: bash setup.sh install brain)"
  local parts part need_dev=0 need_py=0 need_token=0
  parts="$(expand_parts "$@")"
  require_parts_present ${parts}
  say "설치할 파트: ${parts}"
  for part in ${parts}; do
    case "${part}" in
      tools) need_dev=1; need_py=1 ;;
      lab) ;;
      *) need_py=1 ;;
    esac
    case "${part}" in brain|dashboard|portal|sim) need_token=1 ;; esac
  done
  install_system "${parts}"
  if [ "${need_py}" -eq 1 ]; then
    if [ "${need_dev}" -eq 1 ]; then setup_venv dev; else setup_venv; fi
  fi
  case " ${parts} " in *" sim "*) setup_webots; setup_loopback ;; esac
  case " ${parts} " in *" tools "*) case " ${parts} " in *" sim "*) ;; *)
    if ! check_item webots && webots_supported && [ "${MINIMAL}" -eq 0 ]; then mark skip "도착 시험(tools/goal_trials.py)에는 Webots가 필요합니다 — bash setup.sh install sim"; fi ;; esac ;; esac
  case " ${parts} " in *" tools "*)
    title "커밋 전 점검 (git hook)"
    if [ -d .git ] && command -v git >/dev/null 2>&1; then
      if [ "$(git config --get core.hooksPath || true)" = "scripts/git-hooks" ]; then mark ok "scripts/git-hooks/pre-commit"
      else run git config core.hooksPath scripts/git-hooks; [ "${DRY_RUN}" -eq 1 ] || mark ok "걸었습니다: scripts/git-hooks/pre-commit (비밀 파일·비밀값·bandit·데이터 서명)"; fi
    else
      mark skip "git 저장소가 아님 — 건너뜀"
    fi ;;
  esac
  case " ${parts} " in *" lab "*)
    title "어택랩"
    if check_item docker; then mark ok "${DETAIL}"; else mark warn "Docker가 없습니다 — Docker 공식 안내를 따라 설치하세요 (attacklab/INSTALL.md)"; fi ;; esac
  [ "${need_token}" -eq 1 ] && setup_token
  save_parts ${parts}
  if [ "${need_py}" -eq 1 ] && [ -x "${VENV}/bin/python" -o "${DRY_RUN}" -eq 1 ]; then
    if [ "${DRY_RUN}" -eq 1 ] && [ ! -x "${VENV}/bin/python" ]; then say "  [할 일] 이 PC의 설정 (bash setup.sh config)"; else do_config ${parts}; fi
  fi
  [ "${DRY_RUN}" -eq 1 ] && { echo; say "(--dry-run: 아무것도 바꾸지 않았습니다)"; return 0; }
  do_check ${parts} || true
  next_steps "${parts}"
}

# ---------------------------------------------------------------- 이 PC의 설정 (여러 PC로 나눠 쓸 때)
ask() {  # ask <질문> <기본값> → 답 (화면이 없거나 --yes면 기본값)
  local answer=""
  if [ "${ASSUME_YES}" -eq 1 ] || [ ! -t 0 ]; then echo "$2"; return; fi
  printf '%s [%s] ' "$1" "$2" >&2
  read -r answer || true
  echo "${answer:-$2}"
}

local_env_cmd() {  # common/local_env.py의 main (python -m으로 부르면 common 패키지가 먼저 읽어 경고가 난다)
  "${VENV}/bin/python" -c 'import sys; from common.local_env import main; main(sys.argv[1:])' "$@"
}

env_get() { [ -n "${ENV_FILE}" ] && [ -f "${ENV_FILE}" ] && local_env_cmd get "$1" 2>/dev/null || true; }

has_part() { case " $(saved_parts) $* " in *" $1 "*) return 0 ;; esac; return 1; }

local_addresses() {  # 이 PC의 내부망 주소 (인증서·허용 목록과 같은 방법 — common/tls.py)
  [ -x "${VENV}/bin/python" ] && "${VENV}/bin/python" -c 'from common.tls import lan_addresses; print(" ".join(lan_addresses()))' 2>/dev/null || true
}

normalize_address() {  # normalize_address <주소> <기본 포트> <이 PC의 이름들> → 저장할 값 (안내는 표준 오류로)
  local address="$1" port="$2" own="$3" hostport name
  case "${address}" in ""|none) return 0 ;; esac
  hostport="${address#tls://}"
  case "${hostport}" in
    \[*\]:*|[!\[]*:[0-9]*) ;;                      # 포트가 있다 ([IPv6]:포트 또는 이름:포트)
    *) hostport="${hostport}:${port}" ;;
  esac
  name="${hostport%:*}"
  case "${address}" in tls://*) echo "tls://${hostport}"; return 0 ;; esac
  case "${name}" in 127.*|localhost|"[::1]") echo "${hostport}"; return 0 ;; esac
  case " ${own} " in
    *" ${name} "*) mark skip "${address}: 이 PC 자신 — 따로 적지 않습니다 (run.sh가 127.0.0.1로 붙습니다)" >&2; return 0 ;;
  esac
  mark skip "${address} → tls://${hostport} (다른 PC와는 TLS로 — 평문은 같은 PC에서만 받는다)" >&2
  echo "tls://${hostport}"
}

do_config() {
  [ -x "${VENV}/bin/python" ] || die "가상환경이 없습니다 — 먼저 bash setup.sh install <파트>"
  local parts updates="" serve names default_names dashboard brain address pin pins token kind pinned_new=0
  parts="$(saved_parts) $*"
  title "이 PC의 설정 (reflexguard.local.env)"

  # 1) 이 PC가 서버(관제·연산)를 맡으면: 다른 PC에도 열지, 이 PC를 부를 이름
  case " ${parts} " in *" dashboard "*|*" brain "*|*" sim "*)
    serve="${CFG_SERVE_PUBLIC:-$(env_get REFLEXGUARD_SERVE_PUBLIC)}"
    if [ -z "${CFG_SERVE_PUBLIC}" ]; then
      case "$(ask "  다른 PC(휠체어 PC·관제실 등)에서 이 PC의 관제·연산 서버에 붙나요? (y/n)" "$([ "${serve}" = 1 ] && echo y || echo n)")" in
        y|Y|yes|예|네) serve=1 ;; *) serve=0 ;;
      esac
    fi
    updates="${updates}REFLEXGUARD_SERVE_PUBLIC=$([ "${serve}" = 1 ] && echo 1 || echo)
"
    if [ "${serve}" = 1 ]; then
      default_names="$(env_get REFLEXGUARD_PUBLIC_NAMES)"
      if [ -z "${default_names}" ]; then
        default_names="$(hostname 2>/dev/null).local $("${VENV}/bin/python" -c 'from common.tls import lan_addresses; print(" ".join(lan_addresses()))')"
      fi
      names="${CFG_NAMES:-$(ask "  이 PC를 부를 이름·주소 (공백으로 여럿 — 공유기의 고정 IP나 .local 이름 권장)" "${default_names% }")}"
      updates="${updates}REFLEXGUARD_PUBLIC_NAMES=${names}
"
    fi ;;
  esac

  # 2) 다른 PC의 서버에 붙으면: 주소 → 지문 받아 대조 → 토큰
  dashboard="${CFG_DASHBOARD:-$(ask "  관제 서버(로봇·연산 서버가 붙는 곳) 주소 — 이 PC면 Enter" "$(env_get REFLEXGUARD_DASHBOARD)")}"
  brain="${CFG_BRAIN}"
  case " ${parts} " in *" wheelchair "*|*" sim "*|*" tools "*)
    brain="${brain:-$(ask "  커넥톰 연산 서버 주소 — 기기 안에서 계산하면 Enter" "$(env_get REFLEXGUARD_BRAIN_SERVER)")}" ;;
  esac
  own="localhost $(hostname 2>/dev/null) $(hostname 2>/dev/null).local ${names:-$(env_get REFLEXGUARD_PUBLIC_NAMES)} $(local_addresses)"
  dashboard="$(normalize_address "${dashboard}" 8766 "${own}")"
  brain="$(normalize_address "${brain}" 8765 "${own}")"
  updates="${updates}REFLEXGUARD_DASHBOARD=${dashboard}
REFLEXGUARD_BRAIN_SERVER=${brain}
"
  pins="$(env_get REFLEXGUARD_TLS_PIN)"
  for address in ${dashboard} ${brain}; do
    case "${address}" in
      tls://*) ;;
      127.*|localhost:*|"") continue ;;
      *) mark warn "${address}: 다른 PC면 tls://${address}로 쓰세요 (평문은 같은 PC에서만 받는다)"; continue ;;
    esac
    if pin="$("${VENV}/bin/python" -m common.tls --fetch "${address}" 2>&1)"; then
      mark skip "${address} 의 공개키 지문: ${pin}"
      if [ -n "${CFG_PINS}" ]; then
        case ",${CFG_PINS}," in *",${pin},"*) mark ok "--pin과 같습니다" ;;
          *) die "${address}의 지문이 --pin과 다릅니다 — 다른 서버(또는 중간에서 가로챔)일 수 있어 저장하지 않습니다" ;; esac
      elif [ "${ASSUME_YES}" -eq 1 ] || [ ! -t 0 ]; then
        die "묻지 않고 진행할 때는 --pin sha256:… 이 필요합니다 (처음 붙을 때 가짜 서버를 믿지 않게) — 서버 PC에서 python -m common.tls"
      elif ! confirm "  서버 PC 터미널(또는 bash setup.sh config가 보여 준 값)의 지문과 같습니까?"; then
        die "지문을 확인하지 못해 저장하지 않습니다 — 서버 PC에서 python -m common.tls 로 보고 --pin으로 주세요"
      fi
    elif [ -n "${CFG_PINS}" ]; then
      mark warn "${address}에 닿지 않아(${pin}) 받아 온 값과 대조하지 못했습니다 — --pin 값을 그대로 저장합니다"
      pin="${CFG_PINS%%,*}"
    else
      mark bad "${address}에 닿지 않습니다 (${pin}) — 서버를 켠 뒤 다시: bash setup.sh config"
      continue
    fi
    case ",${pins}," in *",${pin},"*) ;; *) pins="${pins:+${pins},}${pin}"; pinned_new=1 ;; esac
  done
  updates="${updates}REFLEXGUARD_TLS_PIN=${pins}
"
  if [ -n "${dashboard}${brain}" ] && printf '%s %s' "${dashboard}" "${brain}" | grep -q 'tls://'; then
    token=""
    # 연산 서버가 원격(tls)이면 그 서버의 장치 열쇠로 붙고, 이 PC에서 계산하면(로컬·none) 서버 토큰을 쓴다.
    # 로컬 연산 서버는 관제 서버와 같은 토큰을 써야, 컨트롤러 하나가 로컬 연산·원격 관제에 모두 붙는다.
    # (장치 열쇠는 관제 서버의 비밀로 만든 값이라 로컬 연산 서버에선 통하지 않는다 — 그러면 bad token.)
    use_device=0
    case "${brain}" in tls://*) use_device=1 ;; esac
    [ "${CFG_TOKEN_STDIN}" -eq 1 ] && use_device=0                                          # --token-stdin: 토큰으로
    { [ "${CFG_DEVICE_KEY_STDIN}" -eq 1 ] || [ -n "${CFG_DEVICE_NAME}" ]; } && use_device=1  # 장치 열쇠를 직접 주면
    if [ "${use_device}" -eq 1 ]; then
      # 장치 열쇠: 휠체어·시뮬레이션 PC가 원격 연산/관제 서버의 장치로 붙을 때 (그 이름에만 통한다 — common/device_auth.py)
      device_name="${CFG_DEVICE_NAME:-$(env_get REFLEXGUARD_DEVICE_NAME)}"
      device_name="${device_name:-$(ask "  이 장치의 이름 (서버 PC에서 python -m common.device_auth <이름> 으로 열쇠를 만든다)" "$(hostname 2>/dev/null)")}"
      if [ "${CFG_DEVICE_KEY_STDIN}" -eq 1 ]; then
        IFS= read -r token || true
      elif [ -z "$(env_get REFLEXGUARD_DEVICE_KEY)" ] && [ -t 0 ] && [ "${ASSUME_YES}" -eq 0 ]; then
        printf '  장치 열쇠 (서버 PC에서 python -m common.device_auth %s — 화면에 보이지 않음): ' "${device_name}" >&2
        IFS= read -rs token || true
        echo >&2
      fi
      updates="${updates}REFLEXGUARD_DEVICE_NAME=${device_name}
REFLEXGUARD_TOKEN=
"
      [ -n "${token}" ] && updates="${updates}REFLEXGUARD_DEVICE_KEY=${token}
"
    else
      # 서버 토큰: 이 PC에서 연산하거나(로컬 연산 서버) 다른 서버 PC가 관제에 붙을 때. 로컬 연산 서버도 이 토큰을 쓴다.
      # 장치 열쇠 방식에서 바꿔 올 수 있으니 예전 장치 이름·열쇠를 비운다 (안 그러면 컨트롤러가 계속 장치 열쇠를 써 bad token).
      if [ "${CFG_TOKEN_STDIN}" -eq 1 ]; then
        IFS= read -r token || true
      elif [ -z "$(env_get REFLEXGUARD_TOKEN)" ] && [ -t 0 ] && [ "${ASSUME_YES}" -eq 0 ]; then
        printf '  서버 PC의 조작 토큰 (서버 PC에서 python -m common.auth — 화면에 보이지 않음): ' >&2
        IFS= read -rs token || true
        echo >&2
      fi
      [ -n "${token}" ] && updates="${updates}REFLEXGUARD_TOKEN=${token}
"
      updates="${updates}REFLEXGUARD_DEVICE_NAME=
REFLEXGUARD_DEVICE_KEY=
"
    fi
  else
    # 이 PC에서 전부 돌린다(원격 서버 없음) → 로컬 서버의 logs/token을 그대로 쓴다. 예전 원격 장치 열쇠는 비운다
    # (남아 있으면 컨트롤러가 그 열쇠로 로컬 연산 서버에 붙으려다 bad token).
    updates="${updates}REFLEXGUARD_DEVICE_NAME=
REFLEXGUARD_DEVICE_KEY=
"
  fi
  if [ "${DRY_RUN}" -eq 1 ]; then
    say "  [할 일] reflexguard.local.env에 쓰기:"
    printf '%s' "${updates}" | sed -e 's/^\(REFLEXGUARD_TOKEN=\|REFLEXGUARD_DEVICE_KEY=\).\+/\1<가림>/' -e 's/^/    /'
    return 0
  fi
  [ -n "${ENV_FILE}" ] || die "REFLEXGUARD_ENV_FILE가 비어 있어 설정을 쓸 수 없습니다"
  printf '%s' "${updates}" | local_env_cmd set-stdin
  local_env_cmd show | sed 's/^/  /'

  # 3) 설정이 정해진 뒤 인증서 — 이 PC가 다른 PC에 서버를 열면, 정한 이름·주소를 넣어 (열쇠는 그대로 — 지문 유지)
  if [ "$(env_get REFLEXGUARD_SERVE_PUBLIC)" = 1 ]; then
    title "TLS 인증서 (이 PC의 서버)"
    REFLEXGUARD_ENV_FILE="${ENV_FILE}" "${VENV}/bin/python" -m common.tls | sed 's/^/  /'
    pin="$("${VENV}/bin/python" -c 'from common.tls import certificate, file_fingerprint; print(file_fingerprint(certificate()[0]))')"
    names="$(env_get REFLEXGUARD_PUBLIC_NAMES)"
    say "  다른 PC에서 (한 번):"
    has_part dashboard && say "    bash setup.sh config --dashboard tls://${names%% *}:8766 --pin ${pin}"
    { has_part brain || has_part sim; } && say "    bash setup.sh config --brain tls://${names%% *}:8765 --pin ${pin}"
    say "  휠체어 PC에는 장치마다 열쇠를 만들어 줍니다: python -m common.device_auth <장치 이름>  (그 PC가 config 때 물어본다)."
    say "  다른 서버 PC(연산 서버 → 관제)는 서버 토큰을 함께 씁니다 (python -m common.auth, config --token-stdin)."
    say "  주소가 바뀌어도 지문은 그대로입니다."
  fi
  [ "${pinned_new}" -eq 1 ] && mark ok "서버 지문을 저장했습니다 — 지문이 다른 서버에는 붙지 않습니다"
  # 다른 PC의 서버에는 이 PC의 클라이언트 인증서를 등록한다 (mTLS — 장치 열쇠·토큰으로 인증, common/mtls.py)
  for address in ${dashboard} ${brain}; do
    case "${address}" in tls://*) ;; *) continue ;; esac
    if result="$(REFLEXGUARD_ENV_FILE="${ENV_FILE}" "${VENV}/bin/python" -m common.mtls enroll "${address}" 2>&1)"; then
      mark ok "${result}"
    else
      mark warn "${address}: ${result} — 서버를 켠 뒤 다시: bash setup.sh config"
    fi
  done
  return 0
}

check_config() {  # check에서: 설정과 실제로 닿는지
  [ -f "${ENV_FILE}" ] && [ -x "${VENV}/bin/python" ] || return 0
  title "이 PC의 설정 (reflexguard.local.env)"
  local_env_cmd show | sed 's/^/  · /'
  local address result mode
  mode="$(perm_of "${ENV_FILE}")"
  [ "${mode}" = 600 ] && mark ok "파일 권한 600" || mark bad "파일 권한 ${mode} — chmod 600 reflexguard.local.env"
  for address in $(env_get REFLEXGUARD_DASHBOARD) $(env_get REFLEXGUARD_BRAIN_SERVER); do
    if result="$("${VENV}/bin/python" -m common.tls --check "${address}" 2>&1)"; then mark ok "${address}: 닿음$(case "${address}" in tls://*) printf ', 지문 일치' ;; esac)"
    else case "${result}" in *지문*) mark bad "${address}: ${result}" ;; *) mark warn "${address}: ${result} (서버가 꺼져 있을 수 있음)" ;; esac
    fi
  done
}

# ---------------------------------------------------------------- 관리
do_update() {
  local parts
  parts="$(saved_parts)"
  [ -n "${parts}" ] || die "기록한 파트가 없습니다 — 먼저 bash setup.sh install <파트>"
  # 코드는 받지 않는다: git pull·새 꾸러미 풀기·복사 등 어떤 방법으로든 직접 받아 온 뒤에 실행한다 (설정·기록·토큰은 그대로)
  title "코드"
  mark skip "코드는 받지 않습니다 — 새 코드를 직접 받아 온 뒤 실행하세요 (git pull, 새 꾸러미를 이 폴더 위에 풀기 등)"
  do_install ${parts}
}

do_test() {
  [ -d tests ] || die "이 제출본에는 자동 시험(tests/)이 들어 있지 않습니다"
  check_item pydev || die "시험 패키지가 없습니다 — bash setup.sh install tools"
  title "자동 시험"
  run "${VENV}/bin/python" -m pytest -q
}

perm_of() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null; }

do_security() {
  local issues=0 path mode port listen
  title "파일 권한 (이 계정만)"
  for path in logs:700 logs/token:600 logs/log.key:600 logs/certs:700 logs/certs/server.key:600; do
    [ -e "${path%%:*}" ] || { mark skip "${path%%:*} 없음"; continue; }
    mode="$(perm_of "${path%%:*}")"
    if [ "${mode}" = "${path##*:}" ]; then mark ok "${path%%:*} ${mode}"
    else mark bad "${path%%:*} ${mode} — ${path##*:}이어야 함 (chmod ${path##*:} ${path%%:*})"; issues=1; fi
  done
  title "열린 포트 (이 PC 밖에서 닿는 것)"
  if command -v ss >/dev/null 2>&1; then
    for port in 1234 8080 8443 8765 8766; do
      listen="$(ss -ltn "sport = :${port}" 2>/dev/null | awk 'NR>1 {print $4}' | tr '\n' ' ')"
      if [ -z "${listen}" ]; then mark skip "${port} 꺼짐"
      elif printf '%s' "${listen}" | grep -q -E '^(127\.|\[::1\]|\[::ffff:127\.)' && ! printf '%s' "${listen}" | grep -q -E '(^| )(0\.0\.0\.0|\*|\[::\]):'; then mark ok "${port} 이 PC만 (${listen% })"
      else mark warn "${port} 바깥에서 닿음 (${listen% }) — --public으로 켠 것인지 확인 (TLS·토큰)"; fi
    done
  else
    mark skip "ss가 없어 건너뜀"
  fi
  title "관제 기록 무결성"
  if [ -x "${VENV}/bin/python" ] && [ -f logs/log.key ] && [ -f tools/verify_logs.py ]; then
    "${VENV}/bin/python" tools/verify_logs.py | sed 's/^/  /' || issues=1
  else
    mark skip "기록이나 열쇠가 아직 없음"
  fi
  title "정적 점검·알려진 취약점 (bandit·semgrep·pip-audit — .venv-security, 해시 잠금)"
  local ignore lock_hash
  lock_hash="$(sha256sum "${SECURITY_LOCK}" 2>/dev/null | cut -c1-16)"
  if [ ! -x "${SECURITY_VENV}/bin/semgrep" ] || [ "$(cat "${SECURITY_VENV}/.lock" 2>/dev/null)" != "${lock_hash}" ]; then
    py="$(find_python)" || die "파이썬이 없습니다"
    run rm -rf "${SECURITY_VENV}"
    run "${py}" -m venv "${SECURITY_VENV}"
    run "${SECURITY_VENV}/bin/python" -m pip install --disable-pip-version-check -q --require-hashes -r "${SECURITY_LOCK}"
    [ "${DRY_RUN}" -eq 1 ] || echo "${lock_hash}" > "${SECURITY_VENV}/.lock"
  fi
  if [ "${DRY_RUN}" -eq 0 ]; then
    if "${SECURITY_VENV}/bin/bandit" -q -c bandit.yaml -r . -ll >/dev/null 2>&1; then mark ok "bandit: 중간 이상 0건"
    else mark bad "bandit: 문제가 있습니다 — ${SECURITY_VENV}/bin/bandit -c bandit.yaml -r . -ll"; issues=1; fi
    if "${SECURITY_VENV}/bin/semgrep" scan --config p/python --metrics=off --error -q --exclude attacklab/vulnerable \
         --exclude tests --exclude .venv --exclude .venv-security . >/dev/null 2>&1; then mark ok "semgrep(p/python): 0건"
    else mark bad "semgrep: 문제가 있거나 규칙을 받지 못했습니다(인터넷) — ${SECURITY_VENV}/bin/semgrep scan --config p/python --metrics=off ."; issues=1; fi
    if "${SECURITY_VENV}/bin/pip-audit" -r requirements-dev.txt >/dev/null 2>&1; then mark ok "pip-audit(제품): 알려진 취약점 0건"
    else mark bad "pip-audit: 제품 의존성에 취약점이 있거나 확인하지 못했습니다 — ${SECURITY_VENV}/bin/pip-audit -r requirements-dev.txt"; issues=1; fi
    ignore="$(grep -oE '^(CVE|GHSA|PYSEC)-[A-Za-z0-9-]+' "${AUDIT_EXCEPTIONS}" | sed 's/^/--ignore-vuln /' | tr '\n' ' ')"
    # shellcheck disable=SC2086
    if "${SECURITY_VENV}/bin/pip-audit" -r "${SECURITY_LOCK}" ${ignore} >/dev/null 2>&1; then mark ok "pip-audit(점검 도구): 0건 (판정한 예외 $(grep -cE '^(CVE|GHSA|PYSEC)-' "${AUDIT_EXCEPTIONS}")개 — ${AUDIT_EXCEPTIONS})"
    else mark bad "pip-audit: 점검 도구에 새 취약점 — ${SECURITY_VENV}/bin/pip-audit -r ${SECURITY_LOCK}"; issues=1; fi
  fi
  echo
  [ "${issues}" -eq 0 ] && say "보안 점검: 문제 없음" || { say "보안 점검: 위 ✗를 확인하세요 (docs/security-diagnosis.md, SECURITY.md)"; return 1; }
}

do_status() {
  title "켜져 있는 서버"
  local port name listen
  for entry in 8080:관제화면 8766:관제접속 8765:연산서버 8443:체험포털 1234:Webots; do
    port="${entry%%:*}"; name="${entry##*:}"
    if command -v ss >/dev/null 2>&1; then
      listen="$(ss -ltn "sport = :${port}" 2>/dev/null | awk 'NR>1 {print $4}' | tr '\n' ' ')"
    else
      listen="$(lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $9}' | tr '\n' ' ')"
    fi
    if [ -n "${listen}" ]; then mark ok "${name} ${port}: ${listen% }"; else mark skip "${name} ${port}: 꺼짐"; fi
  done
  [ -f "${STATE_FILE}" ] && mark skip "이 PC의 파트: $(saved_parts)"
  return 0
}

do_clean() {
  title "만든 실행 환경 지우기"
  local targets="" path
  for path in .venv .venv-security simulation/webots/build; do [ -e "${path}" ] && targets="${targets} ${path}"; done
  [ -n "${targets}" ] || { mark ok "지울 것이 없습니다"; return 0; }
  mark skip "지울 것:${targets} (기록·토큰·인증서·logs/는 그대로)"
  if [ "${DRY_RUN}" -eq 1 ]; then say "  [할 일] rm -rf${targets}"; return 0; fi
  confirm "  지울까요? 다시 쓰려면 bash setup.sh install" || { mark skip "그만둠"; return 0; }
  rm -rf ${targets}
  mark ok "지웠습니다"
}

# ---------------------------------------------------------------- 시작
COMMAND="check"
case "${1:-}" in
  check|install|config|update|test|security|status|clean) COMMAND="$1"; shift ;;
  -h|--help|help) sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  bundle)  # 옵션(--list·--out·--verify)은 tools/bundle.py가 받는다
    shift
    [ -f tools/bundle.py ] || die "꾸러미는 저장소 전체에서 만듭니다 (tools/bundle.py 없음)"
    py="$(find_python)" || die "파이썬 3.${MIN_PY_MINOR} 이상이 없습니다"
    exec "${py}" tools/bundle.py "$@" ;;
esac
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-system) NO_SYSTEM=1 ;;
    --minimal) MINIMAL=1 ;;
    --webots-method) WEBOTS_METHOD="${2:?--webots-method 뒤에 deb 또는 tarball}"; shift ;;
    --webots-method=*) WEBOTS_METHOD="${1#*=}" ;;
    --serve-public) CFG_SERVE_PUBLIC=1 ;;
    --local-only) CFG_SERVE_PUBLIC=0 ;;
    --names) CFG_NAMES="${2:?--names 뒤에 이름·주소}"; shift ;;
    --dashboard) CFG_DASHBOARD="${2:?--dashboard 뒤에 주소 (또는 none)}"; shift ;;
    --brain) CFG_BRAIN="${2:?--brain 뒤에 주소 (또는 none)}"; shift ;;
    --pin) CFG_PINS="${CFG_PINS:+${CFG_PINS},}${2:?--pin 뒤에 sha256:…}"; shift ;;
    --token-stdin) CFG_TOKEN_STDIN=1 ;;
    --device-name) CFG_DEVICE_NAME="${2:?--device-name 뒤에 장치 이름}"; shift ;;
    --device-key-stdin) CFG_DEVICE_KEY_STDIN=1 ;;
    -*) die "모르는 옵션 $1 (bash setup.sh --help)" ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done

detect_env
show_env
case "${COMMAND}" in
  check)
    status=0
    if [ ${#ARGS[@]} -gt 0 ]; then do_check "${ARGS[@]}" || status=$?
    elif [ -n "$(saved_parts)" ]; then do_check $(saved_parts) || status=$?
    else do_check all || status=$?
    fi
    check_config
    exit "${status}" ;;
  install)  do_install ${ARGS[@]+"${ARGS[@]}"} ;;
  config)   do_config ${ARGS[@]+"${ARGS[@]}"} ;;
  update)   do_update ;;
  test)     do_test ;;
  security) do_security ;;
  status)   do_status ;;
  clean)    do_clean ;;
esac
