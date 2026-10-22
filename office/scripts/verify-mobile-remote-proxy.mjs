import assert from 'node:assert/strict';
import http from 'node:http';
import { createPolicy, authorizeRequest, createRemoteProxyServer } from '../server/remote-proxy.mjs';

const manifest = new Set(['/index.html', '/assets/app.js']);
const policy = createPolicy({ manifest, upstreamHost: '127.0.0.1', upstreamPort: 1, registeredHost: 'proxy.test', registeredOrigin: 'https://proxy.test', entryScheme: 'https' });
const base = { method: 'GET', url: '/index.html', headers: { host: 'proxy.test', 'sec-fetch-site': 'same-origin' } };
const cases = [
  ['published static with session', { ...base }, { sessionValid: true }, true],
  ['missing session', { ...base }, {}, false],
  ['unknown route', { ...base, url: '/src/main.js' }, { sessionValid: true }, false],
  ['query rejected', { ...base, url: '/index.html?x=1' }, { sessionValid: true }, false],
  ['wrong host', { ...base, headers: { ...base.headers, host: 'evil.test' } }, { sessionValid: true }, false],
  ['wrong origin', { ...base, headers: { ...base.headers, origin: 'https://evil.test' } }, { sessionValid: true }, false],
  ['client forwarded headers rejected', { ...base, headers: { ...base.headers, 'x-forwarded-host': 'proxy.test' } }, { sessionValid: true }, false],
  ['trusted local serve headers accepted', { ...base, headers: { ...base.headers, 'x-forwarded-host': 'proxy.test', 'x-forwarded-proto': 'https', 'x-forwarded-for': '100.118.227.31' } }, { sessionValid: true, forwardedProxy: true }, true],
  ['cross site rejected', { ...base, headers: { ...base.headers, 'sec-fetch-site': 'cross-site' } }, { sessionValid: true }, false],
  ['write denied', { ...base, method: 'POST' }, { sessionValid: true }, false],
  ['screen disabled', { ...base, url: '/api/screens/health' }, { sessionValid: true }, false],
];
for (const [name, req, trusted, expected] of cases) assert.equal(authorizeRequest(req, policy, trusted).allow, expected, name);
const healthPolicy = createPolicy({ manifest, upstreamHost: '127.0.0.1', upstreamPort: 1, registeredHost: 'proxy.test', registeredOrigin: 'https://proxy.test', screenHealthOnly: true });
assert.equal(authorizeRequest({ ...base, url: '/api/screens/health' }, healthPolicy, { sessionValid: true }).allow, true, 'health opt-in');
assert.throws(() => createPolicy({ manifest, upstreamHost: '192.168.1.2', upstreamPort: 1 }), /loopback/);
assert.throws(() => createPolicy({ manifest, upstreamHost: '127.0.0.1', upstreamPort: 1, rootAliases: { '/x': '/index.html' } }), /rootAliases/);

const upstream = http.createServer((req, res) => { res.setHeader('x-upstream-host', req.headers.host); res.end('upstream-ok'); });
await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve));
const upstreamPort = upstream.address().port;
const proxy = createRemoteProxyServer({ manifest, upstreamHost: '127.0.0.1', upstreamPort, registeredHost: 'proxy.test', registeredOrigin: 'https://proxy.test', login: ({ username, password }) => username === 'offline' && password === 'offline' });
await new Promise((resolve) => proxy.server.listen(0, '127.0.0.1', resolve));
const port = proxy.server.address().port;
const request = (options, body = '') => new Promise((resolve, reject) => { const req = http.request({ ...options, port, host: '127.0.0.1' }, (res) => { let data = ''; res.on('data', (chunk) => { data += chunk; }); res.on('end', () => resolve({ res, data })); }); req.on('error', reject); req.end(body); });
const login = await request({ method: 'POST', path: '/auth/login', headers: { host: 'proxy.test', origin: 'https://proxy.test', 'sec-fetch-site': 'same-origin', 'content-type': 'application/json' } }, JSON.stringify({ username: 'offline', password: 'offline' }));
assert.equal(login.res.statusCode, 204);
const cookies = login.res.headers['set-cookie'].map((value) => value.split(';', 1)[0]).join('; ');
const fetched = await request({ method: 'GET', path: '/index.html', headers: { host: 'proxy.test', cookie: cookies, 'sec-fetch-site': 'same-origin' } });
assert.equal(fetched.res.statusCode, 200);
assert.equal(fetched.data, 'upstream-ok');
proxy.server.close();
upstream.close();
console.log(`mobile remote proxy verification: ${cases.length + 3}/14 passed`);
