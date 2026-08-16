/**
 * Web Fetch — sample A3THER JavaScript plugin.
 *
 * Demonstrates the Node bridge: export `capabilities` and an async
 * `handle(capability, params)` that returns a string.
 */
"use strict";

module.exports = {
  capabilities: [
    {
      name: "fetch_page",
      description: "Fetch a URL and return HTTP status, page title and a short text snippet.",
      parameters: {
        type: "object",
        properties: { url: { type: "string", description: "Absolute http(s) URL" } },
        required: ["url"],
      },
    },
  ],

  async handle(capability, params) {
    if (capability !== "fetch_page") {
      throw new Error("unknown capability: " + capability);
    }
    const url = (params && params.url) || "";
    if (!/^https?:\/\//i.test(url)) {
      throw new Error("url must be an absolute http(s) URL");
    }

    const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const html = await response.text();

    const titleMatch = html.match(/<title[^>]*>([^<]*)<\/title>/i);
    const title = titleMatch ? titleMatch[1].trim() : "";
    const snippet = html
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 240);

    return JSON.stringify({ status: response.status, title, snippet });
  },
};
