#!/usr/bin/env python3
"""Update the Cask definitions and root README with the latest Azul Zulu JDKs."""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

BASE_URL = "https://api.azul.com/metadata/v1/zulu/packages/"
USER_AGENT = "homebrew-zulu-updater/1.0"
LATEST_PACKAGES_QUERY = {
  "availability_type": "ca",
  "os": "macos",
  "archive_type": "dmg",
  "java_package_type": "jdk",
  "javafx_bundled": "false",
  "release_status": "ga",
  "latest": "true",
}
TIMEOUT_SECONDS = 30
MINIMUM_JDK_VERSION = 7
VERSION_HEADER = "## Versions\n\n"
HOMEPAGE = "https://www.azul.com/downloads/?os=macos&package=jdk#zulu"

# Zulu respin versions.
# They update the same bundle version after fixing some packaging issues.
# Respin versions have a number suffix after an underscore: `_1`, `_2` etc.
RESPIN_VERSIONS = frozenset()

# JDKs whose installer package uses the legacy name.
LEGACY_PKG_NAME_VERSIONS = frozenset({9, 10, 12, 14})


class Package(NamedTuple):
  package_uuid: str
  sha256_hash: str
  download_url: str
  java_version: list[int]
  distro_version: list[int]

  @classmethod
  def from_json(cls, data: dict[str, Any]) -> "Package":
    return cls(**{field: data.get(field) for field in cls._fields})

  @property
  def cask_version(self) -> str:
    return f"{self.zulu_version},{self.java_version_string}"

  @property
  def java_version_string(self) -> str:
    return ".".join(map(str, self.java_version))

  @property
  def zulu_version(self) -> str:
    zulu_string = ".".join(map(str, self.distro_version))
    if len(self.distro_version) > 3:
      zulu_string = zulu_string.removesuffix(".0")
    return zulu_string


class PackageSet(NamedTuple):
  x86: Package
  arm: Package | None


def get_json(url: str) -> Any:
  request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
  with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
    return json.load(response)


def get_package_details(version: int, architecture: str) -> Package | None:
  query = urllib.parse.urlencode(
    LATEST_PACKAGES_QUERY | {"java_version": version, "arch": architecture}
  )
  try:
    latest_packages = get_json(f"{BASE_URL}?{query}")
  except OSError as e:
    raise OSError(f"Failed to load JDK {version} {architecture} package list: {e}") from e

  if not latest_packages:
    return None

  # Data are sorted, with the newest packages being returned first.
  package_uuid = latest_packages[0]["package_uuid"]
  try:
    return Package.from_json(get_json(BASE_URL + package_uuid))
  except OSError as e:
    raise OSError(f"Failed to load package details for {package_uuid}: {e}") from e


def load_jdks() -> dict[int, PackageSet]:
  jdk_packages: dict[int, PackageSet] = {}
  jdk_version = MINIMUM_JDK_VERSION
  while True:
    x86_package = get_package_details(jdk_version, "x86")
    print(f"{jdk_version} x86 {x86_package}")

    arm_package = get_package_details(jdk_version, "arm")
    print(f"{jdk_version} ARM {arm_package}")

    if x86_package is None:
      if arm_package is None:
        break
      raise RuntimeError(f"JDK {jdk_version} missing x86 arch")

    jdk_packages[jdk_version] = PackageSet(x86_package, arm_package)
    jdk_version += 1
  return jdk_packages


def render_package(package: Package) -> str:
  return (
    f"    version '{package.cask_version}'\n"
    f"    sha256 '{package.sha256_hash}'\n\n"
    f"    url '{package.download_url}',\n"
    f"        referer: '{HOMEPAGE}'\n\n"
  )


def render_cask(jdk_version: int, packages: PackageSet) -> str:
  out = [f"cask 'zulu-jdk{jdk_version}' do\n\n"]

  if packages.arm is not None:
    out.append("  on_intel do\n")
    out.append(render_package(packages.x86))
    out.append("  end\n")
    out.append("  on_arm do\n")
    out.append(render_package(packages.arm))
    out.append("  end\n")
  else:
    out.append(render_package(packages.x86))

  out.append(f"\n  name 'Azul Zulu® JDK {jdk_version}'")
  out.append("\n  desc 'OpenJDK distribution from Azul'\n")
  out.append(f"  homepage '{HOMEPAGE}'\n\n")
  if jdk_version in LEGACY_PKG_NAME_VERSIONS:
    out.append(f"  pkg 'Double-Click to Install Zulu {jdk_version}.pkg'\n\n")
  else:
    out.append(f"  pkg 'Double-Click to Install Azul Zulu JDK {jdk_version}.pkg'\n\n")
  out.append(f"  uninstall pkgutil: 'com.azulsystems.zulu.{jdk_version}'\n")
  out.append("end\n")
  return "".join(out)


def render_readme(readme_text: str, jdk_packages: dict[int, PackageSet]) -> str:
  readme_start = readme_text[: readme_text.index(VERSION_HEADER) + len(VERSION_HEADER)]
  out = [readme_start, "| JDK | Cask Name | Version |\n", "|--|--|--|\n"]
  for jdk_version, packages in sorted(jdk_packages.items()):
    out.append(
      f"| OpenJDK {jdk_version} | `zulu-jdk{jdk_version}` | {packages.x86.java_version_string} |\n"
    )
  out.append("| Mission Control | `zulu-mc` | 9.1.0.25 |\n")
  return "".join(out)


def main(args: list[str]) -> None:
  if len(args) > 1:
    sys.exit("Usage: updater.py [repository-dir]")

  # Default to the repository containing this script.
  repo_dir = Path(args[0]).resolve() if args else Path(__file__).resolve().parent
  print(f"Repository dir: {repo_dir}")

  readme_file = repo_dir / "README.md"
  cask_dir = repo_dir / "Casks"

  jdk_packages = load_jdks()
  for jdk_version, packages in jdk_packages.items():
    if packages.x86.zulu_version in RESPIN_VERSIONS:
      print(
        "Skipping bundle because this is a respin version of an existing package => "
        f"{packages.x86}",
        file=sys.stderr,
      )
      continue

    cask_file = cask_dir / f"zulu-jdk{jdk_version}.rb"
    cask_file.write_text(render_cask(jdk_version, packages), encoding="utf-8")

  readme_text = readme_file.read_text(encoding="utf-8")
  readme_file.write_text(render_readme(readme_text, jdk_packages), encoding="utf-8")


if __name__ == "__main__":
  main(sys.argv[1:])
