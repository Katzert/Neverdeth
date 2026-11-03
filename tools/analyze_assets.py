import os, sys

def analyze():
    with open('build_clang/asset_list.txt', 'r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]

    data_lines = []
    for l in lines[4:]:
        parts = l.split()
        if len(parts) >= 3:
            sec = parts[-1]
            sz_str = parts[-2].replace(',', '')
            path = ' '.join(parts[:-2])
            if sz_str.isdigit() and sec.isdigit():
                data_lines.append((path, int(sz_str), int(sec)))

    print(f"Total parsed files from asset_list.txt: {len(data_lines)}")

    assets_dir = os.path.abspath('assets')

    categories = {}
    missing_by_cat = {}
    present_by_cat = {}

    for path, sz, sec in data_lines:
        if path.startswith('$SystemUpdate'):
            cat = '$SystemUpdate'
        else:
            cat = path.split('\\')[0] if '\\' in path else 'Root'
        
        categories.setdefault(cat, []).append((path, sz, sec))
        
        # Check existence in assets (case-insensitive check on Windows)
        local_path = os.path.join(assets_dir, path)
        exists = os.path.exists(local_path)
        if not exists:
            parent = os.path.dirname(local_path)
            base = os.path.basename(local_path).lower()
            if os.path.exists(parent):
                for existing in os.listdir(parent):
                    if existing.lower() == base:
                        exists = True
                        break
        
        if exists:
            present_by_cat.setdefault(cat, []).append((path, sz))
        else:
            missing_by_cat.setdefault(cat, []).append((path, sz))

    print("\n" + "="*80)
    print(f"{'Category':20} | {'Total Files':11} | {'Present':8} | {'Missing':8} | {'Missing Size':18}")
    print("="*80)

    total_missing_files = 0
    total_missing_bytes = 0

    for cat in sorted(categories.keys()):
        tot = len(categories[cat])
        pres = len(present_by_cat.get(cat, []))
        miss = len(missing_by_cat.get(cat, []))
        miss_bytes = sum(sz for _, sz in missing_by_cat.get(cat, []))
        if cat != '$SystemUpdate':
            total_missing_files += miss
            total_missing_bytes += miss_bytes
        print(f"{cat:20} | {tot:11} | {pres:8} | {miss:8} | {miss_bytes / (1024*1024):15.2f} MB")

    print("-"*80)
    print(f"TOTAL GAME ASSETS MISSING (excl $SystemUpdate): {total_missing_files} files, {total_missing_bytes / (1024*1024):.2f} MB ({total_missing_bytes / (1024**3):.2f} GB)")

    print("\nMissing files extension breakdown:")
    by_ext = {}
    for cat, items in missing_by_cat.items():
        if cat == '$SystemUpdate': continue
        for p, sz in items:
            ext = os.path.splitext(p)[1].lower()
            by_ext.setdefault(ext, []).append((p, sz))

    for ext, items in sorted(by_ext.items(), key=lambda x: sum(sz for _, sz in x[1]), reverse=True):
        total_sz_mb = sum(sz for _, sz in items) / (1024*1024)
        print(f"  {ext:15}: {len(items):3} files ({total_sz_mb:8.2f} MB)")

    print("\nMissing Envs breakdown:")
    env_sp_missing = [p for p, _ in missing_by_cat.get('Envs', []) if 'envs\\sp' in p.lower()]
    env_mp_missing = [p for p, _ in missing_by_cat.get('Envs', []) if 'envs\\mp' in p.lower()]
    print(f"  Envs/SP missing: {len(env_sp_missing)}")
    print(f"  Envs/MP missing: {len(env_mp_missing)}")

    print("\nMissing Chars breakdown:")
    char_sp_missing = [p for p, _ in missing_by_cat.get('Chars', []) if 'sp_' in p.lower()]
    char_mp_missing = [p for p, _ in missing_by_cat.get('Chars', []) if 'mp_' in p.lower()]
    print(f"  Chars SP missing: {len(char_sp_missing)}")
    print(f"  Chars MP missing: {len(char_mp_missing)}")


    for cat in ('Chars', 'Weapons', 'Anims', 'sounds', 'Sounds', 'Envs', 'FMV', 'Root'):
        if cat in missing_by_cat and missing_by_cat[cat]:
            print(f"\n--- {cat} ({len(missing_by_cat[cat])} missing) ---")
            for p, sz in missing_by_cat[cat]:
                print(f"  {p} ({sz / (1024*1024):.2f} MB)")

if __name__ == '__main__':
    analyze()
