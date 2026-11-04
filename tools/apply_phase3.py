#!/usr/bin/env python3
"""Aplica las modificaciones de la Fase 3 en src/nd_hooks.cpp y neverdead_recomp.87.cpp:
1. NdXdkShadowState y filtrado condicional bajo ND_XDK_FILTER=1.
2. Mediciones QPC de CPU y GPU por frame con telemetria cada 60 frames.
3. Reset en boundary al final de frame y en sub_826B6B38.
"""
import os
import sys

def main():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    hooks_path = os.path.join(root, "src", "nd_hooks.cpp")
    rec87_path = os.path.join(root, "generated", "neverdead_default", "neverdead_recomp.87.cpp")

    with open(hooks_path, "r", encoding="utf-8") as f:
        src = f.read().replace("\r\n", "\n")

    # 1. Insercion de NdXdkShadowState y helpers
    target1 = """static inline uint32_t nd_be32(const uint8_t* p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
         uint32_t(p[3]);
}

static uint32_t g_ring_ops[128];"""

    repl1 = """static inline uint32_t nd_be32(const uint8_t* p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
         uint32_t(p[3]);
}

static inline void nd_store_be32(uint8_t* p, uint32_t val) {
  p[0] = static_cast<uint8_t>(val >> 24);
  p[1] = static_cast<uint8_t>(val >> 16);
  p[2] = static_cast<uint8_t>(val >> 8);
  p[3] = static_cast<uint8_t>(val);
}

//=============================================================================
// Estado Sombra XDK D3D (ND_XDK_FILTER=1)
//=============================================================================
// Reduce la sobrecarga de microcodigo y constantes en el primary buffer:
// - Shaders Type-3 0x27 (PM4_IM_LOAD): si el shader es identico al ultimo fijado,
//   se marca la cabecera como Type-3 PM4_NOP (opcode 0x00).
// - Constantes Type-3 0x2F (PM4_LOAD_ALU_CONSTANT): si las constantes float4
//   c0..c255 ya coinciden con el estado sombra, se marcan como PM4_NOP.
// - Reset automatico en cada frame boundary y en vaciado de ring (sub_826B6B38).
//=============================================================================
static bool NdXdkFilterEnabled() {
  static const bool s = []() {
    const char* v = std::getenv("ND_XDK_FILTER");
    return v != nullptr && v[0] == '1';
  }();
  return s;
}

struct NdXdkShadowState {
  uint32_t current_vs_addr{0};
  uint32_t current_vs_size{0};
  uint32_t current_ps_addr{0};
  uint32_t current_ps_size{0};

  uint32_t alu_consts[256][4]{};
  bool alu_consts_valid[256]{false};

  uint64_t total_shaders{0};
  uint64_t filtered_shaders{0};
  uint64_t total_consts{0};
  uint64_t filtered_consts{0};

  void Reset() {
    current_vs_addr = 0;
    current_vs_size = 0;
    current_ps_addr = 0;
    current_ps_size = 0;
    std::memset(alu_consts_valid, 0, sizeof(alu_consts_valid));
  }
};

static NdXdkShadowState g_xdk_shadow{};

extern "C" void NdResetXdkShadowStateOnBoundary() {
  g_xdk_shadow.Reset();
}

static LARGE_INTEGER g_guest_frame_start_qpc{};
static double g_window_cpu_ms_sum = 0.0;
static double g_window_frame_ms_sum = 0.0;
static double g_window_min_frame_ms = 999999.0;
static double g_window_max_frame_ms = 0.0;
static uint32_t g_window_sample_count = 0;

static uint32_t g_ring_ops[128];"""

    if target1 not in src:
        print("ERROR: target1 no encontrado en src/nd_hooks.cpp")
        return 1
    src = src.replace(target1, repl1, 1)

    # 2. Filtrado y mediciones en NdRingProbe
    target2 = """static void NdRingProbe(uint32_t device, uint64_t swap_index) {
  if (!NdRingEnabled() || device == 0 || g_translate == nullptr) {
    return;
  }"""

    repl2 = """static void NdRingProbe(uint32_t device, uint64_t swap_index) {
  const bool ring_on = NdRingEnabled();
  const bool filter_on = NdXdkFilterEnabled();
  if ((!ring_on && !filter_on) || device == 0 || g_translate == nullptr) {
    return;
  }"""

    if target2 not in src:
        print("ERROR: target2 no encontrado en src/nd_hooks.cpp")
        return 1
    src = src.replace(target2, repl2, 1)

    # 3. Logica de filtrado dentro del bucle de NdRingProbe
    target3 = """        case 0x27: {  // PM4_IM_LOAD
          if (count >= 2) {
            const uint32_t addr_type = nd_be32(p + ((i + 1) << 2));
            const uint32_t start_size = nd_be32(p + ((i + 2) << 2));
            const uint32_t addr = addr_type & ~0x3;
            const uint32_t shader_type = addr_type & 0x3;
            const uint32_t size_dwords = start_size & 0xFFFF;
            if (shader_type == 0) ++im_load_vs; else ++im_load_ps;
            const uint8_t* code = (addr && g_translate) ? g_translate(addr) : nullptr;
            if (code != nullptr && size_dwords > 0) {
              const uint64_t hval = NdFnv1a64(code, size_dwords * 4);
              frame_shader_hashes.push_back(hval);
            }
          }
          break;
        }"""

    repl3 = """        case 0x27: {  // PM4_IM_LOAD
          if (count >= 2) {
            const uint32_t addr_type = nd_be32(p + ((i + 1) << 2));
            const uint32_t start_size = nd_be32(p + ((i + 2) << 2));
            const uint32_t addr = addr_type & ~0x3;
            const uint32_t shader_type = addr_type & 0x3;
            const uint32_t size_dwords = start_size & 0xFFFF;
            if (shader_type == 0) ++im_load_vs; else ++im_load_ps;
            const uint8_t* code = (addr && g_translate) ? g_translate(addr) : nullptr;
            if (code != nullptr && size_dwords > 0) {
              const uint64_t hval = NdFnv1a64(code, size_dwords * 4);
              frame_shader_hashes.push_back(hval);
            }
            if (filter_on && addr != 0) {
              if (shader_type == 0) {
                if (g_xdk_shadow.current_vs_addr == addr && g_xdk_shadow.current_vs_size == size_dwords) {
                  // NOP Type-3 opcode 0x00
                  uint8_t* mut_p = const_cast<uint8_t*>(p);
                  nd_store_be32(mut_p + (i << 2), h & ~0x00007F00);
                  ++g_xdk_shadow.filtered_shaders;
                } else {
                  g_xdk_shadow.current_vs_addr = addr;
                  g_xdk_shadow.current_vs_size = size_dwords;
                  ++g_xdk_shadow.total_shaders;
                }
              } else if (shader_type == 1) {
                if (g_xdk_shadow.current_ps_addr == addr && g_xdk_shadow.current_ps_size == size_dwords) {
                  // NOP Type-3 opcode 0x00
                  uint8_t* mut_p = const_cast<uint8_t*>(p);
                  nd_store_be32(mut_p + (i << 2), h & ~0x00007F00);
                  ++g_xdk_shadow.filtered_shaders;
                } else {
                  g_xdk_shadow.current_ps_addr = addr;
                  g_xdk_shadow.current_ps_size = size_dwords;
                  ++g_xdk_shadow.total_shaders;
                }
              }
            }
          }
          break;
        }
        case 0x2F: {  // PM4_LOAD_ALU_CONSTANT
          if (filter_on && count >= 3) {
            const uint32_t address = nd_be32(p + ((i + 1) << 2)) & 0x3FFFFFFF;
            const uint32_t offset_type = nd_be32(p + ((i + 2) << 2));
            const uint32_t size_dwords = nd_be32(p + ((i + 3) << 2)) & 0xFFF;
            const uint32_t index = offset_type & 0x7FF;
            const uint32_t alu_type = (offset_type >> 16) & 0xFF;
            if (alu_type == 0 && (size_dwords % 4) == 0 && size_dwords > 0) {
              const uint32_t count_vec4 = size_dwords / 4;
              if (index + count_vec4 <= 256 && g_translate != nullptr) {
                const uint8_t* src = g_translate(address);
                if (src != nullptr) {
                  bool all_same = true;
                  for (uint32_t v = 0; v < count_vec4; ++v) {
                    const uint32_t c_idx = index + v;
                    if (!g_xdk_shadow.alu_consts_valid[c_idx]) {
                      all_same = false;
                      break;
                    }
                    const uint32_t w0 = nd_be32(src + ((v * 4 + 0) << 2));
                    const uint32_t w1 = nd_be32(src + ((v * 4 + 1) << 2));
                    const uint32_t w2 = nd_be32(src + ((v * 4 + 2) << 2));
                    const uint32_t w3 = nd_be32(src + ((v * 4 + 3) << 2));
                    if (g_xdk_shadow.alu_consts[c_idx][0] != w0 ||
                        g_xdk_shadow.alu_consts[c_idx][1] != w1 ||
                        g_xdk_shadow.alu_consts[c_idx][2] != w2 ||
                        g_xdk_shadow.alu_consts[c_idx][3] != w3) {
                      all_same = false;
                      break;
                    }
                  }
                  if (all_same) {
                    uint8_t* mut_p = const_cast<uint8_t*>(p);
                    nd_store_be32(mut_p + (i << 2), h & ~0x00007F00);
                    g_xdk_shadow.filtered_consts += count_vec4;
                  } else {
                    for (uint32_t v = 0; v < count_vec4; ++v) {
                      const uint32_t c_idx = index + v;
                      g_xdk_shadow.alu_consts[c_idx][0] = nd_be32(src + ((v * 4 + 0) << 2));
                      g_xdk_shadow.alu_consts[c_idx][1] = nd_be32(src + ((v * 4 + 1) << 2));
                      g_xdk_shadow.alu_consts[c_idx][2] = nd_be32(src + ((v * 4 + 2) << 2));
                      g_xdk_shadow.alu_consts[c_idx][3] = nd_be32(src + ((v * 4 + 3) << 2));
                      g_xdk_shadow.alu_consts_valid[c_idx] = true;
                    }
                    g_xdk_shadow.total_consts += count_vec4;
                  }
                }
              }
            }
          }
          break;
        }"""

    if target3 not in src:
        print("ERROR: target3 no encontrado en src/nd_hooks.cpp")
        return 1
    src = src.replace(target3, repl3, 1)

    # 4. En NdVdSwapHook: mediciones de CPU/GPU y log cada 60 frames
    target4 = """    if (ms > g_max_ms) {
      g_max_ms = ms;
    }"""

    repl4 = """    if (ms > g_max_ms) {
      g_max_ms = ms;
    }
    // Telemetria Fase 3: CPU y GPU cada 60 frames
    LARGE_INTEGER swap_entry_qpc{};
    QueryPerformanceCounter(&swap_entry_qpc);
    double frame_cpu_ms = 0.0;
    if (g_guest_frame_start_qpc.QuadPart > 0 && g_qpf.QuadPart > 0) {
      frame_cpu_ms = static_cast<double>(swap_entry_qpc.QuadPart - g_guest_frame_start_qpc.QuadPart) * 1000.0 /
                     static_cast<double>(g_qpf.QuadPart);
    }
    g_window_frame_ms_sum += ms;
    g_window_cpu_ms_sum += frame_cpu_ms;
    if (ms < g_window_min_frame_ms) g_window_min_frame_ms = ms;
    if (ms > g_window_max_frame_ms) g_window_max_frame_ms = ms;
    ++g_window_sample_count;

    if (g_window_sample_count >= 60) {
      const double avg_ms = g_window_frame_ms_sum / g_window_sample_count;
      const double min_ms = g_window_min_frame_ms;
      const double max_ms = g_window_max_frame_ms;
      const double avg_cpu = g_window_cpu_ms_sum / g_window_sample_count;
      const double avg_gpu = std::max(0.0, avg_ms - avg_cpu);
      REXLOG_INFO("neverdead: [FRAME_TIME] avg={:.1f} ms, min={:.1f} ms, max={:.1f} ms, cpu={:.1f} ms, gpu={:.1f} ms",
                  avg_ms, min_ms, max_ms, avg_cpu, avg_gpu);

      const uint64_t sh_total = g_xdk_shadow.total_shaders + g_xdk_shadow.filtered_shaders;
      const double pct_sh = sh_total > 0 ? (100.0 * g_xdk_shadow.filtered_shaders / sh_total) : 0.0;
      const uint64_t c_total = g_xdk_shadow.total_consts + g_xdk_shadow.filtered_consts;
      const double pct_c = c_total > 0 ? (100.0 * g_xdk_shadow.filtered_consts / c_total) : 0.0;
      REXLOG_INFO("neverdead: [XDK_SHADOW] shaders_filtrados={} ({:.1f}%), constantes_filtradas={} ({:.1f}%)",
                  g_xdk_shadow.filtered_shaders, pct_sh, g_xdk_shadow.filtered_consts, pct_c);

      g_window_frame_ms_sum = 0.0;
      g_window_cpu_ms_sum = 0.0;
      g_window_min_frame_ms = 999999.0;
      g_window_max_frame_ms = 0.0;
      g_window_sample_count = 0;
    }"""

    if target4 not in src:
        print("ERROR: target4 no encontrado en src/nd_hooks.cpp")
        return 1
    src = src.replace(target4, repl4, 1)

    # 5. Reset en boundary al final de NdRingProbe
    target5 = """    }
  }
  s_last = wr;"""

    repl5 = """    }
  }
  s_last = wr;
  NdResetXdkShadowStateOnBoundary();"""

    if target5 not in src:
        print("ERROR: target5 no encontrado en src/nd_hooks.cpp")
        return 1
    src = src.replace(target5, repl5, 1)

    # 6. Actualizacion de g_guest_frame_start_qpc en NdPaceGuestAfterSwapComplete
    target6 = """  g_last_guest_swap_qpc = now;
}"""

    repl6 = """  g_last_guest_swap_qpc = now;
  QueryPerformanceCounter(&g_guest_frame_start_qpc);
}"""

    if target6 not in src:
        print("ERROR: target6 no encontrado en src/nd_hooks.cpp")
        return 1
    src = src.replace(target6, repl6, 1)

    with open(hooks_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("src/nd_hooks.cpp actualizado con exito")

    # Modificar neverdead_recomp.87.cpp para llamar a NdResetXdkShadowStateOnBoundary() en sub_826B6B38
    with open(rec87_path, "r", encoding="utf-8") as f:
        r87 = f.read().replace("\r\n", "\n")

    target_r87 = """DEFINE_REX_FUNC(sub_826B6B38) {
	REX_FUNC_PROLOGUE();"""

    repl_r87 = """extern "C" void NdResetXdkShadowStateOnBoundary();
DEFINE_REX_FUNC(sub_826B6B38) {
	REX_FUNC_PROLOGUE();
	NdResetXdkShadowStateOnBoundary();"""

    if target_r87 in r87 and "NdResetXdkShadowStateOnBoundary" not in r87:
        r87 = r87.replace(target_r87, repl_r87, 1)
        with open(rec87_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(r87)
        print("neverdead_recomp.87.cpp actualizado con exito")
    else:
        print("neverdead_recomp.87.cpp ya tiene el hook o no encontro target")

    return 0

if __name__ == "__main__":
    sys.exit(main())
