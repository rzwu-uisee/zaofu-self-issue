import { readdir, stat } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const DEFAULT_MAX_CHUNK_BYTES = 500_000;

export async function inspectJavaScriptChunks(
  assetsDir,
  maxBytes = DEFAULT_MAX_CHUNK_BYTES,
) {
  const files = (await readdir(assetsDir))
    .filter((name) => name.endsWith(".js"))
    .sort();
  const chunks = await Promise.all(files.map(async (name) => ({
    name,
    bytes: (await stat(resolve(assetsDir, name))).size,
  })));
  return {
    chunks,
    oversized: chunks.filter((chunk) => chunk.bytes > maxBytes),
  };
}

async function main() {
  const scriptDir = dirname(fileURLToPath(import.meta.url));
  const assetsDir = resolve(
    process.argv[2] ?? resolve(scriptDir, "../dist/assets"),
  );
  const maxBytes = Number(
    process.env.ZF_WEB_MAX_CHUNK_BYTES ?? DEFAULT_MAX_CHUNK_BYTES,
  );
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) {
    throw new Error("ZF_WEB_MAX_CHUNK_BYTES must be a positive integer");
  }
  const result = await inspectJavaScriptChunks(assetsDir, maxBytes);
  if (result.chunks.length === 0) {
    throw new Error(`no JavaScript chunks found in ${assetsDir}`);
  }
  if (result.oversized.length > 0) {
    const details = result.oversized
      .map((chunk) => `${chunk.name}=${chunk.bytes}`)
      .join(", ");
    throw new Error(`JavaScript chunk budget ${maxBytes} exceeded: ${details}`);
  }
  const largest = result.chunks.reduce((current, chunk) => (
    chunk.bytes > current.bytes ? chunk : current
  ));
  process.stdout.write(
    `[bundle-budget] ${result.chunks.length} chunks; largest `
    + `${largest.name}=${largest.bytes}; limit=${maxBytes}\n`,
  );
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : error}\n`);
    process.exitCode = 1;
  });
}
