# 어택랩 설치·실행 안내

[← 어택랩](README.md) · 다음: [연습 개요](exercises/00-overview.md)

이 문서는 도커가 없는 우분투에서 어택랩을 띄우고, 확인하고, 끄기까지의 순서입니다. 어택랩을 처음 돌려 볼 때 위에서부터 차례로 따라 하면 됩니다.

- 도커(프로그램을 격리된 '컨테이너' 안에서 돌리는 도구) 설치는 Docker 공식 안내를 따릅니다. 이 문서에는 설치 명령을 옮겨 적지 않습니다.
- 그 뒤는 이 문서의 명령을 차례로 실행합니다.
- 랩은 이 PC의 `127.0.0.1`에만 열리는 교육용 환경입니다([README](README.md)).

## 0. 준비물

| 항목 | 내용 |
|---|---|
| 운영체제 | Ubuntu 22.04 또는 24.04 (64비트) |
| 권한 | `sudo`를 쓸 수 있는 계정 |
| 인터넷 | 처음 한 번 필요 (도커 설치, `python:3.10-slim` 이미지(컨테이너의 바탕이 되는 꾸러미), numpy 받기) |
| 디스크·메모리 | 여유 디스크 2 GB, 메모리 2 GB 이상 권장 |
| 포트 | `127.0.0.1`의 8080·8766·8765·8443이 비어 있어야 함 |
| 저장소 | **프로젝트 폴더 전체** (랩 이미지가 저장소 루트를 빌드에 씁니다. `attacklab/`만으로는 안 됨) |

다른 PC에서 `git clone`으로 받으려면 `attacklab/`이 커밋·푸시되어 있어야 합니다. 그렇지 않으면 프로젝트 폴더를 통째로 복사해 갑니다.

## 1. 도커 설치

Docker 공식 안내를 따라 설치합니다(apt 저장소 방식 권장).

- 설치: <https://docs.docker.com/engine/install/ubuntu/>
- (선택) sudo 없이 `docker` 쓰기: <https://docs.docker.com/engine/install/linux-postinstall/>

설치가 끝나면 확인합니다.

```bash
docker --version
```

```bash
docker compose version
```

두 명령 모두 버전이 나오면 준비가 끝난 것입니다. `compose`가 없다고 나오면 공식 안내의 `docker-compose-plugin` 설치 단계를 빠뜨린 것입니다.

## 2. 포트 비우기 (본편을 같은 PC에서 쓰는 경우)

본편 실행 스크립트(`scripts/run.sh`·`run_server.sh`·`run_sim.sh`)도 8080·8766·8765·8443을 씁니다. 그래서 랩을 띄우기 전에 본편을 끕니다.

- `run.sh`·`run_sim.sh`는 Webots를 닫으면, `run_server.sh`는 그 창에서 Ctrl+C를 누르면 연산 서버·체험·조작 포털도 함께 꺼집니다.
- 관제 서버만 따로 끕니다. 저장소 폴더에서:

```bash
bash scripts/stop_dashboard.sh
```

포트가 비었는지 확인합니다. 아무것도 안 나오면 비어 있는 것입니다.

```bash
ss -ltn | grep -E ':(8080|8766|8765|8443) '
```

## 3. 랩 띄우기

저장소 폴더에서 실행합니다(처음에는 이미지 빌드로 몇 분 걸립니다).

```bash
cd attacklab
```

```bash
sudo docker compose up --build -d
```

- `-d`는 뒤에서(백그라운드로) 실행하라는 뜻입니다. 빼면 로그가 터미널에 바로 흐릅니다.
- 기본은 보안 조치 전 고정본(`LAB_VERSION=before`)입니다. 조치 후 본편은 `sudo LAB_VERSION=after docker compose up --build -d`로 띄웁니다.
- sudo 없이 쓰도록 설정했다면 `sudo`를 빼도 됩니다.

## 4. 확인

| 확인할 것 | 방법 |
|---|---|
| 관제 화면 | 브라우저로 <http://127.0.0.1:8080/live> — 로봇 `wheelchair-01@attacklab`이 보이면 정상 |
| 체험·조작 화면 | <http://127.0.0.1:8443/> — 조작 화면이 열리면 정상 (Webots 대신 대역이라 영상은 없음) |
| 버전 | `sudo docker compose logs server` 앞부분의 `[lab] 버전: before` 또는 `after` |
| 컨테이너 상태 | `sudo docker compose ps` — `server`·`client` 둘 다 running |
| 감사 로그 | `tail -f logs/audit-*.jsonl` |
| 컨테이너 출력 | `sudo docker compose logs -f` |

## 5. 패치 켜고 다시 띄우기

예: 인증 토큰을 켜는 경우. 두 토큰 값은 아무 문자열이나 되지만, 서로 같게 둡니다.

먼저 내리고:

```bash
sudo docker compose down
```

패치를 켜서 다시 띄웁니다.

```bash
sudo PATCH_REQUIRE_TOKEN=1 PATCH_TOKEN=lab-secret REFLEXGUARD_DASHBOARD_TOKEN=lab-secret docker compose up --build -d
```

토글 목록은 [README의 패치 토글](README.md#패치-토글)에, 약점별 연습 순서는 [`exercises/`](exercises/00-overview.md)에 있습니다.

## 6. 끄기

```bash
sudo docker compose down
```

## 문제 해결

| 증상 | 원인·해결 |
|---|---|
| `port is already allocated` | 본편 서비스가 그 포트를 쓰고 있음 → 2단계 |
| `permission denied ... docker.sock` | `sudo`를 붙이거나, 1단계의 post-install(sudo 없이 쓰기) 설정 뒤 다시 로그인 |
| 빌드 중 `pip`·이미지 받기 실패 | 인터넷·프록시 확인 |
| 관제 화면에 로봇이 없음 | `sudo docker compose logs client`로 오류 확인. 서버가 늦게 떴다면 몇 초 뒤 자동으로 다시 접속함 |
| WSL에서 도커 데몬(뒤에서 늘 도는 도커 본체)이 안 뜸 | WSL에 systemd(서비스를 자동으로 켜 주는 관리자)가 꺼져 있으면 `sudo service docker start`로 직접 켜기 |
