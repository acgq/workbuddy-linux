const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const path = require('node:path');
const app = path.resolve(process.argv[2]);
const cliOnly = process.argv.includes('--cli');
const load = createRequire(path.join(app, cliOnly ? 'cli/package.json' : 'package.json'));

(async () => {
  if (!cliOnly) {
    const Database = load('./package.json').dependencies['better-sqlite3']
      ? load('better-sqlite3') : load('node:sqlite').DatabaseSync;
    const db = new Database(':memory:');
    db.exec('CREATE TABLE probe (value TEXT)');
    db.prepare('INSERT INTO probe VALUES (?)').run('linux');
    assert.equal(db.prepare('SELECT value FROM probe').get().value, 'linux');
    db.close();
    const koffi = load('koffi');
    assert.equal(koffi.load(null).func('int getpid()')(), process.pid);
    load('@ban-dal/os-bridge');
  }
  const pty = load('@lydell/node-pty');
  await new Promise((resolve, reject) => {
    let output = '';
    const terminal = pty.spawn('/bin/sh', ['-c', 'printf workbuddy-pty-ok'], {
      name: 'xterm-256color', cols: 80, rows: 24, cwd: app, env: process.env,
    });
    const timer = setTimeout(() => { terminal.kill(); reject(new Error('PTY timed out')); }, 10000);
    terminal.onData(data => { output += data; });
    terminal.onExit(({ exitCode }) => {
      clearTimeout(timer);
      try { assert.equal(exitCode, 0); assert.match(output, /workbuddy-pty-ok/); resolve(); }
      catch (error) { reject(error); }
    });
  });
  console.log(`${cliOnly ? 'CLI Node' : 'Electron'} native checks passed (${process.versions.node}, ABI ${process.versions.modules})`);
})().catch(error => { console.error(error); process.exitCode = 1; });
