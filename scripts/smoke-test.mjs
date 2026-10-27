import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync, openSync, closeSync } from 'node:fs';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const portDir = path.resolve(process.argv[2]);
const output = mkdtempSync(path.join(path.dirname(portDir), 'smoke-'));
const profile = path.join(output, 'profile');
mkdirSync(profile);
const log = openSync(path.join(output, 'console.log'), 'w');
let app;
let xvfb;
let socket;
let nextId = 1;
const pending = new Map();
const env = {
  ...process.env,
  WORKBUDDY_CONFIG_DIR: profile, CODEBUDDY_CONFIG_DIR: profile,
  WORKBUDDY_USER_DATA_DIR: path.join(profile, 'app'),
  XDG_CONFIG_HOME: path.join(profile, 'xdg-config'),
  XDG_DATA_HOME: path.join(profile, 'xdg-data'),
  XDG_CACHE_HOME: path.join(profile, 'cache'),
};

function processes() {
  const entries = [];
  for (const pid of readdirSync('/proc').filter(name => /^\d+$/.test(name))) {
    try {
      const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
      const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
      entries.push({ pid: Number(pid), parent: Number(fields[1]), group: Number(fields[2]), ticks: Number(fields[11]) + Number(fields[12]), name: stat.slice(stat.indexOf('(') + 1, stat.lastIndexOf(')')) });
    } catch {}
  }
  return entries;
}

function applicationProcesses() {
  const entries = processes();
  const owned = new Set([app.pid]);
  let size;
  do {
    size = owned.size;
    for (const p of entries) if (owned.has(p.parent) || p.group === app.pid) owned.add(p.pid);
  } while (owned.size !== size);
  return entries.filter(p => owned.has(p.pid));
}

async function command(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

try {
  const localXvfb = path.resolve('work/test-tools/usr/bin/Xvfb');
  const xvfbPath = process.env.XVFB || (existsSync(localXvfb) ? localXvfb : undefined);
  if (xvfbPath) {
    const display = 100 + (process.pid % 20000);
    env.DISPLAY = `:${display}`;
    delete env.WAYLAND_DISPLAY;
    xvfb = spawn(xvfbPath, [env.DISPLAY, '-screen', '0', '1280x900x24', '-nolisten', 'tcp', '-ac'], { stdio: ['ignore', log, log] });
    for (let attempt = 0; attempt < 50 && !existsSync(`/tmp/.X11-unix/X${display}`); attempt++) {
      if (xvfb.exitCode !== null) throw new Error('Xvfb exited');
      await delay(100);
    }
  }
  app = spawn(path.join(portDir, 'launch-workbuddy'), ['--remote-debugging-port=0', '--ozone-platform=x11'], {
    env, stdio: ['ignore', log, log], detached: true,
  });
  let page;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (app.exitCode !== null) throw new Error(`Application exited: ${app.exitCode}`);
    // Chromium may initialize before WorkBuddy creates its session directory,
    // so DevToolsActivePort isn't reliable. The local endpoint is also logged.
    const match = /DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//.exec(readFileSync(path.join(output, 'console.log'), 'utf8'));
    if (match) {
      const targets = await fetch(`http://127.0.0.1:${match[1]}/json/list`, { signal: AbortSignal.timeout(5000) }).then(r => r.json());
      writeFileSync(path.join(output, 'targets.json'), JSON.stringify(targets, null, 2));
      page = targets.find(target => target.type === 'page' && /\/renderer\/index\.html/.test(target.url));
      if (page) break;
    }
    await delay(500);
  }
  assert.ok(page, 'No application window exposed through CDP');
  socket = new WebSocket(page.webSocketDebuggerUrl);
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id) return;
    const handler = pending.get(message.id);
    if (!handler) return;
    pending.delete(message.id);
    message.error ? handler.reject(new Error(JSON.stringify(message.error))) : handler.resolve(message.result);
  });
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve); socket.addEventListener('error', reject); });
  let text;
  for (let attempt = 0; attempt < 60; attempt++) {
    const result = await command('Runtime.evaluate', { expression: 'document.body.innerText', returnByValue: true });
    text = result.result.value || '';
    if (/登录|Login|Log in|Sign in|开始使用|What can I help|Ideas into reality/i.test(text)) break;
    await delay(500);
  }
  const startupLog = readFileSync(path.join(profile, 'logs/main.log'), 'utf8');
  assert.ok(!startupLog.includes('[StorageBootstrap]'), 'Credential storage initialization failed');
  const events = startupLog.trim().split('\n').map(line => { try { return JSON.parse(line); } catch { return {}; } });
  const credentials = events.find(event => event.message?.[0] === '[CredentialBootstrap] main bootstrap created')?.message?.[1];
  assert.equal(credentials?.symmetricAvailable, true, 'Tencent storage symmetric key is unavailable');
  assert.equal(credentials?.asymmetricMaterialAvailable, true, 'Protected per-user storage key is unavailable');
  assert.match(text, /登录|Login|Log in|Sign in|开始使用|What can I help|Ideas into reality/i, 'Application main content did not render');
  // A fallback window alone is insufficient: the daemon must finish startup.
  for (let attempt = 0; attempt < 60; attempt++) {
    const mainLog = path.join(profile, 'logs/main.log');
    if (existsSync(mainLog) && readFileSync(mainLog, 'utf8').includes('Main bootstrap ready')) break;
    if (attempt === 59) throw new Error('Daemon bootstrap did not complete');
    await delay(500);
  }
  await delay(30000);
  const ticksPerSecond = Number(execFileSync('getconf', ['CLK_TCK'], { encoding: 'utf8' }).trim());
  let previous = new Map(applicationProcesses().map(p => [p.pid, p.ticks]));
  let ticks = 0;
  const ticksByProcess = new Map();
  const idleSamples = [];
  const started = performance.now();
  for (let sample = 0; sample < 15; sample++) {
    await delay(1000);
    const current = applicationProcesses();
    let sampleTicks = 0;
    for (const p of current) {
      const increment = Math.max(0, p.ticks - (previous.get(p.pid) ?? 0));
      ticks += increment;
      sampleTicks += increment;
      const key = `${p.name} (${p.pid})`;
      ticksByProcess.set(key, (ticksByProcess.get(key) || 0) + increment);
    }
    idleSamples.push(sampleTicks / ticksPerSecond * 100);
    previous = new Map(current.map(p => [p.pid, p.ticks]));
  }
  const idleSeconds = (performance.now() - started) / 1000;
  const idleCpuPercent = Math.round(ticks / ticksPerSecond / idleSeconds * 1000) / 10;
  const idleCpuByProcess = Object.fromEntries([...ticksByProcess].map(([name, ticks]) => [name, Math.round(ticks / ticksPerSecond / idleSeconds * 1000) / 10]));
  const screenshot = await command('Page.captureScreenshot', { format: 'png' });
  writeFileSync(path.join(output, 'login.png'), Buffer.from(screenshot.data, 'base64'));
  writeFileSync(path.join(output, 'result.json'), JSON.stringify({ page: page.url, title: page.title, text, profile, storageBootstrap: credentials, idleSeconds, idleCpuPercent, idleCpuByProcess, idleSamples, cpuScope: 'main process, process group and descendants; 100% = one CPU core' }, null, 2));
  console.log(`Application and daemon ready; fresh-profile idle CPU ${idleCpuPercent}% over ${idleSeconds.toFixed(1)}s. Evidence: ${output}`);
} finally {
  socket?.close();
  if (app) {
    try { process.kill(-app.pid, 'SIGTERM'); } catch {}
    await delay(1500);
    if (app.exitCode === null) { try { process.kill(-app.pid, 'SIGKILL'); } catch {} }
  }
  xvfb?.kill('SIGTERM');
  closeSync(log);
}
