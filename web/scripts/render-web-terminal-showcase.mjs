import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "@playwright/test";

const captureDir = path.resolve(process.env.ZF_WEB_TERMINAL_CAPTURE_DIR ?? "");
const outputDir = path.resolve(process.env.ZF_WEB_TERMINAL_SHOWCASE_OUTPUT_DIR ?? "");
const executablePath = process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH;

if (!process.env.ZF_WEB_TERMINAL_CAPTURE_DIR || !process.env.ZF_WEB_TERMINAL_SHOWCASE_OUTPUT_DIR) {
  throw new Error("ZF_WEB_TERMINAL_CAPTURE_DIR and ZF_WEB_TERMINAL_SHOWCASE_OUTPUT_DIR are required");
}

const capture = JSON.parse(
  await readFile(path.join(captureDir, "capture-summary.json"), "utf8"),
);
if (capture.error) throw new Error(`capture is not publishable: ${capture.error}`);
if (capture.assertions?.some((item) => item.status !== "passed")) {
  throw new Error("capture contains a non-passing assertion");
}

await mkdir(path.join(outputDir, "scenes"), { recursive: true });

const offlineSeconds = (
  (Date.parse(capture.offline_response_observed_at) - Date.parse(capture.offline_started_at)) / 1000
).toFixed(1);
const sessionSuffix = String(capture.primary_session_id).slice(-8);
const totalTokens = new Intl.NumberFormat("en-US").format(capture.usage.total_tokens);
const estimatedCost = `$${Number(capture.usage.cost_usd).toFixed(4)} partial estimate`;

const scenes = [
  {
    id: "00-entry",
    eyebrow: "PROJECT-NATIVE ENTRY",
    title: "浏览器里的真实 Coding Agent CLI",
    subtitle: "这是造父 Web Terminal。用户打开任意已授权项目，就能从右上角进入真实 Coding Agent 终端。可用的 Codex 和 Claude Code 不写死在终端配置里，而是由项目初始化后的后端策略自动派生。",
    tts: "这是造斧 Web Terminal。用户打开任意已授权项目，就能从右上角进入真实 Coding Agent 终端。可用的 Codex 和 Claude Code 不写死在终端配置里，而是由项目初始化后的后端策略自动派生。",
    frames: ["00-project-entry.png"],
    layout: "single",
    badges: ["任意受信 Project", "Codex / Claude 自动派生", "长期 Access Token"],
  },
  {
    id: "01-real-pty",
    eyebrow: "REAL HERDR PTY",
    title: "原生 TUI，不是模拟终端",
    subtitle: "点击新建，造父通过 Herdr 在项目根目录启动真实 PTY 和 Codex TUI。它保留 ANSI、滚动、复制、快捷键和原生交互；多个标签页还能独立命名，并分别关联 Session、Provider 与用量。",
    tts: "点击新建，造斧通过 Herdr 在项目根目录启动真实 PTY 和 Codex TUI。它保留 ANSI、滚动、复制、快捷键和原生交互；多个标签页还能独立命名，并分别关联 Session、Provider 与用量。",
    frames: ["02-real-codex-terminal-a.png", "01-provider-menu.png"],
    layout: "main-inset",
    badges: ["ZAOFU_WEB_READY", "Project cwd", "Multi-tab"],
  },
  {
    id: "02-observe",
    eyebrow: "CROSS-TERMINAL OBSERVE",
    title: "两台终端，共享同一个 PTY",
    subtitle: "同一个项目可以从另一台电脑或浏览器登录。终端 A 保持 Control，终端 B 选择 Observe 后，只读看到完全相同的屏幕；多人可以同时旁观，却不会误输入或改变终端尺寸。",
    tts: "同一个项目可以从另一台电脑或浏览器登录。终端 A 保持 Control，终端 B 选择 Observe 后，只读看到完全相同的屏幕；多人可以同时旁观，却不会误输入或改变终端尺寸。",
    frames: ["03-observe-terminal-a.png", "03-observe-terminal-b.png"],
    layout: "split",
    labels: ["终端 A · Control", "终端 B · Observe"],
    badges: ["一个 Session", `…${sessionSuffix}`, "多人只读"],
  },
  {
    id: "03-takeover",
    eyebrow: "EXPLICIT TAKEOVER",
    title: "明确交接控制权，不静默抢占",
    subtitle: "需要交接时，终端 B 显式选择 Take over control。系统替换唯一控制者，终端 A 自动失去写权限并转为观察；新的控制端继续输入，两个窗口都收到同一个真实 Codex 响应。",
    tts: "需要交接时，终端 B 显式选择 Take over control。系统替换唯一控制者，终端 A 自动失去写权限并转为观察；新的控制端继续输入，两个窗口都收到同一个真实 Codex 响应。",
    frames: ["04-takeover-terminal-a.png", "04-takeover-terminal-b.png"],
    layout: "split",
    labels: ["终端 A · 已转 Observe", "终端 B · Take over 后 Control"],
    badges: ["显式接管", "唯一 Controller", "CROSS_TERMINAL_TAKEOVER_OK"],
  },
  {
    id: "04-offline",
    eyebrow: "OFFLINE CONTINUATION",
    title: "浏览器离线，Agent 仍在服务端运行",
    subtitle: `更酷的是离线恢复。终端 B 提交一个延迟任务后断网，浏览器连接已经消失，但 Herdr、PTY 和 Coding Agent 仍在服务端继续运行。${offlineSeconds} 秒后，观察端收到了离线期间生成的 OFFLINE_RECOVERY_OK。`,
    tts: `更酷的是离线恢复。终端 B 提交一个延迟任务后断网，浏览器连接已经消失，但 Herdr、PTY 和 Coding Agent 仍在服务端继续运行。${offlineSeconds} 秒后，观察端收到了离线期间生成的 OFFLINE_RECOVERY_OK。`,
    frames: ["05-controller-offline-terminal-b.png", "05-offline-response-terminal-a.png"],
    layout: "offline",
    labels: ["控制端 B · navigator.onLine = false", "观察端 A · 收到离线期间输出"],
    badges: [`离线窗口 ${offlineSeconds}s`, "PTY 未停止", "OFFLINE_RECOVERY_OK"],
  },
  {
    id: "05-recovered",
    eyebrow: "FRESH-CONTEXT RECOVERY",
    title: "换设备重新登录，继续同一会话",
    subtitle: "网络恢复，或者换一台全新的设备重新登录，只要项目和访问令牌相同，就能重新连接同一个 Session，恢复完整历史并继续控制。全屏、可调 Dock、主题、多标签，以及按标签统计的 Token 和 Cost，一起构成稳定、可追踪的 Web Terminal 体验。",
    tts: "网络恢复，或者换一台全新的设备重新登录，只要项目和访问令牌相同，就能重新连接同一个 Session，恢复完整历史并继续控制。全屏、可调 Dock、主题、多标签，以及按标签统计的 Token 和 Cost，一起构成稳定、可追踪的 Web Terminal 体验。",
    frames: ["06-recovered-terminal-c.png", "07-multi-tab-dock-light.png", "08-recovered-usage.png"],
    layout: "recovery",
    labels: ["全新浏览器 · same Session", "Dock + Theme + Multi-tab", "Agents · per-tab usage"],
    badges: ["历史恢复", `${totalTokens} tokens`, estimatedCost],
  },
];

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function imageData(file) {
  const body = await readFile(path.join(captureDir, "frames", file));
  return `data:image/png;base64,${body.toString("base64")}`;
}

function imageCard(src, label = "", className = "") {
  return `<figure class="screen ${className}">
    ${label ? `<figcaption>${escapeHtml(label)}</figcaption>` : ""}
    <img alt="" src="${src}">
  </figure>`;
}

async function sceneBody(scene) {
  const images = await Promise.all(scene.frames.map(imageData));
  if (scene.layout === "single") return imageCard(images[0], "真实 ZaoFu Dashboard", "hero");
  if (scene.layout === "main-inset") {
    return `<div class="main-inset">
      ${imageCard(images[0], "终端 A · 真实 Codex TUI", "hero")}
      ${imageCard(images[1], "Project 派生 Provider", "inset")}
    </div>`;
  }
  if (scene.layout === "split" || scene.layout === "offline") {
    return `<div class="split ${scene.layout}">
      ${imageCard(images[0], scene.labels[0], "half")}
      <div class="flow-arrow" aria-hidden="true">→</div>
      ${imageCard(images[1], scene.labels[1], "half")}
    </div>`;
  }
  return `<div class="recovery-grid">
    ${imageCard(images[0], scene.labels[0], "recovery-main")}
    <div class="recovery-side">
      ${imageCard(images[1], scene.labels[1], "recovery-small")}
      ${imageCard(images[2], scene.labels[2], "recovery-small")}
    </div>
  </div>`;
}

function styleSheet() {
  return `<style>
    *{box-sizing:border-box}html,body{margin:0;width:3840px;height:2160px;overflow:hidden}
    body{background:#eaf1f5;color:#102d41;font-family:"Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif}
    .stage{position:relative;width:100%;height:100%;padding:78px 110px 314px;background:
      radial-gradient(circle at 91% 6%,rgba(37,99,235,.13),transparent 29%),
      linear-gradient(145deg,#f6fafc 0%,#e8f0f4 70%,#dfeaf0 100%)}
    header{height:194px;display:flex;align-items:flex-start;justify-content:space-between}
    .brand{display:flex;align-items:center;gap:22px;font-size:34px;font-weight:760;letter-spacing:-.02em}
    .brand-mark{width:54px;height:54px;border-radius:14px;background:#102d41;color:#fff;display:grid;place-items:center;font:700 28px/1 ui-monospace}
    .scene-index{font:650 26px/1.2 ui-monospace;color:#476273;letter-spacing:.08em;padding-top:14px}
    .eyebrow{font:700 25px/1.2 ui-monospace;color:#2563eb;letter-spacing:.13em;margin:0 0 20px}
    h1{font-size:70px;line-height:1.05;letter-spacing:-.045em;margin:0;max-width:2600px}
    .content{position:relative;height:1470px;margin-top:12px}
    .screen{position:relative;margin:0;border:2px solid rgba(16,45,65,.18);border-radius:28px;background:#111317;overflow:hidden;box-shadow:0 26px 70px rgba(16,45,65,.16)}
    .screen img{display:block;width:100%;height:100%;object-fit:contain;background:#111317}
    figcaption{position:absolute;z-index:2;left:28px;top:24px;padding:14px 24px;border-radius:999px;background:rgba(246,250,252,.94);color:#102d41;font-size:25px;font-weight:720;border:1px solid rgba(16,45,65,.18)}
    .hero{width:100%;height:100%}.main-inset{position:relative;width:100%;height:100%}.main-inset .hero{width:100%;height:100%}
    .main-inset .inset{position:absolute;right:44px;top:44px;width:1180px;height:666px;border:4px solid #f6fafc}
    .split{display:grid;grid-template-columns:1fr 1fr;gap:92px;align-items:center;width:100%;height:100%;position:relative}
    .split .half{height:1030px}.flow-arrow{position:absolute;z-index:4;left:50%;top:50%;transform:translate(-50%,-50%);width:76px;height:76px;border-radius:50%;display:grid;place-items:center;background:#2563eb;color:white;font-size:42px;font-weight:800;border:8px solid #edf4f7}
    .offline .half:first-child{filter:saturate(.55) brightness(.88)}.offline .flow-arrow{background:#f59e0b}
    .recovery-grid{display:grid;grid-template-columns:2.05fr 1fr;gap:48px;width:100%;height:100%}
    .recovery-main{height:100%}.recovery-side{display:grid;grid-template-rows:1fr 1fr;gap:48px}.recovery-small{height:711px}
    .badges{position:absolute;left:110px;right:110px;bottom:258px;display:flex;justify-content:center;gap:22px;z-index:8}
    .badge{padding:13px 24px;border-radius:999px;background:#fff;border:1px solid rgba(16,45,65,.18);font:700 24px/1.2 ui-monospace;color:#284a60}
    footer{position:absolute;left:0;right:0;bottom:0;height:238px;padding:42px 250px 38px;background:#f9fbfc;border-top:2px solid rgba(16,45,65,.11);display:flex;align-items:center;justify-content:center}
    footer p{margin:0;max-width:3300px;text-align:center;color:#102d41;font-size:43px;line-height:1.5;font-weight:560;letter-spacing:-.015em;text-shadow:none}
  </style>`;
}

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 3840, height: 2160 } });
for (const [index, scene] of scenes.entries()) {
  const body = await sceneBody(scene);
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">${styleSheet()}</head>
    <body><section class="stage">
      <header><div><div class="brand"><span class="brand-mark">ZF</span>ZaoFu · Web Terminal</div></div><div class="scene-index">${String(index + 1).padStart(2, "0")} / ${String(scenes.length).padStart(2, "0")}</div></header>
      <p class="eyebrow">${escapeHtml(scene.eyebrow)}</p><h1>${escapeHtml(scene.title)}</h1>
      <main class="content">${body}</main>
      <div class="badges">${scene.badges.map((badge) => `<span class="badge">${escapeHtml(badge)}</span>`).join("")}</div>
      <footer><p>${escapeHtml(scene.subtitle)}</p></footer>
    </section></body></html>`;
  await page.setContent(html, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    animations: "disabled",
    path: path.join(outputDir, "scenes", `${scene.id}.png`),
  });
}
await browser.close();

const manifest = {
  schema_version: "web-terminal-showcase-scenes.v1",
  source_commit: capture.source_commit,
  capture_summary: path.join(captureDir, "capture-summary.json"),
  width: 3840,
  height: 2160,
  scenes: scenes.map(({ id, eyebrow, title, subtitle, tts, frames }) => ({
    id,
    eyebrow,
    title,
    subtitle,
    tts,
    evidence_frames: frames,
  })),
};
await writeFile(
  path.join(outputDir, "scene-manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
  "utf8",
);
await writeFile(
  path.join(outputDir, "narration-zh.txt"),
  `${scenes.map((scene, index) => `${index + 1}. ${scene.subtitle}`).join("\n\n")}\n`,
  "utf8",
);
await writeFile(
  path.join(outputDir, "narration-tts.txt"),
  `${scenes.map((scene) => scene.tts).join("\n")}\n`,
  "utf8",
);
process.stdout.write(`${JSON.stringify({ scene_count: scenes.length, output_dir: outputDir })}\n`);
