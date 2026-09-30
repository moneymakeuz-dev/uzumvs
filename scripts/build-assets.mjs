import { build } from 'esbuild';
import { cp, mkdir } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'app/static/dist');
await mkdir(path.join(output, 'fonts'), { recursive: true });
for (const weight of [400, 600, 700]) {
  for (const script of ['latin', 'cyrillic']) {
    const filename = `manrope-${script}-${weight}-normal.woff2`;
    await cp(path.join(root, 'node_modules/@fontsource/manrope/files', filename), path.join(output, 'fonts', filename));
  }
}
await build({ entryPoints: [path.join(root, 'frontend/app.js')], outfile: path.join(output, 'app.js'),
  bundle: true, minify: true, target: ['es2022'], format: 'iife', legalComments: 'eof' });
const css = spawnSync(process.execPath, [path.join(root, 'node_modules/@tailwindcss/cli/dist/index.mjs'),
  '-i', path.join(root, 'frontend/styles.css'), '-o', path.join(output, 'app.css'), '--minify'], { stdio: 'inherit' });
if (css.status !== 0) process.exit(css.status ?? 1);
console.log('Local JS, CSS and Manrope fonts built.');
