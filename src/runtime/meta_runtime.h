#pragma once
// Meta's Oculus runtime packages: what is downloaded, how its service
// binaries are patched to run under Wine, and the registry it expects.
#include "runtime/layout.h"
#include <optional>
#include <stop_token>
#include <string>
#include <vector>

namespace riftlift::runtime {

struct MetaPackage {
  std::string name, binary_id, sha256;
  std::vector<std::string> required_files;
  bool verify_signed_runtime = false;
  std::string url() const;  // securecdn.oculus.com/binaries/download/?id=...
};
// oculus-runtime (3766757683456363) and oculus-platform-runtime (28039291642329992).
const std::vector<MetaPackage> &meta_packages();
inline constexpr std::string_view meta_version = "205.0";
// LibOVRRT32_1.dll / LibOVRRT64_1.dll: Meta-signed files that must stay intact.
const std::vector<std::pair<std::string, std::string>> &meta_runtime_signed_files();

struct BytePatch {
  std::size_t offset;
  std::string before, after;
};
struct PatchRecipe {
  std::string file, input_sha256, output_sha256;
  std::vector<BytePatch> changes;
};
const std::vector<PatchRecipe> &meta_runtime_patches();
// The patched bytes, or nothing when `payload` is already patched; throws for
// any other build or a mismatching byte.
std::optional<std::string> patch_bytes(std::string payload, const PatchRecipe &recipe);
// Patches the service binaries in place and moves the Rift/Rift S server
// plugins out of server-plugins.
void patch_meta_runtime(const fs::path &runtime);

bool signed_meta_runtime_current(const fs::path &runtime);
bool meta_package_current(const fs::path &destination, const MetaPackage &package);
// Downloads (to <cache>/meta) and unpacks each package into `support` unless
// it is already current.
void install_meta_packages(const Layout &layout, const fs::path &support, std::stop_token stop);

// The DigiCert root Meta's LibOVR signatures chain to.
inline constexpr std::string_view meta_signing_root_thumbprint = "0563B8630D62D75ABBC8AB1E4BDFB5A899B24D43";
std::string meta_signing_root_registry_key();
std::string meta_signing_root_der();
// The certificate properties Wine's registry store reads (thumbprint, key ID,
// certificate), as lower-case hex for reg.exe /t REG_BINARY.
std::string meta_signing_root_blob_hex();
// Wine's system.reg already holds the certificate.
bool meta_signing_root_installed(const fs::path &prefix);

struct RegistryValue {
  std::string key;
  std::optional<std::string> name;  // nothing: the key's default value (/ve)
  std::string kind, value;
};
// The Oculus base, locale and OVRService values, in the order they are written.
std::vector<RegistryValue> meta_registry_values();
// reg.exe arguments adding one value.
std::vector<std::string> reg_add_arguments(const RegistryValue &value);

} // namespace riftlift::runtime
