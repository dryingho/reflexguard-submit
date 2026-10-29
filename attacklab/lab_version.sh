# 어택랩 버전 고르기 (entrypoint.sh들이 source 한다).
#   LAB_VERSION=before (기본) → attacklab/vulnerable/ 의 고정본 (보안 조치 전, 일부러 취약)
#   LAB_VERSION=after         → 저장소 본편 코드 (보안 조치 후)
# 결과: CODE = 서버들을 돌릴 코드 폴더. APP = 저장소 루트(랩 도구·녹화가 있는 곳, 기본 /app)
APP="${LAB_APP:-/app}"
LAB_VERSION="${LAB_VERSION:-before}"
case "${LAB_VERSION}" in
  before) CODE="${APP}/attacklab/vulnerable" ;;
  after)  CODE="${APP}" ;;
  *) echo "[lab] LAB_VERSION은 before 또는 after 입니다 (받은 값: ${LAB_VERSION})" >&2; exit 2 ;;
esac
export APP CODE LAB_VERSION
echo "[lab] 버전: ${LAB_VERSION} (코드: ${CODE})" >&2
