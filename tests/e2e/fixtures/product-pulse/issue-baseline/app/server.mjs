import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

export const pulse = Object.freeze([
  Object.freeze({
    id: "pulse-003",
    title: "Search",
    status: "healthy",
    updatedAt: "2026-08-01T12:00:00Z",
  }),
  Object.freeze({
    id: "pulse-002",
    title: "Checkout",
    status: "warning",
    updatedAt: "2026-08-01T11:00:00Z",
  }),
  Object.freeze({
    id: "pulse-001",
    title: "Catalog",
    status: "degraded",
    updatedAt: "2026-08-01T10:00:00Z",
  }),
]);

const health = Object.freeze({
  ok: true,
  service: "product-pulse",
  version: "1.0.0",
});

function send(response, statusCode, contentType, body) {
  response.writeHead(statusCode, { "content-type": contentType });
  response.end(body);
}

function renderHomepage() {
  const cards = pulse
    .map(({ title, status }) => `<article><h2>${title}</h2><p>${status}</p></article>`)
    .join("\n");

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Product Pulse</title>
  </head>
  <body>
    <main>
      <h1>Product Pulse</h1>
      ${cards}
    </main>
  </body>
</html>`;
}

export function createProductPulseServer() {
  return createServer((request, response) => {
    const pathname = new URL(request.url ?? "/", "http://localhost").pathname;

    if (request.method === "GET" && pathname === "/health") {
      send(response, 200, "application/json; charset=utf-8", JSON.stringify(health));
      return;
    }

    if (request.method === "GET" && pathname === "/api/pulse") {
      send(response, 200, "application/json; charset=utf-8", JSON.stringify(pulse));
      return;
    }

    if (request.method === "GET" && pathname === "/") {
      send(response, 200, "text/html; charset=utf-8", renderHomepage());
      return;
    }

    send(response, 404, "text/plain; charset=utf-8", "Not Found");
  });
}

export function resolvePort(environment = process.env) {
  return environment.PORT === undefined ? 3000 : Number(environment.PORT);
}

export function startProductPulseServer({ port = resolvePort(), host } = {}) {
  const server = createProductPulseServer();
  server.listen(port, host, () => {
    const address = server.address();
    const activePort = typeof address === "object" && address !== null ? address.port : port;
    console.log(`Product Pulse listening on port ${activePort}`);
  });
  return server;
}

const isEntrypoint = process.argv[1] !== undefined
  && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isEntrypoint) {
  startProductPulseServer();
}
