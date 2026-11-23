import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer as createHttpServer } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { build, createServer } from "vite";
import { expect, test } from "vitest";

import { publicProjectConfig } from "../public-project-config";

test("serves and builds the public API address while keeping project JSON private", async () => {
  const root = await mkdtemp(join(tmpdir(), "agent-py-public-config-"));
  const directory = join(root, "config");
  const baseSecret = "test-only-base-secret-sentinel";
  const overrideSecret = "test-only-override-secret-sentinel";
  const baseAddress = "http://127.0.0.1:8011";
  const overrideAddress = "http://127.0.0.1:8022";
  try {
    await mkdir(directory);
    await writeFile(join(directory, "project.json"), JSON.stringify({
      frontend: { apiBaseUrl: baseAddress },
      llm: { apiKey: baseSecret }
    }));
    expect(publicProjectConfig(directory).define["import.meta.env.PUBLIC_PROJECT_API_BASE_URL"])
      .toBe(JSON.stringify(baseAddress));
    await writeFile(join(directory, "user.project.json"), JSON.stringify({
      frontend: { apiBaseUrl: overrideAddress },
      clsMcpServer: { secretKey: overrideSecret }
    }));
    const browserModule = await readFile(new URL("../src/config.ts", import.meta.url), "utf8");
    await writeFile(join(root, "config.ts"), browserModule);
    await writeFile(join(root, "main.ts"), 'import { API_BASE_URL } from "./config"; console.log(API_BASE_URL);');
    await writeFile(join(root, "index.html"), '<script type="module" src="/main.ts"></script>');

    const config = publicProjectConfig(directory);
    const server = await createServer({
      ...config,
      configFile: false,
      logLevel: "silent",
      root,
      server: { ...config.server, middlewareMode: true, ws: false }
    });
    const listener = createHttpServer(server.middlewares);
    try {
      await new Promise<void>((resolve, reject) => {
        listener.once("error", reject);
        listener.listen(0, "127.0.0.1", resolve);
      });
      const { port } = listener.address() as AddressInfo;
      const url = `http://127.0.0.1:${port}`;
      const responses = await Promise.all([
        fetch(`${url}/config.ts`),
        fetch(`${url}/config/project.json`),
        fetch(`${url}/config/user.project.json`)
      ]);
      expect(responses.map((response) => response.status)).toEqual([200, 403, 403]);
      const bodies = await Promise.all(responses.map((response) => response.text()));
      expect(bodies[0]).toContain(overrideAddress);
      expect(bodies.join("")).not.toContain(baseSecret);
      expect(bodies.join("")).not.toContain(overrideSecret);
    } finally {
      listener.close();
      listener.closeAllConnections();
      await server.close();
    }

    await build({ ...config, configFile: false, logLevel: "silent", root });
    const assetsDir = join(root, "dist", "assets");
    const paths = await readdir(assetsDir);
    const assets = (await Promise.all(paths.map(
      (name) => readFile(join(assetsDir, name), "utf8")
    ))).join("");
    expect(assets).toContain(overrideAddress);
    expect(assets).not.toContain(baseSecret);
    expect(assets).not.toContain(overrideSecret);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}, 20_000);
