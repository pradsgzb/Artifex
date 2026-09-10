import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
let ts;
try {
  ts = require('typescript');
} catch {
  console.log('TypeScript syntax check skipped: global typescript package is unavailable.');
  process.exit(0);
}
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..', 'web', 'angular', 'src');
const files = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.isFile() && entry.name.endsWith('.ts')) files.push(full);
  }
}
walk(root);
let failed = false;
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const result = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      experimentalDecorators: true
    },
    fileName: file,
    reportDiagnostics: true
  });
  for (const diagnostic of result.diagnostics ?? []) {
    if (diagnostic.category === ts.DiagnosticCategory.Error) {
      failed = true;
      console.error(`${file}: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')}`);
    }
  }
}
if (failed) process.exit(1);
console.log(`TypeScript syntax: valid (${files.length} source files)`);
