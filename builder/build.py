"""Create a local mod payload from a supported, user-owned OpenGOAL extraction."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import platform_tools

ROOT = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def require_hash(path,expected):
    if not path.is_file() or sha(path) != expected:
        raise ValueError(f'Unsupported or modified input: {path.name}. Use an original OpenGOAL installation matching this package\'s compatibility.json.')


def preflight(data,tools,config):
    config = regional_config(data, config)
    if sys.flags.optimize:
        raise ValueError('Python optimization disables required validation. Run without -O.')
    if sys.version_info < (3,11):
        raise ValueError('Python 3.11 or newer is required.')
    from importlib.metadata import version,PackageNotFoundError
    for requirement in (ROOT/'requirements.txt').read_text().splitlines():
        if not requirement.strip() or requirement.startswith('#'):
            continue
        package,expected=requirement.split('==')
        try:
            installed=version(package)
        except PackageNotFoundError:
            installed='not installed'
        if installed != expected:
            raise ValueError(f'{package} {expected} is required (found {installed}). Install requirements.txt in the build environment.')
    # Check binary extension imports before reading large game inputs.
    import numpy,scipy,PIL,zstandard
    for name,expected in config['tools'].items():
        require_hash(tools/name,expected)
    for name,expected in {**config['inputs'],**config['source']}.items():
        require_hash(data/name,expected)
    for name,expected in config['fr3Inputs'].items():
        if fr3_asset_hash(data/name,expected['firstModel'],
                          normalize_envmaps=config.get('platform') == 'macos-arm64') != expected['sha256']:
            raise ValueError(f'Unsupported or modified graphics assets: {Path(name).name}')
    for name,expected in config['sourceTrees'].items():
        if tree_hash(data/name) != expected:
            raise ValueError(f'Unsupported or modified source/configuration tree: {name}')


def regional_config(data, config):
    """Select only a verified disc profile, keeping the shared source checks."""
    metadata = json.loads((data/'iso_data/jak3/buildinfo.json').read_text())
    if not isinstance(metadata, list) or len(metadata) != 1:
        raise ValueError('Invalid OpenGOAL disc metadata. Re-extract the original disc.')
    serial = metadata[0].get('serial')
    if serial not in config['supportedSerials']:
        raise ValueError(f'Unsupported Jak 3 disc: {serial}. This package has not been validated for this revision.')
    profiles = config.get('regionalProfiles', {})
    result = dict(config)
    if serial in profiles:
        profile = profiles[serial]
        if metadata[0].get('elf_hash') != profile['elfHash']:
            raise ValueError(f'Unsupported executable revision for {serial}.')
        for key in ('inputs', 'fr3Inputs'):
            result[key] = {**config[key], **profile.get(key, {})}
    elif serial != 'SCES-52460':
        raise ValueError(f'Missing verified compatibility profile for {serial}.')
    result['selectedSerial'] = serial
    # Match the official extractor's compile setup. The territory affects
    # native pad/settings code and must come from the verified disc profile.
    result['defaultTerritory'] = profiles.get(serial, {}).get(
        'defaultTerritory', config.get('defaultTerritory', 1))
    if type(result['defaultTerritory']) is not int or result['defaultTerritory'] not in (0, 1):
        raise ValueError('Missing verified compile territory for this disc profile.')
    return result


def tree_hash(folder):
    files = sorted((p for p in folder.rglob('*') if p.is_file()),key=lambda p:p.relative_to(folder).as_posix())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(folder).as_posix().encode()+b'\0'+sha(path).encode()+b'\n')
    return {'files':len(files),'sha256':digest.hexdigest()}


def fr3_asset_hash(path,first_model,normalize_envmaps=False):
    """Fingerprint consumed assets independently of unused native Hfrag garbage.

    Normal textures and the complete Merc model/index/vertex section are covered.
    Non-Merc geometry is preserved byte for byte from the user's local input by
    the builders, and is intentionally outside this asset compatibility digest.
    """
    import zstandard
    packed=path.read_bytes()
    raw=zstandard.ZstdDecompressor().decompress(packed[8:])
    if len(raw)!=struct.unpack_from('<Q',packed)[0] or raw[:2]!=b'+\0' or raw[-2:]!=b'+\0':
        raise ValueError('Unsupported FR3 format')
    cursor=2
    def number():
        nonlocal cursor
        value=struct.unpack_from('<Q',raw,cursor)[0]
        cursor+=8
        return value
    length=number()
    cursor+=length
    count=number()
    for _ in range(count):
        cursor+=8
        length=number()
        cursor+=length*4
        length=number()
        cursor+=length
        length=number()
        cursor+=length
        cursor+=1
        if cursor>len(raw):
            raise ValueError('Truncated texture table')
    encoded=first_model.encode()
    needle=struct.pack('<Q',len(encoded))+encoded
    first=raw.find(needle)
    if first<cursor+8 or raw.find(needle,first+1)>=0:
        raise ValueError('Merc model section changed')
    merc = raw[first-8:]
    if normalize_envmaps:
        from fr3_fingerprint import canonical_merc
        merc = canonical_merc(merc)
    return hashlib.sha256(raw[:cursor]+merc).hexdigest()


def apply_patch(source,patch):
    """Apply exact unified hunks; refuse fuzzy or partial matches."""
    lines = source.splitlines(keepends=True)
    diff = patch.splitlines(keepends=True)
    out = []
    cursor = 0
    i = 0
    while i < len(diff):
        if not diff[i].startswith('@@ '):
            i += 1
            continue
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',diff[i])
        if match is None:
            raise ValueError('Invalid patch hunk')
        start = int(match[1])-1
        if start < cursor:
            raise ValueError('Overlapping patch hunks')
        out.extend(lines[cursor:start])
        cursor = start
        i += 1
        old_count = new_count = 0
        while i < len(diff) and not diff[i].startswith('@@ '):
            line = diff[i]
            if line.startswith(('--- ','+++ ')):
                break
            if not line or line[0] not in ' +-':
                raise ValueError('Unsupported patch content')
            if line[0] in ' -':
                if cursor >= len(lines) or lines[cursor] != line[1:]:
                    raise ValueError(f'Source patch did not match at line {cursor+1}')
                cursor += 1
                old_count += 1
            if line[0] in ' +':
                out.append(line[1:])
                new_count += 1
            i += 1
        if old_count != int(match[2] or '1') or new_count != int(match[4] or '1'):
            raise ValueError('Patch hunk length mismatch')
    out.extend(lines[cursor:])
    if not out or cursor == 0:
        raise ValueError('Patch did not apply any hunks')
    return ''.join(out)


def run(command,log,env):
    print(f'Running {log.stem}',flush=True)
    with log.open('w',encoding='utf-8') as stream:
        result = subprocess.run([str(x) for x in command],stdout=stream,stderr=subprocess.STDOUT,env=env)
    if result.returncode:
        tail = log.read_text(errors='replace')[-4000:]
        raise RuntimeError(f'{log.name} failed ({result.returncode}):\n{tail}')


def extract(data,tools,project,rip,env,log,native_config):
    config = {'rip_levels':rip,'dump_objs':False,'process_game_text':False,
              'process_game_count':False,'process_part_group_table':False,'extract_collision':False}
    if rip:
        config['dgo_names'] = ['CGO/KERNEL.CGO','CGO/GAME.CGO','DGO/ARENACST.DGO']
    else:
        # GAME holds globally required named render textures. Load the complete
        # object/texture database, but export only common GAME graphics.
        source = (project/'decompiler/config/jak3/ntsc_v1/inputs.jsonc').read_text()
        inputs = json.loads(re.sub(r'//[^\n]*','',source))
        inputs['levels_to_extract'] = []
        path = project/'decompiler/config/jak3/release-inputs.json'
        path.write_text(json.dumps(inputs))
        config['inputs_file'] = 'decompiler/config/jak3/release-inputs.json'
    run([platform_tools.tool_path(tools,'extractor',native_config),'--folder','--game','jak3','--proj-path',project,
         '--decompile','--decomp-config-override',json.dumps(config),'--disable-ansi',
         *platform_tools.compiler_options(native_config),data/'iso_data/jak3'],log,env)


def pack_code(original,replacements,output):
    raw = original.read_bytes()
    out = bytearray(raw[:64])
    cursor = 64
    seen = set()
    for _ in range(struct.unpack_from('<I',raw)[0]):
        size = struct.unpack_from('<I',raw,cursor)[0]
        name = raw[cursor+4:cursor+64]
        key = name.split(b'\0')[0].decode()
        body = raw[cursor+64:cursor+64+size]
        if len(body) != size:
            raise ValueError('Truncated GAME.CGO')
        cursor += 64+((size+15)&~15)
        # Art groups can share a logical name with compiled code (king-rider).
        # Replace only the GOAL code object, preserving the original art entry.
        if key in replacements and body[:4] == b'GOAL':
            if key in seen:
                raise ValueError('Duplicate replacement object')
            seen.add(key)
            body = replacements[key].read_bytes()
        out.extend(struct.pack('<I',len(body))+name+body+b'\0'*(-len(body)%16))
    if cursor != len(raw) or seen != set(replacements):
        raise ValueError('Archive structure or replacement set mismatch')
    output.write_bytes(out)


def build(data,tools,work,output,config):
    print('Verifying supported local inputs',flush=True)
    config = regional_config(data, config)
    preflight(data,tools,config)
    territory_setup = f'(seval (set! *default-territory* {config["defaultTerritory"]}))'
    for target in (work,output):
        if target.exists():
            raise ValueError(f'Use a new empty destination: {target}')
        if target == data or data.is_relative_to(target) or target.is_relative_to(data):
            raise ValueError('Build destinations must be outside the game installation')
    if output.is_relative_to(work) or work.is_relative_to(output):
        raise ValueError('Build and output folders must be separate')
    disk_parent=work.parent
    while not disk_parent.exists():
        disk_parent=disk_parent.parent
    if shutil.disk_usage(disk_parent).free < 1024**3:
        raise ValueError('At least 1 GiB free space is required for this build')
    work.mkdir(parents=True)
    project = work/'extraction'
    project.mkdir()
    env = dict(os.environ,CINEMATIC_BUILD_WORK=str(work))
    for folder in ('goal_src','decompiler','game'):
        shutil.copytree(data/folder,project/folder)
    for folder,expected in config['sourceTrees'].items():
        if tree_hash(project/folder) != expected:
            raise ValueError('Copied source tree changed during build preparation')
    (work/'models').mkdir()
    (work/'prototype').mkdir()
    extract(data,tools,project,True,env,work/'01-extract.log',config)
    for name,folder in [('jakb-c-lod0','common'),('daxter-lod0','common'),('jakc-highres-lod0','arenacst'),('daxter-highres-lod0','arenacst')]:
        shutil.copy2(project/'decompiler_out/jak3/levels'/folder/f'{name}.glb',work/'models'/f'{name}.glb')
    shutil.copy2(data/'out/jak3/fr3/GAME.fr3',work/'prototype/GAME-baseline.fr3')
    run([sys.executable,ROOT/'retarget_glb.py',work],work/'02-retarget.log',env)
    shutil.copytree(work/'prototype/merc_replacements',project/'custom_assets/jak3/merc_replacements')
    extract(data,tools,project,False,env,work/'03-import.log',config)
    shutil.copy2(project/'out/jak3/fr3/GAME.fr3',work/'prototype/GAME.fr3')
    stages = ['group_armor_effects.py','dax_effects.py','restore_draw_modes.py','fix_faces.py','build_transformations.py','restore_authored.py']
    for number,name in enumerate(stages,4):
        run([sys.executable,ROOT/'stages'/name],work/f'{number:02}-{Path(name).stem}.log',env)
    # Tag only authored hair/eyebrow padding after material restoration.
    # The stage requires distinct input/output paths and audits all other bytes.
    os.environ['CINEMATIC_BUILD_WORK']=str(work)
    sys.path.insert(0,str(ROOT/'stages'))
    from tag_dark_vertex_colors import patch_fr3,MARKER as VERTEX_MARKER
    from dark_vertex_runtime import METADATA_INDEX,MARKER as SHADER_MARKER,validate_protocol
    if VERTEX_MARKER != SHADER_MARKER:
        raise ValueError('Dark palette marker protocol mismatch')
    dark_path=work/'prototype/GAME-dark-colors.fr3'
    dark_report=patch_fr3(work/'prototype/GAME-transformations.fr3',dark_path,METADATA_INDEX,work/'prototype/GAME-baseline.fr3')
    dark_report['runtimeProtocol']=validate_protocol()
    (work/'dark-color-validation.json').write_text(json.dumps(dark_report,indent=2))
    os.replace(dark_path,work/'prototype/GAME-transformations.fr3')
    run([sys.executable,ROOT/'validate.py'],work/'08-validate.log',env)
    from fit_belt import fit_belt
    belt_path=work/'prototype/GAME-belt-fit.fr3'
    belt_report=fit_belt(work/'prototype/GAME-transformations.fr3',work/'prototype/GAME-baseline.fr3',belt_path)
    (work/'belt-validation.json').write_text(json.dumps(belt_report,indent=2))
    os.replace(belt_path,work/'prototype/GAME-transformations.fr3')
    # These builders consume only the user's pinned local FR3 and rig objects.
    os.environ['CINEMATIC_BUILD_WORK']=str(work)
    sys.path.insert(0,str(ROOT/'stages'))
    from build_npc_expansion import build as build_npcs
    from build_additional_npcs import build as build_additional_npcs
    from build_air_transport import build as build_air
    print('Building NPCs and air transport',flush=True)
    npc_output=work/'expansion-npcs'
    air_output=work/'expansion-air'
    npc_reports=build_npcs(data_dir=data,output_dir=npc_output,report_path=work/'npc-validation.json',animated_eyes=True)
    npc_reports.extend(build_additional_npcs(data_dir=data,output_dir=npc_output))
    from cinematic_eyes import generate as generate_eyes, patch_source as patch_eye_dispatch
    eye_source=work/'cinematic-eyes.gc'
    eye_report=generate_eyes(data,eye_source,npc_output)
    (work/'cinematic-eyes-report.json').write_text(json.dumps(eye_report,indent=2))
    drawable_source=project/'goal_src/jak3/engine/process-drawable/process-drawable.gc'
    drawable_source.write_text(patch_eye_dispatch(drawable_source.read_text()))
    (work/'npc-validation.json').write_text(json.dumps(npc_reports,indent=2))
    air_reports=build_air(data_dir=data,output_dir=air_output,report_path=work/'air-validation.json')
    for name,relative in [('foreground','engine/gfx/foreground/foreground.gc'),('texture-anim','engine/gfx/texture/texture-anim.gc')]:
        path = project/'goal_src/jak3'/relative
        source = apply_patch(path.read_text(),(ROOT/'patches'/f'{name}.patch').read_text())
        if name == 'foreground':
            from dax_foreground import patch_source
            source = patch_source(source)
            from npc_foreground import patch_source
            source = patch_source(source)
            from dark_vertex_runtime import patch_foreground
            source = patch_foreground(source)
            from dax_shadow import patch_source as patch_shadow
            source=patch_shadow(source)
        path.write_text(source)
    from dark_vertex_runtime import patch_shader,gameplay_hair_texture_scale,gameplay_yellow_tip_scale
    shader_relative=Path('game/graphics/opengl_renderer/shaders/merc2.vert')
    shader_path=project/shader_relative
    hair_tone=gameplay_hair_texture_scale(data/'out/jak3/fr3/GAME.fr3',data/'out/jak3/fr3/mhctycst.fr3')
    hair_tone['yellowTips']=gameplay_yellow_tip_scale(data/'out/jak3/fr3/GAME.fr3',work/'prototype/GAME-transformations.fr3')
    hair_tone['runtimeProtocol']=validate_protocol(hair_tone['scale'],hair_tone['yellowTips']['scale'])
    (work/'dark-hair-tone-validation.json').write_text(json.dumps(hair_tone,indent=2))
    shader_path.write_bytes(patch_shader(shader_path.read_bytes(),dark_report['palette'],hair_tone['scale'],hair_tone['yellowTips']['scale']))
    rider_source=project/'goal_src/jak3/levels/stadium/king-rider.gc'
    from npc_runtime import patch_source as patch_rider_source
    rider_source.write_text(patch_rider_source(rider_source.read_text()))
    # Upstream shared-DGO targets do not carry every prerequisite when built
    # alone. Load the common and vehicle definitions before king-rider.
    run([platform_tools.tool_path(tools,'goalc',config),'--game','jak3','--proj-path',project,'--disable-ansi',
         *platform_tools.compiler_options(config),
         '--cmd','(begin '+territory_setup+' (make "$OUT/obj/foreground.o") (make "$OUT/obj/texture-anim.o") (make "$OUT/obj/process-drawable.o") (make "$OUT/obj/entity-debug.o") (make "$OUT/obj/vehicle-h.o") (make "$OUT/obj/target-pilot.o") (make "$OUT/obj/king-rider.o"))'],work/'09-compile.log',env)
    # Obtain cloth geometry locally. Only the generator, never its generated
    # coordinates or object file, is included in the public source package.
    cloth_config = {'allowed_objects':['jakc-highres-ag'],
                    'dgo_names':['DGO/ARENACST.DGO'], 'disassemble_data':True,
                    'dump_objs':False, 'process_tpages':False,
                    'process_game_text':False, 'process_game_count':False,
                    'process_art_groups':False, 'process_part_group_table':False,
                    'extract_collision':False, 'rip_levels':False, 'levels_extract':False}
    run([platform_tools.tool_path(tools,'extractor',config),'--folder','--game','jak3','--proj-path',project,
         *platform_tools.compiler_options(config),
         '--decompile','--decomp-config-override',json.dumps(cloth_config),'--disable-ansi',
         data/'iso_data/jak3'],work/'10-extract-cloth.log',env)
    from cinematic_skirt import generate as generate_skirt, append_object
    skirt_source = work/'cinematic-skirt.gc'
    skirt_report = generate_skirt(project/'decompiler_out/jak3/jakc-highres-ag.asm',skirt_source)
    (work/'cinematic-skirt-report.json').write_text(json.dumps(skirt_report,indent=2))
    command = '(begin '+territory_setup+' (asm-file "decompiler/config/jak3/all-types.gc") (asm-file '+json.dumps(skirt_source.as_posix())+' :color :write))'
    run([platform_tools.tool_path(tools,'goalc',config),'--game','jak3','--proj-path',project,'--disable-ansi',
         *platform_tools.compiler_options(config),
         '--cmd',command],work/'11-compile-cloth.log',env)
    command = '(begin '+territory_setup+' (asm-file "decompiler/config/jak3/all-types.gc") (asm-file '+json.dumps(eye_source.as_posix())+' :color :write))'
    run([platform_tools.tool_path(tools,'goalc',config),'--game','jak3','--proj-path',project,'--disable-ansi',
         *platform_tools.compiler_options(config),
         '--cmd',command],work/'12-compile-eyes.log',env)
    output.mkdir(parents=True)
    relative_files = ['data/out/jak3/fr3/GAME.fr3','data/out/jak3/iso/GAME.CGO','data/out/jak3/iso/LPATK.DGO']
    for relative in relative_files:
        (output/relative).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(work/'prototype/GAME-transformations.fr3',output/relative_files[0])
    pack_code(data/'out/jak3/iso/GAME.CGO',{name:project/'out/jak3/obj'/f'{name}.o' for name in ('foreground','texture-anim','process-drawable')},output/relative_files[1])
    append_object(output/relative_files[1],project/'out/jak3/obj/cinematic-skirt.o',output/relative_files[1])
    append_object(output/relative_files[1],project/'out/jak3/obj/cinematic-eyes.o',output/relative_files[1],'cinematic-eyes')
    pack_code(data/'out/jak3/iso/LPATK.DGO',{'king-rider':project/'out/jak3/obj/king-rider.o'},output/relative_files[2])
    shader_payload='data/'+shader_relative.as_posix()
    (output/shader_payload).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(shader_path,output/shader_payload)
    relative_files.append(shader_payload)
    for folder in (npc_output,air_output):
        for path in sorted(folder.glob('*.fr3')):
            relative='data/out/jak3/fr3/'+path.name
            if relative.casefold() in {name.casefold() for name in relative_files}:
                raise ValueError('Expansion output collision')
            shutil.copy2(path,output/relative)
            relative_files.append(relative)
    if {name.casefold() for name in relative_files} != {name.casefold() for name in config['payloadPaths']}:
        raise ValueError('Build output file set does not match the release definition')
    manifest = {'schemaVersion':1,'game':'jak3','version':config['version'],'openGoalVersion':config['openGoalVersion'],'platform':config['platform'],
                'displayName':'Jak 3: The Cinematic Legacy','author':'@jacopobassan',
                'website':'https://jacopobassan.me','runtimeSha256':config['tools'][platform_tools.tool_path(tools,'gk',config).name],
                'privateGeneratedPayload':True,'releaseReady':False,
                  'sourceDiscSerial':config['selectedSerial'],
                'workingRevision':'native-convoy-torn-2026-10-29',
                'features':{'jakAndDaxter':True,'npcModels':['Sig','Damas rider','Ashelin','Jinx','Crimson Guard','Cyber Errol','Kleiver rider'],
                            'npcNewFacialAnimations':False,'airTransport':True,'darkVertexPalette':True,
                            'cinematicSkirt':True,'animatedCinematicEyes':['Kleiver','Ashelin','Jinx','Sig'],
                            'convoyTorn':'original gameplay model, eyes, rig and mission props'},
                'files':[
                    {'path':relative,'sha256':sha(output/relative),'bytes':(output/relative).stat().st_size}
                    for relative in relative_files]}
    shutil.copy2(work/'validation.json',output/'validation.json')
    shutil.copy2(work/'dark-color-validation.json',output/'dark-color-validation.json')
    shutil.copy2(work/'belt-validation.json',output/'belt-validation.json')
    shutil.copy2(work/'npc-validation.json',output/'npc-validation.json')
    shutil.copy2(work/'air-validation.json',output/'air-validation.json')
    (output/'DO-NOT-PUBLISH.txt').write_text('This locally generated payload contains game assets from your installation. Distribute the asset-free builder source archive instead.\n')
    preflight(data,tools,config)
    temporary_manifest=output/'manifest.json.new'
    temporary_manifest.write_text(json.dumps(manifest,indent=2))
    os.replace(temporary_manifest,output/'manifest.json')
    print(f'Build and static validation complete: {output}\nRuntime testing is required before promoting this candidate.',flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data',type=Path,required=True,help='Original OpenGOAL active/jak3/data folder')
    parser.add_argument('--tools',type=Path,required=True,help='Original OpenGOAL active/bin folder')
    parser.add_argument('--work',type=Path,required=True,help='New temporary build folder outside OpenGOAL')
    parser.add_argument('--output',type=Path,required=True,help='New local payload folder outside OpenGOAL')
    parser.add_argument('--check',action='store_true',help='Check dependencies and supported input hashes without building')
    parser.add_argument('--platform',choices=platform_tools.PLATFORMS)
    args = parser.parse_args()
    config = platform_tools.select(json.loads((ROOT/'compatibility.json').read_text()),args.platform)
    data,tools,work,output = [p.resolve() for p in (args.game_data,args.tools,args.work,args.output)]
    if args.check:
        preflight(data,tools,config)
        print('Supported input files and dependencies verified')
        return
    build(data,tools,work,output,config)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Build stopped: {exc}',file=sys.stderr)
        sys.exit(1)
