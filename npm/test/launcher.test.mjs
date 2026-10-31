import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { invocation, engineVersion, launcherVersion } from '../bin/launcher.mjs';

test('CLI arguments remain literal and the release wheel is pinned', () => {
  const result = invocation(['pdf-extract', '--input', '/a file;$(unsafe).pdf', '--output', '/new'], 'pdf2data', {});
  assert.equal(result.command, 'uv');
  assert.equal(result.args.at(-3), '/a file;$(unsafe).pdf');
  assert.ok(result.args.includes(`PDF2Data[pdf,word] @ https://github.com/rudraptpsingh/PDF2Data/releases/download/v${launcherVersion}/pdf2data-${engineVersion}-py3-none-any.whl`));
});
test('MCP uses the same version and its optional engine extra', () => {
  const command = invocation(['--workspace', '/work'], 'pdf2data-mcp', {});
  assert.ok(command.args.some(v => v.startsWith('PDF2Data[pdf,mcp,word] @ ')));
  assert.deepEqual(command.args.slice(-3), ['pdf2data-mcp', '--workspace', '/work']);
});
test('explicit local wheel and runtime overrides are passed without a shell', () => {
  const command = invocation(['doctor'], 'pdf2data', { PDF2DATA_UV: '/custom uv', PDF2DATA_ENGINE_SPEC: 'PDF2Data[pdf] @ file:///tmp/source.whl', PDF2DATA_RUNTIME_PYTHON: '/python' });
  assert.equal(command.command, '/custom uv');
  assert.ok(command.args.includes('/python'));
  assert.ok(command.args.includes('PDF2Data[pdf] @ file:///tmp/source.whl'));
});
test('version inspection needs neither uv nor a Python installation', () => {
  const result = spawnSync(process.execPath, ['bin/pdf2data.mjs', '--version'], { cwd: new URL('..', import.meta.url), encoding: 'utf8', env: { ...process.env, PDF2DATA_UV: '/missing/uv' } });
  assert.equal(result.status, 0);
  assert.match(result.stdout, /PDF2Data 0\.2\.0rc1/);
});
test('missing uv gives an actionable error and a nonzero exit', () => {
  const result = spawnSync(process.execPath, ['bin/pdf2data.mjs', 'doctor'], { cwd: new URL('..', import.meta.url), encoding: 'utf8', env: { ...process.env, PDF2DATA_UV: '/missing/uv' } });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /requires uv/);
  assert.equal(result.stdout, '');
});
