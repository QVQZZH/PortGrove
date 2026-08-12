import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Input, Modal, Notification, Progress, Tag, Title } from 'animal-island-ui';
import {
  Activity, Boxes, Braces, CheckCircle2, Clock3, Code2, Container, Copy, Cpu, Database,
  ExternalLink, Globe2, HardDrive, Leaf, MemoryStick, MessageSquare, MonitorSmartphone,
  KeyRound, LockKeyhole, LogIn, LogOut, Moon, Network, Pencil, Plus, RefreshCw, Route,
  Search, Server, Settings, ShieldCheck, Sun, Terminal, Trash2, UserRound, Zap
} from 'lucide-react';
import bush from 'animal-island-ui/items/item-420.png';
import tree from 'animal-island-ui/items/item-458.png';

const TYPES = {
  frontend: { label: '前端', icon: Code2, color: 'app-blue' },
  backend: { label: '后端', icon: Server, color: 'app-orange' },
  service: { label: '服务', icon: Database, color: 'purple' }
};
const STATUS = {
  healthy: { label: '正常', className: 'healthy' },
  warning: { label: '缓慢', className: 'warning' },
  offline: { label: '离线', className: 'offline' }
};
const PROJECT_ICONS = {
  web: { label: '网站 / 前端', icon: MonitorSmartphone },
  api: { label: 'API / 后端', icon: Braces },
  database: { label: '数据库', icon: Database },
  proxy: { label: '网关 / 代理', icon: Route },
  cache: { label: '缓存', icon: Zap },
  queue: { label: '消息队列', icon: MessageSquare },
  container: { label: '容器服务', icon: Container },
  monitoring: { label: '监控工具', icon: Activity },
  storage: { label: '存储服务', icon: HardDrive },
  system: { label: '系统服务', icon: Settings },
  default: { label: '通用项目', icon: Boxes }
};
const CONFIDENCE = { high: '高', medium: '中', low: '低', 高: '高', 中: '中', 低: '低' };

function ProjectIcon({ name, size = 23 }) {
  const Icon = (PROJECT_ICONS[name] || PROJECT_ICONS.default).icon;
  return <Icon size={size} strokeWidth={1.9} />;
}

function StatCard({ color, icon: Icon, label, value, note, children }) {
  return <Card color={color} className="stat-card">
    <div className="stat-top"><span className="stat-icon"><Icon size={20} /></span><span>{label}</span></div>
    <strong className="stat-value">{value}</strong>
    <span className="stat-note">{note}</span>
    {children}
  </Card>;
}

function PortRow({ item }) {
  const type = TYPES[item.type] || TYPES.service;
  const status = STATUS[item.status] || STATUS.offline;
  const Icon = type.icon;
  return <div className="port-item">
    <span className={`port-type ${item.type}`}><Icon size={15} /></span>
    <div className="port-name"><strong>{item.name}</strong><small>{item.host} · {item.process} · {item.exposure}</small></div>
    <Tag size="small" color={type.color} variant="outlined">{type.label}</Tag>
    <code>:{item.port}</code>
    <span className={`health ${status.className}`}><i />{status.label}</span>
    {item.webUrl && <a className="web-link" href={item.webUrl} target="_blank" rel="noreferrer" title={`打开 ${item.webUrl}`} aria-label={`打开端口 ${item.port} 的网页`}><ExternalLink size={14} /></a>}
  </div>;
}

function ProjectCard({ project, onRename }) {
  const healthy = project.ports.filter(port => port.status === 'healthy').length;
  const overall = healthy === project.ports.length ? 'healthy' : project.ports.some(port => port.status === 'offline') ? 'offline' : 'warning';
  return <Card className="project-card">
    <div className={`project-cover ${project.color}`}>
      <div className="project-avatar" title={(PROJECT_ICONS[project.icon] || PROJECT_ICONS.default).label}><ProjectIcon name={project.icon} /></div>
      <div className="project-heading"><h3>{project.name}</h3><p>{project.description}</p></div>
      <span className={`health project-health ${overall}`}><i />{STATUS[overall].label}</span>
    </div>
    <div className="project-facts">
      <span><Boxes size={14} /><b>{project.ports.length}</b> 个端口</span>
      <span><CheckCircle2 size={14} /><b>{healthy}</b> 个正常</span>
      <span><Clock3 size={14} />{project.managed === 'auto' ? `自动识别 · ${CONFIDENCE[project.confidence] || project.confidence}` : '持续探测'}</span>
    </div>
    <div className="port-list">{project.ports.map(port => <PortRow key={port.id} item={port} />)}</div>
    <div className="project-footer">
      <span>负责人 · {project.owner}</span>
      <Button type="dashed" size="small" icon={<Pencil size={13} />} onClick={() => onRename(project)}>编辑</Button>
    </div>
  </Card>;
}

function RenameForm({ project, onSubmit }) {
  const [form, setForm] = useState({
    name: project?.name || '', owner: project?.owner || '', description: project?.description || '', icon: project?.icon || 'default'
  });
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  return <form className="resource-form" onSubmit={event => { event.preventDefault(); onSubmit(form); }}>
    <label>项目名称<Input required value={form.name} onChange={e => set('name', e.target.value)} placeholder="例如：内容管理平台" /></label>
    <label>负责人<Input value={form.owner} onChange={e => set('owner', e.target.value)} placeholder="例如：root" /></label>
    <label>项目说明<Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="一句话说明用途" /></label>
    <fieldset className="icon-field"><legend>项目图标</legend><div className="icon-picker">
      {Object.entries(PROJECT_ICONS).map(([key, option]) => <button key={key} type="button" className={form.icon === key ? 'selected' : ''} onClick={() => set('icon', key)} title={option.label} aria-label={option.label}><ProjectIcon name={key} size={20} /><span>{option.label}</span></button>)}
    </div></fieldset>
    <div className="form-submit"><Button htmlType="submit" type="primary" icon={<Pencil size={15} />}>保存项目</Button></div>
  </form>;
}

function EnrollmentPanel() {
  const [label, setLabel] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const origin = window.location.origin;
  const insecureFlag = origin.startsWith('http://') ? ' --insecure' : '';
  const command = result ? `curl -fsSL '${origin}/api/agent/install.sh' | sudo bash -s -- --server '${origin}' --token '${result.token}'${insecureFlag}` : '';

  const createToken = async event => {
    event.preventDefault();
    setLoading(true);
    try {
      const response = await fetch('/api/enrollment-tokens', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setResult(data);
    } catch (error) {
      Notification.error({ message: '无法生成接入令牌', description: error.message });
    } finally { setLoading(false); }
  };

  const copy = async (value, message) => {
    try {
      await navigator.clipboard.writeText(value);
      Notification.success({ message });
    } catch {
      Notification.error({ message: '复制失败', description: '请手动选择文本复制' });
    }
  };

  if (!result) return <form className="enrollment-form" onSubmit={createToken}>
    <label>服务器备注<Input value={label} onChange={event => setLabel(event.target.value)} placeholder="例如：生产应用服务器" /></label>
    <Button htmlType="submit" type="primary" loading={loading} icon={<KeyRound size={15} />}>生成接入指令</Button>
  </form>;

  return <div className="enrollment-result">
    <div className="enrollment-token"><span><KeyRound size={15} />接入令牌</span><code>{result.token}</code><button type="button" title="复制令牌" aria-label="复制令牌" onClick={() => copy(result.token, '令牌已复制')}><Copy size={15} /></button></div>
    <div className="command-block"><span><Terminal size={15} />远端执行指令</span><code>{command}</code><button type="button" title="复制接入指令" aria-label="复制接入指令" onClick={() => copy(command, '接入指令已复制')}><Copy size={16} /></button></div>
  </div>;
}

function LoginPage({ setupRequired, onLogin, theme, onTheme }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async event => {
    event.preventDefault();
    setLoading(true);
    try {
      if (setupRequired && password !== confirmation) throw new Error('两次输入的密码不一致');
      const response = await fetch(setupRequired ? '/api/auth/setup' : '/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(setupRequired ? { username, password, setupToken } : { username, password })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      onLogin(data);
    } catch (error) {
      Notification.error({ message: '登录失败', description: error.message });
    } finally { setLoading(false); }
  };

  return <main className="login-shell">
    <div className="login-sunrise" aria-hidden="true" />
    <div className="login-forest forest-layer forest-back" /><div className="login-forest forest-layer forest-front" />
    <div className="login-water" aria-hidden="true" />
    <img className="login-tree" src={tree} alt="" />
    <img className="login-bush" src={bush} alt="" />
    <header className="login-topbar">
      <div className="login-brand"><span className="brand-leaf"><Leaf /></span><div><strong>PORT GROVE</strong><small>多服务器端口森林</small></div></div>
      <button className="theme-toggle login-theme" title="切换主题" aria-label="切换主题" onClick={onTheme}>{theme === 'light' ? <Moon /> : <Sun />}</button>
    </header>
    <section className="login-layout">
      <div className="login-welcome">
        <span className="login-eyebrow"><i />PORT GROVE CONTROL</span>
        <h1>{setupRequired ? <>为林地设置<br /><em>第一位管理员</em></> : <>欢迎回来，<br /><em>林地管理员</em></>}</h1>
        <div className="login-ready"><span><ShieldCheck size={15} />安全入口</span><strong>准备就绪</strong></div>
        <div className="login-route" aria-hidden="true">
          <span><Server /></span><i /><span><Network /></span><i /><span className="route-secure"><LockKeyhole /></span>
        </div>
      </div>
      <Card className={`login-panel ${setupRequired ? 'setup-panel' : ''}`}>
        <div className="login-panel-head">
          <div className="login-mark"><LockKeyhole /></div>
          <div><span>{setupRequired ? '首次启用' : '管理员入口'}</span><h2>{setupRequired ? '初始化账户' : '登录控制台'}</h2></div>
        </div>
        <form onSubmit={submit}>
          <label>账号<Input autoFocus autoComplete="username" required value={username} onChange={event => setUsername(event.target.value)} prefix={<UserRound size={16} />} placeholder="请输入管理员账号" /></label>
          {setupRequired && <label>服务器初始化码<Input type="password" autoComplete="off" required value={setupToken} onChange={event => setSetupToken(event.target.value)} prefix={<Terminal size={16} />} placeholder="输入初始化码" /></label>}
          <label>密码<Input type="password" autoComplete={setupRequired ? 'new-password' : 'current-password'} minLength={10} required value={password} onChange={event => setPassword(event.target.value)} prefix={<KeyRound size={16} />} placeholder={setupRequired ? '至少 10 个字符' : '请输入登录密码'} /></label>
          {setupRequired && <label>确认密码<Input type="password" autoComplete="new-password" minLength={10} required value={confirmation} onChange={event => setConfirmation(event.target.value)} prefix={<CheckCircle2 size={16} />} placeholder="再次输入密码" /></label>}
          <Button htmlType="submit" type="primary" loading={loading} icon={setupRequired ? <Leaf size={16} /> : <LogIn size={16} />}>{setupRequired ? '完成初始化' : '进入林地'}</Button>
        </form>
        <div className="login-security"><ShieldCheck size={14} /><span>受保护的管理会话</span></div>
      </Card>
    </section>
  </main>;
}

function AccountForm({ username, onSaved }) {
  const [form, setForm] = useState({ username, currentPassword: '', newPassword: '', confirmation: '' });
  const [loading, setLoading] = useState(false);
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const submit = async event => {
    event.preventDefault();
    if (form.newPassword !== form.confirmation) {
      Notification.error({ message: '保存失败', description: '两次输入的新密码不一致' });
      return;
    }
    setLoading(true);
    try {
      const response = await fetch('/api/auth/account', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: form.username, currentPassword: form.currentPassword, newPassword: form.newPassword })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      Notification.success({ message: '账户已更新' });
      onSaved(data);
    } catch (error) {
      Notification.error({ message: '保存失败', description: error.message });
    } finally { setLoading(false); }
  };
  return <form className="resource-form account-form" onSubmit={submit}>
    <label>管理员账号<Input autoComplete="username" required value={form.username} onChange={event => set('username', event.target.value)} prefix={<UserRound size={16} />} /></label>
    <label>当前密码<Input type="password" autoComplete="current-password" required value={form.currentPassword} onChange={event => set('currentPassword', event.target.value)} prefix={<LockKeyhole size={16} />} /></label>
    <label>新密码<Input type="password" autoComplete="new-password" minLength={10} value={form.newPassword} onChange={event => set('newPassword', event.target.value)} prefix={<KeyRound size={16} />} placeholder="不修改则留空" /></label>
    <label>确认新密码<Input type="password" autoComplete="new-password" minLength={10} value={form.confirmation} onChange={event => set('confirmation', event.target.value)} prefix={<CheckCircle2 size={16} />} /></label>
    <div className="form-submit"><Button htmlType="submit" type="primary" loading={loading} icon={<Settings size={15} />}>保存账户</Button></div>
  </form>;
}

function RemoveServerPanel({ server, onRemove, loading }) {
  return <div className="remove-server-panel">
    <span className="remove-server-icon"><Trash2 /></span>
    <strong>{server.name}</strong>
    <small>{server.publicIp || server.hostname} · {server.listenerCount || 0} 个监听端口</small>
    {!server.supportsDecommission && <small className="remove-warning">Agent v{server.agentVersion} 需要升级后才能远程停止</small>}
    <div><Button type="dashed" loading={loading} onClick={() => onRemove(true)}>仅从面板删除</Button>{server.supportsDecommission && <Button type="primary" loading={loading} icon={<Trash2 size={15} />} onClick={() => onRemove(false)}>通知 Agent 并解绑</Button>}</div>
  </div>;
}

function ServerRail({ servers, selectedId, onSelect }) {
  return <div className="server-rail" role="list" aria-label="服务器列表">
    {servers.map(server => <button type="button" role="listitem" key={server.id} className={`server-node ${selectedId === server.id ? 'selected' : ''}`} onClick={() => onSelect(server.id)}>
      <span className="node-icon"><Server size={17} /></span>
      <span className="node-copy"><strong>{server.name}</strong><small>{server.publicIp || server.hostname}</small></span>
      <span className={`node-state ${server.status}`}><i />{server.status === 'online' ? '在线' : server.status === 'removing' ? '解绑中' : '离线'}</span>
      <span className="node-count">{server.listenerCount}<small>端口</small></span>
    </button>)}
  </div>;
}

export default function App() {
  const [auth, setAuth] = useState({ state: 'checking', setupRequired: false, username: null });
  const [payload, setPayload] = useState({ server: null, servers: [], projects: [], listenerCount: 0, scannedAt: null });
  const [selectedServerId, setSelectedServerId] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [checking, setChecking] = useState(true);
  const [removing, setRemoving] = useState(false);
  const [modal, setModal] = useState(null);
  const [theme, setTheme] = useState(() => new URLSearchParams(window.location.search).get('theme') || localStorage.getItem('port-grove-theme') || 'light');
  const projects = payload.projects || [];
  const serverInfo = payload.server;

  const fetchStatus = async (notify = false, serverId = selectedServerId) => {
    setChecking(true);
    try {
      const params = serverId ? `?server=${encodeURIComponent(serverId)}` : '';
      const response = await fetch(`/api/status${params}`, { cache: 'no-store' });
      if (response.status === 401) { setAuth(current => ({ ...current, state: 'anonymous' })); return; }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const next = await response.json();
      setPayload(next);
      if (next.selectedServerId && next.selectedServerId !== selectedServerId) setSelectedServerId(next.selectedServerId);
      if (notify) Notification.success({ message: '状态已刷新', description: `${next.server?.name || '服务器'}发现 ${next.listenerCount} 个监听端口` });
    } catch (error) {
      Notification.error({ message: '无法读取服务器状态', description: error.message });
    } finally { setChecking(false); }
  };

  useEffect(() => {
    if (auth.state !== 'authenticated') return undefined;
    fetchStatus(false, selectedServerId);
    const timer = window.setInterval(() => fetchStatus(false, selectedServerId), 30000);
    return () => window.clearInterval(timer);
  }, [selectedServerId, auth.state]);

  useEffect(() => {
    fetch('/api/auth/status', { cache: 'no-store' }).then(response => response.json()).then(data => {
      setAuth({ state: data.authenticated ? 'authenticated' : 'anonymous', setupRequired: data.setupRequired, username: data.username });
    }).catch(() => setAuth({ state: 'anonymous', setupRequired: false, username: null }));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('port-grove-theme', theme);
  }, [theme]);

  const ports = useMemo(() => projects.flatMap(project => project.ports), [projects]);
  const counts = useMemo(() => ({
    frontend: ports.filter(p => p.type === 'frontend').length,
    backend: ports.filter(p => p.type === 'backend').length,
    service: ports.filter(p => p.type === 'service').length,
    healthy: ports.filter(p => p.status === 'healthy').length,
    issues: ports.filter(p => p.status !== 'healthy').length,
    web: ports.filter(p => p.webUrl).length
  }), [ports]);
  const filtered = useMemo(() => projects.filter(project => {
    const text = `${project.name} ${project.code} ${project.owner} ${project.description} ${project.ports.map(p => `${p.name} ${p.host} ${p.port} ${p.process}`).join(' ')}`.toLowerCase();
    const typeMatch = filter === 'all' || project.ports.some(port => port.type === filter);
    return typeMatch && text.includes(query.toLowerCase());
  }), [projects, query, filter]);
  const healthRate = ports.length ? Math.round(counts.healthy / ports.length * 100) : 0;
  const maxType = Math.max(counts.frontend, counts.backend, counts.service, 1);

  const renameProject = async form => {
    try {
      const response = await fetch(`/api/servers/${encodeURIComponent(selectedServerId)}/projects/${encodeURIComponent(modal.project.id)}/rename`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form)
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setPayload(await response.json());
      Notification.success({ message: '项目已更新', description: '名称与图标会在后续探测中保留' });
      setModal(null);
    } catch (error) { Notification.error({ message: '保存失败', description: error.message }); }
  };

  const removeServer = async force => {
    setRemoving(true);
    try {
      const response = await fetch(`/api/servers/${encodeURIComponent(modal.server.id)}/remove`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      Notification.success({ message: force ? '服务器记录已删除' : '解绑指令已下发' });
      setModal(null);
      setSelectedServerId('');
      fetchStatus(false, '');
    } catch (error) { Notification.error({ message: '解绑失败', description: error.message }); }
    finally { setRemoving(false); }
  };

  const logout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' }).catch(() => null);
    setPayload({ server: null, servers: [], projects: [], listenerCount: 0, scannedAt: null });
    setAuth(current => ({ ...current, state: 'anonymous', username: null }));
  };

  if (auth.state === 'checking') return <div className="auth-loading"><span className="brand-leaf"><Leaf /></span></div>;
  if (auth.state !== 'authenticated') return <LoginPage setupRequired={auth.setupRequired} theme={theme} onTheme={() => setTheme(current => current === 'light' ? 'dark' : 'light')} onLogin={data => setAuth({ state: 'authenticated', setupRequired: false, username: data.username })} />;

  return <div className="site-shell">
    <header className="site-header">
      <div className="brand"><span className="brand-leaf"><Leaf /></span><div><strong>PORT GROVE</strong><small>多服务器端口森林</small></div></div>
      <div className="header-status"><span><i />{payload.onlineServerCount || 0} / {payload.serverCount || 0} 台服务器在线</span><button className="theme-toggle" title={theme === 'light' ? '切换为深色模式' : '切换为浅色模式'} aria-label={theme === 'light' ? '切换为深色模式' : '切换为浅色模式'} onClick={() => setTheme(current => current === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon /> : <Sun />}</button><Button size="small" type="text" icon={<RefreshCw size={15} />} loading={checking} onClick={() => fetchStatus(true)}>刷新</Button><Button className="account-button" size="small" type="text" icon={<UserRound size={15} />} onClick={() => setModal({ mode: 'account' })}>{auth.username}</Button><Button size="small" type="text" icon={<LogOut size={15} />} onClick={logout}>退出</Button></div>
    </header>

    <main>
      <section className="forest-hero">
        <div className="forest-layer forest-back" /><div className="forest-layer forest-front" />
        <img className="hero-bush" src={bush} alt="" />
        <div className="hero-content"><div><span className="date-chip">SERVER · PROJECT · PORT</span><h1>服务器端口森林</h1><p>{payload.serverCount ? `${payload.serverCount} 台服务器，${payload.onlineServerCount} 台在线` : '正在等待服务器 Agent 上报'}，项目归属与监听状态持续检测。</p></div></div>
      </section>

      <div className="content">
        <section className="server-section">
          <div className="section-title server-title"><div><Title size="middle" color="app-blue">服务器节点</Title><span>{payload.serverCount || 0} 台已接入 · {payload.onlineServerCount || 0} 台在线</span></div><Button type="primary" size="small" icon={<Plus size={15} />} onClick={() => setModal({ mode: 'enroll' })}>接入服务器</Button></div>
          {payload.servers?.length > 0 && <ServerRail servers={payload.servers} selectedId={selectedServerId} onSelect={setSelectedServerId} />}
          {serverInfo && <Card className="server-card">
            <div className="server-main"><span className="server-icon"><Server /></span><div><strong>{serverInfo.name}</strong><p><Globe2 size={13} />{serverInfo.publicIp || serverInfo.hostname} · {serverInfo.provider}</p></div><div className="server-actions"><Tag color={serverInfo.status === 'online' ? 'app-green' : serverInfo.status === 'removing' ? 'app-yellow' : 'app-red'}>{serverInfo.status === 'online' ? '运行中' : serverInfo.status === 'removing' ? '解绑中' : '已离线'}</Tag>{!serverInfo.embedded && <button type="button" title="解绑服务器" aria-label="解绑服务器" onClick={() => setModal({ mode: 'remove', server: { ...serverInfo, listenerCount: payload.listenerCount } })}><Trash2 size={15} /></button>}</div></div>
            <div className="server-metrics">
              <span><Cpu /><small>CPU / LOAD</small><b>{serverInfo.cpuCount} 核 · {serverInfo.load}</b></span>
              <span><MemoryStick /><small>内存</small><b>{serverInfo.memoryUsedGb} / {serverInfo.memoryTotalGb} GB</b><Progress percent={serverInfo.memoryPercent} size="small" showInfo={false} /></span>
              <span><HardDrive /><small>系统磁盘</small><b>{serverInfo.diskUsedGb} / {serverInfo.diskTotalGb} GB</b><Progress percent={serverInfo.diskPercent} size="small" showInfo={false} /></span>
              <span><Clock3 /><small>运行时间 / AGENT</small><b>{serverInfo.uptimeDays} 天 · v{serverInfo.agentVersion}</b></span>
            </div>
          </Card>}
          <div className="hierarchy-line"><i /><span>{serverInfo ? `${serverInfo.name} 上的项目与端口` : '等待节点上报'}</span><i /></div>
        </section>

        <section className="overview-section">
          <div className="section-title"><Title size="middle" color="app-teal">实时概览</Title><span>数据源 · {serverInfo?.name || '未选择服务器'}</span></div>
          <div className="overview-grid">
            <StatCard color="app-teal" icon={Network} label="当前监听端口" value={payload.listenerCount} note={`${projects.length} 个项目 / 服务分组`}><div className="mini-range"><span>1</span><i><b style={{ width: `${Math.max(5, ports.length / 655.35)}%` }} /></i><span>65535</span></div></StatCard>
            <Card className="chart-card"><div className="chart-head"><div><span>服务类型分布</span><strong>{ports.length}</strong></div><Activity size={20} /></div><div className="type-bars">{[['frontend', counts.frontend], ['backend', counts.backend], ['service', counts.service]].map(([type, count]) => <div className="type-bar" key={type}><span>{TYPES[type].label}</span><i><b className={type} style={{ width: `${count / maxType * 100}%` }} /></i><strong>{count}</strong></div>)}</div></Card>
            <Card className="health-card"><div className="health-copy"><span>资源健康度</span><strong>{healthRate}<small>%</small></strong><p>{counts.issues ? `${counts.issues} 个端口需要留意` : '全部资源运行正常'}</p></div><div className="health-donut" style={{ '--rate': healthRate }}><span><CheckCircle2 /></span></div></Card>
            <StatCard color="app-yellow" icon={Globe2} label="可访问网页" value={counts.web} note={`${counts.frontend} 个前端 · ${counts.backend} 个后端`}><Progress percent={ports.length ? Math.round(counts.web / ports.length * 100) : 0} size="small" showInfo={false} /></StatCard>
          </div>
        </section>

        <section className="projects-section">
          <div className="projects-heading"><div><Title size="middle" color="app-green">服务器项目</Title><span>{filtered.length} / {projects.length} 个项目与服务</span></div><div className="filters"><Input value={query} onChange={e => setQuery(e.target.value)} allowClear prefix={<Search size={16} />} placeholder="搜索项目、端口或进程" /><div className="type-filter">{['all', 'frontend', 'backend', 'service'].map(type => <Tag key={type} size="medium" color={type === 'all' ? 'app-green' : TYPES[type].color} variant={filter === type ? 'solid' : 'outlined'} onClick={() => setFilter(type)}>{type === 'all' ? '全部' : TYPES[type].label}</Tag>)}</div></div></div>
          {filtered.length ? <div className="project-grid">{filtered.map(project => <ProjectCard key={project.id} project={project} onRename={project => setModal({ mode: 'rename', project })} />)}</div> : <Card type="dashed" className="empty"><Search /><h3>{checking ? '正在读取 Agent 上报' : '没有找到对应项目'}</h3><p>{checking ? '正在同步监听进程与项目归属。' : '换个关键词或资源类型试试。'}</p></Card>}
        </section>
      </div>
    </main>

    <footer><span>PORT GROVE · 多服务器项目资源台账</span><span>UI components by <a href="https://github.com/guokaigdg/animal-island-ui" target="_blank" rel="noreferrer">Animal Island UI <ExternalLink size={11} /></a> · CC BY-NC 4.0</span></footer>
    <Modal open={Boolean(modal)} title={modal?.mode === 'enroll' ? '接入服务器' : modal?.mode === 'remove' ? '解绑服务器' : modal?.mode === 'account' ? '账户设置' : '编辑项目'} onClose={() => setModal(null)} footer={null} typewriter={false}>
      {modal?.mode === 'rename' && <RenameForm key={`${selectedServerId}-${modal.project.id}`} project={modal.project} onSubmit={renameProject} />}
      {modal?.mode === 'enroll' && <EnrollmentPanel />}
      {modal?.mode === 'remove' && <RemoveServerPanel server={modal.server} onRemove={removeServer} loading={removing} />}
      {modal?.mode === 'account' && <AccountForm username={auth.username} onSaved={data => { setAuth(current => ({ ...current, username: data.username })); setModal(null); }} />}
    </Modal>
  </div>;
}
