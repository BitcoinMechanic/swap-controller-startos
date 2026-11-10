// Production topology + installed SDK reconciler. Only process execution and discovery I/O are fixtures.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript')
const {StartSdk}=require('@start9labs/start-sdk')
const manifest=require(path.resolve('javascript/index.js')).manifest
const real=StartSdk.of().withManifest(manifest).build(true)
function load(file,mocks) {
 const filename=path.resolve(file),mod=new Module(filename,module);mod.filename=filename;mod.paths=module.paths
 mod.require=n=>n in mocks?mocks[n]:require(n)
 mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename)
 return mod.exports
}
async function main() {
 let discovered={config:{btc:{url:'https://fixture-btc',credentials:{incoming:'SECRET_A'}},xbt:{url:'https://fixture-xbt'}},message:'ready'}
 let child,rootRetries=0,builds=0
 function effects(){return {isInContext:true,onLeaveContext:()=>{},child:()=>effects(),constRetry:()=>{rootRetries++}}}
 const root=effects()
 const sdk={...real,setupMain:fn=>fn}
 const mod=load('startos/live/main.ts',{'../sdk':{sdk},'../utils':{mounts:real.Mounts.of()},
  './actions':{directory:'/data/mainnet-swaps-v1',entrypoint:'/runtime/service',invoke:async()=>({worker_fresh:true})},
  './localNodes':{discover:async(e,reactive)=>{assert.equal(reactive,true);child=e;builds++;return structuredClone(discovered)}}})
 const reconciler=await mod.main({effects:root}),starts=[],stops=[],entries=[]
 assert.equal(typeof reconciler.scheduleRerun,'function')
 reconciler.startEntry=async function(entry,hash){
  starts.push(entry.id);entries.push(entry)
  this.running.set(entry.id,{configHash:hash,requires:entry.requires,subcontainer:null,
    healthDaemon:{term:async()=>stops.push(entry.id)}})
 }
 const running=await reconciler.build()
 const settle=async()=>{for(let n=0;n<20;n++)await new Promise(r=>setImmediate(r));if(reconciler.inFlight)await reconciler.inFlight}
 const update=async value=>{discovered=value;child.constRetry();await settle();assert.equal(rootRetries,0)}
 assert.deepEqual(starts,['migrated-worker','local-node-setup','wallet-discovery'])
 // Identical publication / health-only changes do not touch any daemon.
 await update({...discovered,message:'new health text'});assert.equal(starts.length,3);assert.equal(stops.length,0)
 // Changed grant/credential or endpoint replaces only pairing.
 for(const next of [
  {...discovered,config:{...discovered.config,btc:{url:'https://fixture-btc',credentials:{incoming:'SECRET_B'}}}},
  {...discovered,config:{...discovered.config,btc:{url:'https://new-bridge',credentials:{incoming:'SECRET_B'}}}},
  {config:null,message:'node publication missing'},
  {...discovered,message:'publication restored'},
  {config:null,message:'disabled by user'},
  {...discovered,message:'enabled again'},
 ]) await update(next)
 assert.equal(starts.filter(x=>x==='migrated-worker').length,1)
 assert.equal(starts.filter(x=>x==='wallet-discovery').length,1)
 assert.ok(stops.every(x=>x==='local-node-setup'))
 assert.ok(builds>=8)
 // Private config is written only during pairing process startup, never command args or uses.
 const pairing=entries.find(e=>e.kind==='daemon'&&e.id==='local-node-setup'),writes=[],commands=[]
 assert.ok(!JSON.stringify(pairing.uses).includes('SECRET'))
 const exec=await pairing.exec.fn({writeFile:async(...a)=>writes.push(a),exec:async a=>commands.push(a)},new AbortController().signal)
 assert.equal(writes[0][0],'/tmp/local-nodes.json');assert.ok(writes[0][1].includes('SECRET_A'))
 assert.deepEqual(commands,[['chmod','600','/tmp/local-nodes.json']]);assert.ok(!JSON.stringify(exec).includes('SECRET'))
 await running.term()
 console.log('PASS: real SDK reconciliation preserves worker/discovery through credential, grant, endpoint, absence and enablement changes; only pairing is replaced')
}
main().catch(e=>{console.error(e);process.exitCode=1})
