# Port Grove Agent

Agent 是不依赖 LLM 和第三方 Python 包的轻量采集程序。它在本机执行一次 `ss`，按 PID 去重读取 `/proc`，识别项目清单、systemd、容器线索和 HTTP/HTTPS 服务，然后主动向中央面板上报。

## 安装到另一台服务器

推荐直接在主面板点击“接入服务器”，生成并复制远端接入指令：

```bash
curl -fsSL 'https://monitor.example.com/api/agent/install.sh' | \
  sudo bash -s -- --server 'https://monitor.example.com' --token 'pg_xxx'
```

不需要预先克隆仓库。脚本会从主面板下载匹配版本的 Agent，完成依赖检查、首次真实上报、令牌绑定和 systemd 启动。中央端会自动读取请求来源作为服务器地址；也可以额外传入 `--public-ip` 覆盖。

当前中央端只有 HTTP 时，面板生成的测试命令会自动加 `--insecure`；跨公网正式使用前应配置 HTTPS。

已经克隆仓库时也可以直接执行本地脚本：

```bash
sudo ./scripts/install-agent.sh --server https://monitor.example.com
```

不传 `--token` 时，脚本会隐藏提示输入接入令牌。

下面是等价的手动安装方式。

将项目放到 `/opt/port-grove`，然后安装服务：

```bash
sudo cp deploy/port-grove-agent.env.example /etc/port-grove-agent.env
sudo chmod 600 /etc/port-grove-agent.env
sudo cp deploy/port-grove-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now port-grove-agent
```

编辑 `/etc/port-grove-agent.env` 时，`PORT_GROVE_SERVER` 指向中央面板，`AGENT_TOKEN` 必须与中央面板环境变量一致。生产环境应使用 HTTPS；只有可信内网临时测试时才能设置 `ALLOW_INSECURE_HTTP=1`。

查看运行状态：

```bash
systemctl status port-grove-agent
journalctl -u port-grove-agent -f
```

单次验证：

```bash
sudo --preserve-env=PUBLIC_IP,SERVER_NAME,SERVER_PROVIDER \
  python3 agent/agent.py --server https://monitor.example.com --token your-token --once
```

Agent 通常需要 root 运行，才能读取所有监听进程的 `/proc` 信息。以普通用户运行仍可发现端口，但部分进程归属会显示为未知。

## 升级与解绑

重新执行主面板生成的最新接入指令会覆盖 Agent 文件并重启服务，可用于从旧版本升级。

Agent v1.3+ 会读取中央端在上报响应中下发的固定控制指令。选择“通知 Agent 并解绑”后，Agent 会禁用 systemd、确认中央端删除服务器记录与绑定令牌，然后删除 `/etc/port-grove-agent.env`、systemd unit 和 `/opt/port-grove/agent`。

中央端无法主动连入离线服务器，因此离线节点会保持“解绑中”，直到 Agent 再次上报。紧急情况下可以选择“仅从面板删除”，但远端 Agent 不会自动停止。
