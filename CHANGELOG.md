# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [2.0.0] - 2026-10-04

### 破坏性变更

- **目录结构重构**：`deploy/docker-compose.yaml`（v1 单服务）→ 根目录 `docker-compose.yaml`
  （双服务）。旧的 `deploy/` 目录已删除，请改用根目录编排文件。
- **配置路径**：`deploy/config.yaml.example` → `config/config.example.yaml`。
  真实配置 `config/config.yaml` 含订阅链接，已加入 `.gitignore`，**不再入库**。
- **默认订阅方式改为 provider**：由手动跑 `merge-sub.py` 生成静态配置，
  改为 mihomo 内置 `proxy-providers`（`type: http`，每 3600s 自动拉取）。
  静态方案脚本保留在 `scripts/merge-sub.py` 作为备用。

### 新增

- **一体化控制台（8090）**：`webapp/index.html` + `webapp/sub-panel.py`，
  单页面覆盖模式切换、容器启停、订阅管理、节点切换、实时连接、速率、日志。
  - 纯 Python 标准库实现，无第三方依赖
  - 直连 Docker Engine API（unix socket）启停容器，**不依赖 docker CLI**
  - `network_mode: host`，解决跨网络无法解析 mihomo 容器名的问题
- **⚡ 刷新订阅按钮**：立即拉取 provider（实测约 1 秒），替代高频自动刷新
- **🛡 自动守护**：约 60 秒一轮，用**真实 HTTPS 流量**（google + cloudflare）
  检查当前节点，失败才扫描其他节点，找到可用即停止切换
- **节点实测状态**：列表区分「延迟数字」与「✅ 实测通 / ❌ 实测不通」，
  前者只验隧道建立，后者才验真实 TLS 传输
- **`scripts/probe-nodes.py`**：命令行版节点真实测速 + 自动切最快节点
- **`GUI创建项目.md`**：在绿联 GUI 里登记项目的完整步骤
- **`CHANGELOG.md`**

### 修复

- `store-selected: true` + `store-fake-ip: true`：手动选的节点不再被
  provider 更新 / 配置重载冲回第一个
- 健康检查 URL 统一为 `http://cp.cloudflare.com/generate_204`
  （原 gstatic 地址会 404 / 超时，且命中 `GeoIP(cn)` 直连导致假阳性）
- `♻️ 自动选择` 组改为只 `use: [airport]`：原先含 `local` 里的 `direct`
  占位节点，其延迟近 0 导致 url-test 永远选它，**全部流量直连**
- `🌐 代理` 组默认改用 `🔰 选择节点`（手动）：url-test 全挂时会退化成
  选列表第一个死节点，导致 github / google 报 `error: xxx-node`
- 改用完整 `country.mmdb` 替代 China-only `GeoIP.dat`：
  后者缺 US/JP 国家码，`GEOIP,US` 规则直接报 `country code us not found`
- `nameserver-policy` 改用通配符 `+.节点域名`：机场换域名后不再需要
  重跑脚本改多个硬编码域名
- provider 自动刷新间隔 600s → 3600s（过于频繁容易触发机场限流）
- provider 文件传输改用 `cat | ssh "sudo tee"`：
  `ssh "cat > file" < local` 会静默产生 0 字节文件
- `update-sub.sh` 语法校验改用隔离临时目录
- 仓库根 `.gitattributes` 固定 `*.sh text eol=lf`：
  避免 Windows 上 CRLF 导致 Linux 报 `bad interpreter: /bin/bash^M`

### 文档

- README 重写为 v2.0.0 实际结构（双服务、8090 控制台、guard 守护）
- 踩坑记录扩充：按「部署启动 / 配置代理 / 环境相关」分类

---

## [1.0.0] - 2026-10-03

### 新增

- 绿联 NAS 按需 Clash（mihomo）方案，`restart: "no"` 不做常驻网关
- `docker-compose.yaml`：mihomo 容器，7890 混合代理 / 9090 控制器
- `scripts/on.sh` / `off.sh`：开关代理，并同步配置 / 撤销 Docker 守护进程代理
- `scripts/setup.sh`：首次部署初始化
- `scripts/update-sub.sh`：静态订阅更新
- `scripts/merge-sub.py`：Clash 订阅合并
- 内置 metacubexd 面板（`external-ui`），测延迟、切策略组
- provider 机制支持订阅自动更新
- MIT License
