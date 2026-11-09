# -*- coding: utf-8 -*-
"""复现 #843：validity=True 已排除的过期记忆，被默认桶补召回重新装回 pack。"""
import json
import os
import tempfile
import time

from md_cg.mdcos import MdCGOS
from md_cg import bucketindex

FM = ("# 功能名：合成测试\n# 生效条件：离线\n# 子功能：测试\n"
      "# 执行：测试\n# 验证方式：test\n# 不适用条件：无\n\n")

rows = []
with tempfile.TemporaryDirectory(prefix="repro_") as root:
    g = MdCGOS(root, actor="test")
    now = time.time()
    for nid, until in [("active", None), ("expired", "2000-01-01")]:
        g.add(nid, FM + "阀门 合成规格 " + nid,
              layer="knowledge", created_at=now, session="s", valid_until=until,
              verification_basis="test", consistency=False)
    bucketindex.build(g)

    old_env = os.environ.get("MDCG_BUCKET_SCOPE")
    try:
        for mode in ("0", "1"):
            os.environ["MDCG_BUCKET_SCOPE"] = mode
            for rrf in (True, False):
                pack = g.recall("阀门", budget_tokens=6000, k=20, session="s",
                                validity=True, use_rrf=rrf)["pack"]
                info = [{"id": n["id"],
                         "bucket_added": (n.get("metadata") or {}).get("reason") == "bucket_scope",
                         "valid_until": (n.get("frontmatter") or {}).get("valid_until")} for n in pack]
                rows.append({"bucket": mode, "rrf": rrf, "ids": [x["id"] for x in info], "pack": info})
    finally:
        if old_env is None:
            os.environ.pop("MDCG_BUCKET_SCOPE", None)
        else:
            os.environ["MDCG_BUCKET_SCOPE"] = old_env

for r in rows:
    print(json.dumps(r, ensure_ascii=False))

print("\n=== 判定（validity=True 时两分支都不应含 expired）===")
for r in rows:
    has_expired = "expired" in r["ids"]
    ok = not has_expired
    print(f"bucket={r['bucket']} rrf={r['rrf']!s:5} ids={r['ids']}  "
          f"{'PASS' if ok else 'FAIL <<< 缺陷复现'}")
