# 合入特性验收修复

本轮针对验收报告中的并发、回滚、配置漂移、PSK、本地模式、流量及界面问题修复；保持 SSH 默认部署。当前修改留在 `codex/fix-acceptance-bugs` 分支，未推送。

## 写事务与预览

所有 agent 写命令在同一文件锁下运行，写前备份登记表、服务端配置和客户端目录；应用校验失败时恢复文件和旧运行配置。`backup/transaction-*` 保留完整快照。事务日志用于下一次写操作恢复异常中断。

恢复是幂等的：重放事务日志前先检查每条逆操作目标（地址/路由）当前状态，已生效的跳过，不会因重复执行卡住恢复。恢复不完整（系统逆操作或旧配置重新同步失败）时事务日志**保留**，下一条写命令继续尝试；只有确认恢复完整才删除。快照缺失时明确提示手工核对，不产生裸异常。

`restore` 恢复 conf-only 旧备份后，会与登记表渲染结果比对；不一致时明确提示登记表仍是权威、后续写命令会被漂移保护阻止，需重新预览纳管或按登记表 sync。

外部配置变更会阻止普通写操作，不自动覆盖。先读取 `/api/plan`，再人工核对并重新纳管。POST `/api/plan` 与 `/api/apply` 接收同一操作与参数，revision 在 agent 写锁内验证；过期返回 409。revision 绑定请求、磁盘文件（含服务端配置原样 sha256）、registry、客户端文件、运行时 peer 状态、主机名与接口身份，私钥或 PSK 的变化不会被脱敏比较忽略（比对用的是原始字节摘要与未脱敏语义键值）。PSK 在客户端重签中保留。需要接口重启或钩子变更的操作会拒绝。

首次纳管先预览再确认。可识别共享卷 `peer*/*.conf`，通过私钥推导公钥匹配 peer；缺少匹配私钥或 PSK 不一致时仍可管理，但不能导出。无法保留的配置字段会明确报错并阻止纳管。

## 本地部署与重建

`WGP_DRIVER=ssh|local|mock` 仅在进程启动时设置，界面只展示当前模式。旧数据库中的 driver 不覆盖环境变量。本地目录默认 `/config/wg_confs` 与 `/config/.wg-panel`，面板账号与流量数据库在 `/data`。

复制 `docker-compose.local.example.yml` 并填写初始账号密码、会话密钥后部署。WireGuard 容器更换网络空间时，两个容器必须一起重建：

```sh
docker compose -f docker-compose.local.example.yml down
docker compose -f docker-compose.local.example.yml up -d --build --force-recreate
```

不要添加 `-v`，保留配置和面板数据卷。linuxserver 若重新生成配置，面板会检测漂移并阻止写入，需再次预览纳管。

## 监控与只读接口

流量由独立执行器后台采样；SSH 默认 5 秒、本地默认 1 秒，`WGP_TRAFFIC_INTERVAL=0` 禁止采样。状态摘要另有低频缓存，页面读取缓存不会调用 metrics。上传是服务器 rx，下载是服务器 tx。累计与基线持久化，计数重置不产生负速率，缺口用 null 表示；最近 3 分钟细粒度、26 小时分钟级、16 天小时级。

只读 Token 仅支持 GET `/api/state`、`/api/traffic`、`/api/plan`、`/api/peers`、`/api/server`；不能读取详情、配置、二维码或连接设置，也不能调用 POST 预览与应用。

## 验证范围

回归测试使用临时目录、临时 SQLite 和能记录 syncconf 的 fake WireGuard/ip 程序，不接触真实接口。前端测试验证图表、编辑、导出、驱动展示与纳管预览流程，另进行浏览器复核。

2026-10-11 独立复核后全量验证结果：后端 pytest **102 通过**（含 4 项新增事务恢复边界用例：中断恢复、失败后保留事务日志可重试、undo 重放幂等跳过、restore 与登记表不一致提示）；前端 `node --test` **6 通过**；`npm run build` 通过；compose 配置语法检查通过；`git diff --check` 干净。真实 Linux Docker 中的首次纳管、网络权限、系统路由冲突、热更新持续传输和容器重建仍须部署环境验收；模拟测试不能证明这些环境行为。
