"""공용: 모든 파트(휠체어 PC·커넥톰 연산 서버·관제 서버·시뮬레이션)가 같이 쓰는 설정값·제어 명령·관제 서버 연결."""

from common import local_env as _local_env

_local_env.load()  # 이 PC의 설정 파일(reflexguard.local.env)을 환경 변수 기본값으로 — 모든 프로그램이 같은 값을 쓴다
