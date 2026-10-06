const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
for (const name of ['recovery.py','recovery_inspection.py','recovery_workflow.py','recovery_actions.py','quote_workflow.py','quote_actions.py', 'reverse_quote_workflow.py','readiness.py','gate_observation.py','live_policy.py','quote_policy.py','live_preflight.py','preflight_actions.py','deadline_boundary.py','deadline_recovery.py']) {
 assert.ok(fs.readFileSync('Dockerfile','utf8').includes('assets/'+name))
 assert.ok(fs.readFileSync('.dockerignore','utf8').includes('!assets/'+name))
}
const { manifest, actions } = require('../javascript/index.js')
assert.equal(manifest.id, 'swap-controller')
assert.equal(manifest.version, '0.1.0:11')
assert.deepEqual(Object.keys(manifest.images), ['controller'])
assert.deepEqual(manifest.volumes, ['main'])
assert.deepEqual(Object.keys(actions.actions), ['pair-nodes', 'connection-status', 'worker-status', 'recovery-status', 'confirm-recovery-revocation', 'recover-existing-swap', 'quote-status', 'review-swap-quote', 'prepare-swap-quote', 'approve-swap-quote', 'prepare-reverse-swap-quote', 'live-readiness', 'pair-btc-gate-observation', 'pair-xbt-gate-observation', 'review-live-policy', 'inspect-live-forward', 'inspect-live-reverse'])
assert.equal(manifest.images.controller.source.dockerBuild.dockerfile, 'Dockerfile')
const { Daemons } = require(path.join(path.dirname(require.resolve('@start9labs/start-sdk')), 'mainFn/Daemons.js'))
function load(file, modules) {
 const source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={};vm.runInNewContext(source,{exports,require:n=>{assert.ok(n in modules,n);return modules[n]}});return exports
}
async function run() {
 await require('./test-recovery-actions.cjs')()
 await require('./test-quote-actions.cjs')()
 await require('./test-readiness-action.cjs')()
 await require('./test-live-policy-action.cjs')()
 await require('./test-preflight-actions.cjs')()
 await require('./test-gate-observation-action.cjs')()
 let reply={paired:false,ready:false}
 const sub={exec:async()=>({exitCode:0,stdout:JSON.stringify(reply)})}
 const sdk={setupMain:fn=>fn,SubContainer:{of:()=>sub},Daemons}
 const main=load('startos/main.ts',{'./sdk':{sdk},'./utils':{mounts:{},rootDir:'/data'}}).main
 const daemons=await main({effects:{}})
 assert.deepEqual(daemons.entries.map(e=>e.id),['monitor','worker'])
 const health=daemons.entries[0].ready.fn
 assert.equal((await health()).result,'loading')
 reply={paired:true,ready:true};assert.equal((await health()).result,'success')
 reply={paired:true,ready:false};assert.equal((await health()).result,'loading')
 const interfaces=load('startos/interfaces.ts',{'./sdk':{sdk:{setupInterfaces:fn=>fn}}})
 assert.equal((await interfaces.setInterfaces()).length,0)
 const workerHealth=daemons.entries[1].ready.fn
 reply={worker_fresh:true,restored_block:false,backup_paused:false,jobs:[]}
 assert.equal((await workerHealth()).result,'success')
 reply.restored_block=true;assert.equal((await workerHealth()).result,'success')
 reply.worker_fresh=false;assert.equal((await workerHealth()).result,'loading')
 assert.equal(JSON.stringify(daemons.entries).includes('BTC_XBT_DISPOSABLE_CONTAINER'),false)
 let options,restore,preBackup,postBackup
 const chain={setOptions:o=>{options=o;return chain},setPostRestore:fn=>{restore=fn;return chain},setPreBackup:fn=>{preBackup=fn;return chain},setPostBackup:fn=>{postBackup=fn;return chain}}
 const removed=[],hooks=[]
 const backupSdk={SubContainer:{withTemp:async(e,i,m,n,fn)=>fn({exec:async cmd=>{hooks.push(cmd.at(-1));return {exitCode:0}}})},setupBackups:fn=>({configure:fn}),Backups:{ofVolumes:()=>chain},volumes:{main:{subpath:n=>'/volume/'+n}}}
 // setupBackups invokes the provided callback in the real runtime.
 load('startos/backups.ts',{'./sdk':{sdk:{...backupSdk,setupBackups:fn=>{fn();return {}}}},'fs/promises':{unlink:async p=>removed.push(p)},'./utils':{mounts:{},rootDir:'/data'}})
 assert.ok(options.exclude.includes('btc-gate-observation.json'));
 assert.ok(options.exclude.includes('xbt-gate-observation.json'));
 assert.ok(options.exclude.includes('pairing.json'));assert.ok(options.exclude.includes('status.json'))
 assert.ok(options.exclude.includes('execution/regtest-quote-nodes.json'));
 assert.ok(options.exclude.includes('execution/backup-paused.json'));assert.ok(options.exclude.includes('execution/jobs'))
 await preBackup({});await postBackup({});await restore({});assert.deepEqual(hooks,['backup-begin','backup-end','restored'])
 assert.deepEqual(removed,['/volume/pairing.json','/volume/btc-gate-observation.json','/volume/xbt-gate-observation.json','/volume/status.json','/volume/execution/regtest-quote-nodes.json'])
 console.log('Controller bundle, daemon health, no inbound interfaces and restore invalidation OK')
}
run().catch(e=>{console.error(e);process.exit(1)})
