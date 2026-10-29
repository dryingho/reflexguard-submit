"""커넥톰 데이터 서명을 믿는 공개 열쇠 (connectome/integrity.py). 데이터 폴더가 아니라 코드에 둔다 — 데이터를 바꿀 수 있는 사람이
믿는 열쇠까지 바꾸지 못하게. 열쇠 id는 공개 열쇠 SHA-256의 앞 16자. 서명 열쇠(비밀)는 개발 PC에만 (tools/sign_data.py).
열쇠를 바꿀 때는 새 열쇠를 더해 다시 서명하고, 장비가 모두 새 코드로 바뀐 뒤 예전 열쇠를 뺀다."""

TRUSTED_KEYS: dict[str, str] = {
    "70082d5536bbefb5": "c5724208e93f4cba5b94e1df7d08e5e06c71c1c192bba3a10d2c7fdb0f546636",  # 2026-10-26 개발 PC
}
