import assert from "node:assert/strict";
import test from "node:test";

import { createProductPulseServer, pulse, resolvePort } from "../server.mjs";

const expectedPulse = [
  {
    id: "pulse-003",
    title: "Search",
    status: "healthy",
    updatedAt: "2026-08-01T12:00:00Z",
  },
  {
    id: "pulse-002",
    title: "Checkout",
    status: "warning",
    updatedAt: "2026-08-01T11:00:00Z",
  },
  {
    id: "pulse-001",
    title: "Catalog",
    status: "degraded",
    updatedAt: "2026-08-01T10:00:00Z",
  },
];

const server = createProductPulseServer();
let origin;

test.before(async () => {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  assert.notEqual(address, null);
  assert.equal(typeof address, "object");
  origin = `http://127.0.0.1:${address.port}`;
});

test.after(async () => {
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
  assert.equal(server.listening, false);
});

test("GET /health returns the exact Product Pulse health document", async () => {
  const response = await fetch(`${origin}/health`);

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(await response.json(), {
    ok: true,
    service: "product-pulse",
    version: "1.0.0",
  });
});

test("GET /api/pulse returns only the exact sorted fixed pulse rows", async () => {
  const response = await fetch(`${origin}/api/pulse`);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(body, expectedPulse);
  assert.deepEqual(body.map((row) => Object.keys(row)), [
    ["id", "title", "status", "updatedAt"],
    ["id", "title", "status", "updatedAt"],
    ["id", "title", "status", "updatedAt"],
  ]);
  assert.deepEqual(pulse, expectedPulse);
});

test("GET /api/pulse filters rows by an exact status match", async () => {
  const response = await fetch(`${origin}/api/pulse?status=warning`);

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(await response.json(), [expectedPulse[1]]);
});

test("GET /api/pulse does not treat a case-mismatched status as a match", async () => {
  const response = await fetch(`${origin}/api/pulse?status=Warning`);

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(await response.json(), []);
});

test("GET /api/pulse returns an empty array for an unmatched status", async () => {
  const response = await fetch(`${origin}/api/pulse?status=unknown`);

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.deepEqual(await response.json(), []);
});

test("GET / server-renders the title and every pulse title/status pair", async () => {
  const response = await fetch(`${origin}/`);
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "text/html; charset=utf-8");
  assert.match(html, /<h1>Product Pulse<\/h1>/);
  assert.match(html, /<h2>Search<\/h2>\s*<p>healthy<\/p>/);
  assert.match(html, /<h2>Checkout<\/h2>\s*<p>warning<\/p>/);
  assert.match(html, /<h2>Catalog<\/h2>\s*<p>degraded<\/p>/);
});

test("unknown routes return a plain-text 404 response", async () => {
  const response = await fetch(`${origin}/missing`);

  assert.equal(response.status, 404);
  assert.equal(response.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.equal(await response.text(), "Not Found");
});

test("the executable port defaults to 3000 and honors PORT", () => {
  assert.equal(resolvePort({}), 3000);
  assert.equal(resolvePort({ PORT: "43127" }), 43127);
});
