#include "runtime/meta_runtime.h"
#include "common/digest.h"
#include "runtime/archive.h"
#include "runtime/files.h"
#include <format>
#include <stdexcept>

namespace riftlift::runtime {
namespace {
std::string bytes_of(std::initializer_list<unsigned char> values) {
  return std::string(values.begin(), values.end());
}
std::string sha256_of(std::string_view bytes) {
  return digest::sha256({reinterpret_cast<const std::uint8_t *>(bytes.data()), bytes.size()});
}
constexpr std::string_view signing_root_pem =
    "MIIDtzCCAp+gAwIBAgIQDOfg5RfYRv6P5WD8G/AwOTANBgkqhkiG9w0BAQUFADBl"
    "MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3"
    "d3cuZGlnaWNlcnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJv"
    "b3QgQ0EwHhcNMDYxMTEwMDAwMDAwWhcNMzExMTEwMDAwMDAwWjBlMQswCQYDVQQG"
    "EwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNl"
    "cnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJvb3QgQ0EwggEi"
    "MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCtDhXO5EOAXLGH87dg+XESpa7c"
    "JpSIqvTO9SA5KFhgDPiA2qkVlTJhPLWxKISKityfCgyDF3qPkKyK53lTXDGEKvYP"
    "mDI2dsze3Tyoou9q+yHyUmHfnyDXH+Kx2f4YZNISW1/5WBg1vEfNoTb5a3/UsDg+"
    "wRvDjDPZ2C8Y/igPs6eD1sNuRMBhNZYW/lmci3Zt1/GiSw0r/wty2p5g0I6QNcZ4"
    "VYcgoc/lbQrISXwxmDNsIumH0DJaoroTghHtORedmTpyoeb6pNnVFzF1roV9Iq4/"
    "AUaG9ih5yLHa5FcXxH4cDrC0kqZWs72yl+2qp/C3xag/lRbQ/6GW6whfGHdPAgMB"
    "AAGjYzBhMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW"
    "BBRF66Kv9JLLgjEtUYunpyGd823IDzAfBgNVHSMEGDAWgBRF66Kv9JLLgjEtUYun"
    "pyGd823IDzANBgkqhkiG9w0BAQUFAAOCAQEAog683+Lt8ONyc3pklL/3cmbYMuRC"
    "dWKuh+vy1dneVrOfzM4UKLkNl2BcEkxY5NM9g0lFWJc1aRqoR+pWxnmrEthngYTf"
    "fwk8lOa4JiwgvT2zKIn3X/8i4peEH+ll74fg38FnSbNd67IJKusm7Xi+fT8r87cm"
    "NW1fiQG2SVufAQWbqz0lwcy2f8Lxb4bG+mRo64EtlOtCt/qMHt1i8b5QZ7dsvfPx"
    "H2sMNgcWfzd8qVttevESRmCD1ycEvkvOl77DZypoEd+A5wwzZr8TDRRu838fYxAe"
    "+o0bJW1sj6W3YQGx0qMmoRBxna3iw/nDmVG3KwcIzi7mULKn+gpFL6Lw8g==";
constexpr std::string_view signing_root_subject_key_id = "45EBA2AFF492CB82312D518BA7A7219DF36DC80F";
std::string from_hex(std::string_view hex) {
  std::string out;
  for (size_t i = 0; i + 1 < hex.size(); i += 2)
    out += char(std::stoi(std::string(hex.substr(i, 2)), nullptr, 16));
  return out;
}
std::string to_hex(std::string_view bytes) {
  std::string out;
  for (unsigned char c : bytes)
    out += std::format("{:02x}", c);
  return out;
}
std::string base64_decode(std::string_view text) {
  constexpr std::string_view alphabet =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  std::uint32_t buffer = 0;
  int bits = 0;
  for (char c : text) {
    auto value = alphabet.find(c);
    if (value == alphabet.npos)
      continue;  // padding
    buffer = (buffer << 6) | std::uint32_t(value);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out += char((buffer >> bits) & 0xff);
    }
  }
  return out;
}
} // namespace

std::string MetaPackage::url() const {
  return "https://securecdn.oculus.com/binaries/download/?id=" + binary_id;
}
const std::vector<std::pair<std::string, std::string>> &meta_runtime_signed_files() {
  static const std::vector<std::pair<std::string, std::string>> files{
      {"LibOVRRT32_1.dll", "e6435a297861f781d952ba21fdf009bc7d36fdaad0f96e0d39d3b419e1783983"},
      {"LibOVRRT64_1.dll", "f6941275692026b18666bb856d71fe1b19462017b2b2e556fe8df82461f493f5"},
  };
  return files;
}
const std::vector<MetaPackage> &meta_packages() {
  static const std::vector<MetaPackage> packages{
      {"oculus-runtime", "3766757683456363",
       "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009",
       {"LibOVRRT32_1.dll", "LibOVRRT64_1.dll"}, true},
      {"oculus-platform-runtime", "28039291642329992",
       "69a6dedbf6f997459038e9b9e54d6562431c1a107bec17637d59626ab0d36892",
       {"FBCapture.dll", "manifest.json", "oculus-platform-runtime.exe"}, false},
  };
  return packages;
}
const std::vector<PatchRecipe> &meta_runtime_patches() {
  static const std::vector<PatchRecipe> recipes{
      {"OVRServiceLauncher.exe", "c83ac93d1698ba2459036c65806d3e713e3bf5a611c1996096aec1d8e7e3a8c8",
       "68fb190b52195fbac3d1647e4667a1914db60ed7858b8522ffbbb2c696cbdbe9",
       {{11110, bytes_of({0x74}), bytes_of({0xeb})}}},
      {"OVRServer_x64.exe", "70ed0c064c806d7602b66cb3c70bd1c1c60f755409220d750799a653509b24a3",
       "a2b2dc81f8713d1c3d03c38e379e31c5c3d345209be8f7ad781740207311af12",
       {{1472722, bytes_of({0x0f, 0x85, 0x09, 0x02, 0x00, 0x00}), bytes_of({0x90, 0x90, 0x90, 0x90, 0x90, 0x90})},
        {1472754, bytes_of({0x0f, 0x84, 0xe9, 0x01, 0x00, 0x00}), bytes_of({0xe9, 0xdd, 0x01, 0x00, 0x00, 0x90})}}},
      {"OculusAppFramework.dll", "c4b3a35269c34ff640cb87011751cd1ee7b04774570e73eae333fc1a60f097df",
       "f0cf8cfe2f1bba513fab1e4d34a3cf2727705e8083a004b85f6608086d0b6a03",
       {{8731009, bytes_of({0x0f, 0x84, 0x92, 0x00, 0x00, 0x00}), bytes_of({0xe9, 0x93, 0x00, 0x00, 0x00, 0x90})},
        {9208329, bytes_of({0x74}), bytes_of({0xeb})}}},
  };
  return recipes;
}
std::optional<std::string> patch_bytes(std::string payload, const PatchRecipe &recipe) {
  auto digest = sha256_of(payload);
  if (digest == recipe.output_sha256)
    return std::nullopt;
  if (digest != recipe.input_sha256)
    throw std::runtime_error(std::format("unsupported {} build ({}); refusing an unsafe patch",
                                         recipe.file, digest.substr(0, 12)));
  for (auto &change : recipe.changes) {
    if (change.offset + change.before.size() > payload.size() ||
        payload.compare(change.offset, change.before.size(), change.before) != 0)
      throw std::runtime_error(std::format("{} compatibility patch did not match at {}",
                                           recipe.file, change.offset));
    payload.replace(change.offset, change.before.size(), change.after);
  }
  if (sha256_of(payload) != recipe.output_sha256)
    throw std::runtime_error(recipe.file + " compatibility patch produced an unexpected result");
  return payload;
}
void patch_meta_runtime(const fs::path &runtime) {
  for (auto &recipe : meta_runtime_patches()) {
    auto target = runtime / recipe.file;
    if (auto patched = patch_bytes(read_file(target), recipe))
      write_file(target, *patched, fs::perms(0644));
  }
  auto plugins = runtime / "server-plugins";
  auto disabled = runtime / ".riftlift-disabled-server-plugins";
  std::error_code ec;
  for (auto name : {"Rift.dll", "RiftS.dll"}) {
    auto source = plugins / name;
    auto legacy = plugins / ".riftlift-disabled" / name;
    if (fs::is_regular_file(legacy, ec) && !fs::exists(source, ec))
      source = legacy;
    if (fs::is_regular_file(source, ec)) {
      fs::create_directories(disabled);
      fs::rename(source, disabled / name);
    }
  }
  auto legacy_dir = plugins / ".riftlift-disabled";
  if (fs::is_directory(legacy_dir, ec) && fs::is_empty(legacy_dir, ec))
    fs::remove(legacy_dir, ec);
}
bool signed_meta_runtime_current(const fs::path &runtime) {
  for (auto &[name, expected] : meta_runtime_signed_files())
    if (try_sha256_file(runtime / name) != expected)
      return false;
  return true;
}
bool meta_package_current(const fs::path &destination, const MetaPackage &package) {
  try {
    auto marker = parse_json(read_file(destination / ".riftlift-package.json"));
    if (!marker.is_object() || str(marker, "sha256") != package.sha256)
      return false;
    if (package.verify_signed_runtime)
      return signed_meta_runtime_current(destination);
    const auto &files = field(marker, "files");
    if (!files.is_object() || package.required_files.empty())
      return false;
    for (auto &name : package.required_files) {
      const auto &expected = field(files, name);
      if (!expected.is_string() || try_sha256_file(destination / name) != expected.get<std::string>())
        return false;
    }
    return true;
  } catch (...) {
    return false;
  }
}
void install_meta_packages(const Layout &layout, const fs::path &support, std::stop_token stop) {
  fs::create_directories(support);
  for (auto &package : meta_packages()) {
    auto archive = fetch(package.url(), layout.cache / "meta" / (package.name + ".pkg"),
                         package.sha256, Origin::meta, stop);
    auto destination = support / package.name;
    if (meta_package_current(destination, package))
      continue;
    Staging staging(support, "." + package.name + "-unpack-");
    extract_zip(archive, staging.path());
    nlohmann::ordered_json files = nlohmann::ordered_json::object();
    for (auto &name : package.required_files) {
      if (!fs::is_regular_file(staging.path() / name))
        throw std::runtime_error("Meta package " + package.name + " is incomplete");
      files[name] = sha256_file(staging.path() / name);
    }
    nlohmann::ordered_json marker{{"binary_id", package.binary_id},
                                  {"sha256", package.sha256},
                                  {"files", files}};
    write_file(staging.path() / ".riftlift-package.json", marker.dump(2) + "\n");
    replace_directory(staging.path(), destination);
  }
}
std::string meta_signing_root_registry_key() {
  return "HKLM\\Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\" +
         std::string(meta_signing_root_thumbprint);
}
std::string meta_signing_root_der() { return base64_decode(signing_root_pem); }
std::string meta_signing_root_blob_hex() {
  auto property = [](std::uint32_t id, std::string_view value) {
    std::string out;
    for (std::uint32_t word : {id, std::uint32_t(1), std::uint32_t(value.size())})
      for (int i = 0; i < 4; ++i)
        out += char((word >> (8 * i)) & 0xff);  // struct.pack("<III")
    return out + std::string(value);
  };
  return to_hex(property(3, from_hex(meta_signing_root_thumbprint)) +
                property(20, from_hex(signing_root_subject_key_id)) +
                property(32, meta_signing_root_der()));
}
bool meta_signing_root_installed(const fs::path &prefix) {
  const auto key = "[Software\\\\Microsoft\\\\SystemCertificates\\\\Root\\\\Certificates\\\\" +
                   std::string(meta_signing_root_thumbprint) + "]";
  try {
    return read_file(prefix / "pfx/system.reg").find(key) != std::string::npos;
  } catch (...) {
    return false;
  }
}
std::vector<RegistryValue> meta_registry_values() {
  std::vector<RegistryValue> values;
  const std::string base = "C:\\Program Files\\Oculus";
  const std::vector<std::string> roots{"HKCU\\Software\\Oculus VR, LLC\\Oculus",
                                       "HKLM\\Software\\Oculus VR, LLC\\Oculus",
                                       "HKLM\\Software\\WOW6432Node\\Oculus VR, LLC\\Oculus"};
  for (auto &key : roots) {
    values.push_back({key, "Base", "REG_SZ", base});
    values.push_back({key, "UseSystemProxy", "REG_DWORD", "0"});
  }
  for (size_t i = 1; i < roots.size(); ++i)
    values.push_back({roots[i], "Gestalt", "REG_DWORD", "1"});
  for (auto &key : roots)
    values.push_back({key + "\\Config", "UseSystemProxy", "REG_DWORD", "0"});
  const auto wow_config = roots.back() + "\\Config";
  values.push_back({wow_config, "CldrLocaleCode", "REG_SZ", "en"});
  values.push_back({wow_config, "FbtLocaleCode", "REG_SZ", "en_US"});
  values.push_back({wow_config, "LanguageTag", "REG_SZ", "en-US"});
  values.push_back({wow_config, "HomeDemoMode", "REG_DWORD", "0"});
  const std::string service = "HKLM\\System\\CurrentControlSet\\Services\\OVRService";
  values.push_back({service, "DisplayName", "REG_SZ", "Oculus VR Runtime Service"});
  values.push_back({service, "Description", "REG_SZ", "Oculus VR Runtime Service"});
  values.push_back({service, "ImagePath", "REG_EXPAND_SZ",
                    "\"C:\\Program Files\\Oculus\\Support\\oculus-runtime\\OVRServiceLauncher.exe\""});
  values.push_back({service, "ObjectName", "REG_SZ", "LocalSystem"});
  values.push_back({service, "Type", "REG_DWORD", "16"});
  values.push_back({service, "Start", "REG_DWORD", "4"});
  values.push_back({service, "ErrorControl", "REG_DWORD", "1"});
  return values;
}
std::vector<std::string> reg_add_arguments(const RegistryValue &value) {
  std::vector<std::string> args{"reg.exe", "add", value.key};
  if (value.name)
    args.insert(args.end(), {"/v", *value.name});
  else
    args.push_back("/ve");
  args.insert(args.end(), {"/t", value.kind, "/d", value.value, "/f"});
  return args;
}
} // namespace riftlift::runtime
