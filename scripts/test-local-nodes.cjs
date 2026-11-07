const assert = require('node:assert/strict')
const path = require('node:path')
async function run() {
  const node = require(path.resolve(process.argv[2]))
  const controller = require(path.resolve(process.argv[3]))
  const now = 1000, profile = 'xbt-privkeyio-v1'
  const status = { blocked: false, reviews: ['incoming','outgoing'].map(kind => ({
    confirmation: (kind === 'incoming' ? 'a' : 'b').repeat(64), kind, phase:'saved', current:true, paused:false,
    expires_at:now+100, profile,
  })) }
  let minted=[]
  const credential = async id => {
    minted.push(id)
    return {credential:{rune:'PRIVATE_RUNE_'.repeat(10),grant:{kind:id[0]==='a'?'incoming':'outgoing',chain:{profile,environment:'mainnet'}}}}
  }
  const publication = await node.publication(status,profile,credential,now)
  assert.equal(minted.length,2)
  assert.deepEqual(controller.parsePublication(JSON.stringify(publication),profile),publication)
  for(const change of [s=>s.blocked=true,s=>s.reviews.push(s.reviews[0]),s=>s.reviews[0].profile='btc-cln-v1']) {
    const s=structuredClone(status);change(s)
    await assert.rejects(node.publication(s,profile,credential,now))
  }
  for(const change of [s=>s.reviews[0].paused=true,s=>s.reviews[0].current=false,s=>s.reviews[0].phase='review',s=>s.reviews[0].expires_at=now]) {
    const s=structuredClone(status);change(s);minted=[]
    const p=await node.publication(s,profile,credential,now)
    assert.deepEqual(Object.keys(p.credentials),['outgoing']);assert.equal(minted.length,1)
    assert.throws(()=>controller.parsePublication(JSON.stringify(p),profile))
  }
  for(const change of [p=>p.profile='btc-cln-v1',p=>p.internalPort=0,p=>p.credentials.incoming.grant.chain.environment='regtest',p=>p.masterRune='PRIVATE',p=>p.credentials.incoming.rune='bad']) {
    const p=structuredClone(publication);change(p)
    assert.throws(()=>controller.parsePublication(JSON.stringify(p),profile))
  }
  console.log('PASS: approved grants only, no authority expansion, bounded private publication, strict discovery contract')
}
run().catch(()=>{ console.error('Local node contract test failed');process.exitCode=1 })
