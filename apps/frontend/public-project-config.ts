import { readFileSync } from "node:fs";
import { join } from "node:path";

export function publicProjectConfig(directory: string) {
  const base = readConfig(directory, "project.json");
  const overrides = readConfig(directory, "user.project.json", true);
  const baseAddress = readApiBaseUrl(base);
  const overrideAddress = readApiBaseUrl(overrides);
  const apiBaseUrl = overrideAddress === undefined ? baseAddress : overrideAddress;
  if (typeof apiBaseUrl !== "string") {
    throw new Error("Project configuration must define frontend.apiBaseUrl as a string.");
  }

  return {
    define: { "import.meta.env.PUBLIC_PROJECT_API_BASE_URL": JSON.stringify(apiBaseUrl) },
    server: {
      fs: {
        deny: [
          ".env",
          ".env.*",
          "*.{crt,pem}",
          "**/.git/**",
          "**/config/project.json",
          "**/config/user.project.json"
        ]
      }
    }
  };
}

function readApiBaseUrl(config: Record<string, unknown>): unknown {
  const frontend = config.frontend;
  if (frontend === undefined) return undefined;
  if (!isObject(frontend)) {
    throw new Error("Project configuration frontend must be an object.");
  }
  return frontend.apiBaseUrl;
}

function readConfig(
  directory: string,
  filename: string,
  optional = false
): Record<string, unknown> {
  let source: string;
  try {
    source = readFileSync(join(directory, filename), "utf8");
  } catch (error: unknown) {
    if (optional && isObject(error) && error.code === "ENOENT") return {};
    throw new Error(`Unable to read local project configuration ${filename}.`);
  }
  try {
    const parsed: unknown = JSON.parse(source);
    if (isObject(parsed)) return parsed;
  } catch {
    // Keep JSON content out of startup errors.
  }
  throw new Error(`Local project configuration ${filename} must be a JSON object.`);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
