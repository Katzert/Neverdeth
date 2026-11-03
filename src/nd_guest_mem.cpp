// Traductor guest VA -> host para los hooks mid-ASM.
//
// Los hooks se inyectan dentro de funciones recompiladas y solo reciben
// registros; para leer memoria arbitraria del invitado (por ejemplo el
// X_VIDEO_MODE que VdSetDisplayMode recibe en RAM, 0x40000000) necesitan un
// traductor. La app instala el objeto rex::memory::Memory aqui.

#include <rex/rex_app.h>

namespace {

rex::memory::Memory* g_memory = nullptr;

}  // namespace

void nd_guest_mem_set(void* memory) {
  g_memory = static_cast<rex::memory::Memory*>(memory);
}

uint8_t* nd_translate_va(uint32_t guest_va) {
  if (g_memory == nullptr) {
    return nullptr;
  }
  return g_memory->TranslateVirtual<uint8_t*>(guest_va);
}
