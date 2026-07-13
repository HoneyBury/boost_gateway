"""Boost Gateway SDK - Python bindings"""
import os
import platform
from setuptools import setup, Extension

# Determine the shared library extension
if platform.system() == "Windows":
    lib_ext = ".dll"
elif platform.system() == "Darwin":
    lib_ext = ".dylib"
else:
    lib_ext = ".so"

setup(
    name="boost-gateway-sdk",
    version="4.1.0",
    description="Boost Gateway Game Server SDK - Python bindings",
    long_description="Python bindings for the Boost Gateway game server framework SDK",
    author="BoostGateway Team",
    packages=["boost_gateway_sdk"],
    package_dir={"boost_gateway_sdk": "."},
    package_data={"boost_gateway_sdk": [f"libboost_gateway_sdk*{lib_ext}"]},
    include_package_data=True,
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "Programming Language :: C++",
        "Topic :: Games/Entertainment",
    ],
)
