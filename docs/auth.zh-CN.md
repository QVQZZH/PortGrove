# 管理员账户

Port Grove 只有一个管理员账户，不提供注册、多用户、角色或邮件找回。账号与 PBKDF2-SHA256 密码哈希保存在主面板 SQLite 数据库中，明文密码不会写入配置文件。

## 首次初始化

主面板首次启动且数据库中没有管理员时，会生成一次性初始化码：

```bash
sudo python3 scripts/manage-admin.py setup-code
```

在首次访问页面输入初始化码、自定义账号和密码。初始化成功后，本机初始化码文件会立即删除，初始化接口也会永久关闭。初始化码默认与 SQLite 数据库保存在同一目录，生产配置示例路径为 `/var/lib/port-grove/setup-token`，权限为 `600`。

初始化码用于阻止公网扫描者在新服务启动后抢先创建管理员。它只应从主面板服务器本机读取。

## 修改账号或密码

登录后点击页头的管理员账号打开“账户设置”。输入当前密码后可以修改账号，也可以设置新密码。保存时会轮换会话密钥，其他浏览器中的旧会话随即失效。

## 忘记密码

在主面板服务器本机执行：

```bash
cd /opt/port-grove
sudo python3 scripts/manage-admin.py reset --username admin
```

命令会隐藏读取两次新密码，并覆盖单管理员账户。已有服务器、项目、端口记录和 Agent Token 不受影响，所有旧网页登录会话会失效。

查看当前账户状态：

```bash
sudo python3 scripts/manage-admin.py status
```

使用自定义数据库或初始化码路径时，可以添加 `--database` 和 `--setup-token-file`。对应 systemd 环境变量为：

```ini
DATABASE_PATH=/var/lib/port-grove/port_grove.db
SETUP_TOKEN_PATH=/var/lib/port-grove/setup-token
SESSION_TTL=43200
AUTH_DISABLED=0
```

旧版本的 `AUTH_CONFIG` JSON 会在升级后自动迁移一次。迁移完成后，登录只读取 SQLite，旧文件不再影响账号。

`AUTH_DISABLED=1` 只应用于本机开发，不应在公网部署中使用。网页登录会保护服务器数据、项目修改、接入令牌和解绑 API；Agent 上报使用独立机器令牌，不依赖网页登录会话。

公网环境必须在面板前配置 HTTPS、VPN 或可信反向代理。登录能阻止未授权访问，但 HTTP 本身不会加密传输中的账号、密码和 Cookie。
