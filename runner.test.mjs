import test from 'node:test';
import assert from 'node:assert/strict';
import { configuration, cycle, run } from './runner.mjs';

const env = { NIGHTSHIFT_SITE_TOKEN: 'test-only-placeholder' };
const good = () => ({ execution_enabled: false, market: { source: 'Coinbase Exchange public REST depth', execution_enabled: false, books: [{ symbol: 'BTC-USD', source: 'Coinbase Exchange public REST depth', fresh: true, received_at: new Date().toISOString(), exchange_timestamp: new Date().toISOString() }] } });
const response = (data = good()) => new Response(JSON.stringify(data));
test('configuration rejects missing token, foreign origins, and invalid intervals', () => {
  assert.throws(() => configuration({}), /token_missing/);
  assert.throws(() => configuration({ ...env, NIGHTSHIFT_SITE_URL: 'https://evil.example' }), /origin_invalid/);
  assert.throws(() => configuration({ ...env, NIGHTSHIFT_SITE_URL: 'https://nightshift-crypto-lab-zaleski.jetbowl10.chatgpt.site/other' }), /origin_invalid/);
  assert.throws(() => configuration({ ...env, MONITOR_INTERVAL_MS: '0' }), /interval_invalid/);
});
test('cycle sends only a monitor request, forbids redirects, and returns sanitized receipt', async () => {
  const receipt = await cycle(configuration(env), { fetcher: async (url, options) => {
    assert.equal(new URL(url).pathname, '/api/monitor');
    assert.equal(options.method, 'POST'); assert.equal(options.redirect, 'error');
    assert.equal(options.headers['OAI-Sites-Authorization'], 'Bearer test-only-placeholder');
    return response();
  }});
  assert.equal(receipt.status, 'observed'); assert.equal(receipt.execution_enabled, false);
  assert.equal(receipt.book_count, 1); assert.equal(JSON.stringify(receipt).includes(env.NIGHTSHIFT_SITE_TOKEN), false);
});
test('HTTP, JSON and unsafe responses fail closed without echoing upstream data', async () => {
  for (const value of [new Response('secret', {status: 401}), new Response('secret'), response({}), response({...good(), execution_enabled: true}), response({...good(), market: {...good().market, source: 'Binance'}})]) {
    const receipt = await cycle(configuration(env), {fetcher: async () => value});
    assert.equal(receipt.status, 'blocked'); assert.equal(receipt.execution_enabled, false);
    assert.equal(JSON.stringify(receipt).includes('secret'), false);
  }
});
test('network timeout cancels fetch and returns blocked status', async () => {
  const config = {...configuration(env), timeoutMs: 10};
  const receipt = await cycle(config, {fetcher: (_url, {signal}) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), {once: true}))});
  assert.equal(receipt.status, 'blocked'); assert.equal(receipt.reason, 'request_failed');
});
test('oversized responses and mixed-venue books are blocked', async () => {
  const oversized = await cycle(configuration(env), {fetcher: async () => new Response('x'.repeat(2000001))});
  assert.equal(oversized.status, 'blocked');
  const data = good(); data.market.books.push({symbol: 'ETH-USD', source: 'another venue', fresh: true});
  const mixed = await cycle(configuration(env), {fetcher: async () => response(data)});
  assert.equal(mixed.status, 'blocked'); assert.equal(mixed.reason, 'invalid_monitor_response');
});
test('shutdown interrupts an in-flight request', async () => {
  const controller = new AbortController();
  const receipt = await cycle(configuration(env), {signal: controller.signal, fetcher: (_url, {signal}) => new Promise((_resolve, reject) => { signal.addEventListener('abort', () => reject(signal.reason), {once: true}); controller.abort(); })});
  assert.equal(receipt.status, 'blocked');
});
test('runner awaits completion and sleep before the next cycle', async () => {
  const controller = new AbortController(); let active = 0; let maximum = 0; let calls = 0; let sleeps = 0; const logs = [];
  await run(configuration(env), {signal: controller.signal, log: x => logs.push(x), sleep: async () => {sleeps++;}, fetcher: async () => {
    active++; maximum = Math.max(maximum, active); calls++;
    await new Promise(resolve => setImmediate(resolve)); active--;
    if(calls === 3) controller.abort();
    return response();
  }});
  assert.equal(maximum, 1); assert.equal(calls, 3); assert.equal(sleeps, 2);
  assert.equal(logs.length, 3);
});

test('fresh flags cannot override stale or future timestamps', async () => {
  for (const patch of [{received_at:'2000-01-01T00:00:00Z'}, {exchange_timestamp:'2000-01-01T00:00:00Z'}, {received_at:new Date(Date.now()+60000).toISOString()}, {received_at:undefined}]) {
    const data=good(); Object.assign(data.market.books[0],patch);
    const result=await cycle(configuration(env),{fetcher:async()=>response(data)});
    assert.equal(result.fresh_count,0); assert.equal(result.feed_status,'degraded');
  }
});
