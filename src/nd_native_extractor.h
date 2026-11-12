#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace nd::extractor {

constexpr uint32_t kSectorSize = 2048;          // 0x800
constexpr uint32_t kGodHashBlockSize = 4096;    // 0x1000
constexpr uint32_t kGodBlocksPerL0 = 408;       // 0x198
constexpr uint32_t kGodHashesPerL1 = 41412;     // 0xA1C4
constexpr uint32_t kGodBlocksPerFile = 82824;   // 0x14388 (0xA290000 bytes)
constexpr const char* kGdfxMagic = "MICROSOFT*XBOX*MEDIA";

struct FileEntry {
  std::string relative_path;
  uint32_t sector = 0;
  uint32_t size = 0;
  bool is_directory = false;
};

using ProgressCallback = std::function<bool(
    const std::string& current_file,
    uint64_t bytes_extracted,
    uint64_t total_bytes,
    float fraction)>;

#pragma pack(push, 1)
struct GdfxEntryHeader {
  uint16_t l_sub;     // Left subtree offset in DWORDs
  uint16_t r_sub;     // Right subtree offset in DWORDs
  uint32_t sector;    // Starting data sector
  uint32_t size;      // Size in bytes
  uint8_t  attribs;   // 0x10 = Directory
  uint8_t  name_len;  // ASCII filename length
};
#pragma pack(pop)

// Abstract sector reader interface.
class ISectorReader {
 public:
  virtual ~ISectorReader() = default;
  virtual bool ReadSectors(uint32_t start_sector, uint32_t count, void* out_buffer) = 0;
  virtual bool ReadBytes(uint64_t byte_offset, uint32_t count, void* out_buffer) = 0;
  virtual uint64_t GetDirectFileOffset(uint32_t sector) const { return 0; }
  virtual bool SupportsDirectStreaming() const { return false; }
};

// Direct ISO image reader (XISO / XGD2 / XGD3).
class IsoSectorReader : public ISectorReader {
 public:
  explicit IsoSectorReader(const std::filesystem::path& iso_path) {
    file_.open(iso_path, std::ios::binary);
    if (!file_.is_open()) return;
    partition_offset_ = DetectPartitionOffset();
  }

  bool IsValid() const { return file_.is_open() && partition_offset_ != UINT64_MAX; }
  uint64_t partition_offset() const { return partition_offset_; }

  bool ReadSectors(uint32_t start_sector, uint32_t count, void* out_buffer) override {
    if (!IsValid()) return false;
    uint64_t off = partition_offset_ + static_cast<uint64_t>(start_sector) * kSectorSize;
    file_.seekg(static_cast<std::streamoff>(off), std::ios::beg);
    file_.read(reinterpret_cast<char*>(out_buffer), static_cast<std::streamsize>(count) * kSectorSize);
    return file_.good();
  }

  bool ReadBytes(uint64_t byte_offset, uint32_t count, void* out_buffer) override {
    if (!IsValid()) return false;
    file_.seekg(static_cast<std::streamoff>(partition_offset_ + byte_offset), std::ios::beg);
    file_.read(reinterpret_cast<char*>(out_buffer), count);
    return file_.good();
  }

  uint64_t GetDirectFileOffset(uint32_t sector) const override {
    return partition_offset_ + static_cast<uint64_t>(sector) * kSectorSize;
  }

  bool SupportsDirectStreaming() const override { return true; }
  std::ifstream& file() { return file_; }

 private:
  uint64_t DetectPartitionOffset() {
    file_.seekg(0, std::ios::end);
    const uint64_t file_size = static_cast<uint64_t>(file_.tellg());

    // 1. Canonical partition offsets.
    const uint64_t canonical_offsets[] = {
        0x00000000ULL,  // XISO
        0x0FD90000ULL,  // XGD2 Standard (descriptor at 0x0FDA0000)
        0x0FDA0000ULL,  // XGD2 Variant  (descriptor at 0x0FDB0000)
        0x02080000ULL,  // XGD3 Standard (descriptor at 0x02090000)
        0x18300000ULL   // XGD Extended
    };

    char magic[20] = {};
    for (uint64_t base : canonical_offsets) {
      uint64_t s32 = base + 0x10000ULL;  // Sector 32
      if (s32 + 20 <= file_size) {
        file_.seekg(static_cast<std::streamoff>(s32), std::ios::beg);
        file_.read(magic, 20);
        if (std::memcmp(magic, kGdfxMagic, 20) == 0) {
          return base;
        }
      }
    }

    // 2. Sequential scan in 64 KB steps (up to 512 MB).
    constexpr uint64_t kMaxScan = 512ULL * 1024ULL * 1024ULL;
    constexpr uint64_t kStep = 64ULL * 1024ULL;
    for (uint64_t off = 0; off + 0x10000ULL + 20 <= file_size && off < kMaxScan; off += kStep) {
      file_.seekg(static_cast<std::streamoff>(off + 0x10000ULL), std::ios::beg);
      file_.read(magic, 20);
      if (std::memcmp(magic, kGdfxMagic, 20) == 0) {
        return off;
      }
    }
    return UINT64_MAX;
  }

  std::ifstream file_;
  uint64_t partition_offset_ = UINT64_MAX;
};

// Games on Demand (GOD DataXXXX) container reader.
class GodSectorReader : public ISectorReader {
 public:
  explicit GodSectorReader(const std::filesystem::path& god_dir) : god_dir_(god_dir) {}
  ~GodSectorReader() override {
    for (auto& pair : handles_) {
      if (pair.second.is_open()) pair.second.close();
    }
  }

  bool IsValid() {
    std::ifstream& f = GetFileHandle(0);
    return f.is_open();
  }

  bool ReadSectors(uint32_t start_sector, uint32_t count, void* out_buffer) override {
    uint8_t* dst = reinterpret_cast<uint8_t*>(out_buffer);
    for (uint32_t s = 0; s < count; ++s) {
      if (!ReadSingleSector(start_sector + s, dst + (s * kSectorSize))) {
        return false;
      }
    }
    return true;
  }

  bool ReadBytes(uint64_t byte_offset, uint32_t count, void* out_buffer) override {
    uint32_t start_sec = static_cast<uint32_t>(byte_offset / kSectorSize);
    uint32_t sec_offset = static_cast<uint32_t>(byte_offset % kSectorSize);
    uint32_t sec_count = (sec_offset + count + kSectorSize - 1) / kSectorSize;
    std::vector<uint8_t> tmp(sec_count * kSectorSize);
    if (!ReadSectors(start_sec, sec_count, tmp.data())) return false;
    std::memcpy(out_buffer, tmp.data() + sec_offset, count);
    return true;
  }

 private:
  bool ReadSingleSector(uint32_t sector, void* dst) {
    uint32_t file_index = sector / kGodBlocksPerFile;
    uint32_t file_block = sector % kGodBlocksPerFile;
    uint32_t l0_count = (file_block / kGodBlocksPerL0) + 1;
    uint32_t l1_count = (l0_count / kGodHashesPerL1) + 1;
    uint64_t offset = static_cast<uint64_t>(l0_count + l1_count) * kGodHashBlockSize +
                      static_cast<uint64_t>(file_block) * kSectorSize;

    std::ifstream& f = GetFileHandle(file_index);
    if (!f.is_open()) return false;
    f.seekg(static_cast<std::streamoff>(offset), std::ios::beg);
    f.read(reinterpret_cast<char*>(dst), kSectorSize);
    return f.good();
  }

  std::ifstream& GetFileHandle(uint32_t index) {
    auto it = handles_.find(index);
    if (it != handles_.end()) return it->second;

    char name[32];
    std::snprintf(name, sizeof(name), "Data%04u", index);
    std::filesystem::path p = god_dir_ / name;
    handles_[index].open(p, std::ios::binary);
    return handles_[index];
  }

  std::filesystem::path god_dir_;
  std::unordered_map<uint32_t, std::ifstream> handles_;
};

// GDFX directory tree parser and file extractor.
class GdfxParser {
 public:
  explicit GdfxParser(ISectorReader* reader) : reader_(reader) {}

  bool Parse(std::vector<FileEntry>& out_entries) {
    out_entries.clear();
    // Read volume descriptor at sector 32 (offset 0x10000 in the GDFX partition).
    uint8_t vol_desc[kSectorSize];
    if (!reader_->ReadSectors(32, 1, vol_desc)) return false;
    if (std::memcmp(vol_desc, kGdfxMagic, 20) != 0) return false;

    uint32_t root_sector = *reinterpret_cast<uint32_t*>(vol_desc + 0x14);
    uint32_t root_size   = *reinterpret_cast<uint32_t*>(vol_desc + 0x18);

    std::unordered_set<uint64_t> visited_dirs;
    return TraverseDirectory(root_sector, root_size, "", out_entries, visited_dirs, 0);
  }

 private:
  static bool IsValidGdfxComponent(const std::string& name) {
    if (name.empty() || name == "." || name == "..") return false;
    for (unsigned char c : name) {
      if (c < 0x20 || c == '/' || c == '\\' || c == ':' ||
          c == '<' || c == '>' || c == '"' || c == '|' || c == '?' || c == '*') {
        return false;
      }
    }
    return true;
  }

  bool TraverseDirectory(uint32_t sector, uint32_t size, const std::string& parent_path,
                         std::vector<FileEntry>& out_entries,
                         std::unordered_set<uint64_t>& visited_dirs, int depth) {
    constexpr uint32_t kMaxGdfxDirSize = 64u * 1024u * 1024u;
    constexpr int kMaxGdfxDepth = 32;
    if (size == 0) return true;
    if (depth > kMaxGdfxDepth || size > kMaxGdfxDirSize) return false;
    const uint64_t dir_key = (static_cast<uint64_t>(sector) << 32) | size;
    if (!visited_dirs.insert(dir_key).second) return false;

    uint32_t sec_count = static_cast<uint32_t>((static_cast<uint64_t>(size) + kSectorSize - 1) / kSectorSize);
    std::vector<uint8_t> buf(static_cast<size_t>(sec_count) * kSectorSize);
    if (!reader_->ReadSectors(sector, sec_count, buf.data())) return false;

    std::unordered_set<uint16_t> visited;
    auto walk_node = [&](auto& self, uint16_t offset_dword) -> bool {
      if (offset_dword == 0 || offset_dword == 0xFFFF || visited.count(offset_dword)) return true;
      visited.insert(offset_dword);

      size_t byte_off = static_cast<size_t>(offset_dword) * 4;
      if (byte_off + sizeof(GdfxEntryHeader) > size) return false;

      const auto* hdr = reinterpret_cast<const GdfxEntryHeader*>(buf.data() + byte_off);
      if (hdr->name_len == 0 || hdr->name_len > size - (byte_off + sizeof(GdfxEntryHeader))) return false;

      std::string name(reinterpret_cast<const char*>(buf.data() + byte_off + sizeof(GdfxEntryHeader)), hdr->name_len);
      if (!IsValidGdfxComponent(name)) return false;
      std::string full_path = parent_path.empty() ? name : (parent_path + "\\" + name);

      if (!self(self, hdr->l_sub)) return false;

      bool is_dir = (hdr->attribs & 0x10) != 0;
      out_entries.push_back({full_path, hdr->sector, hdr->size, is_dir});
      if (is_dir) {
        if (!TraverseDirectory(hdr->sector, hdr->size, full_path, out_entries, visited_dirs, depth + 1)) {
          return false;
        }
      }

      if (!self(self, hdr->r_sub)) return false;
      return true;
    };

    if (size >= sizeof(GdfxEntryHeader)) {
      const auto* root_hdr = reinterpret_cast<const GdfxEntryHeader*>(buf.data());
      if (root_hdr->name_len > 0 && root_hdr->name_len <= size - sizeof(GdfxEntryHeader)) {
        std::string name(reinterpret_cast<const char*>(buf.data() + sizeof(GdfxEntryHeader)), root_hdr->name_len);
        if (!IsValidGdfxComponent(name)) return false;
        std::string full_path = parent_path.empty() ? name : (parent_path + "\\" + name);
        visited.insert(0);

        if (!walk_node(walk_node, root_hdr->l_sub)) return false;
        bool is_dir = (root_hdr->attribs & 0x10) != 0;
        out_entries.push_back({full_path, root_hdr->sector, root_hdr->size, is_dir});
        if (is_dir) {
          if (!TraverseDirectory(root_hdr->sector, root_hdr->size, full_path, out_entries, visited_dirs, depth + 1)) {
            return false;
          }
        }
        if (!walk_node(walk_node, root_hdr->r_sub)) return false;
      }
    }
    return true;
  }

  ISectorReader* reader_;
};

// Ensures FMV/CESA.bik exists by linking or copying ND_logo_Cred.bik (or rebellion.bik).
inline void EnsureCesaBik(const std::filesystem::path& target_assets_dir) {
  std::error_code ec;
  std::filesystem::path fmv_dir = target_assets_dir / "FMV";

  // Resolve FMV folder case-insensitively.
  if (!std::filesystem::exists(fmv_dir, ec)) {
    for (const auto& entry : std::filesystem::directory_iterator(target_assets_dir, ec)) {
      if (entry.is_directory(ec)) {
        std::string fn = entry.path().filename().string();
        std::transform(fn.begin(), fn.end(), fn.begin(), ::tolower);
        if (fn == "fmv") {
          fmv_dir = entry.path();
          break;
        }
      }
    }
  }

  if (!std::filesystem::exists(fmv_dir, ec)) {
    return;
  }

  // Check for existing CESA.bik (case-insensitive); discard oversized (>10 MB) fallback copies.
  bool cesa_exists = false;
  std::filesystem::path cesa_path = fmv_dir / "CESA.bik";
  for (const auto& entry : std::filesystem::directory_iterator(fmv_dir, ec)) {
    if (entry.is_regular_file(ec)) {
      std::string fn = entry.path().filename().string();
      std::transform(fn.begin(), fn.end(), fn.begin(), ::tolower);
      if (fn == "cesa.bik") {
        auto sz = std::filesystem::file_size(entry.path(), ec);
        if (!ec && sz > 10ULL * 1024ULL * 1024ULL) {
          std::filesystem::remove(entry.path(), ec);
        } else {
          cesa_exists = true;
          cesa_path = entry.path();
          break;
        }
      }
    }
  }

  if (cesa_exists) {
    return;
  }

  // Locate ND_logo_Cred.bik or rebellion.bik as a short replacement clip.
  std::filesystem::path template_path;
  const char* candidates[] = {"nd_logo_cred.bik", "rebellion.bik"};
  for (const char* cand : candidates) {
    for (const auto& entry : std::filesystem::directory_iterator(fmv_dir, ec)) {
      if (entry.is_regular_file(ec)) {
        std::string fn = entry.path().filename().string();
        std::transform(fn.begin(), fn.end(), fn.begin(), ::tolower);
        if (fn == cand) {
          template_path = entry.path();
          break;
        }
      }
    }
    if (!template_path.empty()) break;
  }

  if (template_path.empty() || !std::filesystem::exists(template_path, ec)) {
    return;
  }

#ifdef _WIN32
  // Prefer an NTFS hardlink to avoid extra disk usage.
  if (CreateHardLinkW(cesa_path.wstring().c_str(), template_path.wstring().c_str(), nullptr)) {
    return;
  }
#endif

  // Fallback: direct file copy.
  std::filesystem::copy_file(template_path, cesa_path, std::filesystem::copy_options::overwrite_existing, ec);
}

// Creates compatibility junctions/symlinks in base_dir pointing to assets/ subdirectories.
inline void NdEnsureAssetJunctionsOrLinks(const std::filesystem::path& base_dir, const std::filesystem::path& assets_dir) {
  EnsureCesaBik(assets_dir);
  std::error_code ec;
  std::filesystem::path effective_base = base_dir;
  // Avoid creating junctions in an unrelated parent directory when assets_dir lives elsewhere.
  if (!std::filesystem::exists(effective_base / "assets", ec)) {
    if (std::filesystem::is_directory(assets_dir.parent_path() / "assets", ec)) {
      effective_base = assets_dir.parent_path();
    }
  }
  const char* subdirs[] = {"FMV", "Fonts", "GUIMenu", "misc", "sounds", "Text"};
  for (const char* sub : subdirs) {
    auto link_path = effective_base / sub;
    auto target_path = assets_dir / sub;
    if (!std::filesystem::is_directory(target_path, ec)) {
      continue;
    }
    if (std::filesystem::exists(link_path, ec)) {
      continue;
    }
    std::filesystem::create_directory_symlink(target_path, link_path, ec);
    if (ec) {
      ec.clear();
#ifdef _WIN32
      std::wstring cmd = L"cmd.exe /c mklink /J \"" + link_path.wstring() + L"\" \"" + target_path.wstring() + L"\" >nul 2>&1";
      _wsystem(cmd.c_str());
#endif
    }
  }
}

inline void PrebuildShaderCache(const std::filesystem::path& root_dir) {
  std::error_code ec;
  std::filesystem::path cache_dir = root_dir / "cache";
  std::filesystem::path shaders_dir = cache_dir / "shaders" / "shareable";
  std::filesystem::create_directories(shaders_dir, ec);

  // Remove stale shader catalog if present so it is rebuilt cleanly.
  std::filesystem::path catalog_path = cache_dir / "shader_catalog.bin";
  if (std::filesystem::exists(catalog_path, ec)) {
    std::filesystem::remove(catalog_path, ec);
  }
}

// Native ISO / GOD / extracted folder installer (no external dependencies).
inline bool ExtractAll(
    const std::filesystem::path& input_path,
    const std::filesystem::path& dest_dir,
    ProgressCallback on_progress) {

  std::error_code ec;

#ifdef _WIN32
  ULARGE_INTEGER free_bytes_available{};
  ULARGE_INTEGER total_number_of_bytes{};
  ULARGE_INTEGER total_number_of_free_bytes{};
  std::wstring dest_w = dest_dir.wstring();
  if (GetDiskFreeSpaceExW(dest_w.c_str(), &free_bytes_available, &total_number_of_bytes, &total_number_of_free_bytes)) {
    constexpr uint64_t kRequiredBytes = 7ULL * 1024ULL * 1024ULL * 1024ULL;  // 7 GB
    if (free_bytes_available.QuadPart < kRequiredBytes) {
      if (on_progress) {
        on_progress("Insufficient disk space (< 7 GB available)", 0, 0, 0.0f);
      }
      return false;
    }
  }
#endif

  std::unique_ptr<ISectorReader> reader;

  // Detect input format.
  if (std::filesystem::is_regular_file(input_path, ec)) {
    auto iso = std::make_unique<IsoSectorReader>(input_path);
    if (iso->IsValid()) {
      reader = std::move(iso);
    } else {
      // Check if the selected file belongs to a GOD container directory.
      std::filesystem::path parent = input_path.parent_path();
      if (std::filesystem::exists(parent / "Data0000", ec)) {
        auto god = std::make_unique<GodSectorReader>(parent);
        if (god->IsValid()) {
          reader = std::move(god);
        }
      }
    }
  } else if (std::filesystem::is_directory(input_path, ec)) {
    if (std::filesystem::exists(input_path / "Data0000", ec)) {
      auto god = std::make_unique<GodSectorReader>(input_path);
      if (god->IsValid()) {
        reader = std::move(god);
      }
    } else if (std::filesystem::exists(input_path / "default.xex", ec)) {
      // Already extracted directory: recursive native copy.
      std::vector<std::pair<std::filesystem::path, uint64_t>> files_to_copy;
      uint64_t total_bytes = 0;
      for (const auto& entry : std::filesystem::recursive_directory_iterator(input_path, ec)) {
        if (entry.is_regular_file(ec)) {
          std::string rel = std::filesystem::relative(entry.path(), input_path, ec).string();
          std::string rel_lower = rel;
          std::transform(rel_lower.begin(), rel_lower.end(), rel_lower.begin(), ::tolower);
          if (rel_lower.rfind("$systemupdate", 0) == 0) continue;
          uint64_t sz = entry.file_size(ec);
          files_to_copy.push_back({entry.path(), sz});
          total_bytes += sz;
        }
      }
      uint64_t copied_bytes = 0;
      for (size_t i = 0; i < files_to_copy.size(); ++i) {
        const auto& pair = files_to_copy[i];
        const auto& src = pair.first;
        const auto sz = pair.second;
        std::string rel = std::filesystem::relative(src, input_path, ec).string();
        std::filesystem::path dst = dest_dir / rel;
        std::filesystem::create_directories(dst.parent_path(), ec);
        std::filesystem::copy_file(src, dst, std::filesystem::copy_options::overwrite_existing, ec);
        copied_bytes += sz;
        float frac = total_bytes > 0 ? static_cast<float>(copied_bytes) / static_cast<float>(total_bytes) : 1.0f;
        if (on_progress && !on_progress(rel, copied_bytes, total_bytes, frac)) {
          return false;
        }
      }
      NdEnsureAssetJunctionsOrLinks(dest_dir.parent_path(), dest_dir);
      if (on_progress) {
        on_progress("Building shader cache...", total_bytes, total_bytes, 1.0f);
      }
      PrebuildShaderCache(dest_dir.parent_path());
      return true;
    }
  }

  if (!reader) return false;

  GdfxParser parser(reader.get());
  std::vector<FileEntry> entries;
  if (!parser.Parse(entries)) return false;

  // Filter extractable files (exclude $SystemUpdate and directories).
  std::vector<FileEntry> files;
  uint64_t total_bytes = 0;
  for (const auto& e : entries) {
    if (e.is_directory) continue;
    std::string rel_lower = e.relative_path;
    std::transform(rel_lower.begin(), rel_lower.end(), rel_lower.begin(), ::tolower);
    if (rel_lower.rfind("$systemupdate", 0) == 0) continue;
    files.push_back(e);
    total_bytes += e.size;
  }

  uint64_t extracted_bytes = 0;
  constexpr size_t kChunkSize = 1024 * 1024;  // 1 MB streaming buffer
  std::vector<uint8_t> chunk(kChunkSize);

  const std::filesystem::path dest_root = dest_dir.lexically_normal();
  auto is_path_contained = [&dest_root](const std::filesystem::path& candidate) -> bool {
    std::filesystem::path norm_base = dest_root;
    if (norm_base.filename().empty() && norm_base.has_parent_path() &&
        norm_base != norm_base.root_path()) {
      norm_base = norm_base.parent_path();
    }
    const std::filesystem::path rel = candidate.lexically_normal().lexically_relative(norm_base);
    if (rel.empty() || rel == "." || rel.is_absolute()) return false;
    for (const auto& part : rel) {
      if (part == "..") return false;
    }
    return true;
  };

  for (size_t i = 0; i < files.size(); ++i) {
    const auto& f = files[i];
    std::filesystem::path rel_path(f.relative_path);
    if (rel_path.is_absolute() || rel_path.has_root_name() || rel_path.has_root_directory()) {
      return false;
    }
    std::filesystem::path dst_path = (dest_root / rel_path).lexically_normal();
    if (!is_path_contained(dst_path)) {
      return false;
    }
    std::filesystem::create_directories(dst_path.parent_path(), ec);
    if (ec) {
      return false;
    }

    std::ofstream out(dst_path, std::ios::binary);
    if (!out.is_open()) {
      return false;
    }

    uint32_t remaining = f.size;
    uint32_t current_sec = f.sector;

    if (reader->SupportsDirectStreaming()) {
      auto* iso_reader = static_cast<IsoSectorReader*>(reader.get());
      uint64_t file_pos = iso_reader->GetDirectFileOffset(current_sec);
      iso_reader->file().clear();
      iso_reader->file().seekg(static_cast<std::streamoff>(file_pos), std::ios::beg);
      if (!iso_reader->file().good()) {
        return false;
      }
      while (remaining > 0) {
        uint32_t take = (remaining < kChunkSize) ? remaining : static_cast<uint32_t>(kChunkSize);
        iso_reader->file().read(reinterpret_cast<char*>(chunk.data()), take);
        if (iso_reader->file().gcount() != static_cast<std::streamsize>(take) ||
            (!iso_reader->file().good() && !iso_reader->file().eof())) {
          return false;
        }
        out.write(reinterpret_cast<const char*>(chunk.data()), take);
        if (!out.good()) {
          return false;
        }
        remaining -= take;
        extracted_bytes += take;
        float frac = total_bytes > 0 ? static_cast<float>(extracted_bytes) / static_cast<float>(total_bytes) : 1.0f;
        if (on_progress && !on_progress(f.relative_path, extracted_bytes, total_bytes, frac)) {
          return false;
        }
      }
    } else {
      while (remaining > 0) {
        uint32_t sec_take = (remaining + kSectorSize - 1) / kSectorSize;
        if (sec_take > 512) sec_take = 512;  // 1 MB
        uint32_t bytes_to_read = (sec_take * kSectorSize > remaining) ? remaining : (sec_take * kSectorSize);
        if (!reader->ReadSectors(current_sec, sec_take, chunk.data())) {
          return false;
        }
        out.write(reinterpret_cast<const char*>(chunk.data()), bytes_to_read);
        if (!out.good()) {
          return false;
        }
        current_sec += sec_take;
        remaining -= bytes_to_read;
        extracted_bytes += bytes_to_read;
        float frac = total_bytes > 0 ? static_cast<float>(extracted_bytes) / static_cast<float>(total_bytes) : 1.0f;
        if (on_progress && !on_progress(f.relative_path, extracted_bytes, total_bytes, frac)) {
          return false;
        }
      }
    }
  }

  NdEnsureAssetJunctionsOrLinks(dest_dir.parent_path(), dest_dir);
  if (on_progress) {
    on_progress("Building shader cache...", total_bytes, total_bytes, 1.0f);
  }
  PrebuildShaderCache(dest_dir.parent_path());
  return true;
}

}  // namespace nd::extractor
