const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const { manifest, actions } = require('../javascript/index.js')
assert.equal(manifest.id, 'swap-controller')
assert.equal(manifest.version, '0.1.0:0')
assert.deepEqual(Object.keys(manifest.images), ['controller'])
assert.deepEqual(manifest.volumes, ['main'])
assert.deepEqual(Object.keys(actions.actions), ['pair-nodes', 'connection-status'])
assert.equal(manifest.images.controller.source.dockerBuild.dockerfile, 'Dockerfile')
const { Daemons } = require(path.join(path.dirname(require.resolve('@start9labs/start-sdk')), 'mainFn/Daemons.js'))
function load(file, modules) {
 const source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={};vm.runInNewContext(source,{exports,require:n=>{assert.ok(n in modules,n);return modules[n]}});return exports
}
async function run() {
 let reply={paired:false,ready:false}
 const sub={exec:async()=>({exitCode:0,stdout:JSON.stringify(reply)})}
 const sdk={setupMain:fn=>fn,SubContainer:{of:()=>sub},Daemons}
 const main=load('startos/main.ts',{'./sdk':{sdk},'./utils':{mounts:{},rootDir:'/data'}}).main
 const daemons=await main({effects:{}})
 assert.deepEqual(daemons.entries.map(e=>e.id),['monitor'])
 const health=daemons.entries[0].ready.fn
 assert.equal((await health()).result,'loading')
 reply={paired:true,ready:true};assert.equal((await health()).result,'success')
 reply={paired:true,ready:false};assert.equal((await health()).result,'loading')
 const interfaces=load('startos/interfaces.ts',{'./sdk':{sdk:{setupInterfaces:fn=>fn}}})
 assert.equal((await interfaces.setInterfaces()).length,0)
 let options,restore
 const chain={setOptions:o=>{options=o;return chain},setPostRestore:fn=>{restore=fn;return chain}}
 const removed=[]
 const backupSdk={setupBackups:fn=>({configure:fn}),Backups:{ofVolumes:()=>chain},volumes:{main:{subpath:n=>'/volume/'+n}}}
 // setupBackups invokes the provided callback in the real runtime.
 load('startos/backups.ts',{'./sdk':{sdk:{...backupSdk,setupBackups:fn=>{fn();return {}}}},'fs/promises':{unlink:async p=>removed.push(p)}})
 assert.ok(options.exclude.includes('pairing.json'));assert.ok(options.exclude.includes('status.json'))
 await restore();assert.deepEqual(removed,['/volume/pairing.json','/volume/status.json'])
 console.log('Controller bundle, daemon health, no inbound interfaces and restore invalidation OK')
}
run().catch(e=>{console.error(e);process.exit(1)})
