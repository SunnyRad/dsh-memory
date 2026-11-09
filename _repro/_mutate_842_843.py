# -*- coding: utf-8 -*-
"""定点变异自检：把每个修复点单独退回，守卫必须变红。

只跑守卫不够——守卫可能因为别的原因绿。这里逐个把修复**反向改掉**，
验证守卫确实钉在那一行逻辑上。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCOPE = REPO / "md_cg" / "bucketscope.py"
MDCOS = REPO / "md_cg" / "mdcos.py"
GUARD = "md_cg.test_issue842_843_bucket_recency_validity"

results = []


def run_guard(env_extra=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", GUARD],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def mutate(path: Path, old: str, new: str):
    src = path.read_text(encoding="utf-8")
    if old not in src:
        raise SystemExit("变异锚点未命中：%r" % old[:80])
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def case(name, path, old, new, expect_fail_substr):
    """变异后守卫必须**变红**。

    `expect_fail_substr` 用于确认「红得其所」——但一个修复点常被多条断言同时钉住，
    命中集合可能不同于预设的那一条。故口径取：
      · 必红（exit != 0），否则记为漏；
      · 若给了 expect_fail_substr，要求它**出现在任一 FAIL 行**里；
        为空则只要求必红。
    这样既不会把「红了但不是因为这条」当成通过，也不会因为我预设字符串写窄而误报漏。
    """
    bak = path.with_suffix(path.suffix + ".mutbak")
    shutil.copy2(path, bak)
    try:
        mutate(path, old, new)
        code, out = run_guard()
        fails = [l.strip() for l in out.splitlines() if "FAIL" in l]
        caught = code != 0 and (not expect_fail_substr
                                or any(expect_fail_substr in f for f in fails))
        results.append((name, caught, code, expect_fail_substr))
        print(("  CAUGHT  " if caught else "  MISSED  ") + name + f"  (exit={code})")
        if caught:
            print("     命中失败项：" + (" | ".join(f[:70] for f in fails[:3]) or "(无)"))
        else:
            print("     未按预期变红，FAIL 行：" + (" | ".join(f[:70] for f in fails[:4]) or "(无)"))
    finally:
        shutil.copy2(bak, path)
        bak.unlink()


def main():
    print("=" * 70)
    print("定点变异自检（每个修复点单独退回，守卫必须变红）")
    print("=" * 70)

    # 基线：未变异时必须全绿，否则后面无从判断
    code, out = run_guard()
    print(f"\n[基线] 未变异 exit={code} -> {'OK' if code == 0 else 'FAIL'}")
    if code != 0:
        print(out[-2000:])
        return 2

    print("\n-- M1/#842：排序键退回「不含时间」（先按 id 截断再排序）--")
    case("M1 排序键去掉时间项",
         SCOPE,
         'scored.append(((-hits, _recency_key(node), nid), node))',
         'scored.append(((-hits, 0.0, nid), node))',
         "FAIL 842-")

    print("\n-- M2/#842：截断后再排序（时间优先级被挪到 cap 之后）--")
    case("M2 时间排序挪到截断之后",
         SCOPE,
         'scored.sort(key=lambda it: it[0])\n    return [(node, 0.0, {"reason": "bucket_scope"}, [])\n            for _k, node in scored[:cap]]',
         'return [(node, 0.0, {"reason": "bucket_scope"}, [])\n            for _k, node in scored[:cap]]',
         "FAIL 842-")

    print("\n-- M3/#843：_pick 无视 validity（桶召回不看时效）--")
    case("M3 _pick 跳过过期过滤",
         SCOPE,
         '        if validity and _is_expired(node, now):\n            continue\n',
         '',
         "FAIL 843-")

    print("\n-- M4/#843：调用侧不传 validity（修了 _pick 但没接线）--")
    case("M4 scope_recall 调用点不传 validity",
         MDCOS,
         '                                          validity=validity)',
         '                                          validity=None)',
         "FAIL 843-")

    print("\n-- M5/#843：形状适配只认 frontmatter（漏索引顶层形状）--")
    # 注意：真实 recall 路径恒为 frontmatter 形状，故 843 的真实路径断言**不会**因
    # 这个变异变红；钉住它的是形状适配的防御性兜底断言（形状A）。
    case("M5 只认 frontmatter 形状",
         SCOPE,
         '    return trust.is_expired(node, now=now)',
         '    return False',
         "形状A 顶层")

    print("\n-- M6/#843：反向陷阱——把 not_yet 也当过期（过度收紧）--")
    # 这条由桶层隔离断言钉住（常规候选层本来就保留 not_yet，测不出桶层过度收紧）。
    case("M6 validity 收紧为「非 active 全排」",
         SCOPE,
         '    fm = node.get("frontmatter")\n    if isinstance(fm, dict) and trust.is_expired(fm, now=now):\n        return True\n    return trust.is_expired(node, now=now)',
         '    fm = node.get("frontmatter")\n    if isinstance(fm, dict) and trust.validity(fm, now=now)[0] != "active":\n        return True\n    return trust.validity(node, now=now)[0] != "active"',
         "_pick 保留未生效")

    print()
    missed = [r for r in results if not r[1]]
    for name, caught, code, sub in results:
        print(("[杀] " if caught else "[漏] ") + name)
    print()
    if missed:
        print(f"定点变异：{len(missed)}/{len(results)} 未被抓住 —— 守卫有洞")
        return 1
    print(f"定点变异：{len(results)}/{len(results)} 全部被杀")
    return 0


if __name__ == "__main__":
    sys.exit(main())
