// Start both the seekdb proxy and the Vite dev server (cross-platform).
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const npx = process.platform === 'win32' ? 'npx.cmd' : 'npx';

const proxy = spawn(process.execPath, [join(root, 'server', 'seekdb-proxy.mjs')], {
  stdio: 'inherit',
});
const vite = spawn(npx, ['vite', '--open'], { cwd: root, stdio: 'inherit', shell: true });

const cleanup = () => {
  proxy.kill();
  vite.kill();
  process.exit(0);
};
process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
proxy.on('exit', () => {
  console.error('[dev] seekdb proxy exited');
  vite.kill();
  process.exit(1);
});
