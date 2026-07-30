import { standaloneJsonMarkdown } from "../src/components/agent-session/jsonDisplay.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

const compactObject = '{"status":"healthy","details":{"surface":"shared"}}';
assert(
  standaloneJsonMarkdown(compactObject) === [
    "```json",
    "{",
    '  "status": "healthy",',
    '  "details": {',
    '    "surface": "shared"',
    "  }",
    "}",
    "```",
  ].join("\n"),
  "standalone object becomes a pretty JSON Markdown block",
);

assert(
  standaloneJsonMarkdown('[{"id":1},{"id":2}]').includes('\n    "id": 1\n'),
  "standalone array is pretty-printed",
);
assert(
  standaloneJsonMarkdown("status: healthy") === "status: healthy",
  "plain text remains unchanged",
);
assert(
  standaloneJsonMarkdown('Status follows: {"status":"healthy"}')
    === 'Status follows: {"status":"healthy"}',
  "prose containing JSON remains unchanged",
);
assert(
  standaloneJsonMarkdown('{"status":') === '{"status":',
  "incomplete JSON remains unchanged",
);
assert(
  standaloneJsonMarkdown('```json\n{"status":"healthy"}\n```')
    === '```json\n{"status":"healthy"}\n```',
  "an existing fenced block is not nested",
);

console.log("agentJsonDisplay.test.ts OK");
