# ⚠️ 보안 조치 전 고정본 — 일부러 취약하게 둔 복사본

이 폴더는 ReflexGuard 본편을 **보안 조치에 들어가기 직전(개발 저장소 dryingho/reflexguard의 커밋 `2581ffb`)** 그대로 얼려 둔 복사본입니다. 어택랩에서 `LAB_VERSION=before`(기본)로 띄우면 이 코드가 돕니다. 같은 시험을 `LAB_VERSION=after`(본편)와 번갈아 돌려 '고치기 전/후'를 비교하는 것이 목적입니다.

- **고치지 마세요.** 약점이 남아 있어야 비교가 됩니다. 조치는 저장소 본편(`common/`, `dashboard/`, `demo/` …)에만 합니다.
- **본편에서 불러다 쓰지 마세요.** 이 폴더는 어택랩 컨테이너 안에서만 돕니다.
- **바깥에 열지 마세요.** 랩 compose처럼 `127.0.0.1`에만 묶어 씁니다.
- 보안 점검 도구의 대상에서 뺍니다 (`bandit.yaml`의 `exclude_dirs`).

어떤 약점이 있고 본편에서 어떻게 고쳤는지는 [README의 보안 기능](../../README.md#보안-기능)과 [어택랩의 보안 조치 단계](../README.md#본편의-보안-조치-단계)에 있습니다.

> 이 폴더 안의 다른 README(`dashboard/README.md` 등)도 얼린 그대로라, 그 안의 문서 링크(`../docs/…`)는 열리지 않습니다.

## 만든 방법

커밋된 내용만 꺼냈습니다 (작업 중이던 수정분은 섞이지 않음).

```bash
# 개발 저장소(dryingho/reflexguard)에서
git archive 2581ffb common dashboard demo brain_server wheelchair connectome \
  simulation/*.py simulation/webots/plugins/robot_windows/reflexguard | tar -x -C attacklab/vulnerable
```

담긴 것: 관제 대시보드, 체험·조작 서버, 커넥톰 연산 서버, 휠체어 PC 파이프라인, 커넥톰 회로·데이터, 장면 정의, 조작 화면. 녹화(`attacklab/client/data/`)와 랩 도구(`attacklab/server`, `attacklab/client`)는 두 버전이 함께 씁니다.

### 얼린 뒤 고친 것 (보안과 무관한 오류만)

- `dashboard/web/index.html`: 휴대폰이 없을 때 안내 문구의 따옴표가 겹쳐 화면 스크립트 전체가 문법 오류로 멈췄습니다(관제 화면이 '연결하는 중…'에서 멈춤).
  - 본편은 보안 5단계(개발 저장소 커밋 `9abe9d7`)에서 고쳤고, 고정본에는 같은 한 줄만 옮겼습니다.
  - 약점(인증 없음·출처 확인 없음 등)은 그대로입니다.
