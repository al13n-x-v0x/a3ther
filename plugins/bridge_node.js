/**
 * A3THER JavaScript Plugin Bridge
 * ================================
 *
 * Spawned by the plugin manager for every JS plugin:
 *
 *     node bridge_node.js <plugin-dir>
 *
 * Speaks the same newline-delimited JSON-RPC 2.0 protocol the MCP host
 * uses on stdio, so the Python side drives it through StdioTransport:
 *
 *     -> {"jsonrpc":"2.0","id":1,"method":"capabilities"}
 *     <- {"jsonrpc":"2.0","id":1,"result":{"capabilities":[...]}}
 *
 *     -> {"jsonrpc":"2.0","id":2,"method":"call",
 *         "params":{"capability":"fetch_page","arguments":{"url":"https://..."}}}
 *     <- {"jsonrpc":"2.0","id":2,"result":"..."}
 *
 * A plugin is a folder containing a3ther-plugin.json with "entry":
 * "plugin.js", and the entry module must export:
 *
 *     module.exports = {
 *       capabilities: [{ name, description, parameters }],
 *       handle: async (capability, params) => string
 *     };
 */
"use strict";

const fs = require("fs");
const path = require("path");

const pluginDir = process.argv[2];
if (!pluginDir) {
  process.stderr.write("usage: node bridge_node.js <plugin-dir>\n");
  process.exit(1);
}

let plugin = null;
let loadError = null;

function resolveEntry(dir) {
  const manifestPath = path.join(dir, "a3ther-plugin.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  return path.join(dir, manifest.entry || "plugin.js");
}

try {
  plugin = require(resolveEntry(pluginDir));
} catch (err) {
  loadError = String((err && err.stack) || err);
}

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function readCapabilities() {
  if (loadError) return { error: loadError };
  if (plugin && Array.isArray(plugin.capabilities)) {
    return { capabilities: plugin.capabilities };
  }
  return { capabilities: [] };
}

process.stdin.setEncoding("utf8");
let buffer = "";

process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;

    let message;
    try {
      message = JSON.parse(line);
    } catch (_) {
      continue;
    }
    if (!message || typeof message.method !== "string") continue;

    (async () => {
      let result;
      try {
        if (message.method === "capabilities") {
          result = readCapabilities();
        } else if (message.method === "call") {
          if (loadError) throw new Error(loadError);
          if (!plugin || typeof plugin.handle !== "function") {
            throw new Error("plugin has no handle() function");
          }
          const { capability, arguments: args } = message.params || {};
          result = await plugin.handle(capability, args || {});
          if (result === undefined) result = null;
        } else if (message.method === "ping") {
          result = "pong";
        } else {
          throw new Error("unknown method: " + message.method);
        }
        send({ jsonrpc: "2.0", id: message.id, result });
      } catch (err) {
        send({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32000, message: String((err && err.stack) || err) },
        });
      }
    })();
  }
});
