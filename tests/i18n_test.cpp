#include "launcher/i18n.h"
#include <doctest/doctest.h>

using namespace riftlift::launcher;

TEST_CASE("translations") {
  i18n::set_language("fr");
  CHECK(i18n::language() == "fr");
  CHECK(i18n::tr("Library") == "Bibliothèque");
  CHECK(i18n::tr("Lone Echo") == "Lone Echo");
  CHECK(i18n::trf("Uninstall {}?", std::string("Lone Echo")) == "Désinstaller Lone Echo ?");
  i18n::set_language("en");
  CHECK(i18n::tr("Library") == "Library");
  CHECK(i18n::trf("Uninstall {}?", std::string("Lone Echo")) == "Uninstall Lone Echo?");
  i18n::set_language("xx");
  CHECK(i18n::language() == "en");
}
