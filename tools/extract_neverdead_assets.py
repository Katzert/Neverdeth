"""
NeverDead Xbox 360 GDFX / SVOD Asset Extractor
Extracts and repairs assets from Xbox 360 Games on Demand container:
D:/Emulacion_y_ROMs/Xbox_360/4B4E081F/00007000/E7D01E468FBBCE3EC1F3.data
"""

import os, struct, sys, argparse, hashlib

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_GOD_DIR = r'D:\Emulacion_y_ROMs\Xbox_360\4B4E081F\00007000\E7D01E468FBBCE3EC1F3.data'
DEFAULT_OUT_DIR = r'C:\Users\katze\.gemini\antigravity-ide\scratch\vibe-recomp\neverdead_project\assets'

BLOCK_SIZE = 0x800          # 2048 bytes
HASH_BLOCK_SIZE = 0x1000     # 4096 bytes
BLOCKS_PER_L0_HASH = 0x198   # 408 blocks per Level 0 hash block
HASHES_PER_L1_HASH = 0xA1C4  # 41412 blocks
BLOCKS_PER_FILE = 0x14388    # 82824 blocks per DataXXXX file (0xA290000 bytes)

class SvodContainer:
    def __init__(self, god_dir):
        self.god_dir = god_dir
        self.file_handles = {}

    def get_file(self, index):
        if index not in self.file_handles:
            fname = os.path.join(self.god_dir, f'Data{index:04d}')
            if not os.path.exists(fname):
                raise FileNotFoundError(f'GOD part not found: {fname}')
            self.file_handles[index] = open(fname, 'rb')
        return self.file_handles[index]

    def read_sector(self, sector):
        file_index = sector // BLOCKS_PER_FILE
        file_block = sector % BLOCKS_PER_FILE
        l0_count = (file_block // BLOCKS_PER_L0_HASH) + 1
        l1_count = (l0_count // HASHES_PER_L1_HASH) + 1
        offset = (l0_count + l1_count) * HASH_BLOCK_SIZE + file_block * BLOCK_SIZE
        f = self.get_file(file_index)
        f.seek(offset)
        return f.read(BLOCK_SIZE)

    def read_sectors(self, start_sector, size):
        num_sectors = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
        data = bytearray()
        for s in range(start_sector, start_sector + num_sectors):
            data.extend(self.read_sector(s))
        return bytes(data[:size])

    def parse_directory(self, sector, size):
        buf = self.read_sectors(sector, size)
        entries = []
        visited = set()

        def walk_tree(offset_dword):
            if offset_dword == 0 or offset_dword == 0xffff or offset_dword in visited:
                return
            visited.add(offset_dword)
            byte_off = offset_dword * 4
            if byte_off + 14 > len(buf):
                return
            left, right, sec, fsize, attr, name_len = struct.unpack('<HHIIBB', buf[byte_off:byte_off+14])
            if name_len == 0 or byte_off + 14 + name_len > len(buf):
                return
            name = buf[byte_off+14:byte_off+14+name_len].decode('ascii', errors='replace')
            
            walk_tree(left)
            entries.append({'name': name, 'sector': sec, 'size': fsize, 'is_dir': bool(attr & 0x10), 'attr': attr})
            walk_tree(right)

        if len(buf) >= 14:
            left, right, sec, fsize, attr, name_len = struct.unpack('<HHIIBB', buf[0:14])
            if name_len > 0 and 14 + name_len <= len(buf):
                name = buf[14:14+name_len].decode('ascii', errors='replace')
                visited.add(0)
                walk_tree(left)
                entries.append({'name': name, 'sector': sec, 'size': fsize, 'is_dir': bool(attr & 0x10), 'attr': attr})
                walk_tree(right)

        return entries

    def get_file_tree(self):
        vol_desc = self.read_sector(32)
        if vol_desc[:20] != b'MICROSOFT*XBOX*MEDIA':
            raise ValueError(f'Invalid GDFX Volume Descriptor Magic: {vol_desc[:20]}')
        root_sector, root_size = struct.unpack('<II', vol_desc[20:28])
        
        all_dirs = []
        all_files = []

        def walk(sector, size, current_path=''):
            entries = self.parse_directory(sector, size)
            for e in entries:
                full_path = os.path.join(current_path, e['name']) if current_path else e['name']
                if e['is_dir']:
                    all_dirs.append((full_path, e['sector'], e['size']))
                    walk(e['sector'], e['size'], full_path)
                else:
                    all_files.append((full_path, e['sector'], e['size']))

        walk(root_sector, root_size)
        return all_dirs, all_files

    def extract_file(self, start_sector, size, dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        num_sectors = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
        remaining = size
        with open(dest_path, 'wb') as out_f:
            for s in range(start_sector, start_sector + num_sectors):
                sec_data = self.read_sector(s)
                take = min(remaining, len(sec_data))
                out_f.write(sec_data[:take])
                remaining -= take

def main():
    parser = argparse.ArgumentParser(description='NeverDead GDFX Asset Extractor')
    parser.add_argument('--god-dir', default=DEFAULT_GOD_DIR, help='Path to GOD .data folder')
    parser.add_argument('--dest', default=DEFAULT_OUT_DIR, help='Destination assets folder')
    parser.add_argument('--list', action='store_true', help='List all files in container')
    parser.add_argument('--menu-only', action='store_true', help='Extract only files needed for FMV intro & GUIMenu')
    parser.add_argument('--missing-only', action='store_true', help='Extract only missing non-$SystemUpdate files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be extracted without writing to disk')
    parser.add_argument('--fix-all', action='store_true', help='Extract all non-$SystemUpdate files')
    parser.add_argument('--file', help='Extract specific file (e.g. misc/Common.asrBE)')
    parser.add_argument('--prefix', help='Extract all files under a prefix (e.g. Envs/SP/SP_00_Prologue)')
    args = parser.parse_args()

    print(f'Opening GOD container at: {args.god_dir}')
    container = SvodContainer(args.god_dir)
    all_dirs, all_files = container.get_file_tree()
    print(f'GDFX image parsed: {len(all_dirs)} directories, {len(all_files)} files.')

    if args.list:
        print(f"\n{'Path':50} {'Size (bytes)':15} {'Sector':10}")
        print('-' * 77)
        for f, sec, sz in sorted(all_files, key=lambda x: x[0].lower()):
            print(f'{f:50} {sz:15,} {sec:10}')
        return

    CRITICAL_PREFIXES = ('fmv', 'guimenu', 'fonts', 'text', 'misc', 'sounds')

    files_to_extract = []
    if args.file:
        target = args.file.replace('/', '\\').lower()
        matched = [entry for entry in all_files if entry[0].lower() == target]
        if not matched:
            print(f'Error: File {args.file} not found in GDFX!')
            return
        files_to_extract = matched
    elif args.menu_only:
        for f, sec, sz in all_files:
            low = f.lower()
            if any(low.startswith(p + '\\') for p in CRITICAL_PREFIXES):
                files_to_extract.append((f, sec, sz))
    elif args.missing_only:
        for f, sec, sz in all_files:
            if not f.lower().startswith('$systemupdate'):
                dest_file = os.path.join(args.dest, f)
                # Case-insensitive check
                exists = os.path.exists(dest_file)
                if not exists:
                    pdir = os.path.dirname(dest_file)
                    base = os.path.basename(dest_file).lower()
                    if os.path.exists(pdir):
                        for ex in os.listdir(pdir):
                            if ex.lower() == base:
                                exists = True
                                break
                if not exists:
                    files_to_extract.append((f, sec, sz))
    elif args.prefix:
        p = args.prefix.replace('/', '\\').lower()
        files_to_extract = [e for e in all_files if e[0].lower().startswith(p)]
    elif args.fix_all:
        for f, sec, sz in all_files:
            if not f.lower().startswith('$systemupdate'):
                files_to_extract.append((f, sec, sz))
    else:
        print('Specify an action: --list, --menu-only, --missing-only, --fix-all, or --file <path>')
        return

    print(f'\nExtracting {len(files_to_extract)} files to {args.dest}...')
    total_bytes = sum(sz for _, _, sz in files_to_extract)
    if args.dry_run:
        print(f'Dry run: {len(files_to_extract)} files selected ({total_bytes / (1024*1024):.2f} MB). No files written.')
        return

    extracted_bytes = 0

    for i, (f, sec, sz) in enumerate(files_to_extract, 1):
        dst = os.path.join(args.dest, f)
        print(f'[{i}/{len(files_to_extract)}] {f} ({sz:,} bytes)...', end='', flush=True)
        container.extract_file(sec, sz, dst)
        extracted_bytes += sz
        print(' DONE')

    print(f'\nExtraction complete! Extracted {len(files_to_extract)} files ({extracted_bytes / (1024*1024):.2f} MB).')

if __name__ == '__main__':
    main()
