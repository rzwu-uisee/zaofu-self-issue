import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJsonUrl = new URL("../package.json", import.meta.url);

test("app package exposes the complete dependency-free Product Pulse contract", async () => {
  const packageJson = JSON.parse(await readFile(packageJsonUrl, "utf8"));

  assert.deepEqual(packageJson, {
    name: "product-pulse",
    version: "1.0.0",
    private: true,
    type: "module",
    scripts: {
      start: "node server.mjs",
      test: "node --test",
    },
    dependencies: {},
    devDependencies: {},
  });
});
