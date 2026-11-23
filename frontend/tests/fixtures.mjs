import { expect, test as base } from "@playwright/test";

export { expect };
export const test = base.extend({
  baseURL: async ({}, use) => {
    const port = process.env.GOLDSCOUT_SYNTHETIC_PORT;
    if (!/^[1-9]\d{0,4}$/.test(port ?? "") || Number(port) > 65535)
      throw new Error("The isolated synthetic browser server is unavailable.");
    await use(`http://127.0.0.1:${port}`);
  },
});
