import { setTimeout as delay } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';

const ORIGIN = 'https://nightshift-crypto-lab-zaleski.jetbowl10.chatgpt.site';
const SOURCE = 'Coinbase Exchange public REST depth';

export function configuration(env = process.env) {
  const token = env.NIGHTSHIFT_SITE_TOKEN?.trim();
  if (!token || /[\r\n]/.test(token)) throw Error('token_missing_or_invalid');
  const origin = env.NIGHTSHIFT_SITE_URL || ORIGIN;
  if (origin !== ORIGIN) throw Error('origin_invalid');
  const intervalMs = Number(env.MONITOR_INTERVAL_MS || 15000);
  if (!Number.isSafeInteger(intervalMs) || intervalMs < 15000 || intervalMs > 300000) throw Error('interval_invalid');
  return { origin, token, intervalMs, timeoutMs: 12000 };
}

async function boundedJson(response) {
  if (!response.body) throw Error('missing_body');
  const reader = response.body.getReader();
  const chunks = []; let length = 0;
  try {
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 2000000) throw Error('oversized_body');
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
}

// This requests a server-side observation. It has no order path or exchange credentials.
export async function cycle(config, {fetcher = fetch, signal} = {}) {
  const receipt = { time: new Date().toISOString(), process: 'sentinel-monitor', execution_enabled: false };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.timeoutMs);
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, {once: true});
  try {
    const response = await fetcher(`${config.origin}/api/monitor`, {
      method: 'POST', redirect: 'error', signal: controller.signal,
      headers: {'content-type': 'application/json', 'OAI-Sites-Authorization': `Bearer ${config.token}`},
      body: '{}'
    });
    if (!response.ok) {
      await response.body?.cancel();
      return {...receipt, status: 'blocked', reason: 'site_http_error', http_status: response.status};
    }
    const data = await boundedJson(response);
    if (data?.execution_enabled !== false || data.market?.execution_enabled !== false || data.market?.source !== SOURCE ||
        !Array.isArray(data.market.books) || !data.market.books.length || data.market.books.some(book => book.source !== SOURCE || !/^[A-Z0-9]+-USD$/.test(book.symbol))) {
      return {...receipt, status: 'blocked', reason: 'invalid_monitor_response'};
    }
    const now = Date.now();
    const recent = value => { const age=now-Date.parse(value); return Number.isFinite(age) && age>=-1000 && age<=30000; };
    const freshCount = data.market.books.filter(book => book.fresh === true && !book.error && recent(book.received_at) &&
      (book.exchange_timestamp == null || recent(book.exchange_timestamp))).length;
    return {...receipt, status: 'observed', feed_status: freshCount === data.market.books.length ? 'fresh_observations' : 'degraded',
      book_count: data.market.books.length, fresh_count: freshCount};
  } catch {
    // Never log upstream bodies, URLs, error messages, or credentials.
    return {...receipt, status: 'blocked', reason: 'request_failed'};
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

export async function run(config, {signal, fetcher = fetch, log = value => console.log(JSON.stringify(value)), sleep = (ms, signal) => delay(ms, undefined, {signal})} = {}) {
  while (!signal?.aborted) {
    log(await cycle(config, {fetcher, signal}));
    if (signal?.aborted) break;
    // Completion-based delay guarantees at most one request in flight per process.
    try { await sleep(config.intervalMs, signal); }
    catch { if (signal?.aborted) break; throw Error('sleep_failed'); }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const controller = new AbortController();
  process.once('SIGTERM', () => controller.abort());
  process.once('SIGINT', () => controller.abort());
  try { await run(configuration(), {signal: controller.signal}); }
  catch { console.error(JSON.stringify({status:'startup_failed', execution_enabled:false, reason:'configuration_or_runner_error'})); process.exitCode = 1; }
}
