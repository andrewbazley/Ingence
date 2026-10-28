// Write the licenses of every library the explorer bundles beside the bundle.
// The build strips comments, so the notices the MIT and ISC licenses require travel as a file:
// src/ingence/web/explorer/THIRD_PARTY_NOTICES.txt, shipped in the wheel with the bundle.
import {readFileSync, writeFileSync, existsSync} from 'node:fs';
import {join} from 'node:path';

const bundled = ['react', 'react-dom', 'scheduler', '@dagrejs/dagre', '@dagrejs/graphlib'];
const parts = [
  'Third-party software bundled into explorer.js (the Graph workspace of the Ingence Dashboard).',
  'Each is used under its own license, reproduced below as its package ships it.',
  '',
];
for (const name of bundled) {
  const folder = join('node_modules', name);
  if (!existsSync(folder)) continue;
  const manifest = JSON.parse(readFileSync(join(folder, 'package.json'), 'utf8'));
  const licence = ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'license'].map((file) => join(folder, file)).find(existsSync);
  parts.push('='.repeat(78), `${manifest.name} ${manifest.version} (${manifest.license})`, '='.repeat(78));
  parts.push(licence ? readFileSync(licence, 'utf8').trim() : `License: ${manifest.license}`, '');
}
writeFileSync(join('..', 'src', 'ingence', 'web', 'explorer', 'THIRD_PARTY_NOTICES.txt'), `${parts.join('\n').trimEnd()}\n`);
