#!/usr/bin/env python3
# ============================================================
# merge-sub.py —— 机场订阅 → NAS 可用配置
# ============================================================
# 用法：python3 merge-sub.py <订阅文件> <输出config路径>
#
# 设计原则：
#   机场订阅的 proxies / proxy-groups / rules 是精华，全部保留
#   机场订阅的「监听设置」是给单机 PC 用的，必须改造：
#     allow-lan: false           → true      （否则局域网连不上）
#     external-controller:127... → 0.0.0.0   （否则面板连不上）
#     bind-address 未设           → "*"
#   DNS 追加防污染配置（机场的 DNS 通常是明文，易被投毒）
# ============================================================

import sys
import ipaddress
import yaml
from collections import Counter


def _is_ip(s):
    """判断字符串是否为 IP 地址"""
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def main():
    if len(sys.argv) < 3:
        print("用法: merge-sub.py <订阅文件> <输出路径>", file=sys.stderr)
        return 1

    sub_path, out_path = sys.argv[1], sys.argv[2]

    try:
        with open(sub_path, encoding='utf-8') as f:
            sub = yaml.safe_load(f)
    except Exception as e:
        print(f"订阅解析失败: {e}", file=sys.stderr)
        return 1

    if not isinstance(sub, dict) or 'proxies' not in sub:
        print("订阅缺少 proxies 段", file=sys.stderr)
        return 1

    proxies = sub.get('proxies') or []
    print(f"  节点: {len(proxies)} 个  {dict(Counter(p.get('type') for p in proxies))}")

    # ---------- 保留机场精华 ----------
    out = {}
    out['proxies'] = proxies
    if sub.get('proxy-groups'):
        out['proxy-groups'] = sub['proxy-groups']
    if sub.get('rules'):
        out['rules'] = sub['rules']
    if sub.get('hosts'):
        out['hosts'] = sub['hosts']

    # ---------- 注入 NAS 必需配置（放最前面，YAML 顺序不影响语义）----------
    nas_config = {
        'mixed-port': 7890,
        'socks-port': 7891,
        'allow-lan': True,
        'bind-address': '*',
        'mode': 'rule',
        'log-level': 'info',
        'ipv6': False,
        'unified-delay': True,
        'tcp-concurrent': True,
        'find-process-mode': 'strict',
        'external-controller': '0.0.0.0:9090',
        'external-ui': 'ui',
        'secret': '',
    }

    # 合并顺序：NAS 配置在前，机场内容在后
    merged = {**nas_config, **out}

    # ---------- DNS 防污染加固 ----------
    # ⚠️ 关键坑：不能把 nameserver 全换成 DoH！
    #    DoH 服务器（如 https://1.1.1.1/dns-query）的域名解析本身也依赖 DNS，
    #    且 1.1.1.1 在国内被墙，会导致「dns resolve failed: context deadline exceeded」
    #    → 连节点域名都解析不了，全盘不通。
    #    正确做法：国内明文 DNS 打底（快且通），DoH 只做 fallback 防投毒。
    dns = merged.get('dns') or {}
    dns['enable'] = True
    dns['listen'] = '0.0.0.0:1053'
    dns['ipv6'] = False
    dns.setdefault('enhanced-mode', 'fake-ip')
    dns.setdefault('fake-ip-range', '198.18.0.1/16')
    fif = dns.get('fake-ip-filter') or []
    for extra in ['*.lan', '*.local', '*.ugreen.com', '+.msftconnecttest.com']:
        if extra not in fif:
            fif.append(extra)
    dns['fake-ip-filter'] = fif
    # default-nameserver 必须用纯 IP（用于解析 DoH 域名）
    dns['default-nameserver'] = ['223.5.5.5', '119.29.29.29']
    # nameserver：国内明文 DNS 打底，保证节点域名可解析
    dns['nameserver'] = [
        '223.5.5.5',
        '119.29.29.29',
        'https://dns.alidns.com/dns-query',
        'https://doh.pub/dns-query',
    ]
    # fallback 走国内可达的 DoH（cloudflare/google 在大陆会被 reset）
    dns['fallback'] = [
        'https://doh.pub/dns-query',
        'https://dns.alidns.com/dns-query',
    ]
    dns['fallback-filter'] = {'geoip': True, 'geoip-code': 'CN'}

    # ⚠️ 关键：节点服务器域名必须直连解析，不能进 fake-ip / fallback 流程，
    #    否则会出现「dns resolve failed」导致全部节点不可用。
    #    把订阅里所有节点的域名加入 nameserver-policy，强制用国内 DNS 解析。
    node_domains = set()
    for p in proxies:
        srv = p.get('server')
        if srv and not _is_ip(srv):
            # 取主域名（去掉最末级子域前缀过深的场景，保留完整域名更精确）
            node_domains.add(srv)
    if node_domains:
        policy = dns.get('nameserver-policy') or {}
        for dom in sorted(node_domains):
            policy[dom] = ['223.5.5.5', '119.29.29.29']
        dns['nameserver-policy'] = policy
        print(f"  节点域名直连解析: {len(node_domains)} 个")

    merged['dns'] = dns

    # 性能
    merged['profile'] = {'store-selected': True, 'store-fake-ip': True}

    # ---------- 写盘 ----------
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            yaml.dump(merged, f, allow_unicode=True, sort_keys=False,
                      default_flow_style=False, width=1000)
    except Exception as e:
        print(f"写入失败: {e}", file=sys.stderr)
        return 1

    print(f"  策略组: {len(merged.get('proxy-groups') or [])}")
    print(f"  规则: {len(merged.get('rules') or [])}")
    print(f"  allow-lan: {merged['allow-lan']}  "
          f"controller: {merged['external-controller']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
