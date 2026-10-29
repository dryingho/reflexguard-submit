# 커넥톰 회로 (`connectome/`)

MaleCNS 초파리 커넥톰에서 뽑은 회피 반사 회로입니다. ① 휠체어 PC 안에서도, ② 커넥톰 연산 서버에서도 같은 코드를 씁니다.

```
칸별 루밍 ─▶ LPLC2 185개 (수용장마다 자기 칸을 읽음) ─▶ DNp01 좌·우 (LIF, 1 ms 단위) ─▶ 위험도·회피 방향 ─▶ 디코더 STOP/LEFT/RIGHT/NONE
칸별 옆 흐름 ─▶ HS 6개 ─▶ DNa02·DNp15 ─▶ 왼쪽·오른쪽 벽 가까움
```

| 파일 | 하는 일 |
|---|---|
| `brain.py` | `MaleCNSBrain.step(루밍, dt_ms)` → `BrainDecision`(위험도·신호·좌우 회피 세기·옆벽 가까움·반응한 뉴런). `Circuit`(LPLC2→DNp01 LIF), `HSCircuit`, `Decoder`(히스테리시스) |
| `data/malecns-circuit.json` | LPLC2→DNp01 연결 가중치와 모델 버전 |
| `data/malecns-lplc2-rf.json` | LPLC2마다 시야의 어디를 보는지(수용장) |
| `data/malecns-hs.json` | HS 세포의 수용장과 DNa02·DNp15 연결 |
| `extract_malecns_lplc2_rf.py`, `extract_malecns_hs.py` | MaleCNS 공개 데이터(feather 파일)에서 위 `data/`를 다시 뽑는 스크립트. `pandas`·`pyarrow`(LPLC2는 `scipy`도)가 더 필요하다 — 프로젝트 실행에는 필요 없다 |

- 계산은 가볍습니다: 영상 한 장에 약 1 ms.
- 데이터 출처: MaleCNS v1.0 (HHMI Janelia·케임브리지대·MRC LMB·구글 리서치), CC-BY-4.0, https://male-cns.janelia.org/download/
- 자세한 설명: [커넥톰 로직](../docs/connectome.md)
