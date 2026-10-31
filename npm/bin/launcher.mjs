import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
export const engineVersion = pkg.version.replace(/-rc\.(\d+)$/, 'rc$1');
export const launcherVersion = pkg.version;

export function invocation(args, entry = 'pdf2data', env = process.env) {
  if (!['pdf2data', 'pdf2data-mcp'].includes(entry)) throw new Error('Unknown PDF2Data entry point');
  const wheel = `https://github.com/rudraptpsingh/PDF2Data/releases/download/v${pkg.version}/pdf2data-${engineVersion}-py3-none-any.whl`;
  const extras = entry === 'pdf2data-mcp' ? 'pdf,mcp,word' : 'pdf,word';
  const spec = env.PDF2DATA_ENGINE_SPEC || `PDF2Data[${extras}] @ ${wheel}`;
  return {
    command: env.PDF2DATA_UV || 'uv',
    args: ['tool', 'run', '--quiet', '--python', env.PDF2DATA_RUNTIME_PYTHON || '3.12', '--from', spec, entry, ...args]
  };
}

export function main(args, entry = 'pdf2data') {
  if (args.length === 1 && args[0] === '--version') {
    process.stdout.write(`PDF2Data ${engineVersion} (npm launcher ${pkg.version})\n`);
    return;
  }
  const command = invocation(args, entry);
  const child = spawn(command.command, command.args, { stdio: 'inherit', shell: false,
    env: { ...process.env, PYTHONUTF8: process.env.PYTHONUTF8 || '1' } });
  const signals = ['SIGINT', 'SIGTERM'];
  const handlers = new Map(signals.map(signal => [signal, () => child.kill(signal)]));
  for (const [signal, handler] of handlers) process.on(signal, handler);
  child.on('error', error => {
    const message = error.code === 'ENOENT'
      ? 'PDF2Data requires uv. Install it from https://docs.astral.sh/uv/getting-started/installation/ and retry. No Python models are downloaded by the launcher.'
      : `PDF2Data could not start: ${error.message}`;
    process.stderr.write(message + '\n');
    process.exitCode = 2;
  });
  child.on('exit', (code, signal) => {
    for (const [event, handler] of handlers) process.removeListener(event, handler);
    process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1);
  });
}
