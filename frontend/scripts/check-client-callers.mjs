#!/usr/bin/env node
/**
 * Check that every API client method has a caller in the app.
 *
 * `AdvisorClient` (src/api/types.ts) and the admin helpers (src/api/backoffice.ts)
 * are the only way components reach the backend. A method left on either
 * interface is dead weight the contract still has to carry, so this fails when
 * one is never referenced outside its own import statement.
 *
 * References are read from the TypeScript syntax tree, so a mention in prose
 * (JSX text, a comment, a string) does not count as a caller.
 *
 * Run from frontend/: `node scripts/check-client-callers.mjs`
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function read(path) {
  return readFileSync(join(root, path), 'utf8');
}

function filesUnder(directory, extensions) {
  const output = [];
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) output.push(...filesUnder(path, extensions));
    else if (extensions.some((extension) => name.endsWith(extension))) output.push(path);
  }
  return output;
}

const interfaceBody = /export interface AdvisorClient \{([\s\S]*?)\n\}/.exec(read('src/api/types.ts'));
if (!interfaceBody) throw new Error('AdvisorClient interface not found in src/api/types.ts');
const clientMethods = [...interfaceBody[1].matchAll(/^\s{2}(\w+)\(/gm)].map((match) => match[1]);

const backofficeSource = read('src/api/backoffice.ts');
const backofficeFunctions = [
  ...backofficeSource.matchAll(/^export (?:async )?function (\w+)/gm),
].map((match) => match[1]);

const consumers = [
  ...filesUnder(join(root, 'src/components'), ['.ts', '.tsx']),
  ...filesUnder(join(root, 'src/admin'), ['.ts', '.tsx']),
  ...filesUnder(join(root, 'src/app'), ['.ts', '.tsx']),
  ...filesUnder(join(root, 'src/state'), ['.ts', '.tsx']),
  join(root, 'src/main.tsx'),
  join(root, 'src/admin/main.tsx'),
];

const referenced = new Set();
for (const path of consumers) {
  const source = ts.createSourceFile(
    path,
    readFileSync(path, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const visit = (node) => {
    if (ts.isIdentifier(node)) {
      const parent = node.parent;
      const imported =
        ts.isImportSpecifier(parent) ||
        ts.isImportClause(parent) ||
        ts.isNamespaceImport(parent) ||
        ts.isExportSpecifier(parent);
      if (!imported) referenced.add(node.text);
    }
    node.forEachChild(visit);
  };
  visit(source);
}

const missing = [...clientMethods, ...backofficeFunctions].filter((name) => !referenced.has(name));

if (missing.length) {
  console.error(`No caller found for ${missing.length} client method(s):`);
  for (const name of missing) console.error(`  ${name}`);
  process.exit(1);
}
console.log(
  `Every client method has a caller (${clientMethods.length} AdvisorClient, ` +
    `${backofficeFunctions.length} backoffice).`,
);
