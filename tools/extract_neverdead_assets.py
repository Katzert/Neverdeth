"""
NeverDead Xbox 360 Asset Extractor (Universal ISO / GOD / Folder)
Extracts and verifies game assets from:
1. Xbox 360 Disc Images (.iso - XISO, XGD2, XGD3)
2. Games on Demand containers (.data / LIVE packages)
3. Extracted game folders containing default.xex
"""

import os
import sys
import struct
import argparse
import shutil

sys.stdout.reconfigure(encoding='utf-8')

BLOCK_SIZE = 0x800          # 2048 bytes
HASH_BLOCK_SIZE = 0x1000     # 4096 bytes
BLOCKS_PER_L0_HASH = 0x198   # 408 blocks per Level 0 hash block
HASHES_PER_L1_HASH = 0xA1C4  # 41412 blocks
BLOCKS_PER_FILE = 0x14388    # 82824 blocks per DataXXXX file (0xA290000 bytes)
GDFX_MAGIC = b'MICROSOFT*XBOX*MEDIA'

SENTINEL_FILES = [
    ('default.xex', 8 * 1024 * 1024),
    (os.path.join('envs', 'sp', 'sp_00_prologue.xbox360'), 10 * 1024 * 1024),
]

def find_case_insensitive(base_dir, rel_path):
    parts = rel_path.replace('/', '\\').split('\\')
    curr = base_dir
    for part in parts:
        if not os.path.isdir(curr):
            return None
        target = part.lower()
        found = None
        for entry in os.listdir(curr):
            if entry.lower() == target:
                found = os.path.join(curr, entry)
                break
        if not found:
            return None
        curr = found
    return curr

def ensure_cesa_bik(dest_dir):
    """Fallback automatico: crea CESA.bik como hardlink o copia de Front_End.bik si falta."""
    if not os.path.isdir(dest_dir):
        return
    fmv_dir = os.path.join(dest_dir, 'FMV')
    if not os.path.exists(fmv_dir):
        for item in os.listdir(dest_dir):
            if item.lower() == 'fmv' and os.path.isdir(os.path.join(dest_dir, item)):
                fmv_dir = os.path.join(dest_dir, item)
                break
    if not os.path.exists(fmv_dir):
        return

    has_cesa = any(f.lower() == 'cesa.bik' for f in os.listdir(fmv_dir))
    if has_cesa:
        return

    fe_path = None
    for f in os.listdir(fmv_dir):
        if f.lower() == 'front_end.bik':
            fe_path = os.path.join(fmv_dir, f)
            break

    if not fe_path or not os.path.isfile(fe_path):
        return

    cesa_path = os.path.join(fmv_dir, 'CESA.bik')
    print(f"\n[FMV] Applying automatic fallback: CESA.bik <- Front_End.bik")
    try:
        os.link(fe_path, cesa_path)
        print("[FMV] Hard link created successfully (0 additional disk bytes).")
    except Exception:
        import shutil
        shutil.copyfile(fe_path, cesa_path)
        print("[FMV] Physical copy created successfully.")


def verify_assets(target_dir):
    ensure_cesa_bik(target_dir)
    print(f"\n[ASSET VERIFICATION] Checking directory: {target_dir}")
    all_ok = True
    for rel_path, min_size in SENTINEL_FILES:
        found_path = find_case_insensitive(target_dir, rel_path)
        if not found_path or not os.path.isfile(found_path):
            print(f"  [MISSING] {rel_path} not found!")
            all_ok = False
            continue
        size = os.path.getsize(found_path)
        if size < min_size:
            print(f"  [TRUNCATED] {rel_path} ({size:,} bytes < min {min_size:,} bytes)")
            all_ok = False
        else:
            print(f"  [OK] {rel_path} ({size:,} bytes)")
    return all_ok

class GdfxFileSystem:
    def __init__(self, reader):
        self.reader = reader

    def parse_directory(self, sector, size):
        buf = self.reader.read_sectors(sector, size)
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
        vol_desc = self.reader.read_sector(32)
        if vol_desc[:20] != GDFX_MAGIC:
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
                sec_data = self.reader.read_sector(s)
                take = min(remaining, len(sec_data))
                out_f.write(sec_data[:take])
                remaining -= take

class SvodContainer:
    def __init__(self, god_dir):
        self.god_dir = god_dir
        self.file_handles = {}
        self.fs = GdfxFileSystem(self)

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

    def get_file_tree(self):
        return self.fs.get_file_tree()

    def extract_file(self, start_sector, size, dest_path):
        return self.fs.extract_file(start_sector, size, dest_path)

    def close(self):
        for f in self.file_handles.values():
            try:
                f.close()
            except Exception:
                pass
        self.file_handles.clear()

class IsoContainer:
    def __init__(self, iso_path):
        self.iso_path = iso_path
        self.file = open(iso_path, 'rb')
        self.partition_offset = self._detect_partition()
        self.fs = GdfxFileSystem(self)

    def _detect_partition(self):
        # Offsets canonicos donde reside el sector 32 (descriptor GDFX)
        known_sector32_offsets = [
            (0x00010000, 0x00000000, "XISO / Unpatched"),
            (0x0FDA0000, 0x0FD90000, "XGD2 Standard"),
            (0x0FDB0000, 0x0FDA0000, "XGD2 Variant"),
            (0x02090000, 0x02080000, "XGD3 Standard"),
        ]

        file_size = os.path.getsize(self.iso_path)
        for s32_off, part_base, desc in known_sector32_offsets:
            if s32_off + 20 <= file_size:
                self.file.seek(s32_off)
                magic = self.file.read(20)
                if magic == GDFX_MAGIC:
                    print(f"  [ISO] GDFX partition detected at 0x{part_base:08X} ({desc})")
                    return part_base

        # Escaneo en streaming por bloques de 64 KB sin cargar la ISO en RAM
        print("  [ISO] Scanning 64KB blocks for GDFX Volume Descriptor...")
        scan_offset = 0
        chunk_size = 64 * 1024
        while scan_offset + 0x10000 + 20 <= file_size and scan_offset < 512 * 1024 * 1024:
            self.file.seek(scan_offset + 0x10000)
            magic = self.file.read(20)
            if magic == GDFX_MAGIC:
                print(f"  [ISO] GDFX partition found at offset 0x{scan_offset:08X}")
                return scan_offset
            scan_offset += chunk_size

        raise RuntimeError(
            "Error: No valid Xbox 360 GDFX partition found in ISO.\n"
            "The image must be decrypted or in standard XISO/XGD2/XGD3 format."
        )

    def read_sector(self, sector):
        offset = self.partition_offset + sector * BLOCK_SIZE
        self.file.seek(offset)
        return self.file.read(BLOCK_SIZE)

    def read_sectors(self, start_sector, size):
        offset = self.partition_offset + start_sector * BLOCK_SIZE
        self.file.seek(offset)
        return self.file.read(size)

    def get_file_tree(self):
        return self.fs.get_file_tree()

    def extract_file(self, start_sector, size, dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        offset = self.partition_offset + start_sector * BLOCK_SIZE
        self.file.seek(offset)
        remaining = size
        buffer_size = 1024 * 1024
        with open(dest_path, 'wb') as out_f:
            while remaining > 0:
                chunk = min(remaining, buffer_size)
                data = self.file.read(chunk)
                if not data:
                    break
                out_f.write(data)
                remaining -= len(data)

    def close(self):
        if self.file:
            self.file.close()

def resolve_input(input_path):
    if not input_path:
        return None, None

    if os.path.isfile(input_path):
        lower = input_path.lower()
        if lower.endswith('.iso'):
            return 'ISO', input_path
        # Puede ser un archivo LIVE de GOD (e.g. E7D01E468FBBCE3EC1F3) con carpeta .data adyacente
        god_dir = input_path + '.data'
        if os.path.isdir(god_dir):
            return 'GOD', god_dir
        # Comprobar si es un archivo ISO sin extension
        try:
            with open(input_path, 'rb') as f:
                f.seek(0x10000)
                if f.read(20) == GDFX_MAGIC:
                    return 'ISO', input_path
        except Exception:
            pass

    if os.path.isdir(input_path):
        # Caso GOD .data dir
        if os.path.isfile(os.path.join(input_path, 'Data0000')):
            return 'GOD', input_path
        for item in os.listdir(input_path):
            if item.lower().endswith('.data') and os.path.isdir(os.path.join(input_path, item)):
                if os.path.isfile(os.path.join(input_path, item, 'Data0000')):
                    return 'GOD', os.path.join(input_path, item)

        # Caso Carpeta Extraida con default.xex
        if find_case_insensitive(input_path, 'default.xex'):
            return 'EXTRACTED', input_path

    return 'UNKNOWN', input_path

def copy_extracted_folder(src_dir, dest_dir, menu_only=False, missing_only=False, dry_run=False):
    src_abs = os.path.abspath(src_dir)
    dest_abs = os.path.abspath(dest_dir)
    if src_abs.lower() == dest_abs.lower():
        print(f"Source folder and destination folder are identical ({dest_abs}). Nothing to copy.")
        return True

    CRITICAL_PREFIXES = ('fmv', 'guimenu', 'fonts', 'text', 'misc', 'sounds')
    copied_count = 0
    copied_bytes = 0

    for root, dirs, files in os.walk(src_dir):
        for f in files:
            full_src = os.path.join(root, f)
            rel = os.path.relpath(full_src, src_dir)
            rel_lower = rel.lower()

            if rel_lower.startswith('$systemupdate'):
                continue
            if menu_only and not any(rel_lower.startswith(p) for p in CRITICAL_PREFIXES):
                continue

            full_dest = os.path.join(dest_dir, rel)
            if missing_only and os.path.exists(full_dest):
                continue

            size = os.path.getsize(full_src)
            copied_count += 1
            copied_bytes += size

            if not dry_run:
                os.makedirs(os.path.dirname(full_dest), exist_ok=True)
                shutil.copy2(full_src, full_dest)
                print(f"  [COPIED] {rel} ({size:,} bytes)")
            else:
                print(f"  [DRY-RUN] {rel} ({size:,} bytes)")

    print(f"Copied {copied_count} files ({copied_bytes / (1024*1024):.2f} MB).")
    return True

def main():
    parser = argparse.ArgumentParser(description='NeverDead Xbox 360 Universal Asset Extractor')
    parser.add_argument('--input', '-i', help='Path to game dump (ISO file, GOD .data folder, or extracted folder)')
    parser.add_argument('--output', '-o', default='assets', help='Destination folder for extracted assets (default: assets)')
    parser.add_argument('--god-dir', help='(Legacy) Path to GOD .data folder')
    parser.add_argument('--dest', help='(Legacy) Destination assets folder')
    parser.add_argument('--verify-only', action='store_true', help='Verify presence and integrity of game assets in destination')
    parser.add_argument('--list', action='store_true', help='List all files in container')
    parser.add_argument('--menu-only', action='store_true', help='Extract only files needed for FMV intro & GUIMenu')
    parser.add_argument('--missing-only', action='store_true', help='Extract only missing non-$SystemUpdate files')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be extracted without writing to disk')
    parser.add_argument('--fix-all', action='store_true', help='Extract all non-$SystemUpdate files')
    parser.add_argument('--file', help='Extract specific file (e.g. misc/Common.asrBE)')
    parser.add_argument('--prefix', help='Extract all files under a prefix (e.g. Envs/SP/SP_00_Prologue)')
    args = parser.parse_args()

    dest_dir = args.output
    if args.dest:
        dest_dir = args.dest

    input_source = args.input or args.god_dir

    if args.verify_only:
        target = dest_dir
        if not os.path.isdir(target) and input_source and os.path.isdir(input_source):
            target = input_source
        ok = verify_assets(target)
        if ok:
            print(f"\nResult: Game data complete and verified in '{target}'. Ready to play!\n")
            sys.exit(0)
        else:
            print(f"\nResult: Game data incomplete in '{target}'. Please provide a valid game copy.\n")
            sys.exit(1)

    if not input_source:
        # Fallback por defecto si no se especifico input
        default_god = r'D:\Emulacion_y_ROMs\Xbox_360\4B4E081F\00007000\E7D01E468FBBCE3EC1F3.data'
        if os.path.isdir(default_god):
            input_source = default_god
        else:
            parser.print_help()
            print("\nError: Please provide --input <path_to_iso_or_god_or_folder>")
            sys.exit(1)

    kind, resolved_path = resolve_input(input_source)
    print(f"Resolved input: {resolved_path} (Type: {kind})")
    print(f"Destination: {dest_dir}")

    if kind == 'EXTRACTED':
        copy_extracted_folder(resolved_path, dest_dir, menu_only=args.menu_only, missing_only=args.missing_only, dry_run=args.dry_run)
        verify_assets(dest_dir)
        return

    container = None
    if kind == 'ISO':
        container = IsoContainer(resolved_path)
    elif kind == 'GOD':
        container = SvodContainer(resolved_path)
    else:
        print(f"Error: Unsupported input format for '{input_source}'.")
        print("Must be an Xbox 360 .iso image, a Games on Demand container (.data), or an extracted folder with default.xex.")
        sys.exit(1)

    try:
        all_dirs, all_files = container.get_file_tree()
        print(f"GDFX image parsed successfully: {len(all_dirs)} directories, {len(all_files)} files.")

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
                print(f"Error: File '{args.file}' not found in container!")
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
                    dest_file = os.path.join(dest_dir, f)
                    if not find_case_insensitive(dest_dir, f):
                        files_to_extract.append((f, sec, sz))
        elif args.prefix:
            p = args.prefix.replace('/', '\\').lower()
            files_to_extract = [e for e in all_files if e[0].lower().startswith(p)]
        else: # fix_all or default
            for f, sec, sz in all_files:
                if not f.lower().startswith('$systemupdate'):
                    files_to_extract.append((f, sec, sz))

        print(f"\nExtracting {len(files_to_extract)} files to '{dest_dir}'...")
        total_bytes = sum(sz for _, _, sz in files_to_extract)
        if args.dry_run:
            print(f"Dry run: {len(files_to_extract)} files selected ({total_bytes / (1024*1024):.2f} MB). No files written.")
            return

        extracted_bytes = 0
        for i, (f, sec, sz) in enumerate(files_to_extract, 1):
            dst = os.path.join(dest_dir, f)
            print(f"[{i}/{len(files_to_extract)}] {f} ({sz:,} bytes)...", end='', flush=True)
            container.extract_file(sec, sz, dst)
            extracted_bytes += sz
            print(' DONE')

        print(f"\nExtraction complete! Extracted {len(files_to_extract)} files ({extracted_bytes / (1024*1024):.2f} MB).")
        verify_assets(dest_dir)

    finally:
        if container:
            container.close()

if __name__ == '__main__':
    main()
