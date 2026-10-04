#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sub-panel.py —— 订阅更新控制台（后端）

用途：
  给 NAS 上的 mihomo 提供一个「网页版订阅管理面板」，解决 metacubexd
  面板只能读、不能更新订阅的问题（mihomo RESTful API 是只读的）。

提供：
  GET  /                     控制台页面
  GET  /api/status           订阅信息（流量、到期、节点数、运行状态）
  POST /api/update           触发更新（body: {"url": "可选的新订阅链接"}）
  GET  /api/log              取最近的更新日志
  POST /api/probe            探测节点并切换到最快可用节点
  GET  /api/nodes            当前节点延迟列表

  --- 控制类（转发 mihomo API）---
  GET    /api/proxies                策略组 + 节点全树
  POST   /api/select                 切换节点 body: {"group":"...","name":"..."}
  GET    /api/delay?name=...         测单节点延迟
  GET    /api/connections            实时连接列表
  DELETE /api/connections            一键断开全部
  POST   /api/mode                   切模式 body: {"mode":"rule|global|direct"}
  POST   /api/core/restart           重启内核
  POST   /api/container/{start|stop|restart}   启停容器
  GET    /api/traffic                实时速率（1 秒采样）
  GET    /api/logs/stream            实时日志（SSE 转发）

依赖：仅 Python 3 标准库
运行：python3 sub-panel.py            （默认 0.0.0.0:8090）
"""
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------- 路径配置 ----------------
CLASH_DIR = os.environ.get("CLASH_DIR", "/volume1/docker/clash")
CONFIG_DIR = os.path.join(CLASH_DIR, "config")
CONFIG = os.path.join(CONFIG_DIR, "config.yaml")
SUB_FILE = os.path.join(CLASH_DIR, "sub.txt")
UPDATE_SH = os.path.join(CLASH_DIR, "update.sh")
PROBE_PY = os.path.join(CLASH_DIR, "scripts", "probe-nodes.py")
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

MIHOMO_API = os.environ.get("MIHOMO_API", "http://127.0.0.1:9090")
LISTEN_PORT = int(os.environ.get("PANEL_PORT", "8090"))
# mihomo 面板（metacubexd）本身的端口，从 API 地址里取，供前端拼绝对链接
PANEL_PORT = int(MIHOMO_API.rsplit(":", 1)[-1]) if ":" in MIHOMO_API else 9090

# ---------------- Docker 控制 ----------------
# 容器化部署时，本面板自己就是容器，不能再靠 docker CLI（镜像里没装）。
# 改为直接调 Docker Engine API：HTTP over unix socket，纯标准库即可。
DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
MIHOMO_CT = os.environ.get("MIHOMO_CONTAINER", "mihomo")
DOCKER_API = os.environ.get("DOCKER_API", "")  # 留空则走 unix socket

# 更新状态（内存中）
STATE = {
    "running": False,
    "step": "",
    "log": [],
    "last_result": None,
    "last_time": None,
    # 自动故障转移
    "guard_on": False,
    "guard_checked": None,      # 上次检测时间戳
    "guard_alive": [],          # 实测可用的节点名
    "guard_current": "",# 守护认为应该用的节点
}
STATE_LOCK = threading.Lock()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with STATE_LOCK:
        STATE["log"].append(line)
        STATE["log"] = STATE["log"][-300:]
    print(line, flush=True)


# ---------------- Docker Engine API ----------------
class _UnixHTTPConnection(http.client.HTTPConnection):
    """把 HTTP 请求发到 unix socket 上（Docker daemon 的控制通道）。"""

    def __init__(self, sock_path, timeout=30):
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._sock_path)
        self.sock = s


def _unix_http(method, path, body=None, timeout=60):
    """直接用 http.client 走 unix socket 发请求。

    不走 urllib.request.build_opener —— 那个要求传入已实例化的
    BaseHandler，而我们的 handler 需要 sock_path 参数，构造方式不兼容。
    这里手写一遍，行为最可控。
    返回 (status, body_text)。
    """
    conn = _UnixHTTPConnection(DOCKER_SOCK, timeout=timeout)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", "localhost")
        conn.putheader("Content-Type", "application/json")
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        txt = resp.read().decode("utf-8", "ignore")
        return resp.status, txt
    finally:
        try:
            conn.close()
        except Exception:
            pass


def docker_req(method, path, body=None, timeout=60):
    """调 Docker Engine API。返回 (status_code, 响应文本)。"""
    if DOCKER_API:
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        url = DOCKER_API.rstrip("/") + path
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore")
        except Exception as e:
            return 0, str(e)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    try:
        return _unix_http(method, path, data, timeout=timeout)
    except Exception as e:
        return 0, str(e)


def docker_container_id(name=MIHOMO_CT):
    """按名字查容器 ID，优先取 running 的。"""
    st, txt = docker_req("GET", f"/containers/json?all=1")
    if st != 200:
        return None, txt
    try:
        items = json.loads(txt)
    except Exception:
        return None, "响应解析失败"
    running = None
    for c in items:
        for n in (c.get("Names") or []):
            if n.lstrip("/") == name:
                if c.get("State") == "running":
                    return c["Id"], ""
                running = running or c["Id"]
    if running:
        return running, ""
    return None, f"找不到容器 {name}"


def container_action(action):
    """启停 mihomo 容器。action: start / stop / restart"""
    if action not in ("start", "stop", "restart"):
        raise ValueError("非法操作")

    cid, err = docker_container_id()
    if not cid:
        msg = err or "找不到 mihomo 容器"
        log(f"容器 {action} 失败：{msg}")
        return False, msg

    if action == "start":
        st, txt = docker_req("POST", f"/containers/{cid}/start", timeout=120)
    elif action == "stop":
        st, txt = docker_req("POST", f"/containers/{cid}/stop?t=10", timeout=60)
    else:
        st, txt = docker_req("POST", f"/containers/{cid}/restart?t=10", timeout=90)

    ok = 200 <= st < 300
    log(f"容器 {action} -> HTTP {st}" + ("" if ok else f" ({txt[:200]})"))
    return ok, txt


def container_recreate():
    """重建 mihomo 容器使新配置生效。

    先尝试 Docker API 重建（等价于 docker compose up -d --force-recreate）：
    停止 -> 改名备份 -> 用原镜像与端口新建同名容器。
    失败时回退到 reload 配置（PUT /configs?force=true），不重启容器。
    """
    cid, err = docker_container_id()
    if not cid:
        return False, err or "找不到 mihomo 容器"

    # 采集重建所需参数
    st, info = docker_req("GET", f"/containers/{cid}/json")
    if st != 200:
        return False, f"读取容器信息失败 HTTP {st}"
    try:
        d = json.loads(info)
    except Exception:
        return False, "容器信息解析失败"

    image = d.get("Config", {}).get("Image", "")
    env = d.get("Config", {}).get("Env") or []
    binds = (d.get("HostConfig", {}).get("Binds") or [])
    ports = []
    for p, binding in (d.get("NetworkSettings", {}).get("Ports") or {}).items():
        for b in binding or []:
            ports.append(f"{b.get('HostPort', '')}:{p}")

    # 旧容器改名，让新容器能占用原名
    backup = f"{MIHOMO_CT}_old_{int(time.time())}"
    st, txt = docker_req("POST", f"/containers/{cid}/rename?name={backup}")
    if not (200 <= st < 300):
        return False, f"重命名旧容器失败 HTTP {st}: {txt[:200]}"

    payload = {
        "Image": image,
        "Env": env,
        "Binds": binds,
        "ExposedPorts": {p: {} for p in
                         (d.get("Config", {}).get("ExposedPorts") or {})},
        "HostConfig": {
            "Binds": binds,
            "PortBindings": {
                p: [{"HostPort": p.split(":")[0]}] for p in ports},
            "CapAdd": d.get("HostConfig", {}).get("CapAdd") or [],
            "RestartPolicy": d.get("HostConfig", {}).get("RestartPolicy")
                             or {"Name": "no"},
        },
        "NetworkingConfig": {
            "EndpointsConfig": {
                d.get("HostConfig", {}).get("NetworkMode", "bridge"): {}
            }
        },
    }
    st, txt = docker_req(
        "POST", f"/containers/create?name={MIHOMO_CT}", body=payload, timeout=90)
    if not (200 <= st < 300):
        # 新建失败就把名字改回去，避免留下孤儿容器
        docker_req("POST", f"/containers/{cid}/rename?name={MIHOMO_CT}")
        return False, f"新建容器失败 HTTP {st}: {txt[:200]}"

    new_cid = ""
    try:
        new_cid = json.loads(txt).get("Id", "")
    except Exception:
        pass

    st, txt = docker_req("POST", f"/containers/{new_cid}/start", timeout=120)
    if not (200 <= st < 300):
        return False, f"新容器启动失败 HTTP {st}: {txt[:200]}"

    # 新容器起来了再删旧的
    docker_req("DELETE", f"/containers/{cid}?v=1&force=1", timeout=120)
    log(f"容器已重建（原容器 {backup} 已清理）")
    return True, "recreated"


# ---------------- 订阅信息解析 ----------------
def read_sub_url():
    try:
        with open(SUB_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except FileNotFoundError:
        pass
    return ""


def fetch_sub_headers(url, timeout=20):
    """下载订阅头部，返回 (userinfo_dict, filename)"""
    req = urllib.request.Request(url, headers={"User-Agent": "clash-verge/v1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        h = r.headers
        userinfo = h.get("subscription-userinfo", "")
        cdisp = h.get("content-disposition", "")
        # 必须把 body 读掉，否则连接不释放
        r.read(1024 * 1024)
    info = {}
    for part in userinfo.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            info[k.strip()] = v.strip()
    fname = ""
    m = re.search(r'filename="?([^";]+)"?', cdisp)
    if m:
        fname = m.group(1)
    return info, fname


def human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


def human_date(ts):
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "未知"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def get_yaml_stats():
    """读 config.yaml 拿节点/策略组/规则信息。

    provider 架构下节点不在 config.yaml 里，而是由 mihomo 自动下载到
    providers/airport.yaml。所以这里额外统计 provider 文件与规则集。
    """
    stats = {"nodes": 0, "groups": 0, "rules": 0,
             "provider_nodes": 0, "ruleset_lines": 0, "arch": "static"}
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            import yaml  # noqa
            d = yaml.safe_load(f) or {}
    except Exception:
        return stats

    stats["groups"] = len(d.get("proxy-groups") or [])
    stats["rules"] = len(d.get("rules") or [])

    if d.get("proxy-providers"):
        stats["arch"] = "provider"
        # 统计 provider 文件里的实际节点数
        for pdir in (os.path.join(CONFIG_DIR, "providers"),):
            if not os.path.isdir(pdir):
                continue
            for fn in os.listdir(pdir):
                if not fn.endswith((".yaml", ".yml")):
                    continue
                try:
                    with open(os.path.join(pdir, fn), "r", encoding="utf-8") as f:
                        pd = yaml.safe_load(f) or {}
                    stats["provider_nodes"] += len(pd.get("proxies") or [])
                except Exception:
                    pass
        # 规则集总条数
        rsdir = os.path.join(CONFIG_DIR, "ruleset")
        if os.path.isdir(rsdir):
            for fn in os.listdir(rsdir):
                try:
                    with open(os.path.join(rsdir, fn), "r", encoding="utf-8") as f:
                        stats["ruleset_lines"] += sum(1 for _ in f)
                except Exception:
                    pass
        stats["nodes"] = stats["provider_nodes"]
    else:
        stats["nodes"] = len(d.get("proxies") or [])
    return stats


def mihomo_get(path, timeout=8):
    with urllib.request.urlopen(MIHOMO_API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def mihomo_req(method, path, body=None, timeout=10):
    """通用请求。返回 (status_code, 响应文本)。

    mihomo 的写接口一律返回 204/200 且响应体为空，错误时返回 4xx+JSON。
    注意：路径里的策略组名必须用 quote(name, safe='') 编码 —— 少了 safe=''
    会漏掉 emoji 等多字节字符，导致 400。
    """
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MIHOMO_API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "ignore")
            return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return 0, str(e)


def enc(name):
    """URL 路径段编码。safe 必须为空字符串，否则 emoji / 斜杠不会被转义。"""
    return urllib.parse.quote(str(name), safe="")


# 策略组类型：这些是可以「切换目标」的组
GROUP_TYPES = ("Selector", "URLTest", "Fallback", "LoadBalance")
# 非真实节点（内置出口），延迟测试要跳过
BUILTIN = {"DIRECT", "REJECT", "PASS", "COMPATIBLE", "GLOBAL",
           "PASS-RULE", "REJECT-DROP"}
# 测速用的 URL。gstatic 在部分机场节点上会 404/超时，
# Cloudflare 的 generate_204 更稳，且与 config 里 url-test 保持一致。
TEST_URL = os.environ.get("TEST_URL", "http://cp.cloudflare.com/generate_204")
# local provider 里的占位节点（direct 类型），测速时要排除
PLACEHOLDER = "占位-直连"

# ---------------- 自动故障转移 ----------------
# 守护要用的真实流量检测地址。不能只打 generate_204 —— 那只能证明
# "隧道建立 + 收到 1 字节"，不验证 TLS 握手和真实传输。
# 实测出现过「延迟测速 152ms 绿灯，但 google / github 全 000」，
# 所以守护必须打真实站点。
GUARD_PROBES = [
    ("https://www.google.com/generate_204", (200, 204)),
    ("https://cp.cloudflare.com/generate_204", (200, 204)),
]
GUARD_INTERVAL = int(os.environ.get("GUARD_INTERVAL", "60"))  # 秒
GUARD_FAIL_TOLERANCE = 2          # 当前节点连续失败几次才切换
GUARD_PROBE_TIMEOUT = 12# 单次探测超时
MAIN_GROUP = os.environ.get("MAIN_GROUP", "🔰 选择节点")


def real_probe(node_name, timeout=GUARD_PROBE_TIMEOUT):
    """真实流量探测：临时把 MAIN_GROUP 切到 node_name，请求真实站点。

    返回 True 表示该节点确实能跑通真实流量（而不是只会回 generate_204）。

    注意：会临时改动当前选择，所以调用方负责恢复。
    """
    code, _ = mihomo_req("PUT", f"/proxies/{enc(MAIN_GROUP)}",
                         {"name": node_name}, timeout=10)
    if code not in (200, 204):
        return False
    time.sleep(1.5)  # 等节点握手，否则首个请求必失败
    for url, oks in GUARD_PROBES:
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "Mozilla/5.0 guard")
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {"http": MIHOMO_API.replace(":9090", ":7890"),
                     "https": MIHOMO_API.replace(":9090", ":7890")}))
            with opener.open(req, timeout=timeout) as r:
                if r.status in oks:
                    return True
        except Exception:
            continue
    return False


def list_airport_nodes():
    """从🔰 选择节点 组里取所有真实节点名（排除占位与内置出口）。"""
    code, txt = mihomo_req("GET", f"/proxies/{enc(MAIN_GROUP)}")
    if code != 200:
        return []
    try:
        d = json.loads(txt)
    except Exception:
        return []
    out = []
    for n in d.get("all", []):
        if n in BUILTIN or n == PLACEHOLDER:
            continue
        out.append(n)
    return out


def guard_once():
    """跑一轮守护：检测当前节点，必要时切到能用的。

    ⚠️ 关键设计（实测得出）：
    探测某个节点必须**临时切换主组**过去，这会中断该节点上所有既有连接。
    早期版本一轮要遍历 7 个节点（切 6 次），结果反而制造了大量断线，
    实测成功率只有 67%。现在改成：
      1. 只测当前节点（1 次切换，不切就不测）
      2. 只有当前节点真的不通，才去扫其他节点
      3. 找到第一个可用的就立刻停止扫描
    这样正常情况下每轮只切换 1 次。

    返回 (是否发生了切换, 切换到的节点名 或 None)
    """
    nodes = list_airport_nodes()
    if not nodes:
        return False, None

    # 当前节点
    code, txt = mihomo_req("GET", f"/proxies/{enc(MAIN_GROUP)}")
    cur = ""
    if code == 200:
        try:
            cur = json.loads(txt).get("now", "")
        except Exception:
            cur = ""

    # 情形一：当前节点可用 → 什么都不用做
    if cur and cur in nodes and real_probe(cur):
        with STATE_LOCK:
            STATE["guard_alive"] = [cur]
            STATE["guard_checked"] = time.time()
            STATE["guard_current"] = cur
        return False, cur

    # 情形二：当前节点挂了，扫其他节点找替补（找到一个就停）
    alive = []
    for n in nodes:
        if n == cur:
            continue
        if real_probe(n):
            alive.append(n)
            break  # 找到一个就够，不要继续制造断线

    with STATE_LOCK:
        STATE["guard_alive"] = alive
        STATE["guard_checked"] = time.time()

    if not alive:
        log("守护: 所有节点都不可用，保持现状")
        return False, None

    # 切到找到的第一个可用节点
    target = alive[0]
    code, _ = mihomo_req("PUT", f"/proxies/{enc(MAIN_GROUP)}",
                         {"name": target}, timeout=10)
    changed = code in (200, 204) and target != cur
    with STATE_LOCK:
        STATE["guard_current"] = target
    if changed:
        log(f"守护: {cur or '无'} → {target}")
        return True, target
    return False, target


def guard_loop():
    """守护主循环：按 GUARD_INTERVAL 周期检测并自动切换节点。"""
    # 记录上次实际执行时间，避免刚启动就连跑
    last = time.time()
    while True:
        time.sleep(5)
        now = time.time()
        # 周期未到就等；周期到了才检测
        if now - last < GUARD_INTERVAL:
            continue
        last = now
        try:
            if STATE.get("guard_on"):
                guard_once()
        except Exception as e:
            print(f"[guard] 异常: {e}", flush=True)



def proxies_tree():
    """返回给前端渲染用的策略组 + 节点树。

    ⚠️ 关键事实（实测得出，别改错）：
    `GET /proxies` 的顶层字典里**只有策略组和内置出口**，真实节点
    （vmess/trojan/ss…）**只出现在各组的 `all` 数组里**，顶层没有它们的条目。

    所以判断「某个成员是不是另一个组」的正确方式是：看它是否也出现在
    顶层且 type 属于 GROUP_TYPES。不能靠顶层 type 反推节点类型。

    返回：
      {"groups": [{name,type,now,all,hidden,
                   members:[{name,kind,builtin}]}],
       "node_names": [...],          # 所有真实节点名（去重）
       "builtin_names": [...]}       # 内置出口名
    """
    proxies = mihomo_get("/proxies", timeout=8).get("proxies") or {}

    # 顶层出现过的 = 组；其余顶层项 = 内置出口（Direct/Reject/Pass…）
    group_names = {k for k, v in proxies.items()
                   if v.get("type") in GROUP_TYPES}
    builtin_names = {k for k in proxies if k not in group_names}

    groups = []
    node_names = []          # 按出现顺序去重
    seen = set()

    for k, v in proxies.items():
        if v.get("type") not in GROUP_TYPES:
            continue
        members = []
        for m in (v.get("all") or []):
            if m in group_names:
                kind = "GROUP"
            elif m in builtin_names:
                kind = "BUILTIN"
            else:
                kind = "NODE"
                if m not in seen:
                    seen.add(m)
                    node_names.append(m)
            members.append({"name": m, "kind": kind})
        groups.append({
            "name": k,
            "type": v.get("type"),
            "now": v.get("now"),
            "all": v.get("all") or [],
            "hidden": bool(v.get("hidden")),
            "members": members,
        })

    # 主选择组排最前，方便前端默认展开
    def prio(g):
        n = g["name"]
        if "选择节点" in n:
            return 0
        if "自动选择" in n:
            return 1
        if n == "GLOBAL":
            return 9
        return 5
    groups.sort(key=prio)

    return {
        "groups": groups,
        "node_names": node_names,
        "node_count": len(node_names),
        "builtin_names": sorted(builtin_names),
    }


def test_group_delays(group):
    """用组级端点一次性测出组内所有成员的延迟。

    ⚠️ 关键事实（实测得出，别改错）：
    1. mihomo v1.19.32 **不支持** `GET /proxies/{节点名}/delay` —— 一律404，
       只能用 `GET /group/{组名}/delay`。
    2. 组级端点一次返回该组**当前存活**成员的延迟，形如
        {"🇯🇵 免费-日本5-Ver.9": 146}
       **没返回的成员就是超时/挂了**，前端应据此判定，不要当作"没测到"。

    返回 {节点名: 延迟ms}；请求失败返回空 dict。
    """
    url = (f"/group/{enc(group)}/delay?timeout=8000"
           "&url=" + urllib.parse.quote(TEST_URL, safe=""))
    code, txt = mihomo_req("GET", url, timeout=40)
    if code != 200:
        return {}
    try:
        d = json.loads(txt)
    except Exception:
        return {}
    # 过滤内置出口和占位节点（direct 类型，延迟没意义且会误导）
    return {k: v for k, v in d.items()
            if k not in BUILTIN and k != PLACEHOLDER
            and isinstance(v, (int, float))}


def traffic_sample():
    """实时速率：与上次采样对比算差值。

    /connections 的 downloadTotal / uploadTotal 是累计字节数，
    必须自己做差才能得到速率。
    """
    global _LAST_TRAF
    try:
        d = mihomo_get("/connections", timeout=5)
    except Exception:
        return {"up": 0, "down": 0, "total_up": 0, "total_down": 0,
                "conns": 0, "memory": 0}
    up = int(d.get("uploadTotal") or 0)
    down = int(d.get("downloadTotal") or 0)
    now = time.time()
    prev, pt = _LAST_TRAF
    rate_up = rate_down = 0
    if prev and now > pt:
        dt = now - pt
        rate_up = max(0, (up - prev[0]) / dt)
        rate_down = max(0, (down - prev[1]) / dt)
    _LAST_TRAF = ((up, down), now)
    return {
        "up": rate_up,
        "down": rate_down,
        "total_up": up,
        "total_down": down,
        "conns": len(d.get("connections") or []),
        "memory": int(d.get("memory") or 0),
    }


_LAST_TRAF = (None, 0)   # ((up, down), timestamp)


def connections_list(limit=60):
    """当前连接列表，按下载流量倒序取前 N 条。"""
    d = mihomo_get("/connections", timeout=6)
    conns = d.get("connections") or []
    conns.sort(key=lambda c: int(c.get("download") or 0), reverse=True)
    out = []
    for c in conns[:limit]:
        chains = c.get("chains") or []
        out.append({
            "id": c.get("id"),
            "host": c.get("metadata", {}).get("host") or c.get("metadata", {}).get("destinationIP"),
            "dst": c.get("metadata", {}).get("destinationIP", ""),
            "port": c.get("metadata", {}).get("destinationPort", ""),
            "net": c.get("metadata", {}).get("network", ""),
            "type": c.get("metadata", {}).get("type", ""),
            "proc": c.get("metadata", {}).get("processPath", "") or "",
            "rule": c.get("rule", ""),
            "rule_payload": c.get("rulePayload", ""),
            "chains": chains,
            "node": chains[-1] if chains else "",
            "up": int(c.get("upload") or 0),
            "down": int(c.get("download") or 0),
            "start": c.get("start", ""),
        })
    return {
        "connections": out,
        "total": len(conns),
        "downloadTotal": int(d.get("downloadTotal") or 0),
        "uploadTotal": int(d.get("uploadTotal") or 0),
        "memory": int(d.get("memory") or 0),
    }


def container_status():
    """读 mihomo 容器状态字符串，走 Docker API（不依赖 docker CLI）。"""
    try:
        st, txt = docker_req("GET", f"/containers/{MIHOMO_CT}/json")
        if st != 200:
            return "未知"
        d = json.loads(txt)
        state = (d.get("State") or {})
        status = state.get("Status") or state.get("State") or "未知"
        return status
    except Exception:
        return "未知"


def collect_status():
    url = read_sub_url()
    info, fname = {}, ""
    err = None
    if url:
        try:
            info, fname = fetch_sub_headers(url)
        except Exception as e:
            err = str(e)

    upload = int(info.get("upload", 0) or 0)
    download = int(info.get("download", 0) or 0)
    total = int(info.get("total", 0) or 0)
    used = upload + download
    # 50GB 套餐只用了 20MB 时，四舍五入到 1 位小数会显示 0.0%，
    # 看着像坏了。小于 1% 时保留两位小数，大于等于 1% 保留一位。
    pct_raw = used / total * 100 if total else 0
    pct = round(pct_raw, 2 if pct_raw < 1 else 1)

    st = get_yaml_stats()
    try:
        ver = mihomo_get("/version", timeout=5)
        mode = mihomo_get("/configs", timeout=5).get("mode", "?")
    except Exception:
        ver, mode = {}, "?"

    # 当前选中的节点：优先看自动选择组（provider 架构下由 url-test 自动挑）
    now, auto = None, None
    try:
        proxies = mihomo_get("/proxies", timeout=6).get("proxies") or {}
        for k in ("♻️ 自动选择", "🔰 选择节点", "🚀 节点选择", "节点选择", "选择节点"):
            if k in proxies and proxies[k].get("now"):
                if k == "♻️ 自动选择":
                    auto = proxies[k]["now"]
                else:
                    now = proxies[k]["now"]
        now = now or auto
    except Exception:
        pass

    # provider 文件最后更新时间（判断 mihomo 是否自动拉过）
    prov_mtime = None
    airport = os.path.join(CONFIG_DIR, "providers", "airport.yaml")
    if os.path.exists(airport):
        prov_mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(os.path.getmtime(airport)))

    host = url.split("/")[2] if url.count("/") >= 2 else ""
    return {
        "sub_url": url,
        "sub_host": host,
        "filename": fname or "未命名订阅",
        "upload": human_size(upload),
        "download": human_size(download),
        "used": human_size(used),
        "total": human_size(total),
        "used_pct": pct,
        "expire": human_date(info.get("expire")),
        "expire_ts": int(info.get("expire", 0) or 0),
        "error": err,
        "nodes": st["nodes"],
        "groups": st["groups"],
        "rules": st["rules"],
        "ruleset_lines": st["ruleset_lines"],
        "arch": st["arch"],
        "auto_node": auto,
        # 自动故障转移状态（供前端展示"实测可用"）
        "guard_on": STATE.get("guard_on", False),
        "guard_alive": STATE.get("guard_alive", []),
        "guard_checked": STATE.get("guard_checked"),
        "prov_updated": prov_mtime,
        "container": container_status(),
        "core": ver.get("version", "?"),
        "mode": mode,
        "current_node": now,
        "panel_port": PANEL_PORT,
        "running": STATE["running"],
        "step": STATE["step"],
    }


# ---------------- 更新流程 ----------------
def _run(cmd, cwd=None, timeout=180):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return (p.returncode, (p.stdout or "") + (p.stderr or ""))


def do_update(new_url=None):
    """执行完整更新。new_url 不为空则先替换 sub.txt"""
    t0 = time.time()
    with STATE_LOCK:
        if STATE["running"]:
            return
        STATE["running"] = True
        STATE["log"] = []
        STATE["step"] = "开始"
        STATE["last_result"] = None

    try:
        is_provider = is_provider_arch()

        # 换订阅地址：provider 架构下要改 config.yaml 里的 url，不是 sub.txt
        if new_url:
            new_url = new_url.strip()
            if not new_url.startswith(("http://", "https://")):
                raise ValueError("订阅链接必须以 http:// 或 https:// 开头")
            with open(SUB_FILE, "w", encoding="utf-8") as f:
                f.write(new_url + "\n")
            log("已保存新订阅链接到 sub.txt")
            if is_provider:
                log("provider 架构：正在同步到 config.yaml 的 proxy-providers.airport.url ...")
                if not patch_provider_url(new_url):
                    raise RuntimeError("改写 config.yaml 失败，请手动检查")
                log("config.yaml 已更新")

        if is_provider:
            ok = update_provider()
        else:
            ok = update_static()

        # 探测节点
        with STATE_LOCK:
            STATE["step"] = "探测节点"
        if os.path.exists(PROBE_PY):
            log("开始探测节点可用性...")
            rc2, out2 = _run(["python3", PROBE_PY], cwd=CLASH_DIR, timeout=180)
            for line in out2.splitlines():
                log(line)

        with STATE_LOCK:
            STATE["step"] = "完成"
            STATE["last_result"] = "success" if ok else "failed"
            STATE["last_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        log(f"更新{'完成' if ok else '失败'}，耗时 {time.time() - t0:.1f} 秒")
    except Exception as e:
        log(f"异常: {e}")
        with STATE_LOCK:
            STATE["last_result"] = "failed"
            STATE["last_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with STATE_LOCK:
            STATE["running"] = False
            STATE["step"] = ""


def is_provider_arch():
    """判断当前是 provider 架构还是旧的 static 架构"""
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            txt = f.read()
        return "proxy-providers:" in txt
    except Exception:
        return False


def patch_provider_url(new_url):
    """把新订阅链接写进 config.yaml 的 proxy-providers.airport.url"""
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            lines = f.readlines()
        out, in_pp, done = [], False, False
        for ln in lines:
            if ln.startswith("proxy-providers:"):
                in_pp = True
            elif in_pp and ln and not ln[0].isspace() and not ln.startswith("#"):
                in_pp = False   # 到了下一个顶层键
            if in_pp and ln.strip().startswith("url:") and not done:
                out.append(f'    url: "{new_url}"\n')
                done = True
                continue
            out.append(ln)
        if not done:
            return False
        with open(CONFIG, "w", encoding="utf-8") as f:
            f.writelines(out)
        return True
    except Exception:
        return False


def update_provider():
    """provider 架构：让 mihomo 重新拉取 provider 并重载配置。

    mihomo 有个 RESTful 端点可以触发 provider 立即更新：
      PUT /providers/proxies/airport  （刷新单个 provider）
    不带 body 也生效。失败则退回「重建容器」。
    """
    import urllib.request as _r

    log("开始更新订阅（provider 架构）...")
    with STATE_LOCK:
        STATE["step"] = "触发 provider 重新拉取"

    ok = False
    try:
        req = _r.Request(f"{MIHOMO_API}/providers/proxies/airport",
                         method="PUT", data=b"")
        req.add_header("Content-Type", "application/json")
        with _r.urlopen(req, timeout=30) as r:
            log(f"provider 刷新请求已提交 (HTTP {r.status})")
        ok = True
    except Exception as e:
        log(f"provider 刷新接口调用失败：{e}，改用重建容器")

    time.sleep(8)

    # 校验 provider 文件是否真的更新了
    airport = os.path.join(CONFIG_DIR, "providers", "airport.yaml")
    if os.path.exists(airport):
        mt = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(airport)))
        size = os.path.getsize(airport)
        log(f"provider 文件: {size} 字节，更新时间 {mt}")
    else:
        log("[!] provider 文件不存在")

    if not ok:
        with STATE_LOCK:
            STATE["step"] = "重建容器"
        log("重建容器使配置生效...")
        r_ok, r_msg = container_recreate()
        if r_ok:
            time.sleep(15)
        else:
            # 重建失败不代表订阅没更新，退一步让内核重载配置即可
            log(f"重建容器失败（{r_msg[:120]}），改为重载配置...")
            rc2, _txt = mihomo_req("PUT", "/configs?force=true",
                                   body={"path": CONFIG}, timeout=30)
            log("配置重载 " + ("成功" if 200 <= rc2 < 300 else f"失败 HTTP {rc2}"))
            time.sleep(8)

    # 统计节点
    try:
        import yaml  # noqa
        with open(airport, "r", encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        ps = d.get("proxies") or []
        log(f"节点数: {len(ps)}")
    except Exception:
        pass

    return True


def update_static():
    """旧的 static 架构：跑 update.sh 脚本"""
    log("开始更新订阅（static 架构）...")
    with STATE_LOCK:
        STATE["step"] = "下载并合并配置"
    if not os.path.exists(UPDATE_SH):
        log(f"[!] 找不到 {UPDATE_SH}，无法更新")
        return False
    rc, out = _run(["bash", UPDATE_SH], cwd=CLASH_DIR, timeout=300)
    for line in out.splitlines():
        log(line)
    return rc == 0


def do_probe():
    try:
        rc, out = _run(["python3", PROBE_PY], cwd=CLASH_DIR, timeout=180)
        return out
    except Exception as e:
        return f"探测失败: {e}"


def do_refresh():
    """手动刷新订阅：强制 mihomo 重新拉取 provider（秒级，不改配置）。"""
    log("手动刷新订阅...")
    with STATE_LOCK:
        STATE["step"] = "刷新订阅"
    code, txt = mihomo_req("PUT", "/providers/proxies/airport", timeout=45)
    if code in (200, 204):
        log("订阅已刷新")
    else:
        log(f"刷新失败 (HTTP {code}): {txt[:120]}")
    with STATE_LOCK:
        STATE["step"] = ""



# ---------------- HTTP 处理 ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "SubPanel/1.0"

    def log_message(self, fmt, *args):
        pass  # 静音

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/", "/index.html"):
            fp = os.path.join(WEB_DIR, "index.html")
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, "index.html not found", "text/plain; charset=utf-8")
            return

        if path == "/api/status":
            try:
                self._send(200, collect_status())
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        if path == "/api/log":
            with STATE_LOCK:
                self._send(200, {
                    "running": STATE["running"],
                    "step": STATE["step"],
                    "log": list(STATE["log"]),
                    "last_result": STATE["last_result"],
                    "last_time": STATE["last_time"],
                })
            return

        if path == "/api/nodes":
            try:
                proxies = mihomo_get("/proxies", timeout=8).get("proxies") or {}
                out = []
                for k, v in proxies.items():
                    if v.get("type") in ("Selector", "URLTest", "Fallback"):
                        out.append({"group": k, "now": v.get("now"),
                                    "members": v.get("all") or []})
                self._send(200, {"groups": out})
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # ---- 策略组 / 节点树 ----
        if path == "/api/proxies":
            try:
                self._send(200, proxies_tree())
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # ---- 测延迟（整组一次，返回组内所有成员的延迟）----
        if path == "/api/delay":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            group = (q.get("group") or [""])[0]
            if not group:
                self._send(400, {"error": "缺少 group 参数"})
                return
            delays = test_group_delays(group)
            if not delays:
                self._send(200, {"group": group, "delays": {},
                                 "msg": "该组无可测节点，或全部超时"})
            else:
                self._send(200, {"group": group, "delays": delays})
            return

        # ---- 实时连接 ----
        if path == "/api/connections":
            try:
                self._send(200, connections_list())
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # ---- 实时速率 ----
        if path == "/api/traffic":
            self._send(200, traffic_sample())
            return

        # ---- 规则列表 ----
        if path == "/api/rules":
            try:
                rules = mihomo_get("/rules", timeout=8).get("rules") or []
                self._send(200, {"rules": [
                    {"type": r.get("type"), "payload": r.get("payload"),
                     "proxy": r.get("proxy")} for r in rules]})
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # ---- 实时日志（SSE 转发）----
        if path == "/api/logs/stream":
            self._stream_logs(urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query))
            return

        self._send(404, {"error": "not found"})

    def _stream_logs(self, q):
        """把 mihomo 的 /logs（SSE）原样转发给浏览器。

        mihomo 返回的是 text/event-stream，每行形如
          {"type":"info","payload":"[TCP] ... using 日本5"}
        """
        level = (q.get("level") or ["info"])[0]
        if level not in ("debug", "info", "warning", "error"):
            level = "info"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            return

        src = MIHOMO_API + f"/logs?level={level}"
        try:
            req = urllib.request.Request(src, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=300) as r:
                # 不能用 readline()：mihomo 的分帧不保证以单个 \n 结束，
                # readline 会一直阻塞等换行，导致一条都收不到。
                # 改成按字节读、自己拆行，读到即转发。
                buf = b""
                while True:
                    chunk = r.read1(4096) if hasattr(r, "read1") else r.read(1)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        self.wfile.write(b"data: " + line + b"\n\n")
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass   # 浏览器关掉了，正常
        except Exception as e:
            try:
                err = json.dumps({"type": "warning",
                                  "payload": f"日志流中断: {e}"}, ensure_ascii=False)
                self.wfile.write(f"data: {err}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/update":
            if STATE["running"]:
                self._send(409, {"error": "已有更新任务在运行"})
                return
            body = self._json_body()
            url = (body.get("url") or "").strip() or None
            t = threading.Thread(target=do_update, args=(url,), daemon=True)
            t.start()
            self._send(202, {"ok": True, "msg": "更新已启动"})
            return

        if path == "/api/probe":
            out = do_probe()
            self._send(200, {"ok": True, "output": out})
            return

        # ---- 手动刷新订阅（轻量：只重新拉 provider，不改配置）----
        if path == "/api/refresh":
            t = threading.Thread(target=do_refresh, daemon=True)
            t.start()
            self._send(202, {"ok": True, "msg": "刷新已启动"})
            return

        # ---- 自动故障转移开关 ----
        if path == "/api/guard":
            b = self._json_body()
            on = b.get("on")
            if on is None:
                on = not STATE.get("guard_on")
            with STATE_LOCK:
                STATE["guard_on"] = bool(on)
            log("守护: " + ("已开启" if STATE["guard_on"] else "已关闭"))
            if STATE["guard_on"]:
                t = threading.Thread(target=guard_once, daemon=True)
                t.start()
            self._send(200, {"ok": True, "on": STATE["guard_on"]})
            return

        # ---- 切换节点 ----
        if path == "/api/select":
            b = self._json_body()
            group = (b.get("group") or "").strip()
            name = (b.get("name") or "").strip()
            if not group or not name:
                self._send(400, {"error": "缺少 group 或 name"})
                return
            code, txt = mihomo_req("PUT", f"/proxies/{enc(group)}",
                                   {"name": name})
            if code in (200, 204):
                log(f"切换节点: {group} -> {name}")
                self._send(200, {"ok": True, "group": group, "now": name})
            else:
                self._send(502, {"error": f"切换失败 (HTTP {code})",
                                 "detail": txt[:200]})
            return

        # ---- 切模式 ----
        if path == "/api/mode":
            b = self._json_body()
            mode = (b.get("mode") or "").strip()
            if mode not in ("rule", "global", "direct"):
                self._send(400, {"error": "mode 必须是 rule/global/direct"})
                return
            code, txt = mihomo_req("PATCH", "/configs", {"mode": mode})
            if code in (200, 204):
                log(f"切换模式 -> {mode}")
                self._send(200, {"ok": True, "mode": mode})
            else:
                self._send(502, {"error": f"切换失败 (HTTP {code})",
                                 "detail": txt[:200]})
            return

        # ---- 重启内核 ----
        if path == "/api/core/restart":
            log("收到重启内核请求")
            code, txt = mihomo_req("POST", "/restart", timeout=15)
            # 重启内核会短暂断开 API，属正常
            self._send(200, {"ok": code in (200, 204), "detail": txt[:200]})
            return

        # ---- 启停容器 ----
        if path.startswith("/api/container/"):
            action = path.rsplit("/", 1)[-1]
            try:
                ok, out = container_action(action)
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            except Exception as e:
                self._send(500, {"error": str(e)})
                return
            self._send(200 if ok else 500,
                       {"ok": ok, "action": action,
                        "container": container_status()})
            return

        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path

        # ---- 一键断开全部连接 ----
        if path == "/api/connections":
            code, txt = mihomo_req("DELETE", "/connections")
            if code in (200, 204):
                log("已断开全部连接")
                self._send(200, {"ok": True})
            else:
                self._send(502, {"error": f"断开失败 (HTTP {code})",
                                 "detail": txt[:200]})
            return

        # ---- 断开单条连接 ----
        if path.startswith("/api/connections/"):
            cid = path.rsplit("/", 1)[-1]
            code, txt = mihomo_req("DELETE", f"/connections/{enc(cid)}")
            if code in (200, 204):
                self._send(200, {"ok": True})
            else:
                self._send(502, {"error": f"断开失败 (HTTP {code})"})
            return

        self._send(404, {"error": "not found"})


def main():
    addr = ("0.0.0.0", LISTEN_PORT)
    print(f"订阅控制台已启动: http://0.0.0.0:{LISTEN_PORT}/", flush=True)
    print(f"  CLASH_DIR = {CLASH_DIR}", flush=True)
    print(f"  MIHOMO_API = {MIHOMO_API}", flush=True)
    srv = ThreadingHTTPServer(addr, Handler)
    # 守护线程：按 GUARD_INTERVAL 周期检测并自动切换节点
    threading.Thread(target=guard_loop, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


if __name__ == "__main__":
    main()
