const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript')
function load(file,mocks={}) {
 const filename=path.resolve(file),mod=new Module(filename,module);mod.filename=filename;mod.paths=module.paths
 mod.require=n=>n in mocks?mocks[n]:require(n)
 mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename)
 return mod.exports
}
async function main() {
 const bundled=require(path.resolve(process.argv[2]||'javascript/index.js'))
 assert.equal(bundled.manifest.version,'#privkeyio:0.1.0:18');assert.equal(bundled.manifest.id,'swap-controller')
 assert.deepEqual(bundled.manifest.volumes,['main','discovery']);assert.deepEqual(Object.keys(bundled.manifest.images),['controller'])
 const ids=['local-node-setup','migrated-initialize','legacy-history',...['forward','reverse'].flatMap(d=>['pair','new','confirm','cancel'].map(a=>'migrated-'+a+'-'+d)),
  'migrated-status','migrated-history','migrated-invoice','migrated-settings','wallet-discovery']
 assert.deepEqual(Object.keys(bundled.actions.actions),ids)
 const effects={eventId:'transition-test'};let response,calls=0
 const {legacyProgram}=load('startos/live/legacyProgram.ts')
 assert.ok(legacyProgram.includes('legacy_authority_imported=False'))
 const sdk={volumes:{main:{subpath:n=>'/synthetic/'+n}},Mounts:{of:()=>({mountVolume:o=>{
  assert.deepEqual(o,{volumeId:'main',subpath:null,mountpoint:'/data',readonly:true});return {readonly:true}
 }})},SubContainer:{withTemp:async(e,image,m,name,fn)=>{
  assert.equal(e,effects);assert.deepEqual(image,{imageId:'controller'});assert.equal(m.readonly,true)
  return fn({exec:async argv=>{assert.deepEqual(argv,['python3','-I','-B','-c',legacyProgram,'/data','admit']);calls++;return response}})
 }}}
 const legacy=load('startos/live/legacy.ts',{'../sdk':{sdk},'./legacyProgram':{legacyProgram}})
 response={exitCode:0,stdout:JSON.stringify({admitted:true,legacy_records_preserved:true,legacy_authority_imported:false})}
 await legacy.inspectLegacy(effects);assert.equal(calls,1)
 for(const bad of [{exitCode:1,stdout:'PRIVATE'}, {exitCode:0,stdout:'{}'},
   {exitCode:0,stdout:JSON.stringify({admitted:false,reason:'PRIVATE=value'})}]) {
  response=bad;await assert.rejects(()=>legacy.inspectLegacy(effects),e=>!e.message.includes('PRIVATE')&&e.message.includes('preserved'))
 }
 let admitted=0,reject=false
 const current=load('startos/versions/current.ts',{'../live/legacy':{inspectLegacy:async()=>{admitted++;if(reject)throw Error('refused')}}}).current
 const previous=load('startos/versions/v_privkeyio0.1.0_0.ts').v_privkeyio_0_1_0_0
 const priorOne=load('startos/versions/v_privkeyio0.1.0_1.ts',{'../live/legacy':{inspectLegacy:async()=>assert.fail('historical admission selected')},'../live/legacyProgramV1':load('startos/live/legacyProgramV1.ts')}).v_privkeyio_0_1_0_1
 const priorTwo=load('startos/versions/v_privkeyio0.1.0_2.ts',{'../live/legacy':{inspectLegacy:async()=>assert.fail('historical admission selected')},'../live/legacyProgramV2':load('startos/live/legacyProgramV2.ts')}).v_privkeyio_0_1_0_2
 const priorThree=load('startos/versions/v_privkeyio0.1.0_3.ts',{'../live/legacy':{inspectLegacy:async()=>{throw Error('historical admission selected')}}}).v_privkeyio_0_1_0_3
 const priorFour=load('startos/versions/v_privkeyio0.1.0_4.ts',{'../live/legacy':{inspectLegacy:async()=>{throw Error('historical admission selected')}}}).v_privkeyio_0_1_0_4
 const priorFive=load('startos/versions/v_privkeyio_0_1_0_5.ts',{'../live/legacy':{inspectLegacy:async()=>{admitted++;if(reject)throw Error('refused')}}}).v_privkeyio_0_1_0_5
 const graph=load('startos/versions/index.ts',{'./v_privkeyio_0_1_0_5':{v_privkeyio_0_1_0_5:priorFive},'./current':{current},'./v_privkeyio0.1.0_4':{v_privkeyio_0_1_0_4:priorFour},'./v_privkeyio0.1.0_3':{v_privkeyio_0_1_0_3:priorThree},'./v_privkeyio0.1.0_2':{v_privkeyio_0_1_0_2:priorTwo},'./v_privkeyio0.1.0_1':{v_privkeyio_0_1_0_1:priorOne},'./v_privkeyio0.1.0_0':{v_privkeyio_0_1_0_0:previous}}).versionGraph
 const {ExtendedVersion}=Module.createRequire(require.resolve('@start9labs/start-sdk'))('@start9labs/start-core/exver')
 const parse=v=>ExtendedVersion.parse(v),to=parse('#privkeyio:0.1.0:18'),from=parse('0.1.0:24')
 const versions=[],e={setDataVersion:async({version})=>versions.push(version)}
 await graph.migrate({effects:e,from,to});assert.equal(admitted,1);assert.equal(versions.at(-1),to.toString())
 reject=true;await assert.rejects(()=>graph.migrate({effects:e,from,to}));assert.equal(versions.length,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:0'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:1'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:2'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:3'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:4'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:5'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:6'),to});assert.equal(admitted,2)
 await graph.migrate({effects:e,from:parse('#privkeyio:0.1.0:7'),to});assert.equal(admitted,2)
 for(const v of ['0.1.0:23','0.1.0:25','#other:0.1.0:24','0.1.1:0'])assert.equal(graph.canMigrateFrom().satisfiedBy(parse(v)),false,v)
 assert.equal(graph.canMigrateTo().satisfiedBy(from),false)
 let taskCount=0,exists=false,setup
 const initSdk={setupOnInit:fn=>{setup=fn;return fn},setupInit:()=>null,setupUninit:()=>null,
  action:{createOwnTask:async(_e,a,severity)=>{assert.equal(a,'initialize');assert.equal(severity,'critical');taskCount++}}}
 load('startos/init/index.ts',{'../sdk':{sdk:initSdk},'../dependencies':{dependencies:null},'../interfaces':{setInterfaces:null},
  '../versions':{versionGraph:graph},'../actions':{actions:{get:id=>{assert.equal(id,'migrated-initialize');return 'initialize'}}},
  '../backups':{restoreInit:null},'../live/legacy':{present:async name=>{assert.equal(name,'mainnet-swaps-v1');return exists}}})
 await setup(effects,'update');assert.equal(taskCount,1)
 exists=true;await setup(effects,'update');assert.equal(taskCount,1)
 exists=false;await setup(effects,'restore');assert.equal(taskCount,1)
 await setup(effects,'install');assert.equal(taskCount,2)
 let options,restore,config
 const chain={setOptions:o=>{options=o;return chain},setPostRestore:f=>{restore=f;return chain}}
 const writes=[]
 load('startos/backups.ts',{'./sdk':{sdk:{setupBackups:fn=>{config=fn;return {}},Backups:{ofVolumes:()=>chain},volumes:sdk.volumes}},
  'fs/promises':{writeFile:async(...args)=>writes.push(args),lstat:async()=>({isFile:()=>true})}})
 await config();await restore()
 for(const name of ['pairing.json','forward-session-pairing.json','reverse-session-pairing.json','execution/jobs','execution/forward-swaps','execution/reverse-swaps','mainnet-swaps-v1'])assert.ok(options.exclude.includes(name),name)
 assert.ok(!options.exclude.includes('execution/restored.json'));assert.ok(!options.exclude.includes('execution/forward-pilot-restore-epoch.json'))
 assert.equal(writes[0][0],'/synthetic/swap-restored.json');assert.equal(writes[0][2].flag,'wx')
 const realSdk=require('@start9labs/start-sdk').StartSdk.of().withManifest(bundled.manifest).build(true)
 let checked=0,execs=0,deny=false
 const actionSdk={...realSdk,SubContainer:{withTemp:async(e,i,m,n,fn)=>fn({exec:async()=>{execs++;return {exitCode:0,stdout:'{"result":{"initialized":true}}'}}})}}
 const mod=load('startos/live/actions.ts',{'./discovery':{discoverySettings:bundled.actions.actions['wallet-discovery']},'./localNodes':{localSetup:bundled.actions.actions['local-node-setup']},'../sdk':{sdk:actionSdk},'../utils':{mounts:{}},
  './legacy':{inspectLegacy:async()=>({records:[{direction:'forward',phase:'settled'}],inert_temporary_files_preserved:{pilot_attention_status:1}}),present:async()=>true,
    verifyInitialization:async()=>{checked++;if(deny)throw Error('refused')}}})
 const historyAction=mod.createActions().get('legacy-history');await historyAction.getInput({effects})
 const history=await historyAction.run({effects,input:{}})
 assert.equal(history.result.value[0].value,'1');assert.equal(history.result.value.at(-1).name,'Retained historical worker warning files');assert.equal(history.result.value.at(-1).value,'1');assert.ok(!JSON.stringify(history).includes('PRIVATE'))
 await assert.rejects(()=>mod.invoke(effects,{action:'initialize',confirmed:false}));assert.equal(checked,0);assert.equal(execs,0)
 deny=true;await assert.rejects(()=>mod.invoke(effects,{action:'initialize',confirmed:true}));assert.equal(execs,0)
 deny=false;await mod.invoke(effects,{action:'initialize',confirmed:true});assert.equal(checked,2);assert.equal(execs,1)
 console.log('PASS: exact-version graph, read-only migration, refusal controls, explicit setup task, all installed actions, credential backup exclusions and restore fence')
}
main().catch(e=>{console.error(e);process.exitCode=1})
