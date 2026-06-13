const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const parsed = { prefix: 'moldy-langgraph-v3' };
  for (let index = 2; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--prefix') {
      const value = argv[index + 1];
      if (!value) throw new Error('--prefix requires a value');
      parsed.prefix = value;
      index += 1;
    }
  }
  return parsed;
}

function isInside(root, candidate) {
  const normalizedRoot = path.resolve(root);
  const normalizedCandidate = path.resolve(candidate);
  return normalizedCandidate === normalizedRoot || normalizedCandidate.startsWith(`${normalizedRoot}${path.sep}`);
}

function safeBasename(value) {
  return String(value).replace(/[^a-zA-Z0-9._-]/g, '-').replace(/^-+/, '') || 'moldy-langgraph-v3';
}

function outputPath(filename) {
  const outputDir = path.resolve(process.env.OUTPUTS_DIR || process.env.SKILL_OUTPUT_DIR || '.');
  const resolved = path.resolve(outputDir, filename);
  if (!isInside(outputDir, resolved)) {
    throw new Error(`output must stay inside ${outputDir}`);
  }
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  return resolved;
}

function main() {
  const args = parseArgs(process.argv);
  const prefix = safeBasename(args.prefix);
  const reportName = `${prefix}-report.md`;
  const notesName = `${prefix}-notes.txt`;

  fs.writeFileSync(
    outputPath(reportName),
    [
      '# LangGraph v3 E2E Report',
      '',
      '- Planning state came from `values.todos`.',
      '- Subagent activity stayed scoped to the delegated task.',
      '- Artifact replay stayed outside the root transcript.',
      '',
    ].join('\n'),
    'utf8',
  );
  fs.writeFileSync(
    outputPath(notesName),
    [
      'LangGraph v3 plain-text artifact',
      'tool: execute_in_skill',
      'state: replay-ready',
      '',
    ].join('\n'),
    'utf8',
  );

  console.log(`Wrote ${reportName} and ${notesName}`);
}

main();
