import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_MAX_CHUNK_BYTES,
  inspectJavaScriptChunks,
} from "../scripts/check-bundle-size.mjs";

test("bundle budget reports only oversized JavaScript chunks", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "zf-bundle-budget-"));
  try {
    await writeFile(resolve(root, "small.js"), Buffer.alloc(64));
    await writeFile(
      resolve(root, "large.js"),
      Buffer.alloc(DEFAULT_MAX_CHUNK_BYTES + 1),
    );
    await writeFile(resolve(root, "ignored.css"), Buffer.alloc(600_000));

    const result = await inspectJavaScriptChunks(root);

    assert.deepEqual(result.oversized, [{
      name: "large.js",
      bytes: DEFAULT_MAX_CHUNK_BYTES + 1,
    }]);
    const command = spawnSync(
      process.execPath,
      [
        fileURLToPath(new URL("../scripts/check-bundle-size.mjs", import.meta.url)),
        root,
      ],
      { encoding: "utf-8" },
    );
    assert.equal(command.status, 1);
    assert.match(command.stderr, /chunk budget 500000 exceeded/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
