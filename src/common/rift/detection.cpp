#include "common/rift/detection.h"
#include <algorithm>
#include <array>
#include <cstring>
#include <fstream>
#include <functional>
namespace riftlift::rift {
namespace {
std::string lower(std::string value) {
  std::ranges::transform(value, value.begin(),
                         [](unsigned char c) { return char(std::tolower(c)); });
  return value;
}
std::string name_of(const fs::path &file) { return lower(paths::utf8(file.filename())); }
// Every regular file below `directory`, skipping hidden folders.
void walk(const fs::path &directory, const std::function<bool(const fs::path &)> &visit) {
  std::error_code ec;
  fs::recursive_directory_iterator it(directory, fs::directory_options::skip_permission_denied, ec), end;
  for (; !ec && it != end; it.increment(ec)) {
    if (it->is_directory(ec)) {
      if (paths::utf8(it->path().filename()).starts_with("."))
        it.disable_recursion_pending();
      continue;
    }
    if (it->is_regular_file(ec) && visit(it->path()))
      return;
  }
}
template <typename T> bool read_at(std::ifstream &in, std::uint64_t offset, T &value) {
  in.seekg(std::streamoff(offset));
  return bool(in.read(reinterpret_cast<char *>(&value), sizeof value));
}
bool contains_any(const fs::path &file, const std::vector<std::string> &needles) {
  size_t overlap = 0;
  for (const auto &n : needles)
    overlap = std::max(overlap, n.size());
  overlap = overlap ? overlap - 1 : 0;
  std::ifstream in(paths::io_path(file), std::ios::binary);
  std::string tail, chunk(1 << 20, '\0');
  while (in) {
    in.read(chunk.data(), std::streamsize(chunk.size()));
    auto got = size_t(in.gcount());
    if (!got)
      break;
    auto payload = lower(tail + chunk.substr(0, got));
    for (const auto &n : needles)
      if (payload.find(n) != std::string::npos)
        return true;
    tail = overlap && payload.size() > overlap ? payload.substr(payload.size() - overlap) : payload;
  }
  return false;
}
const std::set<std::string> &unity_plugins() {
  static const std::set<std::string> names{"oculusxrplugin.dll", "ovrplugin.dll"};
  return names;
}
std::vector<fs::path> unity_executables(const fs::path &directory) {
  std::vector<fs::path> out;
  std::error_code ec;
  for (const auto &entry : fs::directory_iterator(directory, ec)) {
    auto name = paths::utf8(entry.path().filename());
    if (!entry.is_directory(ec) || !name.ends_with("_Data"))
      continue;
    auto exe = directory / paths::from_utf8(name.substr(0, name.size() - 5) + ".exe");
    if (is_pe64(exe))
      out.push_back(exe);
  }
  std::ranges::sort(out);
  return out;
}
// */Binaries/Win64/*-Win64-Shipping.exe one or two folders down.
std::vector<fs::path> unreal_executables(const fs::path &directory) {
  std::vector<fs::path> out;
  std::error_code ec;
  auto search = [&](const fs::path &project) {
    std::error_code inner;
    for (const auto &entry : fs::directory_iterator(project / "Binaries" / "Win64", inner)) {
      auto name = lower(paths::utf8(entry.path().filename()));
      if (name.ends_with("-win64-shipping.exe") && is_pe64(entry.path()))
        out.push_back(entry.path());
    }
  };
  for (const auto &first : fs::directory_iterator(directory, ec)) {
    if (!first.is_directory(ec))
      continue;
    search(first.path());
  }
  for (const auto &first : fs::directory_iterator(directory, ec)) {
    if (!first.is_directory(ec))
      continue;
    std::error_code inner;
    for (const auto &second : fs::directory_iterator(first.path(), inner))
      if (second.is_directory(inner))
        search(second.path());
  }
  std::ranges::sort(out);
  out.erase(std::unique(out.begin(), out.end()), out.end());
  return out;
}
} // namespace

bool is_pe64(const fs::path &file) {
  std::ifstream in(paths::io_path(file), std::ios::binary);
  std::array<char, 64> header{};
  if (!in.read(header.data(), header.size()) || header[0] != 'M' || header[1] != 'Z')
    return false;
  std::uint32_t offset;
  std::memcpy(&offset, header.data() + 0x3C, 4);
  std::array<char, 6> signature{};
  in.seekg(offset);
  return in.read(signature.data(), 6) && std::memcmp(signature.data(), "PE\0\0\x64\x86", 6) == 0;
}
std::optional<std::set<std::string>> imported_dlls(const fs::path &file) {
  std::ifstream in(paths::io_path(file), std::ios::binary);
  std::uint32_t pe = 0, signature = 0;
  if (!read_at(in, 0x3C, pe) || !read_at(in, pe, signature) || signature != 0x00004550)
    return std::nullopt;
  std::uint16_t sections = 0, optional_size = 0, magic = 0;
  if (!read_at(in, pe + 6, sections) || !read_at(in, pe + 20, optional_size))
    return std::nullopt;
  const std::uint64_t optional = pe + 24;
  if (!read_at(in, optional, magic) || (magic != 0x10B && magic != 0x20B))
    return std::nullopt;
  const std::uint64_t directories = optional + (magic == 0x20B ? 112 : 96);
  struct Section {
    std::uint32_t virtual_size, virtual_address, raw_size, raw_offset;
  };
  std::vector<Section> table;
  const std::uint64_t first = optional + optional_size;
  for (std::uint16_t i = 0; i < sections && i < 96; ++i) {
    Section s{};
    if (!read_at(in, first + i * 40ull + 8, s))
      return std::nullopt;
    table.push_back(s);
  }
  auto file_offset = [&](std::uint32_t rva) -> std::optional<std::uint64_t> {
    for (const auto &s : table) {
      auto size = std::max(s.virtual_size, s.raw_size);
      if (rva >= s.virtual_address && rva < s.virtual_address + size)
        return std::uint64_t(rva - s.virtual_address) + s.raw_offset;
    }
    return std::nullopt;
  };
  auto read_name = [&](std::uint32_t rva) -> std::string {
    auto at = file_offset(rva);
    if (!at)
      return {};
    in.clear();
    in.seekg(std::streamoff(*at));
    std::string name;
    for (char c; name.size() < 260 && in.get(c) && c;)
      name += c;
    return lower(name);
  };
  std::set<std::string> out;
  // Import descriptors are 20 bytes with the name RVA at +12; delay-load
  // descriptors are 32 bytes with the name RVA at +4.
  for (auto [index, stride, name_at] : {std::array<unsigned, 3>{1, 20, 12}, std::array<unsigned, 3>{13, 32, 4}}) {
    std::uint32_t rva = 0, size = 0;
    in.clear();
    if (!read_at(in, directories + index * 8ull, rva) || !read_at(in, directories + index * 8ull + 4, size) || !rva)
      continue;
    auto at = file_offset(rva);
    if (!at)
      continue;
    for (unsigned i = 0; i < 4096; ++i) {
      std::array<std::uint32_t, 8> row{};
      in.clear();
      if (!read_at(in, *at + std::uint64_t(i) * stride, row))
        break;
      if (std::ranges::all_of(std::span(row).first(stride / 4), [](auto v) { return v == 0; }))
        break;
      auto name = read_name(row[name_at / 4]);
      if (!name.empty())
        out.insert(name);
    }
  }
  return out;
}
bool uses_oculus_runtime(const fs::path &directory) {
  std::vector<fs::path> executables;
  bool found = false;
  walk(directory, [&](const fs::path &file) {
    auto name = name_of(file);
    if (name.ends_with(".exe") && is_pe64(file))
      executables.push_back(file);
    if (name != "libovrrt64_1.dll" && !unity_plugins().contains(name))
      return false;
    bool x64_folder = false;
    for (const auto &part : file)
      x64_folder |= lower(paths::utf8(part)) == "win64" || lower(paths::utf8(part)) == "x86_64";
    found = name == "libovrrt64_1.dll" || x64_folder || is_pe64(file);
    return found;
  });
  if (found)
    return true;
  return std::ranges::any_of(executables, [](auto &exe) { return contains_any(exe, {"libovrrt64_1.dll"}); });
}
bool uses_openvr_runtime(const fs::path &directory) {
  bool found = false;
  walk(directory, [&](const fs::path &file) { return found = name_of(file) == "openvr_api.dll"; });
  return found;
}
bool uses_unity_oculus_plugin(const fs::path &directory) {
  bool found = false;
  walk(directory, [&](const fs::path &file) { return found = unity_plugins().contains(name_of(file)); });
  return found;
}
bool uses_d3d12_runtime(const fs::path &executable) {
  if (auto imported = imported_dlls(executable); imported && !imported->empty()) {
    if (imported->contains("d3d12.dll") && !imported->contains("d3d11.dll"))
      return true;
    // Multi-renderer engines import both and default to D3D11 unless asked;
    // a real D3D11 import outweighs an unused D3D12 renderer.
    if (imported->contains("d3d11.dll"))
      return false;
  }
  static const std::string wide{"d\0003\000d\0001\0002\000.\000d\000l\000l\000", 18};
  return contains_any(executable, {"d3d12.dll", wide});
}
bool is_unreal_shipping(const fs::path &executable) {
  std::vector<std::string> parts;
  for (const auto &part : executable)
    parts.push_back(lower(paths::utf8(part)));
  return name_of(executable).ends_with("-win64-shipping.exe") && parts.size() >= 3 &&
         parts[parts.size() - 3] == "binaries" && parts[parts.size() - 2] == "win64";
}
bool is_unity_player(const fs::path &executable) {
  std::error_code ec;
  return fs::is_directory(executable.parent_path() / paths::from_utf8(paths::utf8(executable.stem()) + "_Data"), ec);
}
std::optional<fs::path> unreal_shipping_for_bootstrap(const fs::path &executable) {
  auto stem = paths::utf8(executable.stem());
  auto shipping = executable.parent_path() / paths::from_utf8(stem) / "Binaries" / "Win64" /
                  paths::from_utf8(stem + "-Win64-Shipping.exe");
  if (lower(paths::utf8(executable.extension())) == ".exe" && is_pe64(shipping))
    return shipping;
  return std::nullopt;
}
fs::path best_windows_executable(const fs::path &root, const fs::path &preferred) {
  std::error_code ec;
  auto directory = fs::weakly_canonical(root, ec);
  if (ec)
    directory = root;
  auto chosen = preferred.empty() ? fs::path{} : fs::weakly_canonical(directory / preferred, ec);
  if (auto unreal = unreal_executables(directory); !unreal.empty()) {
    if (!chosen.empty()) {
      auto stem = lower(paths::utf8(chosen.stem()));
      for (const auto &exe : unreal)
        if (name_of(exe).starts_with(stem))
          return exe;
    }
    return unreal.front();
  }
  auto unity = unity_executables(directory);
  if (!chosen.empty() && std::ranges::find(unity, chosen) != unity.end())
    return chosen;
  if (!unity.empty())
    return unity.front();
  if (!chosen.empty() && is_pe64(chosen))
    return chosen;
  static const std::array<std::string_view, 9> not_games{
      "crash", "helper", "installer", "prereq", "redist", "report", "setup", "unins", "uninstall"};
  std::vector<fs::path> candidates;
  walk(directory, [&](const fs::path &file) {
    auto name = name_of(file);
    if (name.ends_with(".exe") && is_pe64(file) &&
        std::ranges::none_of(not_games, [&](auto marker) { return name.find(marker) != std::string::npos; }))
      candidates.push_back(file);
    return false;
  });
  if (candidates.empty())
    throw std::runtime_error("No 64-bit game executable was found (preferred: " +
                             (preferred.empty() ? std::string("<auto-detect>") : paths::utf8(preferred)) + ").");
  auto depth = [&](const fs::path &p) { return std::distance(p.lexically_relative(directory).begin(),
                                                             p.lexically_relative(directory).end()); };
  std::ranges::sort(candidates, [&](const fs::path &a, const fs::path &b) {
    auto da = depth(a), db = depth(b);
    return da != db ? da < db : a < b;
  });
  return candidates.front();
}
} // namespace riftlift::rift
