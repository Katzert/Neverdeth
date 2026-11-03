import os

def update_neverdead_app():
    path = 'src/neverdead_app.h'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the start of MapBaseExeSections to unlock permissions before reading
    old_code = (
        '    uint8_t* guest_base = mem->TranslateVirtual<uint8_t*>(0x82000000u);\n'
        '    if (!guest_base) {\n'
        '      REXLOG_ERROR("neverdead: MapBaseExeSections cannot translate guest 0x82000000");\n'
        '      return;\n'
        '    }\n\n'
        '    FILE* f = fopen("..\\\\NeverDead_base.exe", "rb");'
    )

    new_code = (
        '    uint8_t* guest_base = mem->TranslateVirtual<uint8_t*>(0x82000000u);\n'
        '    if (!guest_base) {\n'
        '      REXLOG_ERROR("neverdead: MapBaseExeSections cannot translate guest 0x82000000");\n'
        '      return;\n'
        '    }\n\n'
        '#ifdef _WIN32\n'
        '    // CRITICO: Desbloquear permisos de escritura en todo el rango [0x82000000, 0x82C10000)\n'
        '    // antes de fread() porque Windows inicia la arena de .rdata como PAGE_READONLY.\n'
        '    DWORD old_prot = 0;\n'
        '    VirtualProtect(guest_base, 0x00C10000u, PAGE_EXECUTE_READWRITE, &old_prot);\n'
        '#endif\n\n'
        '    FILE* f = fopen("..\\\\NeverDead_base.exe", "rb");'
    )

    assert old_code in content, "old_code not found in neverdead_app.h"
    content = content.replace(old_code, new_code, 1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("neverdead_app.h updated: VirtualProtect unlocked before fread in MapBaseExeSections!")

if __name__ == '__main__':
    update_neverdead_app()
