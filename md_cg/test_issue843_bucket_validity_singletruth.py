# -*- coding: utf-8 -*-
"""#843 补丁的两个生产级缺陷守卫（针对 PR #848 / ac3d50ed 的修复）。

PR #848 把 validity 透传到了桶补召回，但 `bucketscope._is_expired` 是**手写日期解析**，
引入两个可复现缺陷：

## 缺陷 1：NameError 被 except 静默吞掉

`_is_expired` 调 `time.mktime(time.strptime(...))`，而 `bucketscope` 模块**没有
`import time`**。`validity=True` 且节点带 `valid_until` 时必抛 `NameError`。

更糟的是 `mdcos.recall` 的桶补召回整段包在 `except Exception: _bucket_scope_n = 0`，
异常被吞掉 => 表现为「桶补召回一条都没补」，而不是报错。
**在效条目也一起丢失**，比原 #843 更隐蔽。

## 缺陷 2：过期别名漏判

`trust.validity` 的终点别名是
`effective_until` > `valid_until` > `expires_at` > `expire_at` > `expiry` > `deadline`，
手写只认 `valid_until` / `validUntil`。用后五个别名标记过期的记忆被判成「未过期」，
照样装回 pack —— #843 的原始症状并未修全。

修法：`_is_expired` 复用 `trust.is_expired` 单一真源，不在本模块另立时间解析。

跑法：python -X utf8 -m md_cg.test_issue843_bucket_validity_singletruth
"""
from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from md_cg import bucketscope as bs  # noqa: E402
from md_cg import trust  # noqa: E402

FAILS = []
PAST = "2000-01-01"

#: trust.validity 认的全部终点别名（单一真源口径）。断言本模块对每一个都判得出。
UNTIL_ALIASES = ("effective_until", "valid_until", "expires_at",
                 "expire_at", "expiry", "deadline")


def ok(cond, name, detail=""):
    if cond:
        print("  PASS " + name)
    else:
        print("  FAIL " + name + "  " + str(detail)[:160])
        FAILS.append(name)


def _node(fm):
    return {"id": "x", "content": "阀门 合成规格", "frontmatter": fm}


def main():
    print("=" * 70)
    print("#843 补丁缺陷守卫：单一真源 + 不得崩溃")
    print("=" * 70)
    now = time.time()

    # ---- 缺陷 1：不得 NameError（曾经 time 未导入，必崩）----
    print()
    print("-- 缺陷1 判据不得崩溃（曾经 time 未导入 -> NameError）--")
    for alias in UNTIL_ALIASES:
        try:
            bs._is_expired(_node({alias: PAST}), now)
            ok(True, "不崩溃 alias=%s" % alias)
        except Exception as e:                      # noqa: BLE001
            ok(False, "不崩溃 alias=%s" % alias, "%s: %s" % (type(e).__name__, e))

    # ---- 缺陷 2：必须与 trust 单一真源逐个别名一致 ----
    print()
    print("-- 缺陷2 与 trust.is_expired 逐别名一致（漏判即 #843 修不全）--")
    for alias in UNTIL_ALIASES:
        fm = {alias: PAST}
        want = trust.is_expired(fm, now=now)
        try:
            got = bs._is_expired(_node(fm), now)
        except Exception as e:                      # noqa: BLE001
            ok(False, "判据一致 alias=%s" % alias, "崩溃 %s" % type(e).__name__)
            continue
        ok(got == want,
           "判据一致 alias=%s（trust=%s）" % (alias, "过期" if want else "未过期"),
           "本模块=%s trust=%s" % (got, want))

    # 防空转：至少有一个别名真的判成过期，否则上面的"一致"全是 False==False
    ok(any(trust.is_expired({a: PAST}, now=now) for a in UNTIL_ALIASES),
       "非空转：这批别名里确有过期可判")

    # ---- 形状适配：frontmatter 与顶层两种形状都要判得出 ----
    print()
    print("-- 形状适配：store.get 的 frontmatter / 索引节点顶层 --")
    ok(bs._is_expired(_node({"valid_until": PAST}), now), "frontmatter 形状判过期")
    ok(bs._is_expired({"id": "y", "content": "x", "valid_until": PAST}, now),
       "顶层形状判过期")
    ok(not bs._is_expired(_node({}), now), "无时间轴不猜测（False）")
    ok(not bs._is_expired(_node({"valid_from": "2099-01-01"}), now),
       "未生效不算过期（与 trust 同源）")
    ok(not bs._is_expired(_node({"valid_until": "2099-01-01"}), now),
       "在效节点不误判")

    print()
    print("全部通过" if not FAILS else "失败 %d 项" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
