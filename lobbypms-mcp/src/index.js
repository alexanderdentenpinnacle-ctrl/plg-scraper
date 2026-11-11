// LobbyPMS MCP server on Cloudflare Workers (MCP Streamable HTTP, JSON responses, stateless).
// Endpoint: POST /mcp (also accepted on /). Secrets: LOBBY_API_TOKEN, optional MCP_AUTH_TOKEN.
import { makeClient } from './lobby.js';
import { TOOLS } from './tools.js';

const PROTOCOL_VERSION = '2025-06-18';
const SERVER_INFO = { name: 'lobbypms-mcp', version: '2.0.0' };

const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
});

export function listTools(env) {
  return TOOLS.map((t) => ({
    name: t.name,
    description: t.write && env.WRITE_ENABLED !== 'true' ? `[WRITES DISABLED] ${t.description}` : t.description,
    inputSchema: t.inputSchema,
    annotations: { readOnlyHint: !t.write, destructiveHint: !!t.write },
  }));
}

export async function callTool(name, args, env, fetchImpl) {
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) return { isError: true, content: [{ type: 'text', text: `Unknown tool: ${name}` }] };
  try {
    const client = makeClient(env, fetchImpl);
    const result = await tool.handler(args || {}, { env, client });
    return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
  } catch (e) {
    return { isError: true, content: [{ type: 'text', text: e.message || String(e) }] };
  }
}

async function handleRpc(msg, env, fetchImpl) {
  const { id, method, params } = msg;
  const ok = (result) => ({ jsonrpc: '2.0', id, result });
  switch (method) {
    case 'initialize':
      return ok({ protocolVersion: params?.protocolVersion || PROTOCOL_VERSION, capabilities: { tools: {} }, serverInfo: SERVER_INFO });
    case 'ping':
      return ok({});
    case 'tools/list':
      return ok({ tools: listTools(env) });
    case 'tools/call':
      return ok(await callTool(params?.name, params?.arguments, env, fetchImpl));
    default:
      if (id === undefined) return null; // notification (e.g. notifications/initialized)
      return { jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${method}` } };
  }
}

export default {
  async fetch(request, env, _ctx, fetchImpl = fetch) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, Mcp-Session-Id, Mcp-Protocol-Version',
      } });
    }
    if (request.method === 'GET' && url.pathname === '/health') {
      return json({ ok: true, ...SERVER_INFO, writes_enabled: env.WRITE_ENABLED === 'true' });
    }
    if (!['/', '/mcp'].includes(url.pathname)) return json({ error: 'Not found' }, 404);
    if (request.method === 'GET') return new Response('Method Not Allowed', { status: 405 }); // no SSE stream
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

    if (env.MCP_AUTH_TOKEN) {
      const auth = request.headers.get('Authorization') || '';
      const key = url.searchParams.get('key');
      if (auth !== `Bearer ${env.MCP_AUTH_TOKEN}` && key !== env.MCP_AUTH_TOKEN) return json({ error: 'Unauthorized' }, 401);
    }

    let body;
    try { body = await request.json(); } catch {
      return json({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }, 400);
    }
    if (Array.isArray(body)) {
      const out = (await Promise.all(body.map((m) => handleRpc(m, env, fetchImpl)))).filter(Boolean);
      return out.length ? json(out) : new Response(null, { status: 202 });
    }
    const out = await handleRpc(body, env, fetchImpl);
    return out ? json(out) : new Response(null, { status: 202 });
  },
};
