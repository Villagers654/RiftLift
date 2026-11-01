#include "common/digest.h"
#include "runtime/meta_runtime.h"
#include <doctest/doctest.h>

using namespace riftlift;
using namespace riftlift::runtime;

namespace {
std::string sha(std::string_view bytes) {
  return digest::sha256({reinterpret_cast<const std::uint8_t *>(bytes.data()), bytes.size()});
}
} // namespace

TEST_CASE("runtime: Meta binaries are patched only from the known build") {
  std::string input = "abcdefgh";
  std::string output = "abXYefgh";
  PatchRecipe recipe{"Test.exe", sha(input), sha(output), {{2, "cd", "XY"}}};
  auto patched = patch_bytes(input, recipe);
  REQUIRE(patched);
  CHECK(*patched == output);
  CHECK_FALSE(patch_bytes(output, recipe));  // already patched
  CHECK_THROWS_WITH(patch_bytes("other", recipe),
                    doctest::Contains("unsupported Test.exe build"));
  PatchRecipe wrong{"Test.exe", sha(input), sha(output), {{3, "cd", "XY"}}};
  CHECK_THROWS_WITH(patch_bytes(input, wrong), doctest::Contains("did not match at 3"));
  PatchRecipe beyond{"Test.exe", sha(input), sha(output), {{7, "hh", "XY"}}};
  CHECK_THROWS(patch_bytes(input, beyond));
}

TEST_CASE("runtime: Meta runtime patch recipes") {
  auto &recipes = meta_runtime_patches();
  REQUIRE(recipes.size() == 3);
  CHECK(recipes[1].file == "OVRServer_x64.exe");
  CHECK(recipes[1].changes[1].offset == 1472754);
  CHECK(recipes[1].changes[1].after == std::string("\xe9\xdd\x01\x00\x00\x90", 6));
  for (auto &recipe : recipes)
    for (auto &change : recipe.changes)
      CHECK(change.before.size() == change.after.size());
}

TEST_CASE("runtime: Meta packages") {
  auto &packages = meta_packages();
  REQUIRE(packages.size() == 2);
  CHECK(packages[0].url() == "https://securecdn.oculus.com/binaries/download/?id=3766757683456363");
  CHECK(packages[0].verify_signed_runtime);
  CHECK(packages[1].binary_id == "28039291642329992");
}

TEST_CASE("runtime: Meta signing root registry blob") {
  auto der = meta_signing_root_der();
  CHECK(der.size() == 955);
  CHECK(static_cast<unsigned char>(der[0]) == 0x30);
  auto blob = meta_signing_root_blob_hex();
  // <id 3><flag 1><size 20><SHA-1 thumbprint>...
  CHECK(blob.starts_with("030000000100000014000000"
                         "0563b8630d62d75abbc8ab1e4bdfb5a899b24d43"
                         "140000000100000014000000"
                         "45eba2aff492cb82312d518ba7a7219df36dc80f"
                         "2000000001000000bb030000"));
  CHECK(blob.size() == 2 * (12 + 20 + 12 + 20 + 12 + der.size()));
  CHECK(sha(der) == "3e9099b5015e8f486c00bcea9d111ee721faba355a89bcf1df69561e3dc6325c");
}

TEST_CASE("runtime: Meta registry values") {
  auto values = meta_registry_values();
  CHECK(values.size() == 22);
  CHECK(values.front().key == "HKCU\\Software\\Oculus VR, LLC\\Oculus");
  CHECK(values.back().name == "ErrorControl");
  CHECK(reg_add_arguments(values.front()) ==
        std::vector<std::string>{"reg.exe", "add", "HKCU\\Software\\Oculus VR, LLC\\Oculus", "/v", "Base", "/t",
                                 "REG_SZ", "/d", "C:\\Program Files\\Oculus", "/f"});
  CHECK(reg_add_arguments({"HKLM\\X", std::nullopt, "REG_SZ", "v"}) ==
        std::vector<std::string>{"reg.exe", "add", "HKLM\\X", "/ve", "/t", "REG_SZ", "/d", "v", "/f"});
  CHECK(meta_signing_root_registry_key() ==
        "HKLM\\Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\0563B8630D62D75ABBC8AB1E4BDFB5A899B24D43");
}
