# -*- coding: utf-8 -*-
"""issue #842 / #843 守卫：桶作用域召回的「时间优先级」与「时效口径」两个入口。

#842 —— 默认桶召回先按 id 截取再按时间排序，9 条记录时最新一条被丢掉。
        根因：`_pick` 用 `(-hits, nid)` 排序后在 cap 处截断，`_recency_key` 只在
        截断**之后**才生效，丢掉的最新条目再也找不回来。
#843 —— `recall(validity=True)` 已排除的过期记忆，被默认桶补召回重新装回 pack。
        根因：桶补召回是装包的第二入口，`scope_recall` 未接收 validity，
        也未在 append 前复用过期判据。

两条守卫都必须经过**真实索引构建**与**完整 recall 调用**——只测 `_pick` 的返回值
会漏掉装包阶段（#843 的缺陷就发生在装包阶段，不在选取阶段）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from md_cg import bucketindex as bi  # noqa: E402
from md_cg import bucketscope as bs  # noqa: E402
from md_cg.mdcos import MdCGOS  # noqa: E402

FAILS = []

CCG = ("# 功能名：{n}\n# 生效条件：c\n# 子功能：s\n# 执行：e\n"
       "# 验证方式：test\n# 不适用条件：无\n\n{body}\n")


def ok(cond, name, detail=""):
    if cond:
        print("  PASS " + name)
    else:
        print("  FAIL " + name + "  " + str(detail)[:200])
        FAILS.append(name)


# ---------------------------------------------------------------- #842
def guard_842():
    print("\n-- #842 桶内时间优先级必须先于 cap 截断 --")
    base = time.mktime(time.strptime("2026-11-09 09:00:00", "%Y-%m-%d %H:%M:%S"))
    for mode in ("session", "day"):
        for count in (8, 9):
            with tempfile.TemporaryDirectory(prefix="i842_") as tmp:
                records = {
                    f"a{i}": {"id": f"a{i}", "content": "索引",
                              "frontmatter": {"created_at": base + i, "session": "s"}}
                    for i in range(count - 1)
                }
                # id 字典序**最靠后**、时间**最新**——正是被 cap 先丢掉的那条
                records["z_latest"] = {
                    "id": "z_latest", "content": "索引",
                    "frontmatter": {"created_at": base + 100, "session": "s"},
                }
                store = SimpleNamespace(root=Path(tmp), index={"nodes": records},
                                        get=records.get)
                bi.build(store)
                result = bs.scope_recall(store, "索引",
                                         "s" if mode == "session" else None,
                                         now=base + 200)
                ids = [node["id"] for node, _, _, _ in result]
                ok("z_latest" in ids and ids[0] == "z_latest",
                   f"842-{mode}-{count} 最新记录入选且排第一（cap={bs.DEFAULT_CAP}）", ids)

    # 超过 cap 时必须**优先留最近的**，而不是按 id 留
    with tempfile.TemporaryDirectory(prefix="i842_") as tmp:
        recs = {f"n{i:02d}": {"id": f"n{i:02d}", "content": "索引",
                              "frontmatter": {"created_at": base + i, "session": "s"}}
                for i in range(12)}
        st = SimpleNamespace(root=Path(tmp), index={"nodes": recs}, get=recs.get)
        bi.build(st)
        got = [n["id"] for n, _s, _q, _p in bs.scope_recall(st, "索引", "s", now=base + 500)]
        ok(got == ["n11", "n10", "n09", "n08", "n07", "n06", "n05", "n04"],
           "842-cap 超限时按时间降序取前 cap 条", got)

    # 同时间：id 兜底，保证确定性
    with tempfile.TemporaryDirectory(prefix="i842_") as tmp:
        recs = {nid: {"id": nid, "content": "索引",
                      "frontmatter": {"created_at": base, "session": "s"}}
                for nid in ("c", "a", "b")}
        st = SimpleNamespace(root=Path(tmp), index={"nodes": recs}, get=recs.get)
        bi.build(st)
        r1 = [n["id"] for n, _s, _q, _p in bs.scope_recall(st, "索引", "s", now=base + 10)]
        r2 = [n["id"] for n, _s, _q, _p in bs.scope_recall(st, "索引", "s", now=base + 10)]
        ok(r1 == r2 == ["a", "b", "c"], "842-同时间按 id 兜底且确定", (r1, r2))


# ---------------------------------------------------------------- #843
def guard_843():
    print("\n-- #843 桶补召回不得绕过 validity 口径 --")
    rows = []
    with tempfile.TemporaryDirectory(prefix="i842_") as root:
        g = MdCGOS(root, actor="test")
        now = time.time()
        g.add("active", CCG.format(n="现行", s="w", e="1", body="阀门 合成规格 现行"),
              layer="knowledge", created_at=now, session="s",
              verification_basis="test", consistency=False)
        g.add("expired", CCG.format(n="过期", s="w", e="2", body="阀门 合成规格 过期"),
              layer="knowledge", created_at=now, session="s",
              valid_until="2000-01-01", verification_basis="test", consistency=False)
        bi.build(g)
        old = os.environ.get("MDCG_BUCKET_SCOPE")
        try:
            for mode in ("0", "1"):
                os.environ["MDCG_BUCKET_SCOPE"] = mode
                for rrf in (True, False):
                    pack = g.recall("阀门", budget_tokens=6000, k=20, session="s",
                                    validity=True, use_rrf=rrf)["pack"]
                    rows.append((mode, rrf, [n["id"] for n in pack],
                                 [(n.get("metadata") or {}).get("reason") for n in pack]))
        finally:
            if old is None:
                os.environ.pop("MDCG_BUCKET_SCOPE", None)
            else:
                os.environ["MDCG_BUCKET_SCOPE"] = old

        for mode, rrf, ids, reasons in rows:
            ok("expired" not in ids,
               f"843-validity=True bucket={mode} rrf={rrf} 不装回过期条目", ids)
            # 反向对照：不能被「整段滤空」冒充通过——在效条目必须仍在
            ok("active" in ids,
               f"843-validity=True bucket={mode} rrf={rrf} 在效条目仍在（非空转）", ids)

    with tempfile.TemporaryDirectory(prefix="i842_") as root:
        g = MdCGOS(root, actor="test")
        now = time.time()
        g.add("active2", CCG.format(n="现行", s="w", e="1", body="闸门 合成 现行"),
              layer="knowledge", created_at=now, session="s2",
              verification_basis="test", consistency=False)
        g.add("expired2", CCG.format(n="过期", s="w", e="2", body="闸门 合成 过期"),
              layer="knowledge", created_at=now, session="s2",
              valid_until="2000-01-01", verification_basis="test", consistency=False)
        bi.build(g)
        old = os.environ.get("MDCG_BUCKET_SCOPE")
        os.environ["MDCG_BUCKET_SCOPE"] = "1"
        try:
            for rrf in (True, False):
                off = [n["id"] for n in g.recall("闸门", budget_tokens=6000, k=20,
                                                 session="s2", use_rrf=rrf)["pack"]]
                ok("expired2" in off,
                   f"843-off-rrf={rrf} validity 未启用时过期条目照旧可见（不擅自收紧）", off)
        finally:
            if old is None:
                os.environ.pop("MDCG_BUCKET_SCOPE", None)
            else:
                os.environ["MDCG_BUCKET_SCOPE"] = old

    # not_yet 不得被连坐（与 672 同一语义：只排已过期）
    with tempfile.TemporaryDirectory(prefix="i842_") as root:
        g = MdCGOS(root, actor="test")
        now = time.time()
        g.add("future", CCG.format(n="未生效", s="w", e="1", body="龙头 合成 未生效"),
              layer="knowledge", created_at=now, session="s3",
              valid_from="2099-01-01", verification_basis="test", consistency=False)
        bi.build(g)
        old = os.environ.get("MDCG_BUCKET_SCOPE")
        os.environ["MDCG_BUCKET_SCOPE"] = "1"
        try:
            pk = [n["id"] for n in g.recall("龙头", budget_tokens=6000, k=20,
                                            session="s3", validity=True)["pack"]]
            ok("future" in pk, "843-未生效条目保留（validity 只排已过期）", pk)
        finally:
            if old is None:
                os.environ.pop("MDCG_BUCKET_SCOPE", None)
            else:
                os.environ["MDCG_BUCKET_SCOPE"] = old

    # **桶层隔离**：上面那条走常规候选层，候选层本来就保留 not_yet——
    # 即使桶层把 not_yet 误当过期剔除，结果也照样正确。必须直接量桶层。
    print("\n-- 桶层隔离：_pick 自身不得把 not_yet / 无时间轴 当过期 --")
    base = time.time()
    cases = {
        "not_yet": {"id": "ny", "content": "龙头 内容",
                    "frontmatter": {"valid_from": "2099-01-01", "created_at": base}},
        "no_axis": {"id": "na", "content": "龙头 内容",
                    "frontmatter": {"created_at": base}},
        "expired": {"id": "ex", "content": "龙头 内容",
                    "frontmatter": {"valid_until": "2000-01-01", "created_at": base}},
        "live": {"id": "lv", "content": "龙头 内容",
                 "frontmatter": {"valid_until": "2099-01-01", "created_at": base}},
    }
    st = SimpleNamespace(index={"nodes": cases}, get=cases.get)
    kept = [n["id"] for n, _s, _q, _p in
            bs._pick(st, list(cases), ["龙头"], 10, validity=True, now=base)]
    ok("ny" in kept, "_pick 保留未生效（not_yet ≠ 过期）", kept)
    ok("na" in kept, "_pick 保留无时间轴（不猜测）", kept)
    ok("lv" in kept, "_pick 保留在效条目", kept)
    ok("ex" not in kept, "_pick 剔除已过期", kept)


# ------------------------------------------------- 判据单点：形状适配
def guard_shape():
    print("\n-- 判据单点：索引顶层 / store.get frontmatter 两种形状都判得出 --")
    from md_cg import trust  # noqa: PLC0415

    old_node = {"id": "x", "valid_until": "2000-01-01"}
    new_node = {"id": "y", "frontmatter": {"valid_until": "2000-01-01"}}
    live = {"id": "z", "valid_until": "2099-01-01", "created_at": 1.0}
    ok(bs._is_expired(old_node, None) is True, "形状A 顶层 valid_until 判过期")
    ok(bs._is_expired(new_node, None) is True, "形状B frontmatter.valid_until 判过期")
    ok(bs._is_expired(live, None) is False, "在效节点不误判")
    ok(bs._is_expired({"id": "w"}, None) is False, "无时间轴不猜测（False）")
    ok(trust.is_expired({"valid_from": "2099-01-01"}) is False,
       "未生效不算过期（与 trust 同源）")


def main():
    print("=" * 70)
    print("issue #842 / #843 守卫：桶召回的时间优先级与时效口径")
    print("=" * 70)
    guard_842()
    guard_843()
    guard_shape()
    print()
    if FAILS:
        print("issue842_843：%d 项失败" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("issue842_843：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
