import {mkdir,open,readFile,rename,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createPaperAccount} from './paper.mjs';
// One local writer; deploy only with a persistent volume. A crash leaves a lock
// for operator inspection, never an automatic stale-lock takeover.
export async function openPaperStore(directory) {
 if(!directory)throw Error('persistent_directory_required');
 const dir=resolve(directory),path=join(dir,'account.json'),lock=join(dir,'writer.lock');
 await mkdir(dir,{recursive:true,mode:0o700});
 try{await mkdir(lock);}catch{throw Error('paper_store_locked');}
 let account,queue=Promise.resolve(),closed=false,poisoned=false,closing;
 async function save(value){
  if(value?.simulated!==true||value.currency!=='USD'||!Array.isArray(value.fills)||!Array.isArray(value.positions)||!/^\d+\.\d{8}$/.test(value.cash))throw Error('invalid_account');
  const temporary=join(dir,'account.pending');
  const f=await open(temporary,'w',0o600);
  try{await f.writeFile(JSON.stringify(value));await f.sync();}finally{await f.close();}
  await rename(temporary,path);
  const d=await open(dir,'r');try{await d.sync();}finally{await d.close();}
 }
 try{
  try{account=JSON.parse(await readFile(path,'utf8'));if(account?.simulated!==true||account.currency!=='USD'||!Array.isArray(account.fills)||!Array.isArray(account.positions)||!/^\d+\.\d{8}$/.test(account.cash))throw Error('invalid_account');}
  catch(e){if(e.code!=='ENOENT')throw e;account=createPaperAccount();await save(account);}
 }catch(e){await rm(lock,{recursive:true});throw e;}
 return {
  async read(){await queue;if(closed||poisoned)throw Error('store_unavailable');return structuredClone(account);},
  transact(fn){
   if(closed||poisoned)return Promise.reject(Error('store_unavailable'));
   const operation=queue.then(async()=>{if(poisoned)throw Error('store_unavailable');const next=await fn(structuredClone(account));try{await save(next);}catch(e){poisoned=true;throw e;}account=structuredClone(next);return structuredClone(account);});
   queue=operation.catch(()=>{});return operation;
  },
  close(){
   // Release this writer's lock once, even if shutdown is requested again
   // after a replacement writer has acquired it.
   if(closing)return closing;
   closed=true;
   closing=(async()=>{await queue;await rm(lock,{recursive:true});})();
   return closing;
  }
 };
}
