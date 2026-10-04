#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe-nodes.py —— 探测 mihomo 节点可用性并自动切换到最快节点

背景：机场免费节点常常只有一部分存活，订阅里默认排第一的节点很可能是死的。
     更新完订阅后，如果主选择组指向一个死节点，代理就会一直超时。

做什么：
  1. 从 RESTful API 读取所有策略组
  2. 找到"主选择组"（Selector 类型、名字含"选择节点/节点选择/Proxy"等）
  3. 对该组下的所有真实节点逐个测延迟
  4. 把主选择组切到延迟最低的存活节点
  5. 打印结果表

用法：
  python3 probe-nodes.py                  # 自动探测并切换
  python3 probe-nodes.py --dry-run        # 只探测，不切换
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://127.0.0.1:9090"
# 用 Cloudflare 的 204：gstatic 在部分机场节点上会 404/超时，
# 会误判成「节点全挂」（实测 7 个节点实际都能用，却全显示超时）。
TEST_URL = "http://cp.cloudflare.com/generate_204"
TIMEOUT_MS = 6000

# 主选择组的候选名（按优先级）
SEL_CANDIDATES = [
    "🔰 选择节点",
    "🚀 节点选择",
    "节点选择",
    "选择节点",
    "Proxy",
    "GLOBAL",
]

# 非真实节点的名字（策略组名 / 内置项），不参与延迟测试
SKIP_NAMES = {"DIRECT", "REJECT", "PASS", "COMPATIBLE", "GLOBAL"}

# local provider 里的占位节点（direct 类型，无实际流量，延迟恒为 0 会污染结果）
PLACEHOLDER = "占位-直连"


def api_get(path, timeout=25):
    url = API + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api_put_json(path, payload, timeout=15):
    url = API + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "ignore")


def probe_group(group, timeout_ms=TIMEOUT_MS):
    """测一个策略组内**所有成员**的延迟。

    ⚠️ mihomo v1.19.32 **不支持** `GET /proxies/{节点名}/delay`（一律 404），
    必须用组级端点 `GET /group/{组名}/delay`，它一次返回组内所有成员的延迟：
        {"🇯🇵 免费-日本5-Ver.9": 146, ...}
    返回 {节点名: 延迟ms}；请求失败返回空 dict。
    """
    enc = urllib.parse.quote(group, safe="")
    path = (f"/group/{enc}/delay?timeout={timeout_ms}"
            f"&url={urllib.parse.quote(TEST_URL, safe='')}")
    try:
        d = api_get(path, timeout=(timeout_ms // 1000) + 15)
        return {k: v for k, v in d.items()
                if k not in SKIP_NAMES and isinstance(v, (int, float))}
    except Exception:
        return {}


def find_main_selector(proxies):
    for cand in SEL_CANDIDATES:
        if cand in proxies:
            return cand
    # 兜底：找第一个 Selector 且成员里有真实节点的
    for k, v in proxies.items():
        if v.get("type") == "Selector":
            return k
    return None


def main():
    dry_run = "--dry-run" in sys.argv

    try:
        data = api_get("/proxies")
    except Exception as e:
        print(f"  [✗] 无法连接面板 API: {e}")
        return 1

    proxies = data.get("proxies") or {}
    sel = find_main_selector(proxies)
    if not sel:
        print("  [!] 未找到可用的选择组")
        return 1

    grp = proxies[sel]
    members = grp.get("all") or []
    # 只保留真实节点（排除 DIRECT/REJECT、其它策略组、占位节点）
    real = [m for m in members
            if m not in SKIP_NAMES
            and m != PLACEHOLDER
            and proxies.get(m, {}).get("type") not in ("Selector", "URLTest", "Fallback", "LoadBalance", "Direct")]

    print(f"  选择组: {sel}")
    print(f"  成员  : {len(members)} 个（其中真实节点 {len(real)} 个），开始测延迟...")
    print()

    delays = probe_group(sel)
    results = []
    for name in real:
        d = delays.get(name)
        results.append((name, d))
        tag = f"{d} ms" if d is not None else "超时/失败"
        print(f"    {'✓' if d is not None else '✗'} {name:<28} {tag}")

    alive = [(n, d) for n, d in results if d is not None]
    alive.sort(key=lambda x: x[1])

    print()
    if not alive:
        if not delays:
            print("  [✗] 测速接口无返回。可能原因：")
            print("      1) 机场侧拥堵（免费节点常见，等几分钟再试）")
            print("      2) 测速 URL 不可达")
            print("      3) 节点全挂")
        else:
            print("  [✗] 所有节点均不可用！请检查订阅是否过期或机场故障。")
        return 1

    best, best_delay = alive[0]
    print(f"  存活节点: {len(alive)}/{len(real)}")
    print(f"  最快节点: {best} ({best_delay} ms)")

    if dry_run:
        print("  [!] --dry-run 模式，未切换")
        return 0

    cur = grp.get("now")
    if cur == best:
        print(f"  [✓] 当前已是该节点，无需切换")
        return 0

    enc = urllib.parse.quote(sel, safe="")
    try:
        status, body = api_put_json(f"/proxies/{enc}", {"name": best})
        # 有些版本对 body 格式挑剔，无论 HTTP 状态都回头验证实际结果
    except urllib.error.HTTPError as e:
        pass  # 忽略，下面用 GET 验证真实结果
    except Exception as e:
        print(f"  [!] 切换请求异常: {e}")

    time.sleep(1.5)
    try:
        now = api_get(f"/proxies/{enc}").get("now")
    except Exception:
        now = None

    if now == best:
        print(f"  [✓] 已切换: {cur} → {best}")
        return 0
    else:
        print(f"  [!] 切换后实际为: {now}（期望 {best}）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
