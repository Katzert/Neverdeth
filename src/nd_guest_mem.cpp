// Guest VA -> host address translator for mid-ASM hooks.
//
// Hooks injected inside recompiled functions only receive guest registers.
// Reading guest memory structures (such as X_VIDEO_MODE passed to
// VdSetDisplayMode in RAM) requires translating guest VAs via the active
// rex::memory::Memory instance registered by the app.

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
