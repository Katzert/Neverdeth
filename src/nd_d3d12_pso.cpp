// Neverdeth - D3D12 PSO caching, portable bundle persistence, and async precompilation.

#include "nd_internal.h"

#ifdef _WIN32
#include <d3d12.h>
#endif

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <sstream>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <rex/cvar.h>
#include <rex/filesystem/vfs.h>
#include <rex/logging.h>

#ifdef _WIN32
namespace {

std::filesystem::path NdDecodeUtf8Path(const std::string& utf8) {
  if (utf8.empty()) return {};
  const int wlen = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, nullptr, 0);
  if (wlen > 1) {
    std::wstring wide(static_cast<size_t>(wlen - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), -1, wide.data(), wlen);
    return std::filesystem::path(wide);
  }
  return std::filesystem::path(utf8);
}

std::filesystem::path NdGetCacheDir() {
  const std::filesystem::path exe_dir = rex::filesystem::GetExecutableFolder();
  std::error_code ec;
  std::filesystem::path base_dir = exe_dir;
  if (!std::filesystem::is_directory(exe_dir / "assets", ec) &&
      std::filesystem::is_directory(exe_dir.parent_path() / "assets", ec)) {
    base_dir = exe_dir.parent_path();
  }

  const std::string root = rex::cvar::GetFlagByName("cache_root");
  if (!root.empty()) {
    const std::filesystem::path configured = NdDecodeUtf8Path(root);
    return configured.is_relative() ? (base_dir / configured) : configured;
  }
  return base_dir / "cache";
}

using NdCreateGraphicsPipelineStateFn = HRESULT(STDMETHODCALLTYPE*)(
    ID3D12Device* This,
    const D3D12_GRAPHICS_PIPELINE_STATE_DESC* pDesc,
    REFIID riid,
    void** ppPipelineState);

using NdCreateRootSignatureFn = HRESULT(STDMETHODCALLTYPE*)(
    ID3D12Device* This,
    UINT nodeMask,
    const void* pBlobWithRootSignature,
    SIZE_T blobLengthInBytes,
    REFIID riid,
    void** ppvRootSignature);

NdCreateGraphicsPipelineStateFn g_real_create_graphics_pipeline_state = nullptr;
NdCreateRootSignatureFn g_real_create_root_signature = nullptr;

SRWLOCK g_pso_cache_srw = SRWLOCK_INIT;
std::unordered_map<uint64_t, ID3D12PipelineState*> g_pso_memory_cache;
std::mutex g_pso_inflight_mtx;
std::condition_variable g_pso_inflight_cv;
std::unordered_set<uint64_t> g_pso_compiling_hashes;

ID3D12PipelineLibrary1* g_d3d12_pipeline_library = nullptr;
std::atomic<bool> g_pipeline_library_init_attempted{false};
std::atomic<bool> g_pipeline_library_has_disk_blob{false};
// ID3D12PipelineLibrary1::LoadGraphicsPipeline and StorePipeline are not thread-safe.
std::mutex g_pipeline_library_mtx;

struct NdRootSigRecord {
  std::vector<uint8_t> blob;
  uint64_t hash = 0;
};

SRWLOCK g_root_sig_srw = SRWLOCK_INIT;
std::unordered_map<void*, NdRootSigRecord> g_root_sig_by_ptr;
std::unordered_map<uint64_t, ID3D12RootSignature*> g_root_sig_by_hash;

struct NdAutonomousInputElement {
  std::string semantic_name;
  UINT semantic_index = 0;
  DXGI_FORMAT format = DXGI_FORMAT_UNKNOWN;
  UINT input_slot = 0;
  UINT aligned_byte_offset = 0;
  D3D12_INPUT_CLASSIFICATION input_slot_class = D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA;
  UINT instance_data_step_rate = 0;
};

struct NdAutonomousSoDeclaration {
  UINT stream = 0;
  std::string semantic_name;
  UINT semantic_index = 0;
  BYTE start_component = 0;
  BYTE component_count = 0;
  BYTE output_slot = 0;
};

struct NdAutonomousPso {
  uint64_t pso_hash = 0;
  uint64_t root_sig_hash = 0;
  std::vector<uint8_t> root_sig_blob;
  D3D12_GRAPHICS_PIPELINE_STATE_DESC desc{};
  std::vector<uint8_t> vs;
  std::vector<uint8_t> ps;
  std::vector<uint8_t> gs;
  std::vector<uint8_t> ds;
  std::vector<uint8_t> hs;
  std::vector<NdAutonomousInputElement> input_elements;
  std::vector<NdAutonomousSoDeclaration> so_declarations;
  std::vector<UINT> so_buffer_strides;
};

constexpr char kBundleMagic[8] = {'N', 'D', 'P', '3', '\0', '\0', '\0', '\0'};
constexpr uint32_t kMaxRootSig = 1024u * 1024u;
constexpr uint32_t kMaxShaderBlob = 16u * 1024u * 1024u;
constexpr uint32_t kMaxInputElements = 32u;
constexpr uint16_t kMaxSemanticName = 64u;
constexpr uint32_t kMaxSoEntries = 64u;
constexpr uint32_t kMaxSoStrides = 16u;

void NdHashCombine(uint64_t& seed, uint64_t value) {
  seed ^= value + 0x9e3779b97f4a7c15ULL + (seed << 6) + (seed >> 2);
}

template <typename T>
void NdHashVal(uint64_t& seed, const T& val) {
  NdHashCombine(seed, NdFnv1a64(&val, sizeof(T)));
}

uint64_t NdComputeDeterministicPsoHash(const D3D12_GRAPHICS_PIPELINE_STATE_DESC* pDesc,
                                       uint64_t root_sig_hash) {
  uint64_t hash = 14695981039346656037ULL;
  NdHashCombine(hash, root_sig_hash);

  auto hash_bc = [&](uint64_t stage_tag, const D3D12_SHADER_BYTECODE& bc) {
    NdHashCombine(hash, stage_tag);
    const uint64_t len = (bc.pShaderBytecode && bc.BytecodeLength > 0) ? bc.BytecodeLength : 0;
    NdHashCombine(hash, len);
    if (len > 0) {
      NdHashCombine(hash, NdFnv1a64(bc.pShaderBytecode, len));
    }
  };
  hash_bc(1, pDesc->VS);
  hash_bc(2, pDesc->PS);
  hash_bc(3, pDesc->GS);
  hash_bc(4, pDesc->DS);
  hash_bc(5, pDesc->HS);

  NdHashVal(hash, pDesc->StreamOutput.NumEntries);
  if (pDesc->StreamOutput.NumEntries > 0 && pDesc->StreamOutput.pSODeclaration != nullptr) {
    for (UINT i = 0; i < pDesc->StreamOutput.NumEntries; ++i) {
      const auto& so = pDesc->StreamOutput.pSODeclaration[i];
      NdHashVal(hash, so.Stream);
      if (so.SemanticName != nullptr) {
        const uint32_t name_len = static_cast<uint32_t>(std::strlen(so.SemanticName));
        NdHashVal(hash, name_len);
        if (name_len > 0) {
          NdHashCombine(hash, NdFnv1a64(so.SemanticName, name_len));
        }
      }
      NdHashVal(hash, so.SemanticIndex);
      NdHashVal(hash, so.StartComponent);
      NdHashVal(hash, so.ComponentCount);
      NdHashVal(hash, so.OutputSlot);
    }
  }
  NdHashVal(hash, pDesc->StreamOutput.NumStrides);
  if (pDesc->StreamOutput.NumStrides > 0 && pDesc->StreamOutput.pBufferStrides != nullptr) {
    for (UINT i = 0; i < pDesc->StreamOutput.NumStrides; ++i) {
      NdHashVal(hash, pDesc->StreamOutput.pBufferStrides[i]);
    }
  }
  NdHashVal(hash, pDesc->StreamOutput.RasterizedStream);

  NdHashVal(hash, pDesc->BlendState.AlphaToCoverageEnable);
  NdHashVal(hash, pDesc->BlendState.IndependentBlendEnable);
  for (int i = 0; i < 8; ++i) {
    const auto& rt = pDesc->BlendState.RenderTarget[i];
    NdHashVal(hash, rt.BlendEnable);
    NdHashVal(hash, rt.LogicOpEnable);
    NdHashVal(hash, rt.SrcBlend);
    NdHashVal(hash, rt.DestBlend);
    NdHashVal(hash, rt.BlendOp);
    NdHashVal(hash, rt.SrcBlendAlpha);
    NdHashVal(hash, rt.DestBlendAlpha);
    NdHashVal(hash, rt.BlendOpAlpha);
    NdHashVal(hash, rt.LogicOp);
    NdHashVal(hash, rt.RenderTargetWriteMask);
  }

  NdHashVal(hash, pDesc->SampleMask);

  const auto& rs = pDesc->RasterizerState;
  NdHashVal(hash, rs.FillMode);
  NdHashVal(hash, rs.CullMode);
  NdHashVal(hash, rs.FrontCounterClockwise);
  NdHashVal(hash, rs.DepthBias);
  NdHashVal(hash, rs.DepthBiasClamp);
  NdHashVal(hash, rs.SlopeScaledDepthBias);
  NdHashVal(hash, rs.DepthClipEnable);
  NdHashVal(hash, rs.MultisampleEnable);
  NdHashVal(hash, rs.AntialiasedLineEnable);
  NdHashVal(hash, rs.ForcedSampleCount);
  NdHashVal(hash, rs.ConservativeRaster);

  const auto& ds = pDesc->DepthStencilState;
  NdHashVal(hash, ds.DepthEnable);
  NdHashVal(hash, ds.DepthWriteMask);
  NdHashVal(hash, ds.DepthFunc);
  NdHashVal(hash, ds.StencilEnable);
  NdHashVal(hash, ds.StencilReadMask);
  NdHashVal(hash, ds.StencilWriteMask);
  auto hash_op = [&](const D3D12_DEPTH_STENCILOP_DESC& op) {
    NdHashVal(hash, op.StencilFailOp);
    NdHashVal(hash, op.StencilDepthFailOp);
    NdHashVal(hash, op.StencilPassOp);
    NdHashVal(hash, op.StencilFunc);
  };
  hash_op(ds.FrontFace);
  hash_op(ds.BackFace);

  NdHashVal(hash, pDesc->InputLayout.NumElements);
  if (pDesc->InputLayout.pInputElementDescs) {
    for (UINT i = 0; i < pDesc->InputLayout.NumElements; ++i) {
      const auto& elem = pDesc->InputLayout.pInputElementDescs[i];
      if (elem.SemanticName != nullptr) {
        NdHashCombine(hash, NdFnv1a64(elem.SemanticName, std::strlen(elem.SemanticName)));
      }
      NdHashVal(hash, elem.SemanticIndex);
      NdHashVal(hash, elem.Format);
      NdHashVal(hash, elem.InputSlot);
      NdHashVal(hash, elem.AlignedByteOffset);
      NdHashVal(hash, elem.InputSlotClass);
      NdHashVal(hash, elem.InstanceDataStepRate);
    }
  }

  NdHashVal(hash, pDesc->IBStripCutValue);
  NdHashVal(hash, pDesc->PrimitiveTopologyType);
  NdHashVal(hash, pDesc->NumRenderTargets);
  for (int i = 0; i < 8; ++i) {
    NdHashVal(hash, pDesc->RTVFormats[i]);
  }
  NdHashVal(hash, pDesc->DSVFormat);
  NdHashVal(hash, pDesc->SampleDesc.Count);
  NdHashVal(hash, pDesc->SampleDesc.Quality);
  NdHashVal(hash, pDesc->NodeMask);
  NdHashVal(hash, pDesc->Flags);

  return hash;
}

bool NdWriteAutonomousPsoToStream(std::ostream& out, const NdAutonomousPso& pso) {
  if (pso.root_sig_blob.size() > kMaxRootSig || pso.vs.size() > kMaxShaderBlob ||
      pso.ps.size() > kMaxShaderBlob || pso.gs.size() > kMaxShaderBlob ||
      pso.ds.size() > kMaxShaderBlob || pso.hs.size() > kMaxShaderBlob ||
      pso.input_elements.size() > kMaxInputElements ||
      pso.so_declarations.size() > kMaxSoEntries ||
      pso.so_buffer_strides.size() > kMaxSoStrides) {
    return false;
  }
  for (const auto& elem : pso.input_elements) {
    if (elem.semantic_name.size() > kMaxSemanticName) return false;
  }
  for (const auto& so : pso.so_declarations) {
    if (so.semantic_name.size() > kMaxSemanticName) return false;
  }

  out.write(reinterpret_cast<const char*>(&pso.pso_hash), sizeof(pso.pso_hash));
  out.write(reinterpret_cast<const char*>(&pso.root_sig_hash), sizeof(pso.root_sig_hash));
  const uint32_t rs_sz = static_cast<uint32_t>(pso.root_sig_blob.size());
  out.write(reinterpret_cast<const char*>(&rs_sz), sizeof(rs_sz));
  if (rs_sz > 0) {
    out.write(reinterpret_cast<const char*>(pso.root_sig_blob.data()), rs_sz);
  }

  D3D12_GRAPHICS_PIPELINE_STATE_DESC desc_copy = {};
  desc_copy.StreamOutput.NumEntries = pso.desc.StreamOutput.NumEntries;
  desc_copy.StreamOutput.NumStrides = pso.desc.StreamOutput.NumStrides;
  desc_copy.StreamOutput.RasterizedStream = pso.desc.StreamOutput.RasterizedStream;
  desc_copy.BlendState = pso.desc.BlendState;
  desc_copy.SampleMask = pso.desc.SampleMask;
  desc_copy.RasterizerState = pso.desc.RasterizerState;
  desc_copy.DepthStencilState = pso.desc.DepthStencilState;
  desc_copy.IBStripCutValue = pso.desc.IBStripCutValue;
  desc_copy.PrimitiveTopologyType = pso.desc.PrimitiveTopologyType;
  desc_copy.NumRenderTargets = pso.desc.NumRenderTargets;
  std::memcpy(desc_copy.RTVFormats, pso.desc.RTVFormats, sizeof(desc_copy.RTVFormats));
  desc_copy.DSVFormat = pso.desc.DSVFormat;
  desc_copy.SampleDesc = pso.desc.SampleDesc;
  desc_copy.NodeMask = pso.desc.NodeMask;
  desc_copy.Flags = pso.desc.Flags;
  out.write(reinterpret_cast<const char*>(&desc_copy), sizeof(desc_copy));

  auto write_blob = [&](const std::vector<uint8_t>& blob) {
    const uint32_t sz = static_cast<uint32_t>(blob.size());
    out.write(reinterpret_cast<const char*>(&sz), sizeof(sz));
    if (sz > 0) {
      out.write(reinterpret_cast<const char*>(blob.data()), sz);
    }
  };
  write_blob(pso.vs);
  write_blob(pso.ps);
  write_blob(pso.gs);
  write_blob(pso.ds);
  write_blob(pso.hs);

  const uint32_t num_elems = static_cast<uint32_t>(pso.input_elements.size());
  out.write(reinterpret_cast<const char*>(&num_elems), sizeof(num_elems));
  for (const auto& elem : pso.input_elements) {
    const uint16_t name_len = static_cast<uint16_t>(elem.semantic_name.size());
    out.write(reinterpret_cast<const char*>(&name_len), sizeof(name_len));
    if (name_len > 0) {
      out.write(elem.semantic_name.data(), name_len);
    }
    out.write(reinterpret_cast<const char*>(&elem.semantic_index), sizeof(elem.semantic_index));
    out.write(reinterpret_cast<const char*>(&elem.format), sizeof(elem.format));
    out.write(reinterpret_cast<const char*>(&elem.input_slot), sizeof(elem.input_slot));
    out.write(reinterpret_cast<const char*>(&elem.aligned_byte_offset),
              sizeof(elem.aligned_byte_offset));
    out.write(reinterpret_cast<const char*>(&elem.input_slot_class), sizeof(elem.input_slot_class));
    out.write(reinterpret_cast<const char*>(&elem.instance_data_step_rate),
              sizeof(elem.instance_data_step_rate));
  }

  const uint32_t num_so = static_cast<uint32_t>(pso.so_declarations.size());
  out.write(reinterpret_cast<const char*>(&num_so), sizeof(num_so));
  for (const auto& so : pso.so_declarations) {
    out.write(reinterpret_cast<const char*>(&so.stream), sizeof(so.stream));
    const uint16_t name_len = static_cast<uint16_t>(so.semantic_name.size());
    out.write(reinterpret_cast<const char*>(&name_len), sizeof(name_len));
    if (name_len > 0) {
      out.write(so.semantic_name.data(), name_len);
    }
    out.write(reinterpret_cast<const char*>(&so.semantic_index), sizeof(so.semantic_index));
    out.write(reinterpret_cast<const char*>(&so.start_component), sizeof(so.start_component));
    out.write(reinterpret_cast<const char*>(&so.component_count), sizeof(so.component_count));
    out.write(reinterpret_cast<const char*>(&so.output_slot), sizeof(so.output_slot));
  }

  const uint32_t num_strides = static_cast<uint32_t>(pso.so_buffer_strides.size());
  out.write(reinterpret_cast<const char*>(&num_strides), sizeof(num_strides));
  if (num_strides > 0) {
    out.write(reinterpret_cast<const char*>(pso.so_buffer_strides.data()),
              static_cast<std::streamsize>(num_strides) * sizeof(UINT));
  }
  return out.good();
}

bool NdReadAutonomousPsoFromStream(std::istream& in, NdAutonomousPso& pso) {
  pso = NdAutonomousPso{};

  if (!in.read(reinterpret_cast<char*>(&pso.pso_hash), sizeof(pso.pso_hash))) return false;
  if (!in.read(reinterpret_cast<char*>(&pso.root_sig_hash), sizeof(pso.root_sig_hash))) {
    return false;
  }
  uint32_t rs_sz = 0;
  if (!in.read(reinterpret_cast<char*>(&rs_sz), sizeof(rs_sz))) return false;
  if (rs_sz > kMaxRootSig) return false;
  pso.root_sig_blob.resize(rs_sz);
  if (rs_sz > 0 && !in.read(reinterpret_cast<char*>(pso.root_sig_blob.data()), rs_sz)) {
    return false;
  }

  if (!in.read(reinterpret_cast<char*>(&pso.desc), sizeof(pso.desc))) return false;

  // Null all raw pointer fields deserialized from disk; workers reconstruct them from blobs.
  pso.desc.pRootSignature = nullptr;
  pso.desc.VS = {};
  pso.desc.PS = {};
  pso.desc.DS = {};
  pso.desc.HS = {};
  pso.desc.GS = {};
  pso.desc.InputLayout = {};
  pso.desc.StreamOutput.pSODeclaration = nullptr;
  pso.desc.StreamOutput.NumEntries = 0;
  pso.desc.StreamOutput.pBufferStrides = nullptr;
  pso.desc.StreamOutput.NumStrides = 0;
  pso.desc.CachedPSO = {};

  auto read_blob = [&](std::vector<uint8_t>& blob) -> bool {
    uint32_t sz = 0;
    if (!in.read(reinterpret_cast<char*>(&sz), sizeof(sz))) return false;
    if (sz > kMaxShaderBlob) return false;
    blob.resize(sz);
    if (sz > 0 && !in.read(reinterpret_cast<char*>(blob.data()), sz)) return false;
    return true;
  };
  if (!read_blob(pso.vs)) return false;
  if (!read_blob(pso.ps)) return false;
  if (!read_blob(pso.gs)) return false;
  if (!read_blob(pso.ds)) return false;
  if (!read_blob(pso.hs)) return false;

  uint32_t num_elems = 0;
  if (!in.read(reinterpret_cast<char*>(&num_elems), sizeof(num_elems))) return false;
  if (num_elems > kMaxInputElements) return false;
  pso.input_elements.clear();
  pso.input_elements.reserve(num_elems);
  for (uint32_t i = 0; i < num_elems; ++i) {
    NdAutonomousInputElement elem;
    uint16_t name_len = 0;
    if (!in.read(reinterpret_cast<char*>(&name_len), sizeof(name_len))) return false;
    if (name_len > kMaxSemanticName) return false;
    elem.semantic_name.resize(name_len);
    if (name_len > 0 && !in.read(&elem.semantic_name[0], name_len)) return false;
    if (!in.read(reinterpret_cast<char*>(&elem.semantic_index), sizeof(elem.semantic_index))) {
      return false;
    }
    if (!in.read(reinterpret_cast<char*>(&elem.format), sizeof(elem.format))) return false;
    if (!in.read(reinterpret_cast<char*>(&elem.input_slot), sizeof(elem.input_slot))) return false;
    if (!in.read(reinterpret_cast<char*>(&elem.aligned_byte_offset),
                 sizeof(elem.aligned_byte_offset))) {
      return false;
    }
    if (!in.read(reinterpret_cast<char*>(&elem.input_slot_class), sizeof(elem.input_slot_class))) {
      return false;
    }
    if (!in.read(reinterpret_cast<char*>(&elem.instance_data_step_rate),
                 sizeof(elem.instance_data_step_rate))) {
      return false;
    }
    pso.input_elements.push_back(std::move(elem));
  }

  uint32_t num_so = 0;
  if (!in.read(reinterpret_cast<char*>(&num_so), sizeof(num_so))) return false;
  if (num_so > kMaxSoEntries) return false;
  pso.so_declarations.clear();
  pso.so_declarations.reserve(num_so);
  for (uint32_t i = 0; i < num_so; ++i) {
    NdAutonomousSoDeclaration so;
    if (!in.read(reinterpret_cast<char*>(&so.stream), sizeof(so.stream))) return false;
    uint16_t name_len = 0;
    if (!in.read(reinterpret_cast<char*>(&name_len), sizeof(name_len))) return false;
    if (name_len > kMaxSemanticName) return false;
    so.semantic_name.resize(name_len);
    if (name_len > 0 && !in.read(&so.semantic_name[0], name_len)) return false;
    if (!in.read(reinterpret_cast<char*>(&so.semantic_index), sizeof(so.semantic_index))) {
      return false;
    }
    if (!in.read(reinterpret_cast<char*>(&so.start_component), sizeof(so.start_component))) {
      return false;
    }
    if (!in.read(reinterpret_cast<char*>(&so.component_count), sizeof(so.component_count))) {
      return false;
    }
    if (!in.read(reinterpret_cast<char*>(&so.output_slot), sizeof(so.output_slot))) return false;
    pso.so_declarations.push_back(std::move(so));
  }

  uint32_t num_strides = 0;
  if (!in.read(reinterpret_cast<char*>(&num_strides), sizeof(num_strides))) return false;
  if (num_strides > kMaxSoStrides) return false;
  pso.so_buffer_strides.resize(num_strides);
  if (num_strides > 0 &&
      !in.read(reinterpret_cast<char*>(pso.so_buffer_strides.data()),
               static_cast<std::streamsize>(num_strides) * sizeof(UINT))) {
    return false;
  }
  return true;
}

std::mutex g_pso_bundle_mtx;
std::mutex g_pso_bundle_lifecycle_mtx;
std::mutex g_pso_bundle_join_mtx;
std::mutex g_pso_bundle_file_mtx;
std::deque<NdAutonomousPso> g_pso_bundle_queue;
std::unordered_set<uint64_t> g_bundle_enqueued_hashes;
std::atomic<bool> g_pso_bundle_worker_running{false};
bool g_pso_bundle_worker_started = false;
bool g_pso_bundle_shutdown = false;
std::thread g_pso_bundle_worker;
std::condition_variable g_pso_bundle_cv;

void NdOpenOrRepairBundle(const std::filesystem::path& bundle_path,
                          std::unordered_set<uint64_t>* out_hashes,
                          std::vector<NdAutonomousPso>* out_items) {
  std::error_code ec;
  bool valid_header = false;
  uint64_t valid_end = 0;

  if (std::filesystem::exists(bundle_path, ec)) {
    std::ifstream in(bundle_path, std::ios::binary);
    char magic[sizeof(kBundleMagic)] = {};
    if (in.is_open() && in.read(magic, sizeof(magic)) &&
        std::memcmp(magic, kBundleMagic, sizeof(kBundleMagic)) == 0) {
      valid_header = true;
      valid_end = static_cast<uint64_t>(in.tellg());
      NdAutonomousPso item;
      while (NdReadAutonomousPsoFromStream(in, item)) {
        const std::streampos pos = in.tellg();
        if (pos < 0) break;
        valid_end = static_cast<uint64_t>(pos);
        if (out_hashes) out_hashes->insert(item.pso_hash);
        if (out_items) out_items->push_back(std::move(item));
      }
    }
  }

  if (out_items != nullptr) {
    // Read-only scan (precompile worker).
    return;
  }

  if (!valid_header) {
    std::ofstream reset(bundle_path, std::ios::binary | std::ios::trunc);
    if (reset.is_open()) {
      reset.write(kBundleMagic, sizeof(kBundleMagic));
      reset.flush();
    }
  } else {
    const uint64_t disk_size = std::filesystem::file_size(bundle_path, ec);
    if (!ec && disk_size > valid_end) {
      std::filesystem::resize_file(bundle_path, valid_end, ec);
    }
  }
}

void NdStartBundleWorkerIfNeeded() {
  std::lock_guard<std::mutex> life_lk(g_pso_bundle_lifecycle_mtx);
  if (g_pso_bundle_worker_started || g_pso_bundle_shutdown) return;

  const std::filesystem::path cache_dir = NdGetCacheDir();
  const std::filesystem::path bundle_path = cache_dir / "nd_pso_bundle.bin";
  g_pso_bundle_worker_started = true;
  g_pso_bundle_worker_running.store(true, std::memory_order_release);
  g_pso_bundle_worker = std::thread([cache_dir, bundle_path]() {
    std::error_code ec;
    std::filesystem::create_directories(cache_dir, ec);

    std::unordered_set<uint64_t> disk_hashes;
    uint64_t committed_size = 0;
    {
      std::lock_guard<std::mutex> file_lk(g_pso_bundle_file_mtx);
      NdOpenOrRepairBundle(bundle_path, &disk_hashes, nullptr);
      std::error_code size_ec;
      committed_size = std::filesystem::file_size(bundle_path, size_ec);
      if (size_ec || committed_size < sizeof(kBundleMagic)) {
        committed_size = sizeof(kBundleMagic);
      }
    }

    uint32_t open_fail_retries = 0;
    while (true) {
      std::vector<NdAutonomousPso> batch;
      {
        std::unique_lock<std::mutex> lk(g_pso_bundle_mtx);
        g_pso_bundle_cv.wait_for(lk, std::chrono::milliseconds(200), []() {
          return !g_pso_bundle_queue.empty() ||
                 !g_pso_bundle_worker_running.load(std::memory_order_acquire);
        });
        while (!g_pso_bundle_queue.empty()) {
          batch.push_back(std::move(g_pso_bundle_queue.front()));
          g_pso_bundle_queue.pop_front();
        }
        if (batch.empty() && !g_pso_bundle_worker_running.load(std::memory_order_acquire)) {
          break;
        }
      }
      if (!batch.empty()) {
        std::lock_guard<std::mutex> file_lk(g_pso_bundle_file_mtx);
        std::ofstream out(bundle_path, std::ios::binary | std::ios::app);
        if (!out.is_open()) {
          static bool s_warned_open_fail = false;
          if (!s_warned_open_fail) {
            REXLOG_WARN("neverdead: [PSO-BUNDLE] Failed to open bundle file for append: {}",
                        bundle_path.string());
            s_warned_open_fail = true;
          }
          if (open_fail_retries < 5) {
            ++open_fail_retries;
            std::lock_guard<std::mutex> lk(g_pso_bundle_mtx);
            for (auto it = batch.rbegin(); it != batch.rend(); ++it) {
              g_pso_bundle_queue.push_front(std::move(*it));
            }
          } else {
            REXLOG_WARN("neverdead: [PSO-BUNDLE] Dropping {} unwritten bundle items after max retries",
                        batch.size());
            open_fail_retries = 0;
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(50));
          continue;
        }
        open_fail_retries = 0;

        std::vector<uint64_t> pending_hashes;
        uint64_t batch_bytes = 0;
        bool write_ok = true;

        for (const auto& item : batch) {
          if (disk_hashes.find(item.pso_hash) == disk_hashes.end()) {
            std::ostringstream rec_ss(std::ios::binary);
            if (!NdWriteAutonomousPsoToStream(rec_ss, item)) {
              continue;
            }
            const std::string rec_data = rec_ss.str();
            if (!rec_data.empty()) {
              out.write(rec_data.data(), static_cast<std::streamsize>(rec_data.size()));
              if (!out.good()) {
                write_ok = false;
                break;
              }
              batch_bytes += rec_data.size();
              pending_hashes.push_back(item.pso_hash);
            }
          }
        }

        if (write_ok && !pending_hashes.empty()) {
          out.flush();
          if (!out.good()) {
            write_ok = false;
          }
        }

        if (!write_ok) {
          out.close();
          std::error_code rollback_ec;
          std::filesystem::resize_file(bundle_path, committed_size, rollback_ec);
          REXLOG_WARN("neverdead: [PSO-BUNDLE] Write/flush failed, rolled back to committed size {}",
                      committed_size);
        } else {
          for (uint64_t h : pending_hashes) {
            disk_hashes.insert(h);
          }
          committed_size += batch_bytes;
        }
      }
    }
  });
}

void NdEnqueuePsoToBundle(const NdAutonomousPso& item) {
  if (NdQaDisabled("bundle")) return;
  NdStartBundleWorkerIfNeeded();
  bool should_notify = false;
  {
    std::lock_guard<std::mutex> lk(g_pso_bundle_mtx);
    if (g_pso_bundle_shutdown) return;
    if (g_bundle_enqueued_hashes.insert(item.pso_hash).second) {
      g_pso_bundle_queue.push_back(item);
      should_notify = true;
    }
  }
  if (should_notify) {
    g_pso_bundle_cv.notify_one();
  }
}

void NdEnsurePipelineLibrary(ID3D12Device* device) {
  if (NdQaDisabled("library") || !device) return;
  if (g_pipeline_library_init_attempted.load(std::memory_order_acquire)) return;
  std::lock_guard<std::mutex> lib_lk(g_pipeline_library_mtx);
  if (g_pipeline_library_init_attempted.load(std::memory_order_relaxed)) return;

  ID3D12Device1* dev1 = nullptr;
  if (SUCCEEDED(device->QueryInterface(IID_PPV_ARGS(&dev1))) && dev1) {
    std::vector<uint8_t> init_blob;
    const std::filesystem::path pso_path = NdGetCacheDir() / "d3d12_pipeline_cache.bin";
    std::error_code ec;
    if (std::filesystem::exists(pso_path, ec)) {
      std::ifstream in(pso_path, std::ios::binary | std::ios::ate);
      if (in.is_open()) {
        const std::streamoff sz_off = in.tellg();
        if (sz_off > 0) {
          const size_t sz = static_cast<size_t>(sz_off);
          init_blob.resize(sz);
          in.seekg(0, std::ios::beg);
          if (!in.read(reinterpret_cast<char*>(init_blob.data()), sz)) {
            init_blob.clear();
          }
        }
      }
    }
    if (!init_blob.empty()) {
      const HRESULT hr_lib = dev1->CreatePipelineLibrary(
          init_blob.data(), init_blob.size(), IID_PPV_ARGS(&g_d3d12_pipeline_library));
      if (SUCCEEDED(hr_lib) && g_d3d12_pipeline_library) {
        g_pipeline_library_has_disk_blob.store(true, std::memory_order_release);
      }
    }
    dev1->Release();
  }
  g_pipeline_library_init_attempted.store(true, std::memory_order_release);
}

bool NdTryAcquireFromMemoryCache(uint64_t hash, REFIID riid, void** ppPipelineState, HRESULT& out_hr) {
  ID3D12PipelineState* cached = nullptr;
  AcquireSRWLockShared(&g_pso_cache_srw);
  auto it = g_pso_memory_cache.find(hash);
  if (it != g_pso_memory_cache.end() && it->second != nullptr) {
    cached = it->second;
    cached->AddRef();
  }
  ReleaseSRWLockShared(&g_pso_cache_srw);
  if (!cached) return false;
  out_hr = cached->QueryInterface(riid, ppPipelineState);
  cached->Release();
  return true;
}

void NdStoreMemoryCacheEntry(uint64_t hash, ID3D12PipelineState* pso) {
  if (!pso) return;
  AcquireSRWLockExclusive(&g_pso_cache_srw);
  auto [ins_it, inserted] = g_pso_memory_cache.emplace(hash, nullptr);
  if (inserted || ins_it->second == nullptr) {
    pso->AddRef();
    ins_it->second = pso;
  }
  ReleaseSRWLockExclusive(&g_pso_cache_srw);
}

extern "C" HRESULT STDMETHODCALLTYPE NdCreateRootSignatureDetour(
    ID3D12Device* This,
    UINT nodeMask,
    const void* pBlobWithRootSignature,
    SIZE_T blobLengthInBytes,
    REFIID riid,
    void** ppvRootSignature) {
  if (!g_real_create_root_signature) return E_FAIL;
  const HRESULT hr = g_real_create_root_signature(
      This, nodeMask, pBlobWithRootSignature, blobLengthInBytes, riid, ppvRootSignature);
  if (NdQaDisabled("rootsig")) return hr;
  if (SUCCEEDED(hr) && ppvRootSignature && *ppvRootSignature && pBlobWithRootSignature &&
      blobLengthInBytes > 0) {
    ID3D12RootSignature* rs = nullptr;
    IUnknown* unk = reinterpret_cast<IUnknown*>(*ppvRootSignature);
    if (SUCCEEDED(unk->QueryInterface(IID_PPV_ARGS(&rs))) && rs) {
      const uint64_t hash = NdFnv1a64(pBlobWithRootSignature, blobLengthInBytes);
      ID3D12RootSignature* old_rs = nullptr;
      AcquireSRWLockExclusive(&g_root_sig_srw);
      NdRootSigRecord entry;
      entry.blob.assign(
          reinterpret_cast<const uint8_t*>(pBlobWithRootSignature),
          reinterpret_cast<const uint8_t*>(pBlobWithRootSignature) + blobLengthInBytes);
      entry.hash = hash;
      g_root_sig_by_ptr[*ppvRootSignature] = std::move(entry);
      auto [rs_it, rs_ins] = g_root_sig_by_hash.emplace(hash, rs);
      if (!rs_ins) {
        old_rs = rs_it->second;
        rs_it->second = rs;
      }
      ReleaseSRWLockExclusive(&g_root_sig_srw);
      if (old_rs) old_rs->Release();
    }
  }
  return hr;
}

extern "C" HRESULT STDMETHODCALLTYPE NdCreateGraphicsPipelineStateDetour(
    ID3D12Device* This,
    const D3D12_GRAPHICS_PIPELINE_STATE_DESC* pDesc,
    REFIID riid,
    void** ppPipelineState) {
  if (NdQaDisabled("detour")) {
    return g_real_create_graphics_pipeline_state
               ? g_real_create_graphics_pipeline_state(This, pDesc, riid, ppPipelineState)
               : E_FAIL;
  }

  if (!pDesc || !ppPipelineState || !g_real_create_graphics_pipeline_state) {
    if (g_real_create_graphics_pipeline_state) {
      return g_real_create_graphics_pipeline_state(This, pDesc, riid, ppPipelineState);
    }
    return E_FAIL;
  }

  uint64_t root_sig_hash = 0;
  std::vector<uint8_t> root_blob;
  if (pDesc->pRootSignature != nullptr) {
    AcquireSRWLockShared(&g_root_sig_srw);
    auto r_it = g_root_sig_by_ptr.find(pDesc->pRootSignature);
    if (r_it != g_root_sig_by_ptr.end()) {
      root_sig_hash = r_it->second.hash;
      root_blob = r_it->second.blob;
    }
    ReleaseSRWLockShared(&g_root_sig_srw);
  }

  const uint64_t hash = NdComputeDeterministicPsoHash(pDesc, root_sig_hash);

  HRESULT cache_hr = E_FAIL;
  if (NdTryAcquireFromMemoryCache(hash, riid, ppPipelineState, cache_hr)) {
    return cache_hr;
  }

  {
    std::unique_lock<std::mutex> lk(g_pso_inflight_mtx);
    while (g_pso_compiling_hashes.find(hash) != g_pso_compiling_hashes.end()) {
      g_pso_inflight_cv.wait(lk);
    }
    if (NdTryAcquireFromMemoryCache(hash, riid, ppPipelineState, cache_hr)) {
      return cache_hr;
    }
    g_pso_compiling_hashes.insert(hash);
  }

  struct InFlightGuard {
    uint64_t h;
    bool released = false;
    void release() {
      if (released) return;
      released = true;
      {
        std::lock_guard<std::mutex> lk(g_pso_inflight_mtx);
        g_pso_compiling_hashes.erase(h);
      }
      g_pso_inflight_cv.notify_all();
    }
    ~InFlightGuard() { release(); }
  } inflight_guard{hash};

  wchar_t pso_name[64];
  swprintf_s(pso_name, L"PSO_%016llX", hash);

  NdEnsurePipelineLibrary(This);

  if (g_pipeline_library_has_disk_blob.load(std::memory_order_acquire) &&
      g_d3d12_pipeline_library) {
    ID3D12PipelineState* lib_pso = nullptr;
    HRESULT hr_lib = E_FAIL;
    {
      std::lock_guard<std::mutex> lib_lk(g_pipeline_library_mtx);
      hr_lib = g_d3d12_pipeline_library->LoadGraphicsPipeline(
          pso_name, pDesc, IID_PPV_ARGS(&lib_pso));
    }
    if (SUCCEEDED(hr_lib) && lib_pso) {
      NdStoreMemoryCacheEntry(hash, lib_pso);
      lib_pso->Release();
      HRESULT lib_hr = E_FAIL;
      if (NdTryAcquireFromMemoryCache(hash, riid, ppPipelineState, lib_hr)) {
        return lib_hr;
      }
    }
  }

  struct ActiveCompileGuard {
    ActiveCompileGuard() {
      g_active_pso_compiles.fetch_add(1, std::memory_order_acq_rel);
      g_last_shader_compile_tick_ms.store(GetTickCount64(), std::memory_order_release);
    }
    ~ActiveCompileGuard() {
      g_last_shader_compile_tick_ms.store(GetTickCount64(), std::memory_order_release);
      g_active_pso_compiles.fetch_sub(1, std::memory_order_acq_rel);
    }
  } active_guard;

  // Compile unlocked so ReXGlue's worker threads and the command processor run concurrently.
  const HRESULT hr = g_real_create_graphics_pipeline_state(This, pDesc, riid, ppPipelineState);
  if (SUCCEEDED(hr) && ppPipelineState && *ppPipelineState) {
    IUnknown* unk = reinterpret_cast<IUnknown*>(*ppPipelineState);
    ID3D12PipelineState* compiled_pso = nullptr;
    if (SUCCEEDED(unk->QueryInterface(IID_PPV_ARGS(&compiled_pso))) && compiled_pso) {
      NdStoreMemoryCacheEntry(hash, compiled_pso);
      inflight_guard.release();
      if (g_pipeline_library_has_disk_blob.load(std::memory_order_acquire) &&
          g_d3d12_pipeline_library) {
        std::lock_guard<std::mutex> lib_lk(g_pipeline_library_mtx);
        g_d3d12_pipeline_library->StorePipeline(pso_name, compiled_pso);
      }
      compiled_pso->Release();
    } else {
      inflight_guard.release();
    }

    NdAutonomousPso auto_pso;
    auto_pso.pso_hash = hash;
    auto_pso.root_sig_hash = root_sig_hash;
    auto_pso.root_sig_blob = std::move(root_blob);
    auto_pso.desc = *pDesc;
    auto copy_bytecode = [](const D3D12_SHADER_BYTECODE& bc, std::vector<uint8_t>& dst) {
      if (bc.pShaderBytecode && bc.BytecodeLength > 0) {
        const auto* ptr = reinterpret_cast<const uint8_t*>(bc.pShaderBytecode);
        dst.assign(ptr, ptr + bc.BytecodeLength);
      }
    };
    copy_bytecode(pDesc->VS, auto_pso.vs);
    copy_bytecode(pDesc->PS, auto_pso.ps);
    copy_bytecode(pDesc->GS, auto_pso.gs);
    copy_bytecode(pDesc->DS, auto_pso.ds);
    copy_bytecode(pDesc->HS, auto_pso.hs);

    for (UINT i = 0; i < pDesc->InputLayout.NumElements; ++i) {
      const auto& elem = pDesc->InputLayout.pInputElementDescs[i];
      NdAutonomousInputElement ie;
      if (elem.SemanticName) ie.semantic_name = elem.SemanticName;
      ie.semantic_index = elem.SemanticIndex;
      ie.format = elem.Format;
      ie.input_slot = elem.InputSlot;
      ie.aligned_byte_offset = elem.AlignedByteOffset;
      ie.input_slot_class = elem.InputSlotClass;
      ie.instance_data_step_rate = elem.InstanceDataStepRate;
      auto_pso.input_elements.push_back(std::move(ie));
    }

    if (pDesc->StreamOutput.pSODeclaration != nullptr && pDesc->StreamOutput.NumEntries > 0) {
      const UINT count = std::min<UINT>(pDesc->StreamOutput.NumEntries, kMaxSoEntries);
      auto_pso.so_declarations.reserve(count);
      for (UINT i = 0; i < count; ++i) {
        const auto& so = pDesc->StreamOutput.pSODeclaration[i];
        NdAutonomousSoDeclaration asd;
        asd.stream = so.Stream;
        if (so.SemanticName) asd.semantic_name = so.SemanticName;
        asd.semantic_index = so.SemanticIndex;
        asd.start_component = so.StartComponent;
        asd.component_count = so.ComponentCount;
        asd.output_slot = so.OutputSlot;
        auto_pso.so_declarations.push_back(std::move(asd));
      }
    }
    if (pDesc->StreamOutput.pBufferStrides != nullptr && pDesc->StreamOutput.NumStrides > 0) {
      const UINT count = std::min<UINT>(pDesc->StreamOutput.NumStrides, kMaxSoStrides);
      auto_pso.so_buffer_strides.assign(pDesc->StreamOutput.pBufferStrides,
                                        pDesc->StreamOutput.pBufferStrides + count);
    }

    NdEnqueuePsoToBundle(auto_pso);

    g_last_shader_compile_tick_ms.store(GetTickCount64(), std::memory_order_release);
    g_compiled_shader_count.fetch_add(1, std::memory_order_release);
    return hr;
  }

  return hr;
}

void NdPrecompilePsoBundleWorker(ID3D12Device* device) {
  if (!device || !g_real_create_graphics_pipeline_state) return;
  static std::atomic<bool> s_precompiling{false};
  if (s_precompiling.exchange(true)) return;

  const std::filesystem::path bundle_path = NdGetCacheDir() / "nd_pso_bundle.bin";
  std::vector<NdAutonomousPso> all_psos;
  {
    std::lock_guard<std::mutex> file_lk(g_pso_bundle_file_mtx);
    NdOpenOrRepairBundle(bundle_path, nullptr, &all_psos);
  }

  if (all_psos.empty()) {
    s_precompiling.store(false);
    return;
  }

  std::vector<NdAutonomousPso*> to_compile;
  AcquireSRWLockShared(&g_pso_cache_srw);
  for (auto& item : all_psos) {
    if (g_pso_memory_cache.find(item.pso_hash) == g_pso_memory_cache.end()) {
      to_compile.push_back(&item);
    }
  }
  ReleaseSRWLockShared(&g_pso_cache_srw);

  if (to_compile.empty()) {
    s_precompiling.store(false);
    return;
  }

  NdEnsurePipelineLibrary(device);

  g_is_precompiling_shaders.store(true, std::memory_order_release);
  g_precompile_total.store(static_cast<uint32_t>(to_compile.size()), std::memory_order_release);
  REXLOG_INFO("neverdead: [PSO-PRECOMPILE] Precompiling {} D3D12 pipelines from bundle...",
              to_compile.size());

  // Collect missing root signatures under g_root_sig_srw, then invoke device->CreateRootSignature
  // WITHOUT holding g_root_sig_srw (the CreateRootSignature detour re-acquires g_root_sig_srw).
  struct MissingRootSig {
    uint64_t hash;
    std::vector<uint8_t> blob;
  };
  std::vector<MissingRootSig> missing_roots;
  {
    std::unordered_set<uint64_t> seen_missing;
    AcquireSRWLockShared(&g_root_sig_srw);
    for (auto* item : to_compile) {
      if (item->root_sig_hash != 0 && !item->root_sig_blob.empty() &&
          g_root_sig_by_hash.find(item->root_sig_hash) == g_root_sig_by_hash.end() &&
          seen_missing.insert(item->root_sig_hash).second) {
        missing_roots.push_back({item->root_sig_hash, item->root_sig_blob});
      }
    }
    ReleaseSRWLockShared(&g_root_sig_srw);
  }
  for (const auto& mr : missing_roots) {
    ID3D12RootSignature* rs = nullptr;
    if (SUCCEEDED(device->CreateRootSignature(0, mr.blob.data(), mr.blob.size(),
                                              IID_PPV_ARGS(&rs))) &&
        rs) {
      // Fallback insert if hooks were disabled; release extra local ref either way.
      bool inserted_fallback = false;
      {
        AcquireSRWLockExclusive(&g_root_sig_srw);
        if (g_root_sig_by_hash.find(mr.hash) == g_root_sig_by_hash.end()) {
          g_root_sig_by_hash[mr.hash] = rs;
          inserted_fallback = true;
        }
        ReleaseSRWLockExclusive(&g_root_sig_srw);
      }
      if (!inserted_fallback) {
        rs->Release();
      }
    }
  }

  unsigned int num_threads = std::thread::hardware_concurrency();
  if (num_threads == 0) num_threads = 2;
  // Cap concurrent pipeline compilation workers to avoid D3D12Core lock contention.
  if (num_threads > 4) num_threads = 4;

  std::atomic<size_t> next_index{0};
  std::vector<std::thread> workers;
  workers.reserve(num_threads);

  for (unsigned int t = 0; t < num_threads; ++t) {
    workers.emplace_back([&]() {
      while (true) {
        const size_t idx = next_index.fetch_add(1);
        if (idx >= to_compile.size()) break;

        NdAutonomousPso* item = to_compile[idx];
        D3D12_GRAPHICS_PIPELINE_STATE_DESC desc = item->desc;
        {
          AcquireSRWLockShared(&g_root_sig_srw);
          auto r_it = g_root_sig_by_hash.find(item->root_sig_hash);
          if (r_it != g_root_sig_by_hash.end() && r_it->second != nullptr) {
            desc.pRootSignature = r_it->second;
            desc.pRootSignature->AddRef();
          }
          ReleaseSRWLockShared(&g_root_sig_srw);
        }
        struct RootSigRef {
          ID3D12RootSignature* ptr;
          ~RootSigRef() {
            if (ptr) ptr->Release();
          }
        } rs_ref{desc.pRootSignature};

        if (!item->vs.empty()) {
          desc.VS.pShaderBytecode = item->vs.data();
          desc.VS.BytecodeLength = item->vs.size();
        }
        if (!item->ps.empty()) {
          desc.PS.pShaderBytecode = item->ps.data();
          desc.PS.BytecodeLength = item->ps.size();
        }
        if (!item->gs.empty()) {
          desc.GS.pShaderBytecode = item->gs.data();
          desc.GS.BytecodeLength = item->gs.size();
        }
        if (!item->ds.empty()) {
          desc.DS.pShaderBytecode = item->ds.data();
          desc.DS.BytecodeLength = item->ds.size();
        }
        if (!item->hs.empty()) {
          desc.HS.pShaderBytecode = item->hs.data();
          desc.HS.BytecodeLength = item->hs.size();
        }

        std::vector<D3D12_INPUT_ELEMENT_DESC> elements;
        elements.reserve(item->input_elements.size());
        for (const auto& ie : item->input_elements) {
          D3D12_INPUT_ELEMENT_DESC ed{};
          ed.SemanticName = ie.semantic_name.c_str();
          ed.SemanticIndex = ie.semantic_index;
          ed.Format = ie.format;
          ed.InputSlot = ie.input_slot;
          ed.AlignedByteOffset = ie.aligned_byte_offset;
          ed.InputSlotClass = ie.input_slot_class;
          ed.InstanceDataStepRate = ie.instance_data_step_rate;
          elements.push_back(ed);
        }
        if (!elements.empty()) {
          desc.InputLayout.pInputElementDescs = elements.data();
          desc.InputLayout.NumElements = static_cast<UINT>(elements.size());
        }

        std::vector<D3D12_SO_DECLARATION_ENTRY> so_entries;
        so_entries.reserve(item->so_declarations.size());
        for (const auto& so : item->so_declarations) {
          D3D12_SO_DECLARATION_ENTRY entry{};
          entry.Stream = so.stream;
          entry.SemanticName = so.semantic_name.empty() ? nullptr : so.semantic_name.c_str();
          entry.SemanticIndex = so.semantic_index;
          entry.StartComponent = so.start_component;
          entry.ComponentCount = so.component_count;
          entry.OutputSlot = so.output_slot;
          so_entries.push_back(entry);
        }
        if (!so_entries.empty()) {
          desc.StreamOutput.pSODeclaration = so_entries.data();
          desc.StreamOutput.NumEntries = static_cast<UINT>(so_entries.size());
        }
        if (!item->so_buffer_strides.empty()) {
          desc.StreamOutput.pBufferStrides = item->so_buffer_strides.data();
          desc.StreamOutput.NumStrides = static_cast<UINT>(item->so_buffer_strides.size());
        }

        wchar_t pso_name[64];
        swprintf_s(pso_name, L"PSO_%016llX", item->pso_hash);

        ID3D12PipelineState* pso = nullptr;
        bool already_cached = false;
        {
          std::unique_lock<std::mutex> lk(g_pso_inflight_mtx);
          while (g_pso_compiling_hashes.find(item->pso_hash) != g_pso_compiling_hashes.end()) {
            g_pso_inflight_cv.wait(lk);
          }
          AcquireSRWLockShared(&g_pso_cache_srw);
          already_cached =
              g_pso_memory_cache.find(item->pso_hash) != g_pso_memory_cache.end();
          ReleaseSRWLockShared(&g_pso_cache_srw);
          if (!already_cached) {
            g_pso_compiling_hashes.insert(item->pso_hash);
          }
        }
        if (already_cached) continue;

        struct PrecompileInFlightGuard {
          uint64_t h;
          ~PrecompileInFlightGuard() {
            {
              std::lock_guard<std::mutex> lk(g_pso_inflight_mtx);
              g_pso_compiling_hashes.erase(h);
            }
            g_pso_inflight_cv.notify_all();
          }
        } inflight_guard{item->pso_hash};

        if (g_pipeline_library_has_disk_blob.load(std::memory_order_acquire) &&
            g_d3d12_pipeline_library) {
          std::lock_guard<std::mutex> lib_lk(g_pipeline_library_mtx);
          g_d3d12_pipeline_library->LoadGraphicsPipeline(pso_name, &desc, IID_PPV_ARGS(&pso));
        }
        if (!pso && g_real_create_graphics_pipeline_state && desc.pRootSignature) {
          g_real_create_graphics_pipeline_state(device, &desc, IID_PPV_ARGS(&pso));
          if (pso && g_pipeline_library_has_disk_blob.load(std::memory_order_acquire) &&
              g_d3d12_pipeline_library) {
            std::lock_guard<std::mutex> lib_lk(g_pipeline_library_mtx);
            g_d3d12_pipeline_library->StorePipeline(pso_name, pso);
          }
        }

        if (pso) {
          NdStoreMemoryCacheEntry(item->pso_hash, pso);
          pso->Release();
        }

        g_last_shader_compile_tick_ms.store(GetTickCount64(), std::memory_order_release);
        g_compiled_shader_count.fetch_add(1, std::memory_order_release);
      }
    });
  }

  for (auto& w : workers) {
    if (w.joinable()) w.join();
  }

  AcquireSRWLockShared(&g_pso_cache_srw);
  const size_t cache_size = g_pso_memory_cache.size();
  ReleaseSRWLockShared(&g_pso_cache_srw);

  g_is_precompiling_shaders.store(false, std::memory_order_release);
  REXLOG_INFO("neverdead: [PSO-PRECOMPILE] Completed {} pipelines (in-memory cache: {})",
              to_compile.size(), cache_size);
  s_precompiling.store(false);
}

}  // namespace
#endif  // _WIN32

void NdFlushPsoBundle() {
#ifdef _WIN32
  std::lock_guard<std::mutex> join_lk(g_pso_bundle_join_mtx);
  std::thread worker_to_join;
  {
    std::lock_guard<std::mutex> life_lk(g_pso_bundle_lifecycle_mtx);
    {
      std::lock_guard<std::mutex> lk(g_pso_bundle_mtx);
      g_pso_bundle_shutdown = true;
      g_pso_bundle_worker_running.store(false, std::memory_order_release);
    }
    g_pso_bundle_cv.notify_all();
    if (g_pso_bundle_worker.joinable()) {
      worker_to_join = std::move(g_pso_bundle_worker);
    }
  }
  if (worker_to_join.joinable()) {
    worker_to_join.join();
  }
#endif
}

void NdSavePipelineLibraryCache() {
  // Never call ID3D12PipelineLibrary1::Serialize() on WM_CLOSE: synchronous
  // serialization contends with active render threads and freezes shutdown.
  // PSO descriptors are persisted incrementally via cache/nd_pso_bundle.bin.
  REXLOG_INFO("neverdead: [CLEAN-EXIT] PSO descriptors persisted via nd_pso_bundle.bin");
}

void NdPrecompilePsoBundle(ID3D12Device* device) {
#ifdef _WIN32
  if (!device || !g_real_create_graphics_pipeline_state) return;
  static std::atomic<bool> s_started{false};
  if (s_started.exchange(true)) return;

  static const bool s_enabled = []() {
    const char* env = std::getenv("ND_PSO_PRECOMPILE");
    return env != nullptr && std::atoi(env) == 1;
  }();
  if (!s_enabled) {
    REXLOG_INFO(
        "neverdead: [PSO-PRECOMPILE] skipped at startup (on-demand cache active; set "
        "ND_PSO_PRECOMPILE=1 to enable)");
    return;
  }

  device->AddRef();
  std::thread([device]() {
    struct DeviceRef {
      ID3D12Device* d;
      ~DeviceRef() {
        if (d) d->Release();
      }
    } ref{device};
    std::this_thread::sleep_for(std::chrono::seconds(3));
    NdPrecompilePsoBundleWorker(device);
  }).detach();
#else
  (void)device;
#endif
}

void NdInstallD3D12DeviceHook() {
#ifdef _WIN32
  static std::atomic<bool> s_installed{false};
  if (s_installed.exchange(true)) return;
  if (NdQaDisabled("hooks")) {
    REXLOG_INFO("neverdead: [PSO-DIAG] D3D12 device hooks disabled");
    return;
  }

  HMODULE d3d12_mod = GetModuleHandleA("d3d12.dll");
  if (!d3d12_mod) {
    d3d12_mod = LoadLibraryA("d3d12.dll");
  }
  if (!d3d12_mod) {
    return;
  }
  using D3D12CreateDeviceFn = HRESULT(WINAPI*)(IUnknown*, D3D_FEATURE_LEVEL, REFIID, void**);
  auto pfnCreateDevice =
      reinterpret_cast<D3D12CreateDeviceFn>(GetProcAddress(d3d12_mod, "D3D12CreateDevice"));
  if (!pfnCreateDevice) return;

  ID3D12Device* dev = nullptr;
  const HRESULT hr = pfnCreateDevice(nullptr, D3D_FEATURE_LEVEL_11_0, __uuidof(ID3D12Device),
                                     reinterpret_cast<void**>(&dev));
  if (SUCCEEDED(hr) && dev) {
    void** vtable = *reinterpret_cast<void***>(dev);
    constexpr size_t kSlotPso = 10;
    constexpr size_t kSlotRootSig = 16;

    void* slotPso = vtable[kSlotPso];
    if (slotPso && slotPso != reinterpret_cast<void*>(&NdCreateGraphicsPipelineStateDetour)) {
      DWORD old_prot = 0;
      if (VirtualProtect(&vtable[kSlotPso], sizeof(void*), PAGE_READWRITE, &old_prot)) {
        g_real_create_graphics_pipeline_state =
            reinterpret_cast<NdCreateGraphicsPipelineStateFn>(slotPso);
        vtable[kSlotPso] = reinterpret_cast<void*>(&NdCreateGraphicsPipelineStateDetour);
        DWORD ignored = 0;
        VirtualProtect(&vtable[kSlotPso], sizeof(void*), old_prot, &ignored);
        FlushInstructionCache(GetCurrentProcess(), &vtable[kSlotPso], sizeof(void*));
        REXLOG_INFO(
            "neverdead: [D3D12-HOOK] ID3D12Device::CreateGraphicsPipelineState hooked (vtable[{}])",
            kSlotPso);
      }
    }

    void* slotRoot = vtable[kSlotRootSig];
    if (slotRoot && slotRoot != reinterpret_cast<void*>(&NdCreateRootSignatureDetour)) {
      DWORD old_prot = 0;
      if (VirtualProtect(&vtable[kSlotRootSig], sizeof(void*), PAGE_READWRITE, &old_prot)) {
        g_real_create_root_signature = reinterpret_cast<NdCreateRootSignatureFn>(slotRoot);
        vtable[kSlotRootSig] = reinterpret_cast<void*>(&NdCreateRootSignatureDetour);
        DWORD ignored = 0;
        VirtualProtect(&vtable[kSlotRootSig], sizeof(void*), old_prot, &ignored);
        FlushInstructionCache(GetCurrentProcess(), &vtable[kSlotRootSig], sizeof(void*));
        REXLOG_INFO(
            "neverdead: [D3D12-HOOK] ID3D12Device::CreateRootSignature hooked (vtable[{}])",
            kSlotRootSig);
      }
    }

    dev->Release();
  }
#endif
}
