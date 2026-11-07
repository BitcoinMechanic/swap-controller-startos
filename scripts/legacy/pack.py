"""Pack transition JS with the byte-identical, previously verified controller image.

Usage: python3 package/scripts/legacy/pack.py BUILD_DIR BASELINE_INSTALLERS_DIR
Run within the retained packaging workspace; never replace its signing key.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile

IMAGE='sha256:74c6ee1d33cead69464d3178666dedd6594c073f6a06a725232bdd1e1eba99c3'
BASELINE='173000b99ffa8e5c066d0a3f2d336c5ae1bb952a5aa730fe8c98a706a5513f0a'


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(args,**kwargs):return subprocess.check_output([str(a) for a in args],**kwargs)
def member(package,name):return run(['start-cli','s9pk','inspect',package,'cat',name])
def clauses(value):return set(re.sub(r'[()\s]','',value).split('||'))


def inventory(package,label):
    image=R/(label+'-verify.squashfs');image.write_bytes(member(package,'images/x86_64/controller.squashfs'))
    root=R/(label+'-verify-root')
    # Refuse to reuse an earlier extracted tree as evidence.
    assert not root.exists()
    listing=run(['unsquashfs','-lln',image],text=True)
    owners={}
    for line in listing.splitlines():
        parts=line.split(None,5)
        if len(parts)==6 and parts[5].startswith('squashfs-root/'):
            owners[parts[5].split(' -> ',1)[0].removeprefix('squashfs-root/')]=parts[1]
    run(['unsquashfs','-no-progress','-d',root,image])
    result={}
    for p in root.rglob('*'):
        meta=p.lstat();name=str(p.relative_to(root))
        result[name]=(stat.S_IMODE(meta.st_mode),stat.S_IFMT(meta.st_mode),
            os.readlink(p) if p.is_symlink() else sha(p) if p.is_file() else None,owners[name])
    return result


R,B=(Path(a).resolve() for a in sys.argv[1:]);P=R/'package'
baseline=B/'swap-controller_x86_64.s9pk'
assert sha(baseline)==BASELINE
assert (R/'typecheck.log').read_text()=='' and (R/'sdk-lint.log').read_text()==''
assert 'PASS:' in (R/'package-tests.log').read_text()
assert 'OK' in (R/'validator-tests.log').read_text()
transition=json.loads((R/'runtime-transition.log').read_text().splitlines()[0])
assert transition['legacy_bytes_and_barrier_preserved'] and transition['worker_start_and_restart']
assert transition['restore_fence_refused'] and not transition['legacy_authority_imported']
archive=R/'controller-image.tar'
if not archive.exists():subprocess.run(['docker','image','save','-o',str(archive),IMAGE],check=True)
with tarfile.open(archive) as t:
    manifest=json.load(t.extractfile('manifest.json'));assert len(manifest)==1
    row=manifest[0];raw=t.extractfile(row['Config']).read()
    assert 'sha256:'+hashlib.sha256(raw).hexdigest()==IMAGE
    config=json.loads(raw);assert config['architecture']=='amd64' and config['os']=='linux'
    assert len(row['Layers'])==len(config['rootfs']['diff_ids'])
    for name,expected in zip(row['Layers'],config['rootfs']['diff_ids']):
        assert 'sha256:'+hashlib.file_digest(t.extractfile(name),'sha256').hexdigest()==expected
with (P/'javascript/index.js').open('a') as out:
    out.write('\nmodule.exports.manifest.images.controller.source='+json.dumps({'ociLayout':{'path':str(archive)}})+';\n')
manifest=json.loads(run(['node','-e','console.log(JSON.stringify(require("./javascript/index.js").manifest))'],cwd=P,text=True))
assert manifest['id']=='swap-controller' and manifest['version']=='#privkeyio:0.1.0:1'
expected={'=#privkeyio:0.1.0:1','=#privkeyio:0.1.0:0','=0.1.0:24'}
assert clauses(manifest['canMigrateFrom'])==expected
assert manifest['canMigrateTo']=='=#privkeyio:0.1.0:1'
assert manifest['volumes']==['main'] and manifest['dependencies']=={}
assert set(manifest['images'])=={'controller'}
assert manifest['images']['controller']['arch']==['x86_64'] and manifest['images']['controller']['emulateMissingAs'] is None
(R/'input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
output=R/'swap-controller_x86_64.s9pk'
with (R/'pack.log').open('w') as log:
    subprocess.run(['start-cli','s9pk','pack','--arch=x86_64','-o',str(output)],cwd=P,stdout=log,stderr=subprocess.STDOUT,check=True)
packed=json.loads(run(['start-cli','s9pk','inspect',output,'manifest'],text=True))
(R/'packed-manifest.json').write_text(json.dumps(packed,indent=2)+'\n')
for key in ('id','version','volumes','dependencies','canMigrateTo'):assert packed[key]==manifest[key],key
assert clauses(packed['canMigrateFrom'])==expected
old_meta=json.loads(member(baseline,'images/x86_64/controller.json'))
new_meta=json.loads(member(output,'images/x86_64/controller.json'))
expected_env=dict(item.split('=',1) for item in config['config']['Env'])
assert set(new_meta)==set(old_meta)|{'env'} and new_meta['env']==expected_env
assert {k:v for k,v in new_meta.items() if k!='env'}==old_meta
old_tree,new_tree=inventory(baseline,'baseline'),inventory(output,'candidate')
empty='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
# Older packer injected Docker mount placeholders; the current OCI importer
# retains the image's hostname/resolver bytes and its declared environment.
# No blanket directory exclusions: accept only these exact observed deltas.
expected_delta={
 '.dockerenv':[(493,32768,empty,'0/0'),None],
 'dev/console':[(493,32768,empty,'0/0'),None],
 'dev/pts':[(493,16384,None,'0/0'),None],
 'dev/shm':[(493,16384,None,'0/0'),None],
 'etc/hostname':[(493,32768,empty,'0/0'),(420,32768,'411eb208674138b8ce7624993e678f6b1d8a39c6f2bc4156e10d9466e0c71d2b','0/0')],
 'etc/hosts':[(493,32768,empty,'0/0'),None],
 'etc/mtab':[(511,40960,'/proc/mounts','0/0'),None],
 'etc/resolv.conf':[(493,32768,empty,'0/0'),(420,32768,'a535ac16b8fe54e9e1afbb474c4c19fd8faeddbac8b1e1c17f8f52191ce84f96','0/0')],
}
delta={k:[old_tree.get(k),new_tree.get(k)] for k in old_tree.keys()|new_tree.keys() if old_tree.get(k)!=new_tree.get(k)}
assert delta==expected_delta,'unexpected packed image file/mode/ownership change'
(R/'packed-runtime-comparison.json').write_text(json.dumps(dict(identical_entries=len(old_tree.keys()&new_tree.keys())-len(set(delta)&old_tree.keys()&new_tree.keys()),
    exact_os_mount_differences=delta,metadata_difference='Image-declared environment retained by newer packer'),indent=2)+'\n')
js=R/'packed-js.squashfs';js.write_bytes(member(output,'javascript.squashfs'))
raw=run(['unsquashfs','-cat',js,'index.js'])
assert hashlib.sha256(raw).hexdigest()==sha(P/'javascript/index.js')
assert b'legacy_record_from_other_restore' in raw and b'legacy_authority_imported=False' in raw
(R/'packed-index.js').write_bytes(raw)
subprocess.run(['node','scripts/check-bundle.cjs',str(R/'packed-index.js')],cwd=P,check=True)
receipt=dict(version=manifest['version'],sha256=sha(output),baseline_sha256=BASELINE,
    image=IMAGE,immutable_docker_image_unchanged=True,packed_runtime_files_identical=True,
    packed_os_mount_differences='Eight exact checked Docker mount placeholder differences; see packed-runtime-comparison.json',
    packed_environment='Image-declared PATH and Python flags retained by newer packer',legacy_credentials_imported=False,
    checks=['19 admission controls','SDK version graph and readonly wiring','backup credential exclusions and restore fence',
        'explicit initialization task','14 installed actions and legacy history','typecheck and SDK lint','packed manifest JavaScript and image readback'],
    runtime_transition=transition,inherited_payment_engine_tests='0102c historical receipt; not rerun',
    live_StartOS_acceptance=False,routed_cross_chain_swaps_rerun=False)
(R/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
(R/'SHA256SUMS').write_text(''.join(sha(R/name)+'  '+name+'\n' for name in ('swap-controller_x86_64.s9pk','build-receipt.json')))
print(json.dumps(receipt,indent=2))
