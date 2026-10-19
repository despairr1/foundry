import * as os from "os";
import * as path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    exclude: ["demo-output/**", "node_modules/**", "dist/**"],
    testTimeout: 60000,
    env: {
      // Keep test runs out of the project's .foundry.log and off the TTY dashboard.
      FOUNDRY_LOG_PATH: path.join(os.tmpdir(), "foundry-test.log"),
      FOUNDRY_DASHBOARD: "false"
    }
  }
});
