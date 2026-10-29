"""MaleCNS v1.0 공개 데이터에서 HS 세포(HSN·HSE·HSS, 좌우)의 '시야의 어디서 앞→뒤 흐름을 받는지'와 하행 뉴런 연결을 뽑는다.

HS 세포는 넓은 시야의 가로 움직임을 모으는 소엽판 세포다. 앞→뒤(progressive) 움직임을 감지하는 T4a·T5a에서 입력을 받는다.
앞으로 갈 때 옆의 벽이 가까울수록 그쪽 화면이 빨리 흐르므로, 왼쪽·오른쪽 HS의 활동 차이가 '어느 쪽 벽이 가까운지'를 알려 준다.
(회전으로 생긴 흐름은 루밍 감지기가 이미 되돌린다.)

    기둥 뉴런(Mi1·Mi4·Mi9·Tm1·Tm2·Tm9, 기둥 좌표 있음) → T4/T5의 기둥 좌표 → HS가 받는 T4a·T5a 입력의 기둥 좌표
    → 시야 각도 (LPLC2 추출과 같은 변환: connectome/data/malecns-lplc2-rf.json의 eyes) → 10도 칸으로 묶은 수용장 지도
    HS → DNa02(방향 전환 하행 뉴런)·DNp15(DNHS1) 시냅스 수

입력(공개 파일, https://male-cns.janelia.org/download/ — CC-BY 4.0): 주석 feather, 연결 feather (LPLC2 추출과 같은 파일)
실행:  python connectome/extract_malecns_hs.py <주석 feather> <연결 feather> [출력 json]
필요:  pandas, pyarrow (프로젝트 실행에는 필요 없다)
한계:  HS 세포는 하행 뉴런과 상당 부분 전기 시냅스(gap junction)로 이어지는데, MaleCNS는 화학 시냅스만 센다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 프로젝트 폴더의 패키지(connectome, …)를 불러오기 위해
from connectome.extract_malecns_lplc2_rf import COLUMN_TYPES, T4T5_TYPES, weighted_hex  # noqa: E402

DEFAULT_OUT = ROOT / "connectome" / "data" / "malecns-hs.json"
EYES_FROM = ROOT / "connectome" / "data" / "malecns-lplc2-rf.json"
HS_TYPES = ("HSN", "HSE", "HSS")
PROGRESSIVE = ("T4a", "T5a")          # 앞→뒤 움직임 (T4a: ON, T5a: OFF)
DN_TYPES = ("DNa02", "DNp15")
BIN_DEG = 10.0


def main(argv: list[str]) -> int:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.feather as feather

    ann_path, weights_path = Path(argv[0]), Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT
    eyes = json.loads(EYES_FROM.read_text(encoding="utf-8"))["eyes"]

    ann = pd.read_feather(ann_path, columns=["bodyId", "type", "somaSide", "assignedOlHex1", "assignedOlHex2"])
    ann["type"] = ann["type"].astype(str)
    wanted_types = COLUMN_TYPES + T4T5_TYPES + HS_TYPES + DN_TYPES
    by_type = {t: set(ann.loc[ann["type"] == t, "bodyId"].astype(int)) for t in wanted_types}
    side = dict(zip(ann["bodyId"].astype(int), ann["somaSide"]))
    kind = dict(zip(ann["bodyId"].astype(int), ann["type"]))
    hex_rows = ann.dropna(subset=["assignedOlHex1", "assignedOlHex2"])
    column_hex = {int(b): np.array([h1, h2], dtype=float)
                  for b, t, h1, h2 in hex_rows[["bodyId", "type", "assignedOlHex1", "assignedOlHex2"]].itertuples(index=False)
                  if t in COLUMN_TYPES}

    t4t5_ids = set().union(*(by_type[t] for t in T4T5_TYPES))
    hs_ids = set().union(*(by_type[t] for t in HS_TYPES))
    dn_ids = set().union(*(by_type[t] for t in DN_TYPES))
    table = feather.read_table(weights_path)
    names = table.column_names
    pre_col = next(c for c in names if c in ("body_pre", "bodyId_pre", "pre"))
    post_col = next(c for c in names if c in ("body_post", "bodyId_post", "post"))
    weight_col = next(c for c in names if c in ("weight", "synweight", "count"))
    post_set = pa.array(sorted(t4t5_ids | hs_ids | dn_ids), type=table[post_col].type)
    edges = table.filter(pc.is_in(table[post_col], value_set=post_set)).select([pre_col, post_col, weight_col]).to_pandas()
    edges.columns = ["body_pre", "body_post", "weight"]
    del table

    t4t5_hex = weighted_hex(column_hex, edges, t4t5_ids)   # T4/T5마다 기둥 좌표 (입력 기둥 뉴런의 가중 평균)
    progressive = set().union(*(by_type[t] for t in PROGRESSIVE))

    neurons = []
    for body in sorted(hs_ids):
        eye_side = side.get(body)
        if eye_side not in eyes:
            continue
        eye = eyes[eye_side]
        to_visual = np.array(eye["to_visual"])
        inputs = edges[(edges["body_post"] == body) & edges["body_pre"].isin(progressive)]
        points, weights = [], []
        for pre, weight in zip(inputs["body_pre"], inputs["weight"]):
            center = t4t5_hex.get(int(pre))
            if center is None:
                continue
            back, up = center[0] @ to_visual.T
            points.append(((back - eye["back_min"]) * eye["scale"], (up - eye["up_mid"]) * eye["scale"]))
            weights.append(float(weight))
        if not points:
            continue
        points_arr, w = np.array(points), np.array(weights)
        bins: dict[tuple[float, float], float] = {}
        for (az, el), weight in zip(points_arr, w):  # 10도 칸으로 묶은 수용장 지도 (눈 기준: 앞쪽 끝에서 뒤로 잰 각, 위 +)
            key = (BIN_DEG * (np.floor(az / BIN_DEG) + 0.5), BIN_DEG * (np.floor(el / BIN_DEG) + 0.5))
            bins[key] = bins.get(key, 0.0) + weight
        total = float(w.sum())
        center = (points_arr * w[:, None]).sum(axis=0) / total
        outputs = edges[(edges["body_pre"] == body) & edges["body_post"].isin(dn_ids)]
        to_dn: dict[str, int] = {}
        for post, weight in zip(outputs["body_post"], outputs["weight"]):
            key = f"{kind[int(post)]}_{side.get(int(post))}"
            to_dn[key] = to_dn.get(key, 0) + int(weight)
        neurons.append({
            "id": str(body), "type": kind[body], "side": eye_side,
            "azimuth_deg": round(float(center[0]), 1), "elevation_deg": round(float(center[1]), 1),
            "progressive_synapses": int(total),
            "rf_bins": [[az, el, round(weight / total, 4)] for (az, el), weight in sorted(bins.items())],
            "to_dn": to_dn,
        })
    meta = {
        "source": "MaleCNS", "release": "v1.0", "license": "CC-BY-4.0",
        "source_url": "https://male-cns.janelia.org/download/", "files": [ann_path.name, weights_path.name],
        "method": "HS inputs from T4a/T5a (progressive motion); T4/T5 column = synapse-weighted mean of column-neuron "
                  "inputs; columns -> visual angles with the eye axes of malecns-lplc2-rf.json; 10 deg bins. "
                  "Outputs: chemical synapses to DNa02 and DNp15 (gap junctions are not in the connectome).",
        "bin_deg": BIN_DEG,
    }
    out_path.write_text(json.dumps({**meta, "neurons": neurons}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({len(neurons)} HS)")
    for n in neurons:
        print(f"  {n['type']} {n['side']} az={n['azimuth_deg']} el={n['elevation_deg']} T4a/T5a={n['progressive_synapses']} -> {n['to_dn']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
