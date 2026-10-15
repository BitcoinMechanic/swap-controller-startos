/* Disposable harness adapter, excluded from every packaged Python runtime. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process')
const {execute}=require('./test-migration-actions.cjs')
let failure='action_fixture_refused'
async function main(r, locations={modules:'/opt/actions0100', releases:'/opt/releases', wrapper:'/opt/swap0098/fixture_package_service.py'}) {
  assert.equal(r.disposable_regtest,true)
  assert.ok(['btc','xbt','controller'].includes(r.asset));assert.ok(['a','b'].includes(r.generation))
  const mod=require(locations.modules+'/'+r.asset+'/index.js')
  const runtime=locations.releases+'/'+r.generation
  let backend,command
  function mapped(argv,options) {
    if(r.asset==='controller') {
      assert.deepEqual(argv.slice(0,3),[mod.entrypoint,'--disposable-regtest',mod.directory])
      assert.ok(['init','action','run'].includes(argv[3]));assert.equal(argv.length,4)
      return ['/usr/bin/python3','-I',locations.wrapper,runtime+'/controller','--disposable-regtest',r.directory,argv[3]]
    }
    if(argv[0]==='/usr/local/libexec/swap-node/package_admin.py') {
      assert.deepEqual(argv,mod.adminCommand)
      return [runtime+'/node/package_admin.py','--disposable-regtest','--network-directory',r.directory,'--profile',mod.profile]
    }
    assert.equal(argv[0],'/opt/'+r.asset+'/bin/lightning-cli');assert.equal(argv[2],'--network=regtest')
    assert.ok(['swap-customer-credential','swap-customer-call'].includes(argv[6]))
    return [runtime+'/'+r.asset+'/bin/lightning-cli','--lightning-dir='+path.dirname(r.directory),...argv.slice(2)]
  }
  if(r.lifecycle) return {command:mapped(mod.command)}
  const dispatch=async request=>mod.executeRequest(request,async(argv,options)=>{
    command=mapped(argv,options)
    const p=spawnSync(command[0],command.slice(1),{input:options?.input??'',encoding:'utf8',timeout:120000,maxBuffer:4*1024*1024})
    assert.equal(p.error,undefined);assert.equal(p.stderr,'')
    try { const reply=JSON.parse(p.stdout); backend=r.asset==='controller'?reply.result:reply; if(reply.reason==='service_busy')failure='service_busy' } catch {}
    return {exitCode:p.status,stdout:p.stdout}
  })
  const result=await execute(locations.modules+'/'+r.asset+'/index.js',r.id,r.input,dispatch)
  if(r.id==='migrated-grant-credential') {
    const row=result.output.result.value.find(x=>x.name==='Bounded controller credential')
    assert.equal(row.masked,true);assert.equal(row.copyable,true);backend={credential:JSON.parse(row.value)}
  }
  if(r.id==='migrated-invoice') {
    const row=result.output.result.value.find(x=>x.name==='Payment invoice');assert.equal(row.value,backend.invoice);assert.equal(row.qr,true)
  }
  return {result:backend,ui:result.output}
}
module.exports={main}
if(require.main===module)main(JSON.parse(fs.readFileSync(0,'utf8'))).then(v=>process.stdout.write(JSON.stringify(v)+'\n')).catch(()=>{process.stdout.write(JSON.stringify({reason:failure})+'\n');process.exitCode=1})
