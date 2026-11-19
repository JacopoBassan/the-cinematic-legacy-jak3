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


FR3_DIRECTORY = Path('out/jak3/fr3')


def preflight(data, tools, config, *, allow_texture_pack=False):
    from texture_packs import has_replacements, replacement_folder

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
    source = Path(config.get('sourceData', data)).resolve()
    for name,expected in config['source'].items():
        require_hash(source/name,expected)
    for name,expected in config['inputs'].items():
        require_hash(data/name,expected)
    normalize_envmaps = config.get('platform') in ('macos-arm64', 'linux-x64')
    mismatches = {}
    can_check_pack = allow_texture_pack and has_replacements(replacement_folder(data, 'jak3'))
    for name,expected in config['fr3Inputs'].items():
        actual = fr3_asset_hash(data/name, expected['firstModel'], normalize_envmaps=normalize_envmaps)
        if actual != expected['sha256']:
            mismatches[name] = actual
            if can_check_pack:
                continue
            raise ValueError(
                f'Graphics fingerprint mismatch: {Path(name).name} ({config["selectedSerial"]}). '
                f'Rebuild vanilla Jak 3 with OpenGOAL {config["openGoalVersion"]} and retry. '
                f'If it still fails, share this installer log. Fingerprint: {actual}.')
    for name,expected in config['sourceTrees'].items():
        if tree_hash(source/name) != expected:
            raise ValueError(f'Unsupported or modified source/configuration tree: {name}')
    return mismatches


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

    The fingerprint covers normal textures and the Merc model, index and
    vertex data. It canonicalizes known undefined fields before hashing.
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
    normalize_kleiver_eyes = path.name == 'destrack.fr3'
    if normalize_envmaps or normalize_kleiver_eyes:
        from fr3_fingerprint import canonical_merc
        merc = canonical_merc(merc, normalize_envmaps=normalize_envmaps,
                              normalize_kleiver_eyes=normalize_kleiver_eyes)
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


def extract(data,tools,project,rip,env,log,native_config, *, levels=()):
    config = {'rip_levels':rip,'dump_objs':False,'process_game_text':False,
              'process_game_count':False,'process_part_group_table':False,'extract_collision':False}
    if rip:
        config['dgo_names'] = ['CGO/KERNEL.CGO','CGO/GAME.CGO','DGO/ARENACST.DGO','DGO/TEMA.DGO']
    else:
        # Load the full texture database for GAME and any selected levels.
        source = (project/'decompiler/config/jak3/ntsc_v1/inputs.jsonc').read_text()
        inputs = json.loads(re.sub(r'//[^\n]*','',source))
        if missing := set(levels).difference(inputs['levels_to_extract']):
            raise ValueError(f'Unsupported graphics archives: {sorted(missing)}')
        inputs['levels_to_extract'] = list(levels)
        path = project/'decompiler/config/jak3/release-inputs.json'
        path.write_text(json.dumps(inputs))
        config['inputs_file'] = 'decompiler/config/jak3/release-inputs.json'
    run([platform_tools.tool_path(tools,'extractor',native_config),'--folder','--game','jak3','--proj-path',project,
         '--decompile','--decomp-config-override',json.dumps(config),'--disable-ansi',
         *platform_tools.compiler_options(native_config),data/'iso_data/jak3'],log,env)


def clean_graphics(data, tools, work, project, env, config, mismatches):
    """Rebuild stock inputs only for archives whose checked assets differ."""
    from texture_packs import Fr3, replacement_folder, texture_changes

    names = {Path(relative).stem for relative in mismatches} - {'GAME'}
    dgos = {'rublcst': 'RBCT', 'ltowerb': 'LTOWB', 'lpattack': 'LPATK',
            'forestb': 'FRSTB', 'desertb': 'DESB', 'ctyport': 'CPO',
            'rubblea': 'RUBA', 'factoryd': 'FACD'}
    levels = sorted(dgos.get(name, name.upper()) + '.DGO' for name in names)
    extract(data, tools, project, False, env, work/'00-stock-graphics.log', config, levels=levels)
    stock = work/'stock-graphics'
    stock.mkdir()
    changes = {}
    normalize_envmaps = config.get('platform') in ('macos-arm64', 'linux-x64')
    for relative, expected in config['fr3Inputs'].items():
        original = data/relative
        staged = stock/original.name
        if relative not in mismatches:
            try:
                os.link(original, staged)
            except OSError:
                shutil.copy2(original, staged)
            continue
        rebuilt = project/relative
        if fr3_asset_hash(rebuilt, expected['firstModel'], normalize_envmaps) != expected['sha256']:
            raise ValueError(f'Rebuilt stock graphics did not match {original.name}.')
        clean, active = Fr3(rebuilt), Fr3(original)
        changes[original.name] = texture_changes(clean, active, replacement_folder(data, 'jak3'),
                                                  expected['firstModel'], normalize_envmaps)
        # Keep the user's native backgrounds and collision. Only normalize the
        # texture records for the builders that expect a stock input.
        active.write_with_textures(staged, (texture.record for texture in clean.textures))
    return stock, changes


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
    mismatches = preflight(data, tools, config, allow_texture_pack=True)
    source = Path(config.get('sourceData', data)).resolve()
    territory_setup = f'(seval (set! *default-territory* {config["defaultTerritory"]}))'
    for target in (work,output):
        if target.exists():
            raise ValueError(f'Use a new empty destination: {target}')
        if any(target.is_relative_to(root) or root.is_relative_to(target) for root in (data, source)):
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
        shutil.copytree(source/folder,project/folder)
    for folder,expected in config['sourceTrees'].items():
        if tree_hash(project/folder) != expected:
            raise ValueError('Copied source tree changed during build preparation')
    fr3 = data/FR3_DIRECTORY
    texture_pack_changes = {}
    if mismatches:
        print('Checking active texture pack against a stock graphics rebuild', flush=True)
        fr3, texture_pack_changes = clean_graphics(data, tools, work, project, env, config, mismatches)
    (work/'models').mkdir()
    (work/'prototype').mkdir()
    extract(data,tools,project,True,env,work/'01-extract.log',config)
    for name,folder in [('jakb-c-lod0','common'),('daxter-lod0','common'),('jakc-highres-lod0','arenacst'),('daxter-highres-lod0','arenacst')]:
        shutil.copy2(project/'decompiler_out/jak3/levels'/folder/f'{name}.glb',work/'models'/f'{name}.glb')
    shutil.copy2(fr3/'GAME.fr3',work/'prototype/GAME-baseline.fr3')
    run([sys.executable,ROOT/'retarget_glb.py',work],work/'02-retarget.log',env)
    shutil.copytree(work/'prototype/merc_replacements',project/'custom_assets/jak3/merc_replacements')
    extract(data,tools,project,False,env,work/'03-import.log',config)
    shutil.copy2(project/'out/jak3/fr3/GAME.fr3',work/'prototype/GAME.fr3')
    stages = ['group_armor_effects.py','dax_effects.py','restore_draw_modes.py','fix_faces.py','build_transformations.py','restore_authored.py']
    for number,name in enumerate(stages,4):
        run([sys.executable,ROOT/'stages'/name],work/f'{number:02}-{Path(name).stem}.log',env)
    os.environ['CINEMATIC_BUILD_WORK']=str(work)
    sys.path.insert(0,str(ROOT/'stages'))
    run([sys.executable,ROOT/'validate.py'],work/'08-validate.log',env)
    from fit_belt import fit_belt
    belt_path=work/'prototype/GAME-belt-fit.fr3'
    belt_report=fit_belt(work/'prototype/GAME-transformations.fr3',work/'prototype/GAME-baseline.fr3',belt_path)
    (work/'belt-validation.json').write_text(json.dumps(belt_report,indent=2))
    os.replace(belt_path,work/'prototype/GAME-transformations.fr3')
    from cinematic_forms import add_models as add_cinematic_forms, REPLACEMENT
    dark_path = work/'prototype/GAME-dedicated-dark.fr3'
    dark_report = add_cinematic_forms(work/'prototype/GAME-transformations.fr3',
                               fr3/'arenacst.fr3', fr3/'mhctycst.fr3',
                                       project/'out/jak3/fr3/templea.fr3',
                                       work/'prototype/GAME-faces.fr3', dark_path)
    (work/'transformation-model-validation.json').write_text(json.dumps(dark_report,indent=2))
    os.replace(dark_path, work/'prototype/GAME-transformations.fr3')
    # These builders consume only the user's pinned local FR3 and rig objects.
    from build_npc_expansion import build as build_npcs
    from build_additional_npcs import build as build_additional_npcs
    from build_air_transport import build as build_air
    print('Building NPCs and air transport',flush=True)
    npc_output=work/'expansion-npcs'
    air_output=work/'expansion-air'
    npc_reports=build_npcs(data_dir=data,output_dir=npc_output,report_path=work/'npc-validation.json',animated_eyes=True,native_graphics=fr3)
    npc_reports.extend(build_additional_npcs(data_dir=data,output_dir=npc_output,native_graphics=fr3))
    from cinematic_eyes import generate as generate_eyes, patch_source as patch_eye_dispatch
    eye_source=work/'cinematic-eyes.gc'
    eye_report=generate_eyes(data,eye_source,npc_output,native_graphics=fr3)
    (work/'cinematic-eyes-report.json').write_text(json.dumps(eye_report,indent=2))
    from build_npc_expansion import fit_sig_rider_garment
    sig_path=npc_output/'lwassig.fr3'
    sig_fitted=work/'lwassig-garment-fit.fr3'
    sig_report=fit_sig_rider_garment(sig_path,fr3/'lwassig.fr3',sig_fitted)
    os.replace(sig_fitted,sig_path)
    (work/'sig-garment-validation.json').write_text(json.dumps(sig_report,indent=2))
    drawable_source=project/'goal_src/jak3/engine/process-drawable/process-drawable.gc'
    from npc_runtime import patch_jinx_shoulders
    drawable_source.write_text(patch_jinx_shoulders(patch_eye_dispatch(drawable_source.read_text())))
    (work/'npc-validation.json').write_text(json.dumps(npc_reports,indent=2))
    air_reports=build_air(data_dir=data,output_dir=air_output,report_path=work/'air-validation.json',native_graphics=fr3)
    for name,relative in [('foreground','engine/gfx/foreground/foreground.gc'),('texture-anim','engine/gfx/texture/texture-anim.gc')]:
        path = project/'goal_src/jak3'/relative
        source = apply_patch(path.read_text(),(ROOT/'patches'/f'{name}.patch').read_text())
        if name == 'foreground':
            from dax_foreground import patch_source
            source = patch_source(source)
            from npc_foreground import patch_source
            source = patch_source(source)
            from dark_vertex_runtime import patch_foreground
            source = patch_foreground(source, REPLACEMENT)
            from dax_shadow import patch_source as patch_shadow
            source=patch_shadow(source)
        path.write_text(source)
    from dark_vertex_runtime import patch_eye, patch_shader
    eye_renderer = project/'goal_src/jak3/engine/gfx/foreground/eye.gc'
    eye_renderer.write_text(patch_eye(eye_renderer.read_text(), REPLACEMENT))
    shader_relative=Path('game/graphics/opengl_renderer/shaders/merc2.vert')
    shader_path=project/shader_relative
    shader_path.write_bytes(patch_shader(shader_path.read_bytes(), dark_report['transitionColors']))
    rider_source=project/'goal_src/jak3/levels/stadium/king-rider.gc'
    from npc_runtime import patch_source as patch_rider_source
    rider_source.write_text(patch_rider_source(rider_source.read_text()))
    # Upstream shared-DGO targets do not carry every prerequisite when built
    # alone. Load the common and vehicle definitions before king-rider.
    run([platform_tools.tool_path(tools,'goalc',config),'--game','jak3','--proj-path',project,'--disable-ansi',
         *platform_tools.compiler_options(config),
         '--cmd','(begin '+territory_setup+' (make "$OUT/obj/foreground.o") (make "$OUT/obj/texture-anim.o") (make "$OUT/obj/eye.o") (make "$OUT/obj/process-drawable.o") (make "$OUT/obj/entity-debug.o") (make "$OUT/obj/vehicle-h.o") (make "$OUT/obj/target-pilot.o") (make "$OUT/obj/king-rider.o"))'],work/'09-compile.log',env)
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
    pack_code(data/'out/jak3/iso/GAME.CGO',{name:project/'out/jak3/obj'/f'{name}.o' for name in ('foreground','texture-anim','eye','process-drawable')},output/relative_files[1])
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
    if texture_pack_changes:
        from texture_packs import apply_texture_changes

        for relative in relative_files:
            destination = output/relative
            copied = apply_texture_changes(destination, texture_pack_changes.get(destination.name, {}))
            if copied:
                print(f'Kept {copied} texture pack replacements in {destination.name}', flush=True)
    manifest = {'schemaVersion':1,'game':'jak3','version':config['version'],'openGoalVersion':config['openGoalVersion'],'platform':config['platform'],
                'displayName':'Jak 3: The Cinematic Legacy','author':'@jacopobassan',
                'website':'https://jacopobassan.me','runtimeSha256':config['tools'][platform_tools.tool_path(tools,'gk',config).name],
                'privateGeneratedPayload':True,'releaseReady':False,
                  'sourceDiscSerial':config['selectedSerial'],
                'workingRevision':'native-convoy-torn-2026-10-29',
                'features':{'jakAndDaxter':True,'npcModels':['Sig','Damas rider','Ashelin','Jinx','Crimson Guard','Cyber Errol','Kleiver rider'],
                            'npcNewFacialAnimations':False,'airTransport':True,'dedicatedDarkJak':True,'cinematicLightJakWings':True,
                            'cinematicSkirt':True,'animatedCinematicEyes':['Kleiver','Ashelin','Jinx','Sig'],
                            'convoyTorn':'original gameplay model, eyes, rig and mission props'},
                'files':[
                    {'path':relative,'sha256':sha(output/relative),'bytes':(output/relative).stat().st_size}
                    for relative in relative_files]}
    shutil.copy2(work/'validation.json',output/'validation.json')
    shutil.copy2(work/'transformation-model-validation.json',output/'transformation-model-validation.json')
    shutil.copy2(work/'belt-validation.json',output/'belt-validation.json')
    shutil.copy2(work/'npc-validation.json',output/'npc-validation.json')
    shutil.copy2(work/'air-validation.json',output/'air-validation.json')
    (output/'DO-NOT-PUBLISH.txt').write_text('This locally generated payload contains game assets from your installation. Distribute the asset-free builder source archive instead.\n')
    if preflight(data, tools, config, allow_texture_pack=True) != mismatches:
        raise ValueError('Active game graphics changed during installation. Retry the build.')
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
