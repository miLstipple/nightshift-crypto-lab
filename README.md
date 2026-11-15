# Nightshift continuous monitor

This Node 22+ service repeatedly requests the existing private Nightshift dashboard's Coinbase-only `/api/monitor` endpoint. It is a monitoring runner, **not a trading engine or four autonomous AI agents**. No order placement, strategy qualification, historical backtest, leverage, or real-money access exists here.

Run `npm test`, then set `NIGHTSHIFT_SITE_TOKEN` using the hosting provider's secret environment configuration and run `npm start`. No dependencies are installed. Never put the token in source control, logs, build arguments, or an issue.

The origin is fixed to `https://nightshift-crypto-lab-zaleski.jetbowl10.chatgpt.site`. Optional `MONITOR_INTERVAL_MS` is 15000 by default (minimum 15000). Each completed request is followed by this delay; requests never overlap within one process. Run one instance. Requests abort after 12 seconds, reject redirects, and fail closed on invalid responses. SIGTERM/SIGINT cancel requests and sleep.

The dashboard persists the observation and event using its existing storage. Process receipts in hosting logs are observations of this runner only. A running process does not prove market freshness, strategy readiness, profitability, or execution. Exchange receipt timestamps are not exchange-event timestamps.

## Authentication limitation

`NIGHTSHIFT_SITE_TOKEN` is the Sites service-access bearer. Its longevity and refresh mechanism must be verified before claiming unattended operation. An expired or revoked token produces a blocked receipt; it must be rotated securely. This runner intentionally cannot obtain or refresh credentials by itself. A dedicated durable, least-privilege ingestion credential is preferable for long-term use. Do not transfer a bearer into another service without authorization for that credential destination.

## Remaining gates

Coinbase strategies still need approved validation on untouched data, fees and bid/ask fill modeling, permutation and walk-forward tests, risk controls, durable idempotent order/fill accounting, and execution verification. The connected QuantPilot currently lacks Coinbase support; historical Binance results are historical proxies only. This runner does not resolve that blocker. Paper and real-money trading remain disabled.

## Verification and deployment status

The monitoring-only test command explicitly runs runner.test.mjs. Eight synthetic-fixture tests cover origin restrictions, redacted errors, timeouts, shutdown, non-overlap, response limits, mixed venues and independent timestamp freshness. These are software checks, not strategy backtests or paper performance. Source is prepared for deployment but no continuous service is claimed. A fresh-observations receipt validates timestamps only; it does not establish executable liquidity or trading readiness.

The unfinished paper-execution test draft is excluded from this release because its implementation does not exist. No fill adapter is shipped. Deployment still requires a host, verified unattended credential access, a single-worker configuration, and live process verification.
