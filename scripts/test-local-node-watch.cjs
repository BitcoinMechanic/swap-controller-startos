const assert = require('node:assert/strict')
const path = require('node:path')
const { nodePresence } = require(path.resolve(process.argv[2]))
const flush = async () => { for(let n=0;n<8;n++) await new Promise(resolve=>setImmediate(resolve)) }
function fixture(initial) {
  let status=initial, callbacks=[], cleanups=[], retries=0
  const effects={isInContext:true,constRetry:()=>{ retries++ },onLeaveContext:fn=>cleanups.push(fn),
    getStatus:async ({packageId,callback})=>{assert.equal(packageId,'c-lightning');if(callback)callbacks.push(callback);return structuredClone(status)}}
  return {effects,get retries(){return retries},get subscriptions(){return callbacks.length},
    async update(next){status=next;const pending=callbacks;callbacks=[];pending.forEach(fn=>fn());await flush()},
    async close(){effects.isInContext=false;cleanups.forEach(fn=>fn());const pending=callbacks;callbacks=[];pending.forEach(fn=>fn());await flush()}}
}
async function main() {
  const initial={health:{trust:{result:'success',message:'Preserving outstanding recovery'}},started:'2026-11-09T17:18:00Z',desired:{main:'running'},error:null}
  const present=fixture(initial)
  try {
    assert.equal(await nodePresence(present.effects,'c-lightning').const(),true)
    for(const next of [
      {...initial,health:{trust:{result:'success',message:'Grants ready'}}},
      {...initial,health:{rpc:{result:'loading',message:'Reconnecting'}}},
      {...initial,started:null,desired:{main:'stopped'}},
      {...initial,started:'2026-11-09T17:32:00Z'},
      {...initial,error:{message:'temporary node issue'}},
    ]) {await present.update(next);assert.equal(present.retries,0)}
    await present.update(null);assert.equal(present.retries,1)
  } finally {await present.close()}
  const missing=fixture(null)
  try {
    assert.equal(await nodePresence(missing.effects,'c-lightning').const(),false)
    await missing.update(null);assert.equal(missing.retries,0)
    await missing.update(initial);assert.equal(missing.retries,1)
  } finally {await missing.close()}
  const once=fixture(initial)
  try {assert.equal(await nodePresence(once.effects,'c-lightning').once(),true);assert.equal(once.subscriptions,0)}
  finally {await once.close()}
  console.log('PASS: real SDK subscriptions ignore health/readiness/restart updates, react to removal/installation, and actions read once')
}
main().catch(error=>{console.error(error);process.exitCode=1})
