"""Copy validated OpenGOAL texture replacements into staged Jak 3 graphics."""
from dataclasses import dataclass
from pathlib import Path
import struct


@dataclass(frozen=True)
class Texture:
    key: tuple[int, str, str, int]
    size: tuple[int, int]
    record: memoryview
    pixels: memoryview


class Fr3:
    def __init__(self, path):
        import zstandard

        self.path = Path(path)
        packed = self.path.read_bytes()
        self.raw = zstandard.ZstdDecompressor().decompress(packed[8:])
        if (len(self.raw) != struct.unpack_from('<Q', packed)[0]
                or self.raw[:2] != b'+\0' or self.raw[-2:] != b'+\0'):
            raise ValueError(f'Unsupported FR3 format: {self.path.name}')
        self.data = memoryview(self.raw)
        self.cursor = 2
        self.vector()  # level name
        count, = self.read('<Q')
        self.header = self.data[:self.cursor]
        self.textures = []
        for _ in range(count):
            start = self.cursor
            width, height, combo = self.read('<HHI')
            pixels = self.vector(4)
            name = bytes(self.vector()).decode('utf-8')
            page = bytes(self.vector()).decode('utf-8')
            flag, = self.read('<B')
            self.textures.append(Texture((combo, name, page, flag), (width, height),
                                         self.data[start:self.cursor], pixels))
        self.texture_end = self.cursor

    def read(self, layout):
        output = struct.unpack_from(layout, self.data, self.cursor)
        self.cursor += struct.calcsize(layout)
        return output

    def vector(self, width=1):
        count, = self.read('<Q')
        end = self.cursor + count * width
        if end > len(self.raw):
            raise ValueError(f'Truncated FR3 texture table: {self.path.name}')
        result = self.data[self.cursor:end]
        self.cursor = end
        return result

    def merc(self, first_model, normalize_envmaps=False):
        encoded = first_model.encode()
        needle = struct.pack('<Q', len(encoded)) + encoded
        first = self.raw.find(needle)
        if first < self.texture_end + 8 or self.raw.find(needle, first + 1) >= 0:
            raise ValueError('Merc model section changed')
        merc = self.data[first - 8:]
        normalize_kleiver_eyes = self.path.name == 'destrack.fr3'
        if normalize_envmaps or normalize_kleiver_eyes:
            from fr3_fingerprint import canonical_merc
            return canonical_merc(merc, normalize_envmaps=normalize_envmaps,
                                  normalize_kleiver_eyes=normalize_kleiver_eyes)
        return merc

    def write_with_textures(self, path, records):
        import zstandard

        raw = b''.join([self.header, *records, self.data[self.texture_end:]])
        path.write_bytes(struct.pack('<Q', len(raw)) + zstandard.ZstdCompressor(level=1).compress(raw))


def texture_changes(clean, active, replacements, first_model, normalize_envmaps=False):
    """Accept only changes that the active replacement PNGs explain exactly."""
    from PIL import Image

    if clean.header != active.header or len(clean.textures) != len(active.textures):
        raise ValueError(f'Texture table changed in {active.path.name}')
    if clean.merc(first_model, normalize_envmaps) != active.merc(first_model, normalize_envmaps):
        raise ValueError(f'Model geometry changed in {active.path.name}')
    changes = {}
    for original, modified in zip(clean.textures, active.textures):
        if original.record == modified.record:
            continue
        if original.key != modified.key:
            raise ValueError(f'Texture identity or order changed in {active.path.name}')
        _, name, page, _ = modified.key
        if any(word in ('', '.', '..') or set(word).intersection('/\\:') for word in (name, page)):
            raise ValueError('Unsafe texture name in graphics archive')
        specific = replacements/page/(name + '.png')
        image_path = specific if specific.is_file() else replacements/'_all'/(name + '.png')
        mismatch = f'Texture {page}/{name} in {active.path.name} does not match the active texture pack.'
        if modified.key in changes:
            originals, replacement = changes[modified.key]
            if modified.record != replacement:
                raise ValueError(mismatch)
            originals.add(bytes(original.record))
            continue
        if not image_path.is_file() or not image_path.resolve().is_relative_to(replacements.resolve()):
            raise ValueError(mismatch)
        with Image.open(image_path) as image:
            if (image.format != 'PNG' or image.size != modified.size
                    or image.convert('RGBA').tobytes() != modified.pixels):
                raise ValueError(mismatch)
        changes[modified.key] = ({bytes(original.record)}, bytes(modified.record))
    return changes


def apply_texture_changes(path, changes):
    """Replace RGBA records in place in a staged file, keeping all draw slots."""
    if not changes:
        return 0
    graphics = Fr3(path)
    pending = {(key, record) for key, (originals, _) in changes.items() for record in originals}
    records = []
    replaced = 0
    for texture in graphics.textures:
        if texture.key not in changes:
            records.append(texture.record)
            continue
        originals, replacement = changes[texture.key]
        if texture.record not in originals:
            _, name, page, _ = texture.key
            raise ValueError(f'Texture pack conflicts with the cinematic texture {page}/{name} in {path.name}.')
        pending.discard((texture.key, texture.record))
        records.append(replacement)
        replaced += 1
    if pending:
        (_, name, page, _), _ = next(iter(pending))
        raise ValueError(f'Texture pack entry {page}/{name} is missing from the cinematic {path.name}.')
    graphics.write_with_textures(path, records)
    return replaced


def replacement_folder(data, game):
    return data / 'custom_assets' / game / 'texture_replacements'


def has_replacements(folder):
    return any(folder.glob('*/*.png'))
