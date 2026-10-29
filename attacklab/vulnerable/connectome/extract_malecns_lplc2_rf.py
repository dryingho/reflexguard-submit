"""MaleCNS v1.0 공개 데이터에서 LPLC2마다 '시야의 어디를 보는지'(수용장 중심)를 뽑는다.

LPLC2에는 시각 기둥 좌표가 없으므로 연결을 두 번 따라간다 (시냅스 수 가중 평균):
    기둥 뉴런(Mi1·Mi4·Mi9·Tm1·Tm2·Tm9, assignedOlHex1/2 있음) → T4/T5의 기둥 좌표 → LPLC2의 기둥 좌표
기둥 좌표를 시야 각도로 바꾸는 방향은 T4의 선호 방향에서 얻는다. T4 수상돌기는 끝(Mi9 입력)에서 뿌리(Mi4 입력) 쪽이
선호 방향이고, T4a는 앞→뒤, T4c는 아래→위를 선호한다. 그래서 T4a들의 (Mi4 위치 − Mi9 위치) 평균이 기둥 좌표계의
'뒤쪽', T4c들의 평균이 '위쪽'이 된다. 크기는 이웃 기둥 사이를 홑눈 간격 5도로 맞춘다.

입력(공개 파일, https://male-cns.janelia.org/download/ — CC-BY 4.0):
    body-annotations-male-cns-v1.0-minconf-0.5.feather   (13 MB)
    connectome-weights-male-cns-v1.0-minconf-0.5.feather (1.1 GB)
실행:  python connectome/extract_malecns_lplc2_rf.py <주석 feather> <연결 feather> [출력 json]
필요:  pandas, pyarrow, scipy (프로젝트 실행에는 필요 없다 — 이 스크립트만 쓴다)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "connectome" / "data" / "malecns-lplc2-rf.json"
COLUMN_TYPES = ("Mi1", "Mi4", "Mi9", "Tm1", "Tm2", "Tm9")       # 기둥 좌표가 있는 T4/T5 입력
T4T5_TYPES = tuple(f"T{n}{s}" for n in (4, 5) for s in "abcd")
OMMATIDIAL_DEG = 5.0  # 이웃 홑눈(기둥) 사이 각도 — 초파리 겹눈은 대략 4.5~5도


def weighted_hex(pre_hex: dict[int, np.ndarray], edges, post_ids) -> dict[int, tuple[np.ndarray, float, float]]:
    """post마다 입력 기둥 좌표의 시냅스 수 가중 평균 (중심, 가중 표준편차, 시냅스 합)."""
    out = {}
    sub = edges[edges["body_post"].isin(post_ids) & edges["body_pre"].isin(pre_hex.keys())]
    for post, group in sub.groupby("body_post"):
        points = np.array([pre_hex[b] for b in group["body_pre"]])
        w = group["weight"].to_numpy(dtype=float)
        center = (points * w[:, None]).sum(axis=0) / w.sum()
        spread = float(np.sqrt((((points - center) ** 2).sum(axis=1) * w).sum() / w.sum()))
        out[int(post)] = (center, spread, float(w.sum()))
    return out


def main(argv: list[str]) -> int:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.feather as feather
    from scipy.spatial import cKDTree

    ann_path, weights_path = Path(argv[0]), Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT
    ann = pd.read_feather(ann_path, columns=["bodyId", "type", "somaSide", "assignedOlHex1", "assignedOlHex2"])
    ann["type"] = ann["type"].astype(str)
    by_type = {t: set(ann.loc[ann["type"] == t, "bodyId"].astype(int)) for t in COLUMN_TYPES + T4T5_TYPES + ("LPLC2", "DNp01")}
    side = dict(zip(ann["bodyId"].astype(int), ann["somaSide"]))
    hex_rows = ann.dropna(subset=["assignedOlHex1", "assignedOlHex2"])
    column_hex = {int(b): np.array([h1, h2], dtype=float)
                  for b, t, h1, h2 in hex_rows[["bodyId", "type", "assignedOlHex1", "assignedOlHex2"]].itertuples(index=False)
                  if t in COLUMN_TYPES}

    # 연결표에서 필요한 줄만 (받는 쪽이 T4/T5·LPLC2·DNp01)
    t4t5_ids = set().union(*(by_type[t] for t in T4T5_TYPES))
    wanted = t4t5_ids | by_type["LPLC2"] | by_type["DNp01"]
    table = feather.read_table(weights_path)
    names = table.column_names
    pre_col = next(c for c in names if c in ("body_pre", "bodyId_pre", "pre"))
    post_col = next(c for c in names if c in ("body_post", "bodyId_post", "post"))
    weight_col = next(c for c in names if c in ("weight", "synweight", "count"))
    mask = pc.is_in(table[post_col], value_set=pa.array(sorted(wanted), type=table[post_col].type))
    edges = table.filter(mask).select([pre_col, post_col, weight_col]).to_pandas()
    edges.columns = ["body_pre", "body_post", "weight"]
    del table

    # 1) T4/T5 기둥 좌표, T4a·T4c 선호 방향 (Mi9 → Mi4)
    t4t5_hex = weighted_hex(column_hex, edges, t4t5_ids)
    directions: dict[tuple[str, str], list[np.ndarray]] = {}
    for kind in ("T4a", "T4c"):
        mi4 = {b: v for b, v in column_hex.items() if b in by_type["Mi4"]}
        mi9 = {b: v for b, v in column_hex.items() if b in by_type["Mi9"]}
        at4 = weighted_hex(mi4, edges, by_type[kind])
        at9 = weighted_hex(mi9, edges, by_type[kind])
        for t4 in set(at4) & set(at9):
            directions.setdefault((kind, side.get(t4)), []).append(at4[t4][0] - at9[t4][0])

    # 2) LPLC2 기둥 좌표 (T4/T5 입력의 가중 평균)
    lplc2 = weighted_hex({b: c for b, (c, _, _) in t4t5_hex.items()}, edges, by_type["LPLC2"])

    # 3) 기둥 좌표 → 시야 각도 (눈마다): back = T4a 방향, up = T4c 방향, 이웃 기둥 간격 = 5도
    result_eyes = {}
    for eye in ("L", "R"):
        back = np.mean(directions[("T4a", eye)], axis=0)
        up = np.mean(directions[("T4c", eye)], axis=0)
        to_visual = np.linalg.inv(np.column_stack([back, up]))   # 기둥 좌표 차이 → (뒤쪽, 위쪽) 성분
        columns = np.array([v for b, v in column_hex.items() if b in by_type["Mi1"] and side.get(b) == eye])
        proj = columns @ to_visual.T
        nn = cKDTree(proj).query(proj, k=2)[0][:, 1]
        scale = OMMATIDIAL_DEG / float(np.median(nn))
        result_eyes[eye] = {"to_visual": to_visual, "scale": scale, "back_min": float(proj[:, 0].min()),
                            "up_mid": float(np.median(proj[:, 1])), "columns": len(columns),
                            "azimuth_extent_deg": float(np.ptp(proj[:, 0]) * scale),
                            "elevation_extent_deg": float(np.ptp(proj[:, 1]) * scale),
                            "t4a": len(directions[("T4a", eye)]), "t4c": len(directions[("T4c", eye)]),
                            "back_vs_up_cos": float(back @ up / np.linalg.norm(back) / np.linalg.norm(up))}

    dnp01 = by_type["DNp01"]
    to_gf = edges[edges["body_post"].isin(dnp01) & edges["body_pre"].isin(by_type["LPLC2"])]
    gf_side = {int(b): side.get(int(b)) for b in dnp01}
    neurons = []
    for body, (center, spread, inputs) in sorted(lplc2.items()):
        eye = side.get(body)
        e = result_eyes[eye]
        back, up = center @ e["to_visual"].T
        syn = to_gf[to_gf["body_pre"] == body]
        neurons.append({
            "id": str(body), "side": eye,
            "azimuth_deg": round((back - e["back_min"]) * e["scale"], 1),   # 눈의 맨 앞 기둥에서 뒤쪽으로 잰 각도
            "elevation_deg": round((up - e["up_mid"]) * e["scale"], 1),     # 눈의 가운데 높이에서 위쪽(+)
            "spread_deg": round(spread * float(np.mean(np.linalg.svd(e["to_visual"])[1])) * e["scale"], 1),
            "t4t5_synapses": int(inputs),
            "to_dnp01": {gf_side[int(g)]: int(w) for g, w in zip(syn["body_post"], syn["weight"])},
        })
    meta = {
        "source": "MaleCNS", "release": "v1.0", "license": "CC-BY-4.0",
        "source_url": "https://male-cns.janelia.org/download/",
        "files": [ann_path.name, weights_path.name],
        "method": "column hex (Mi1/Mi4/Mi9/Tm1/Tm2/Tm9) -> T4/T5 -> LPLC2, synapse-weighted means; "
                  "visual axes from T4a (back) and T4c (up) Mi9->Mi4 offsets; neighbour column = 5 deg",
        "eyes": {k: {kk: (vv.tolist() if isinstance(vv, np.ndarray) else vv) for kk, vv in v.items()}
                 for k, v in result_eyes.items()},
    }
    out_path.write_text(json.dumps({**meta, "neurons": neurons}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({len(neurons)} LPLC2)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
