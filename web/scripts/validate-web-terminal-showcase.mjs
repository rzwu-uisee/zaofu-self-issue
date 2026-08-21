import { createReadStream } from "node:fs";
import { mkdir, stat, writeFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { chromium } from "@playwright/test";

const videoDir = path.resolve(process.env.ZF_WEB_TERMINAL_VIDEO_DIR ?? "");
const reportDir = path.resolve(
  process.env.ZF_WEB_TERMINAL_VIDEO_REPORT_DIR ?? "test-results/web-terminal-video-validation",
);
const executablePath = process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH;
const videos = [
  {
    file: "web-terminal-introduction-zh-4k.mp4",
    width: 3840,
    height: 2160,
  },
  {
    file: "web-terminal-introduction-zh-1080p.mp4",
    width: 1920,
    height: 1080,
  },
];
const seekTimes = [0.8, 19.4, 39.0, 56.0, 73.2, 94.0, 115.8];

if (!process.env.ZF_WEB_TERMINAL_VIDEO_DIR) {
  throw new Error("ZF_WEB_TERMINAL_VIDEO_DIR is required");
}
await mkdir(reportDir, { recursive: true });

function contentType(file) {
  return file.endsWith(".mp4") ? "video/mp4" : "text/html; charset=utf-8";
}

const server = http.createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url ?? "/", "http://127.0.0.1").pathname);
  if (pathname === "/") {
    response.writeHead(200, { "Content-Type": contentType("index.html") });
    response.end("<!doctype html><video id=demo controls playsinline></video><canvas id=probe width=320 height=180 hidden></canvas>");
    return;
  }
  const file = path.basename(pathname);
  if (!videos.some((item) => item.file === file)) {
    response.writeHead(404).end();
    return;
  }
  const target = path.join(videoDir, file);
  const metadata = await stat(target);
  const range = request.headers.range;
  if (!range) {
    response.writeHead(200, {
      "Accept-Ranges": "bytes",
      "Content-Length": metadata.size,
      "Content-Type": contentType(file),
    });
    createReadStream(target).pipe(response);
    return;
  }
  const match = /^bytes=(\d+)-(\d*)$/.exec(range);
  if (!match) {
    response.writeHead(416).end();
    return;
  }
  const start = Number(match[1]);
  const end = match[2] ? Number(match[2]) : metadata.size - 1;
  if (start > end || end >= metadata.size) {
    response.writeHead(416).end();
    return;
  }
  response.writeHead(206, {
    "Accept-Ranges": "bytes",
    "Content-Length": end - start + 1,
    "Content-Range": `bytes ${start}-${end}/${metadata.size}`,
    "Content-Type": contentType(file),
  });
  createReadStream(target, { start, end }).pipe(response);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (!address || typeof address === "string") throw new Error("video server did not bind a TCP port");

const browser = await chromium.launch({
  args: ["--autoplay-policy=no-user-gesture-required"],
  headless: true,
  executablePath,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(`http://127.0.0.1:${address.port}/`);
const results = [];

try {
  for (const expected of videos) {
    const source = `http://127.0.0.1:${address.port}/${expected.file}`;
    const metadata = await page.evaluate(async ({ src }) => {
      const video = document.querySelector("video");
      video.src = src;
      video.muted = true;
      video.load();
      await new Promise((resolve, reject) => {
        const timeout = window.setTimeout(() => reject(new Error("canplaythrough timeout")), 30_000);
        video.addEventListener("canplaythrough", () => {
          window.clearTimeout(timeout);
          resolve();
        }, { once: true });
        video.addEventListener("error", () => reject(video.error), { once: true });
      });
      const before = video.currentTime;
      await video.play();
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      video.pause();
      const stream = typeof video.captureStream === "function" ? video.captureStream() : null;
      return {
        audio_tracks: stream?.getAudioTracks().length ?? 0,
        duration: video.duration,
        height: video.videoHeight,
        played_seconds: video.currentTime - before,
        ready_state: video.readyState,
        width: video.videoWidth,
      };
    }, { src: source });
    if (metadata.ready_state !== 4) throw new Error(`${expected.file}: readyState=${metadata.ready_state}`);
    if (metadata.width !== expected.width || metadata.height !== expected.height) {
      throw new Error(`${expected.file}: unexpected dimensions ${metadata.width}x${metadata.height}`);
    }
    if (metadata.duration < 90 || metadata.duration > 150) {
      throw new Error(`${expected.file}: duration ${metadata.duration} is outside 90-150 seconds`);
    }
    if (metadata.audio_tracks < 1) throw new Error(`${expected.file}: decoded audio track is missing`);
    if (metadata.played_seconds < 0.3) throw new Error(`${expected.file}: playback did not advance`);

    const probes = [];
    for (const time of seekTimes) {
      const probe = await page.evaluate(async ({ targetTime }) => {
        const video = document.querySelector("video");
        const canvas = document.querySelector("canvas");
        const context = canvas.getContext("2d", { willReadFrequently: true });
        await new Promise((resolve) => {
          video.addEventListener("seeked", resolve, { once: true });
          video.currentTime = targetTime;
        });
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let hash = 2166136261;
        let nonblank = 0;
        for (let index = 0; index < pixels.length; index += 16) {
          const value = pixels[index] + pixels[index + 1] + pixels[index + 2];
          if (value > 24) nonblank += 1;
          hash ^= value;
          hash = Math.imul(hash, 16777619);
        }
        return {
          hash: (hash >>> 0).toString(16).padStart(8, "0"),
          nonblank_ratio: nonblank / (pixels.length / 16),
          time: video.currentTime,
        };
      }, { targetTime: time });
      if (probe.nonblank_ratio < 0.9) {
        throw new Error(`${expected.file}: frame at ${time}s is unexpectedly blank`);
      }
      probes.push(probe);
    }
    if (new Set(probes.map((item) => item.hash)).size < 6) {
      throw new Error(`${expected.file}: scene-difference probe found fewer than six unique frames`);
    }
    await page.locator("video").screenshot({
      path: path.join(reportDir, `${path.parse(expected.file).name}-playback.png`),
    });
    results.push({ file: expected.file, metadata, probes });
  }
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}

const report = {
  schema_version: "web-terminal-showcase-playback.v1",
  browser_origin: `http://127.0.0.1:${address.port}`,
  results,
  verdict: "passed",
};
await writeFile(
  path.join(reportDir, "playback-validation.json"),
  `${JSON.stringify(report, null, 2)}\n`,
  "utf8",
);
process.stdout.write(`${JSON.stringify({ videos: results.length, verdict: report.verdict })}\n`);
