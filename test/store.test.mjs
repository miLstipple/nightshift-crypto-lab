import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {openPaperStore} from '../store.mjs';
test('repeated close cannot release a replacement writer lock',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'paper-test-'));
 let replacement;
 try{
  const first=await openPaperStore(dir);
  await first.close();
  replacement=await openPaperStore(dir);
  await first.close();
  await assert.rejects(openPaperStore(dir),/paper_store_locked/);
  await assert.rejects(first.transact(a=>a),/store_unavailable/);
 }finally{if(replacement)await replacement.close();await rm(dir,{recursive:true,force:true});}
});
test('persists concurrent transactions and survives restart',async()=>{const dir=await mkdtemp(join(tmpdir(),'paper-test-'));try{let s=await openPaperStore(dir);await Promise.all(Array.from({length:20},()=>s.transact(a=>{a.revision=(a.revision||0)+1;return a;})));assert.equal((await s.read()).revision,20);await s.close();s=await openPaperStore(dir);assert.equal((await s.read()).revision,20);assert.equal((await s.read()).cash,'500.00000000');await s.close();}finally{await rm(dir,{recursive:true,force:true});}});
test('rejects a second writer and corrupt state without resetting funds',async()=>{const dir=await mkdtemp(join(tmpdir(),'paper-test-'));try{const s=await openPaperStore(dir);await assert.rejects(openPaperStore(dir),/locked/);await s.close();await writeFile(join(dir,'account.json'),'broken');await assert.rejects(openPaperStore(dir));}finally{await rm(dir,{recursive:true,force:true});}});
test('failed transaction cannot change persisted balance',async()=>{const dir=await mkdtemp(join(tmpdir(),'paper-test-'));try{const s=await openPaperStore(dir);await assert.rejects(s.transact(a=>{a.cash='0.00000000';throw Error('rejected');}));assert.equal((await s.read()).cash,'500.00000000');await s.close();}finally{await rm(dir,{recursive:true,force:true});}});
