/**
 * LAN production: rebuild, free port 3000, then start next start.
 * Avoids stale next start serving outdated /_next/static chunks (CSS 404).
 */
import { execSync, spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { getLanPort } = require("./detect-lan.mjs");

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WEB_DIR = path.join(ROOT, "packages", "web");

const port = Number(process.env.LAN_PORT || getLanPort() || 3000);

function killPortWindows(p) {
  try {
    const out = execSync(`netstat -ano | findstr :${p} | findstr LISTENING`, {
      encoding: "utf8",
      stdio: ["pipe", "pipe", "ignore"],
    });
    for (const line of out.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const pid = trimmed.split(/\s+/).pop();
      if (pid && /^\d+$/.test(pid) && pid !== "0") {
        try {
          execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
          console.log(`[lan:prod] Stopped previous process on port ${p} (PID ${pid})`);
        } catch {
          /* already exited */
        }
      }
    }
  } catch {
    /* nothing listening */
  }
}

console.log("[lan:prod] Building production bundle…");
execSync("npm run build", { stdio: "inherit", cwd: WEB_DIR });

if (process.platform === "win32") {
  killPortWindows(port);
}

console.log(`[lan:prod] Starting next start on 0.0.0.0:${port}…`);
const child = spawn("npx", ["next", "start", "--hostname", "0.0.0.0", "--port", String(port)], {
  stdio: "inherit",
  shell: true,
  cwd: WEB_DIR,
});

child.on("exit", (code) => process.exit(code ?? 0));
