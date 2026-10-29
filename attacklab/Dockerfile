# 어택랩 공용 이미지 — 서버(대시보드) 티어와 클라이언트(사용자 시뮬레이션·커넥톰) 티어가 같이 쓴다.
# 빌드 컨텍스트는 저장소 루트(docker-compose.yml에서 context: ..). 외부 의존성은 numpy 하나뿐.
# 기본 이미지는 다이제스트로, 파이썬 패키지는 해시로 고정한다 (KISA SW 공급망 체크리스트 8 — 받은 것이 그때 그것인지 확인).
# 올리려면: 새 다이제스트를 확인해 바꾸고(docker buildx imagetools inspect python:3.10-slim), requirements.txt도 함께.
FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

# 저장소를 통째로 (루트 .dockerignore가 .venv·.git·로그·캐시를 뺀다)
COPY . /app

# 교육용 랩이라도 컨테이너는 최소 권한으로: 비루트 사용자로 실행
RUN useradd --create-home lab && mkdir -p /app/attacklab/logs /app/logs && chown -R lab /app
USER lab
