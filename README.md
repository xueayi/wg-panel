<div align="center">

<img src="frontend/public/logo.png" alt="wg-panel" width="120" />

# wg-panel

**WireGuard 云端可视化管理面板**

[![Docker Image](https://img.shields.io/badge/docker-xueayis%2Fwg--panel-2496ED?logo=docker)](https://hub.docker.com/r/xueayis/wg-panel)
[![Source](https://img.shields.io/badge/source-GitHub-181717?logo=github)](https://github.com/xueayi/wg-panel)
[![Guide](https://img.shields.io/badge/教程-从零开始接入-4F46E5)](docs/guide/getting-started.md)

</div>

WireGuard 云端可视化管理面板。跑在 Docker 里，通过 SSH 纳管你的中转节点，
把「配节点、分 IP、发配置」三件事做成界面。

> **🚀 新用户从这里开始：[从零开始接入你的中转节点](docs/guide/getting-started.md)**
> —— 中转节点一次性准备 → 5 分钟部署 → 纳管 → 手机扫码导入 → 站点互联，全套走完。

📖 **先看懂原理，再上手面板**：[《如何优雅地异地组网？超详细的 WireGuard 安装与使用说明》](https://blog.xueayi.site/article/wireguard)
—— 从零搭建中转节点、客户端接入、多地局域网互联的完整讲解（本文档的教程沿用了它的思路，
那一篇的最后也补了一节「用面板把它管起来」）。

面板**不承载任何 VPN 流量**——它只是遥控器，隧道仍然是 客户端 ↔ 中转节点。

## 它做什么

| | |
|---|---|
| 中转节点 | 对外 Endpoint、监听端口、转发网段、客户端 DNS/MTU；改完可一键重签所有客户端配置 |
| 虚拟 IP | 自动分配最小空闲地址，支持指定静态 IP，占用/空闲一屏可见，删除后回收 |
| 客户端 | 新增、停用/启用、改隧道模式与备注、删除（删前自动备份） |
| 配置导入 | 手机扫码（二维码）、电脑下载 `.conf`、复制文本，三件套 |
| 安全网 | 写前自动备份（保留 20 份）可回滚、一键体检、操作审计 |

## 管理思想

直接沿用已在生产验证过的四条硬规则，不另起炉灶：

1. **登记表为准** —— `registry.json` 是权威数据，`wg0.conf` 是它渲染的产物，不是手工草稿
2. **`SaveConfig = false`** —— 不让 wg-quick 把运行时快照写回配置（配置漂移的根源）
3. **变更走 `wg syncconf`** —— 原子、可增可删、不断线；不用 `addconf`、不 sed 改块
4. **写前必备份** —— 每次写配置前落一份带时间戳的备份

**架构上最硬的一条边界**：WireGuard 语义只在 `agent/wgagent.py` 里实现一次。
后端驱动只负责「怎么执行它」，面板里没有第二份渲染 / 同步逻辑，因此不会出现两套实现漂移。

```
React + Vite  →  FastAPI  →  驱动（SSH / Mock）  →  wgagent.py  →  wg0
```

## 本地开发

```bash
cd frontend && npm install && npm run dev        # 前端 5173，已代理 /api 到 8000

cd backend && pip install -r requirements.txt
export WGP_DRIVER=mock WGP_DATA_DIR=./data \
       WGP_ADMIN_PASSWORD=devpass WGP_SECRET_KEY=$(openssl rand -hex 32)
uvicorn app.main:app --reload --port 8000        # 后端 8000
```

`WGP_DRIVER=mock` 用内存里的假节点，不碰任何真实资源，前后端联调够用。

测试：

```bash
cd backend && pytest tests -q --basetemp=/tmp/wgpanel-pytest
```

内核测试用假 `wg` 二进制把 `wgagent.py` 关在临时目录里跑，**不会碰真实的 `/etc/wireguard`**。

## 部署

```bash
cp docker-compose.example.yml docker-compose.yml
cp .env.example .env          # 填真实值；这两个文件都不入库

mkdir -p ssh && cp ~/.ssh/id_ed25519 ssh/ && chmod 600 ssh/id_ed25519
docker compose up -d
```

### 连接设置就在面板里（推荐流程）

容器环境变量只是**初始值**。启动后请打开「**连接设置**」页走一遍：

1. 核对/修改**服务地址（Endpoint）**、SSH 端口与用户、接口名；
2. 看 **SSH 私钥**是否显示"已就绪"（没有就粘贴私钥上传，面板会以 600 落盘并只回指纹）；
3. 点「**测试连接**」——它会重连、把内核脚本重新下发到节点，并读回客户端数量。

通过之后，以后换节点、改端口都在这个页面完成，不用再去 NAS 上编辑 `docker-compose.yml`。

**首次上线建议**：先设 `WGP_READ_ONLY=1`，进「备份 & 审计」页点「纳管既有配置」把现网 peer
收进登记表，确认界面显示的和实际一致后，再去掉只读开关（也可以在「连接设置」页直接切换）。

## 常见问题

**登录口令是什么？** 用户名固定 `admin`，口令就是你部署时写在
`WGP_ADMIN_PASSWORD` 里的那串——它只在**第一次启动**时用来初始化（存进 SQLite 的哈希），
之后改 env 不再生效，请改用「连接设置」→ 修改口令，或调 `POST /api/auth/password`。

**口令忘了怎么办？** 删掉面板数据库里的管理员记录再重启，会重新用 env 里的
`WGP_ADMIN_PASSWORD` 初始化（审计日志与连接设置保留）：

```bash
docker compose exec wg-panel python -c \
  "import sqlite3;c=sqlite3.connect('/data/panel.db');c.execute('DELETE FROM admin');c.commit()"
docker compose restart
```

**备份存在哪？** 节点上的 `/root/wireguard/backup/`，每次写配置前自动落一份，
面板「备份 & 审计」页可以回滚或删除单份。

SSH 私钥只需一条能登到中转节点的密钥（ed25519 / rsa / ecdsa 均可），只读挂载。
面板启动时会自动把 `wgagent.py` 下发到节点（sha256 比对，走 stdin 不经命令行参数）。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WGP_DRIVER` | `mock` | `ssh` 纳管真实节点 / `mock` 演示 |
| `WGP_READ_ONLY` | `0` | `1` 时所有写操作返回 423 |
| `WGP_SSH_HOST` | — | 中转节点地址 |
| `WGP_SSH_USER` | `root` | 需能读写 `/etc/wireguard` |
| `WGP_SSH_KEY` | `/ssh/id_ed25519` | 私钥路径，只读挂载 |
| `WGP_SSH_REMOTE_TOOL` | `/root/wireguard/wgagent.py` | 内核脚本落点 |
| `WGP_IFACE` | `wg0` | 接口名 |
| `WGP_ADMIN_PASSWORD` | — | 仅首次启动用于初始化，之后在界面改 |
| `WGP_SECRET_KEY` | 随机 | 会话签名，建议固定为随机串 |
| `WGP_DATA_DIR` | `./data` | SQLite 位置 |

## 安全约定

- 私钥默认不可见：列表与详情不含私钥，下载 `.conf` 默认 `PrivateKey = <redacted>`，
  需显式取明文并记审计；二维码同理留痕
- 所有对外输出过 `mask_secrets()`；SSH 私钥内容永不进响应或日志
- 删除 / 回滚 / 纳管需二次确认；只读模式可整体关掉写操作
- 仓库里只有占位符（`203.0.113.10`、`__ADMIN_PASSWORD__`），pre-commit 会拦私钥块、
  容器仓库令牌、公网 IP 与超长 base64：

```bash
git config core.hooksPath .githooks
```
