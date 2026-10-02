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

compose 文件里**只有面板自己**的配置——中转节点相关的一切都在面板里填：

```bash
cp docker-compose.example.yml docker-compose.yml
# 只需改两处：WGP_ADMIN_PASSWORD（初始口令）、WGP_SECRET_KEY（openssl rand -hex 32）
docker compose up -d
```

打开 `http://<部署机IP>:13010` → 登录 → 「**连接设置**」→ 填中转节点地址与 SSH 凭证 → 「测试连接」。
就这三步，不需要事先准备私钥目录，也不需要配置任何节点相关的环境变量。

### 连接设置（标准流程）

1. **服务地址（Endpoint）**：中转节点的公网地址，客户端最终连的就是它；
2. **SSH 端口 / 用户**：面板登节点用的（用户需能读写 `/etc/wireguard`，通常是 `root`）；
3. **登录方式**：密钥或口令——
   - 密钥：粘贴私钥上传，面板以 600 落盘，界面只回**指纹**（OpenSSH 风格 `SHA256:…`，
     与 `ssh-keygen -lf 私钥` 的输出一致，可用来核对两边是不是同一把钥匙）；
   - 口令：只写进 600 权限的文件，不进数据库、不进日志、接口不回显；
4. 点「**测试连接**」——重连 + 重新下发内核脚本 + 读回客户端数量。

### 面板账号

初始账号是 **`admin` / `admin`**（没设 `WGP_ADMIN_PASSWORD` 时），登录后顶栏会出现醒目提示，
去「连接设置」→「面板账号」改成自己的用户名与口令即可。口令只在**第一次启动**用于初始化，
之后改 env 不再生效。

**首次上线建议**：先在「连接设置」里打开**只读模式**，去「备份 & 审计」页点「纳管既有配置」
把现网 peer 收进登记表，确认界面显示的和实际一致后，再关掉只读。

## 常见问题

**登录账号是什么？** 初始是 **`admin` / `admin`**；如果你在 compose 里设了
`WGP_ADMIN_PASSWORD`，那就是 `admin` + 你设的那串。它只在**第一次启动**时用于初始化
（存进 SQLite 的哈希），之后改 env 不再生效——请到「连接设置」→「面板账号」里改用户名与口令。

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
实际上只需要两个变量；其余都有默认值，而且都能在面板里改（面板里改过的值会覆盖 env）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `WGP_ADMIN_PASSWORD` | `admin` | 仅首次启动用于初始化账号，之后在界面改 |
| `WGP_SECRET_KEY` | 随机 | 会话签名，建议固定为随机串 |
| `WGP_DATA_DIR` | `./data` | 面板数据目录（SQLite + 上传的密钥/口令） |
| `WGP_READ_ONLY` | `0` | 也可在面板里切换 |

以下变量只是**初始值**，通常不需要设置（在「连接设置」里改更省事）：
`WGP_DRIVER`(`ssh`)、`WGP_IFACE`(`wg0`)、`WGP_SSH_HOST`、`WGP_SSH_USER`(`root`)、
`WGP_SSH_PORT`(`22`)、`WGP_SSH_AUTH`(`key`/`password`)、`WGP_SSH_KEY`、`WGP_SSH_REMOTE_TOOL`。

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
