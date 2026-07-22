"""Boost Gateway SDK - Python bindings"""
import os
import platform
from setuptools import Distribution, setup
from wheel.bdist_wheel import bdist_wheel

# Determine the shared library extension
if platform.system() == "Windows":
    lib_ext = ".dll"
elif platform.system() == "Darwin":
    lib_ext = ".dylib"
else:
    lib_ext = ".so"

class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


class PlatformWheel(bdist_wheel):
    """The ctypes wrapper is ABI-independent but carries a platform binary."""

    def get_tag(self):
        _, _, platform_tag = super().get_tag()
        return "py3", "none", platform_tag


native_candidates = [name for name in os.listdir(".") if name.startswith("libboost_gateway_sdk") and name.endswith(lib_ext)]
if platform.system() == "Windows":
    native_candidates.extend(name for name in os.listdir(".") if name == "boost_gateway_sdk.dll")
if not native_candidates:
    raise RuntimeError(f"missing bundled BoostGateway native library ({lib_ext})")


setup(
    name="boost-gateway-sdk",
    version="4.2.0",
    description="Boost Gateway Game Server SDK - Python bindings",
    long_description="Python bindings for the Boost Gateway game server framework SDK",
    author="BoostGateway Team",
    packages=["boost_gateway_sdk"],
    package_dir={"boost_gateway_sdk": "."},
    package_data={"boost_gateway_sdk": [f"libboost_gateway_sdk*{lib_ext}", "_native_manifest.json"]},
    include_package_data=True,
    distclass=BinaryDistribution,
    cmdclass={"bdist_wheel": PlatformWheel},
    license_files=["LICENSE"],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "Programming Language :: C++",
        "Topic :: Games/Entertainment",
    ],
)
