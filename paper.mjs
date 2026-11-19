// Forward-only paper accounting. No exchange credentials or real-order path.
const S = 100000000n;
const D = s => { if(typeof s !== 'string' || !/^\d+(\.\d{1,8})?$/.test(s)) throw Error('invalid_decimal'); const [a,b='']=s.split('.');return BigInt(a)*S+BigInt(b.padEnd(8,'0')); };
const F = n => (n<0n?'-':'')+String((n<0n?-n:n)/S)+'.'+String((n<0n?-n:n)%S).padStart(8,'0');
const ceil = (a,b) => (a+b-1n)/b;
const fail = reason => {throw Error(reason);};
export function createPaperAccount() {return {version:1,currency:'USD',simulated:true,cash:F(500n*S),realizedPnl:F(0n),peakEquity:F(500n*S),positions:[],fills:[],lossStreak:0,halted:false};}
function validateBook(book,now) {
 if(book?.source!=='Coinbase'||!Number.isFinite(now)||!Number.isFinite(book.receivedAt)||now-book.receivedAt>30000||book.receivedAt>now+1000)fail('stale_or_wrong_venue');
 if(book.exchangeAt!=null&&(!Number.isFinite(book.exchangeAt)||now-book.exchangeAt>30000||book.exchangeAt>now+1000))fail('stale_exchange');
 const sides={};
 for(const side of ['asks','bids']) {
  if(!Array.isArray(book[side])||!book[side].length)fail('empty_book');
  sides[side]=book[side].map(level=>{if(!Array.isArray(level)||level.length<2)fail('book_shape');const [p,q]=level.map(D);if(p<=0n||q<=0n)fail('book_value');return [p,q];});
  for(let i=1;i<sides[side].length;i++)if(side==='asks'?sides[side][i][0]<=sides[side][i-1][0]:sides[side][i][0]>=sides[side][i-1][0])fail('unsorted_book');
 }
 if(sides.bids[0][0]>=sides.asks[0][0])fail('crossed_book');
 return sides;
}
function qualified(p,order) {
 if(!p||p.source!=='Coinbase'||p.strategyId!==order.strategyId||p.frozen!==true||!p.evidenceId||p.buyHoldAfterCostsPassed!==true)fail('qualification_missing');
 for(const k of ['inSample','permutations','walkForward','walkForwardPermutations']) {
  const g=p.gates?.[k];if(g?.passed!==true)fail('qualification_failed');
  if(k.toLowerCase().includes('permutation')&&(!Number.isInteger(g.trials)||g.trials<1000||!Number.isFinite(g.pValue)||g.pValue<0||g.pValue>=.01))fail('qualification_permutation');
 }
}
export function executePaperOrder({account,order,book,qualification,now}) {
 if(account?.simulated!==true||account.currency!=='USD')fail('paper_account_required');
 if(!order||typeof order.id!=='string'||!order.id||!['buy','sell'].includes(order.side)||!/^\w+-USD$/.test(order.symbol)||!order.reason||!order.strategyId)fail('invalid_order');
 const intent=JSON.stringify([order.symbol,order.side,order.quantity,order.stop??null,order.strategyId,order.reason,order.feeBps]);
 const previous=account.fills.find(f=>f.orderId===order.id);
 if(previous){if(previous.intent!==intent)fail('idempotency_conflict');return {account,fill:previous,duplicate:true};}
 const sides=validateBook(book,now);if(book.symbol!==order.symbol)fail('symbol_mismatch');
 const qty=D(order.quantity),feeBps=D(order.feeBps);if(qty<=0n||feeBps>1000n*S)fail('quantity_or_fee');
 let remaining=qty,notional=0n;const levels=[];
 for(const [price,available] of sides[order.side==='buy'?'asks':'bids']) {
  const quantity=remaining<available?remaining:available;
  if(!quantity)break;
  // Round purchases up and sales down at each level, conservatively.
  notional+=order.side==='buy'?ceil(price*quantity,S):price*quantity/S;
  levels.push({price:F(price),quantity:F(quantity)});remaining-=quantity;
 }
 if(remaining)fail('insufficient_depth');
 const fee=ceil(notional*feeBps,10000n*S),a=structuredClone(account),cash=D(a.cash);
 let position=a.positions.find(p=>p.symbol===order.symbol);
 let pnl=0n;
 if(order.side==='buy') {
  if(a.halted)fail('halted');
  qualified(qualification,order);
  // A cached price without a recent source timestamp cannot justify new risk.
  // Exits validate their own book and remain possible when another feed is stale.
  for (const held of a.positions) {
   if (held.symbol === book.symbol) continue;
   const recent = t => Number.isFinite(t) && now-t >= -1000 && now-t <= 30000;
   if (!recent(held.markReceivedAt) || (held.markExchangeAt != null && !recent(held.markExchangeAt))) fail('stale_position_mark');
  }
  const equity=cash+a.positions.reduce((sum,p)=>sum+D(p.quantity)*(p.symbol===book.symbol?sides.bids[0][0]:D(p.markBid))/S,0n);
  const peak=D(a.peakEquity);
  if(equity*100n<=peak*85n)fail('halt_drawdown');
  if(position)fail('position_already_open');
  const stop=D(order.stop);if(stop<=0n||stop>=sides.bids[0][0])fail('invalid_stop');
  if(notional+fee>cash)fail('insufficient_cash');
  const risk=notional+fee-qty*stop/S+ceil(qty*stop/S*feeBps,10000n*S);
  if(risk*100n>equity)fail('trade_risk');
  const totalRisk=a.positions.reduce((s,p)=>s+D(p.risk),risk);
  if(totalRisk*100n>equity*2n)fail('aggregate_risk');
  a.cash=F(cash-notional-fee);
  a.positions.push({symbol:order.symbol,quantity:F(qty),cost:F(notional+fee),stop:F(stop),risk:F(risk),markBid:F(sides.bids[0][0]),markReceivedAt:book.receivedAt,markExchangeAt:book.exchangeAt??null,strategyId:order.strategyId});
  if(equity>peak)a.peakEquity=F(equity);
 } else {
  if(!position||D(position.quantity)<qty)fail('insufficient_position');
  const oldQty=D(position.quantity),cost=D(position.cost),allocated=qty===oldQty?cost:cost*qty/oldQty;
  pnl=notional-fee-allocated;a.cash=F(cash+notional-fee);
  const oldPnl=a.realizedPnl.startsWith('-')?-D(a.realizedPnl.slice(1)):D(a.realizedPnl);a.realizedPnl=F(oldPnl+pnl);
  if(qty===oldQty)a.positions=a.positions.filter(p=>p!==position);
  else {position.quantity=F(oldQty-qty);position.cost=F(cost-allocated);position.risk=F(D(position.risk)*(oldQty-qty)/oldQty);}
  a.lossStreak=pnl<0n?a.lossStreak+1:pnl>0n?0:a.lossStreak;
  if(a.lossStreak>=3)a.halted=true;
 }
 const fill={orderId:order.id,intent,symbol:order.symbol,side:order.side,quantity:F(qty),notional:F(notional),fee:F(fee),realizedPnl:F(pnl),simulated:true,source:'Coinbase',receivedAt:book.receivedAt,time:now,levels,assumptions:'Snapshot depth fill; fees are configured estimates; latency and subsequent liquidity changes are not modeled.'};
 a.fills.push(fill);return {account:a,fill,duplicate:false};
}
