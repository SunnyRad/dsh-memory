# -*- coding: utf-8 -*-
"""复现 #842：默认桶召回先按 id 截取再按时间排序，9 条记录时最新一条被丢掉。"""
import json
import time
from pathlib import Path
import tempfile
from types import SimpleNamespace

from md_cg import bucketindex as bi, bucketscope as bs

# 统一输出编码，避免 Windows 控制台编码问题
base = time.mktime(time.strptime("2026-11-09 09:00:00", "%Y-%m-%d %H:%M:%S"))
rows = []
for mode in ("session", "day"):
    for count in (8, 9):
        with tempfile.TemporaryDirectory(prefix="repro_") as tmp:
            records = {
                f"a{i}": {"id": f"a{i}", "content": "索引",
                          "frontmatter": {"created_at": base + i, "session": "s"}}
                for i in range(count - 1)
            }
            records["z_latest"] = {
                "id": "z_latest", "content": "索引",
                "frontmatter": {"created_at": base + 100, "session": "s"},
            }
            store = SimpleNamespace(root=Path(tmp), index={"nodes": records},
                                    get=records.get)
            bi.build(store)
            result = bs.scope_recall(store, "索引", "s" if mode == "session" else None,
                                     now=base + 200)
            ids = [node["id"] for node, _, _, _ in result]
            rows.append({"mode": mode, "nodes": count, "cap": bs.DEFAULT_CAP,
                         "latest_included": "z_latest" in ids, "ids": ids})

for r in rows:
    print(json.dumps(r, ensure_ascii=False))

print("\n=== 判定 ===")
for r in rows:
    expect = True  # 四种情况都应当包含最新记录
    ok = (r["latest_included"] == expect)
    print(f"{r['mode']:8} n={r['nodes']}  latest_included={r['latest_included']!s:5} "
          f"{'PASS' if ok else 'FAIL <<< 缺陷复现'}")
