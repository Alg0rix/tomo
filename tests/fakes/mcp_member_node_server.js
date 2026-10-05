#!/usr/bin/env node
/* Minimal deterministic MCP fixture for Member-sandbox tests (no deps).
 * Newline-delimited JSON-RPC on stdio: initialize, tools/list, tools/call.
 * Single tool: nodeecho(text) -> "node-echo: <text>". Runs identically on
 * the host (Admin discovery via the real SDK) and inside the restricted
 * tomo:sandbox container (Member calls).
 */
'use strict';

const readline = require('readline');

const rl = readline.createInterface({ input: process.stdin, terminal: false });

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function result(id, payload) {
  send({ jsonrpc: '2.0', id: id, result: payload });
}

function failure(id, message) {
  send({ jsonrpc: '2.0', id: id, error: { code: -32000, message: String(message).slice(0, 300) } });
}

rl.on('line', (raw) => {
  const line = raw.trim();
  if (!line) return;
  let message;
  try {
    message = JSON.parse(line);
  } catch (err) {
    return;
  }
  if (typeof message !== 'object' || message === null) return;
  const method = message.method;
  const id = message.id;
  const params = message.params || {};
  if (method === 'initialize') {
    result(id, {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'tomo-member-node-fixture', version: '1.0' },
    });
  } else if (method === 'tools/list') {
    result(id, { tools: [{ name: 'nodeecho', description: 'Echo text back', inputSchema: { type: 'object' } }] });
  } else if (method === 'resources/list') {
    result(id, { resources: [] });
  } else if (method === 'resources/templates/list') {
    result(id, { resourceTemplates: [] });
  } else if (method === 'prompts/list') {
    result(id, { prompts: [] });
  } else if (method === 'tools/call') {
    try {
      const args = params.arguments || {};
      if (params.name !== 'nodeecho') throw new Error('unknown tool: ' + params.name);
      result(id, { content: [{ type: 'text', text: 'node-echo: ' + String(args.text || '') }] });
    } catch (err) {
      failure(id, err && err.message ? err.message : err);
    }
  } else if (method === 'ping') {
    result(id, {});
  } else if (id !== undefined && id !== null && typeof method === 'string' && !method.startsWith('notifications/')) {
    failure(id, 'unknown method: ' + method);
  }
});
