import fs from 'node:fs';
import http from 'node:http';
import crypto from 'node:crypto';
import path from 'node:path';
import { URL } from 'node:url';
import { fileURLToPath } from 'node:url';

const SAFE_METHODS = new Set(['GET', 'HEAD']);
const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE', 'TRACE']);
const FETCH_SITE = new Set(['same-origin', 'same-site', 'none']);
const SESSION_COOKIE = 'office_preview_session';
const CSRF_COOKIE = 'office_preview_csrf';
const DEFAULT_HOST = '127.0.0.1:5173';

function bool(value) {
  return value === true;
}

function token() {
  return crypto.randomBytes(32).toString('base64url');
}

function parseCookie(header = '') {
  const result = {};
  for (const part of header.split(';')) {
    const index = part.indexOf('=');
    if (index > 0) result[part.slice(0, index).trim()] = part.slice(index + 1).trim();
  }
  return result;
}

function validManifest(manifest) {
  if (!manifest || (!Array.isArray(manifest.files) && (!manifest.files || typeof manifest.files !== 'object'))) {
    throw new Error('manifest.files must be an array or object');
  }
  const files = new Set();
  const entries = Array.isArray(manifest.files) ? manifest.files : Object.keys(manifest.files);
  for (const item of entries) {
    const value = typeof item === 'string' ? item : item?.path;
    if (typeof value !== 'string' || !value.startsWith('/') || value.includes('\\') || value.includes('..')) {
      throw new Error('manifest contains an invalid file path');
    }
    files.add(value);
  }
  return files;
}

export function loadPublishedManifest(manifestPath) {
  return validManifest(JSON.parse(fs.readFileSync(manifestPath, 'utf8')));
}

function normalizePath(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl, 'http://proxy.invalid');
  } catch {
    return { ok: false, reason: 'invalid_url' };
  }
  if (parsed.search) return { ok: false, reason: 'query_not_allowed' };
  const rawPath = parsed.pathname;
  if (/%(?:2f|2e|5c)/i.test(rawPath) || rawPath.includes('\\') || rawPath.includes('//')) {
    return { ok: false, reason: 'path_encoding_not_allowed' };
  }
  let decoded;
  try { decoded = decodeURIComponent(rawPath); } catch { return { ok: false, reason: 'invalid_url_encoding' }; }
  if (decoded.includes('\\') || decoded.includes('//')) return { ok: false, reason: 'path_separator_not_allowed' };
  const parts = decoded.split('/').filter((part, index, all) => index > 0 && index < all.length - 1 ? part : part !== '');
  if (parts.some((part) => part === '.' || part === '..')) return { ok: false, reason: 'path_segments_not_allowed' };
  return { ok: true, path: decoded === '/' ? '/' : decoded };
}

function originAllowed(req, config) {
  const origin = req.headers.origin;
  if (!origin) return true;
  return origin === config.registeredOrigin;
}

function hostAllowed(req, config) {
  return typeof req.headers.host === 'string' && req.headers.host === config.registeredHost;
}

function fetchMetadataAllowed(req) {
  const value = req.headers['sec-fetch-site'];
  return !value || FETCH_SITE.has(value);
}

function csrfAllowed(req, cookies, csrfCookie, expectedToken) {
  const header = req.headers['x-csrf-token'];
  return typeof header === 'string'
    && header.length > 0
    && header === cookies[csrfCookie]
    && header === expectedToken;
}

export function createPolicy(config = {}) {
  const manifest = config.manifest instanceof Set ? config.manifest : validManifest(config.manifest);
  const registeredHost = config.registeredHost || DEFAULT_HOST;
  const registeredOrigin = config.registeredOrigin || `https://${registeredHost}`;
  const entryScheme = config.entryScheme || 'https';
  if (!['http', 'https'].includes(entryScheme)) throw new Error('entryScheme must be http or https');
  if (!config.upstreamHost || !/^127\.0\.0\.1$|^::1$/.test(config.upstreamHost)) {
    throw new Error('upstreamHost must be loopback');
  }
  if (!Number.isInteger(config.upstreamPort) || config.upstreamPort < 1 || config.upstreamPort > 65535) {
    throw new Error('upstreamPort must be a valid port');
  }
  if (config.rootAliases && (Object.keys(config.rootAliases).length !== 1 || config.rootAliases['/'] !== '/index.html' || !manifest.has('/index.html'))) {
    throw new Error('rootAliases must be empty or {"/":"/index.html"}');
  }
  const rootAliases = config.rootAliases || { '/': '/index.html' };
  return {
    manifest,
    registeredHost,
    registeredOrigin,
    entryScheme,
    upstreamHost: config.upstreamHost,
    upstreamPort: config.upstreamPort,
    screenHealthOnly: config.screenHealthOnly === true,
    rootAliases,
    sessionCookie: config.sessionCookie || SESSION_COOKIE,
    csrfCookie: config.csrfCookie || CSRF_COOKIE,
  };
}

export function authorizeRequest(req, policy, trusted = {}) {
  const method = String(req.method || '').toUpperCase();
  if (!SAFE_METHODS.has(method) && !WRITE_METHODS.has(method)) return { allow: false, status: 405, reason: 'method_not_allowed' };
  const normalized = normalizePath(req.url || '/');
  if (!normalized.ok) return { allow: false, status: 400, reason: normalized.reason };
  const forwarded = req.headers['x-forwarded-host'] || req.headers['x-forwarded-proto'] || req.headers['x-forwarded-for'];
  if (forwarded) {
    const trustedServe = trusted.forwardedProxy === true
      && req.headers['x-forwarded-host'] === policy.registeredHost
      && req.headers['x-forwarded-proto'] === policy.entryScheme
      && typeof req.headers['x-forwarded-for'] === 'string';
    if (!trustedServe) return { allow: false, status: 400, reason: 'forwarded_headers_not_allowed' };
  }
  if (!hostAllowed(req, policy)) return { allow: false, status: 421, reason: 'invalid_host' };
  if (!originAllowed(req, policy)) return { allow: false, status: 403, reason: 'invalid_origin' };
  if (!fetchMetadataAllowed(req)) return { allow: false, status: 403, reason: 'cross_site_request' };
  const pathName = policy.rootAliases[normalized.path] || normalized.path;
  if (pathName === '/auth/login') {
    if (method !== 'POST') return { allow: false, status: 405, reason: 'method_not_allowed' };
  } else if (pathName.startsWith('/api/screens/')) {
    if (!(policy.screenHealthOnly && pathName === '/api/screens/health' && method === 'GET')) {
      return { allow: false, status: 404, reason: 'screen_route_disabled' };
    }
  } else if (WRITE_METHODS.has(method)) {
    return { allow: false, status: 403, reason: 'write_disabled' };
  }
  if (pathName !== '/auth/login' && !bool(trusted.sessionValid)) return { allow: false, status: 401, reason: 'session_required' };
  if (pathName !== '/auth/login' && WRITE_METHODS.has(method)
    && !csrfAllowed(req, parseCookie(req.headers.cookie), policy.csrfCookie, trusted.csrfToken)) {
    return { allow: false, status: 403, reason: 'csrf_required' };
  }
  if (pathName !== '/auth/login' && !policy.manifest.has(pathName) && pathName !== '/api/screens/health') {
    return { allow: false, status: 404, reason: 'path_not_published' };
  }
  return { allow: true, path: pathName, method };
}

function cookie(name, value, maxAge, secure) {
  return `${name}=${value}; Path=/${secure ? '; Secure' : ''}; HttpOnly; SameSite=Strict${maxAge === undefined ? '' : `; Max-Age=${maxAge}`}`;
}

export function createRemoteProxyServer(options = {}) {
  const policy = createPolicy(options);
  const sessions = new Map();
  const login = options.login || (() => false);
  const server = http.createServer((req, res) => {
    const cookies = parseCookie(req.headers.cookie);
    const session = sessions.get(cookies[policy.sessionCookie]);
    const remoteAddress = req.socket.remoteAddress;
    const trusted = {
      sessionValid: Boolean(session && session.expiresAt > Date.now()),
      csrfToken: session?.csrf,
      forwardedProxy: remoteAddress === '127.0.0.1' || remoteAddress === '::1' || remoteAddress === '::ffff:127.0.0.1',
    };
    const decision = authorizeRequest(req, policy, trusted);
    if (!decision.allow) {
      res.statusCode = decision.status;
      res.setHeader('content-type', 'application/json; charset=utf-8');
      res.end(JSON.stringify({ error: decision.reason }));
      return;
    }
    if (decision.path === '/auth/login') {
      if (req.method !== 'POST') { res.statusCode = 405; res.end(); return; }
      let body = '';
      req.on('data', (chunk) => { body += chunk; if (body.length > 8192) req.destroy(); });
      req.on('end', () => {
        let credentials;
        try { credentials = JSON.parse(body || '{}'); } catch { credentials = {}; }
        if (!login(credentials)) { res.statusCode = 401; res.end(JSON.stringify({ error: 'invalid_credentials' })); return; }
        const sid = token();
        const csrf = token();
        sessions.set(sid, { csrf, expiresAt: Date.now() + (options.sessionTtlMs || 30 * 60 * 1000) });
        res.statusCode = 204;
        const secure = policy.entryScheme === 'https';
        res.setHeader('set-cookie', [cookie(policy.sessionCookie, sid, undefined, secure), cookie(policy.csrfCookie, csrf, undefined, secure)]);
        res.end();
      });
      return;
    }
    const upstreamPath = decision.path;
    const upstream = http.request({
      host: policy.upstreamHost,
      port: policy.upstreamPort,
      method: req.method,
      path: upstreamPath,
      headers: {
        host: `${policy.upstreamHost}:${policy.upstreamPort}`,
        origin: `http://${policy.upstreamHost}:${policy.upstreamPort}`,
        accept: req.headers.accept || '*/*',
        'x-forwarded-proto': policy.entryScheme,
        ...(req.headers['last-event-id'] ? { 'last-event-id': req.headers['last-event-id'] } : {}),
      },
    }, (upstreamRes) => {
      const headers = { ...upstreamRes.headers };
      if (String(headers['content-type'] || '').startsWith('text/event-stream')) {
        headers['cache-control'] = 'no-cache';
        headers['x-accel-buffering'] = 'no';
        headers.connection = 'keep-alive';
      }
      res.writeHead(upstreamRes.statusCode || 502, headers);
      upstreamRes.pipe(res);
    });
    upstream.setTimeout(120000);
    upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
    req.pipe(upstream);
  });
  return { server, policy, sessions };
}

export function startFromEnv() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const manifestPath = process.env.OFFICE_PROXY_MANIFEST || path.join(root, 'contributions', 'mobile-remote-access', 'evidence', 'published-manifest.json');
  const manifest = loadPublishedManifest(manifestPath);
  const upstreamAddress = process.env.OFFICE_PREVIEW_UPSTREAM;
  if (!upstreamAddress) throw new Error('OFFICE_PREVIEW_UPSTREAM must be explicitly configured');
  const [upstreamHost, upstreamPortText] = upstreamAddress.split(':');
  const registeredHost = process.env.OFFICE_PROXY_HOST;
  const registeredOrigin = process.env.OFFICE_PROXY_ORIGIN;
  const entryScheme = process.env.OFFICE_PROXY_SCHEME;
  if (!registeredHost || !registeredOrigin || !entryScheme) {
    throw new Error('OFFICE_PROXY_HOST, OFFICE_PROXY_ORIGIN and OFFICE_PROXY_SCHEME must be explicitly configured');
  }
  const proxy = createRemoteProxyServer({
    manifest,
    upstreamHost,
    upstreamPort: Number(upstreamPortText),
    registeredHost,
    registeredOrigin,
    entryScheme,
    screenHealthOnly: process.env.OFFICE_PROXY_SCREEN_HEALTH === '1',
    login: (credentials) => Boolean(process.env.OFFICE_PROXY_USER && process.env.OFFICE_PROXY_PASSWORD && credentials.username === process.env.OFFICE_PROXY_USER && credentials.password === process.env.OFFICE_PROXY_PASSWORD),
  });
  const bindHost = process.env.OFFICE_PROXY_BIND;
  const bindPort = Number(process.env.OFFICE_PROXY_PORT);
  if (!bindHost || !Number.isInteger(bindPort) || bindPort < 1 || bindPort > 65535) {
    throw new Error('OFFICE_PROXY_BIND and OFFICE_PROXY_PORT must be explicitly configured');
  }
  proxy.server.listen(bindPort, bindHost, () => console.log(`remote proxy listening on ${bindHost}:${bindPort}`));
  return proxy.server;
}

if (process.argv[1] && path.basename(process.argv[1]) === 'remote-proxy.mjs') startFromEnv();
