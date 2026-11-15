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

The test command runs monitor, paper-accounting and persistence suites. Twenty-one
synthetic-fixture tests passed. These are software checks, not strategy backtests
or paper performance. Source is not deployed. A fresh-observations receipt validates
timestamps only; it does not establish executable liquidity or trading readiness.

## Forward paper accounting components (not yet deployed or wired to runner)

`paper.mjs` applies a supplied order to a supplied Coinbase depth snapshot using
fixed-point decimals. Buys walk asks; sells walk bids; insufficient depth rejects
the entire order. All fills are explicitly simulated, with configured estimated
fees and no latency model. No live order API or keys are present.

`store.mjs` serializes account updates, fsyncs an atomic snapshot and rejects a
second writer. A persistent disk is mandatory in deployment. A crash can leave
`writer.lock`; inspect persisted state before manually removing that lock.
The component is not connected to the Site ledger; never initialize it as a
second account alongside the existing $500 account. Import/reconcile that
account before integration.

Entry checks: supplied frozen qualification evidence, all four gates, net
buy-and-hold pass, 1% trade risk and 2% aggregate stop risk. Inputs are internal
trusted objects, not authenticated evidence: no public order endpoint may accept
client-supplied qualification. The production integration must resolve evidence
from a trusted registry. Market-state integration, cross-asset fresh marking,
persistent drawdown/daily-loss halts, automatic protective exits, product
increments/minimums, fee configuration, runner wiring and dashboard reconciliation
are still pending. This is an accounting component, not an operating desk.

All tests use synthetic fixtures; passing tests are not strategy qualification,
forward-paper performance, continuous heartbeats, or actual order activity.
