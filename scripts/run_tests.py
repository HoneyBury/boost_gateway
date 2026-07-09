#!/usr/bin/env python3
"""
Unified test runner — wraps ctest with layer-specific filtering.

Usage:
    python3 scripts/run_tests.py                        # Run all (ctest --preset default)
    python3 scripts/run_tests.py unit                   # ctest -L unit
    python3 scripts/run_tests.py integration            # ctest -L integration
    python3 scripts/run_tests.py e2e                    # ctest -L e2e
    python3 scripts/run_tests.py sdk                    # ctest -L sdk
    python3 scripts/run_tests.py perf                   # Show perf test instructions
    python3 scripts/run_tests.py all                    # Same as no argument
    python3 scripts/run_tests.py --list                 # List available layers

Options:
    --preset <name>     CMake test preset (default: "default")
    --timeout <sec>     Test timeout in seconds (default: 300)
    --parallel <n>      Parallel test jobs (default from ctest)
    --build-dir <path>  Build directory override
    --verbose           Verbose ctest output
"""

import argparse
import sys
import subprocess
import os


LAYERS = {
    "unit": {
        "label": "unit",
        "description": "Unit tests (pure logic, sub-second to seconds)",
        "optional": False,
        "requires_build_flag": None,
    },
    "integration": {
        "label": "integration",
        "description": "Integration tests (in-memory backend simulation, minutes)",
        "optional": False,
        "requires_build_flag": None,
    },
    "e2e": {
        "label": "e2e",
        "description": "Multi-process E2E tests (real OS processes, minutes)",
        "optional": False,
        "requires_build_flag": None,
    },
    "sdk": {
        "label": "sdk",
        "description": "SDK tests (C++/C ABI unit + business flow integration)",
        "optional": False,
        "requires_build_flag": None,
    },
    "perf": {
        "label": None,
        "description": "Performance benchmarks (30s smoke / 30min baseline)",
        "optional": True,
        "requires_build_flag": "BOOST_BUILD_PERF_TESTS=ON",
        "hint": (
            "Perf tests require:\n"
            "  1. cmake -DBOOST_BUILD_PERF_TESTS=ON <other args>\n"
            "  2. cmake --build <dir> --parallel\n"
            "  3. ctest -R perf --preset release"
        ),
    },
    "fuzz": {
        "label": None,
        "description": "Fuzz tests (protocol codec fuzzing)",
        "optional": True,
        "requires_build_flag": "BOOST_BUILD_FUZZ_TESTS=ON",
        "hint": (
            "Fuzz tests require:\n"
            "  1. cmake -DBOOST_BUILD_FUZZ_TESTS=ON <other args>\n"
            "  2. cmake --build <dir> --parallel\n"
            "  3. ctest -R fuzz --preset default"
        ),
    },
    "security": {
        "label": None,
        "description": "Security tests (protocol security, TLS edge cases)",
        "optional": True,
        "requires_build_flag": "BOOST_BUILD_SECURITY_TESTS=ON",
        "hint": (
            "Security tests require:\n"
            "  1. cmake -DBOOST_BUILD_SECURITY_TESTS=ON <other args>\n"
            "  2. cmake --build <dir> --parallel\n"
            "  3. ctest -R security --preset default"
        ),
    },
}


def find_build_dir(preset):
    """Attempt to auto-detect the build directory for a preset."""
    # Common build directory conventions
    candidates = [
        "build/default",
        "build/release",
        f"build/{preset}",
    ]
    for path in candidates:
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "CTestTestfile.cmake")):
            return os.path.abspath(path)
    return None


def list_layers():
    print("Available test layers:")
    print()
    for name, info in LAYERS.items():
        flag = f"  [requires {info['requires_build_flag']}]" if info["requires_build_flag"] else ""
        optional = " (optional)" if info["optional"] else ""
        print(f"  {name:12s}  {info['description']}{optional}{flag}")
    print()
    print("Usage:  python3 scripts/run_tests.py <layer> [options]")
    print("        python3 scripts/run_tests.py --list")
    sys.exit(0)


def build_ctest_command(args, layer_info):
    cmd = ["ctest"]

    preset = args.preset
    build_dir = args.build_dir

    # Try to auto-detect build dir if not specified
    if build_dir is None:
        build_dir = find_build_dir(preset)

    if build_dir:
        cmd.extend(["--test-dir", build_dir])

    # Use label filter for layers with labels
    label = layer_info.get("label")
    if label:
        cmd.extend(["-L", label])
    else:
        # For layers without labels (perf, fuzz, security), use regex on test name
        layer_regex = {
            "perf": "perf",
            "fuzz": "fuzz",
            "security": "security",
        }
        regex = layer_regex.get(args.layer, "")
        if regex:
            cmd.extend(["-R", regex])

    if args.timeout:
        cmd.extend(["--timeout", str(args.timeout)])
    if args.parallel:
        cmd.extend(["--parallel", str(args.parallel)])
    if args.verbose:
        cmd.append("--output-on-failure")

    return cmd


def main():
    parser = argparse.ArgumentParser(
        description="Unified test runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Layers:\n"
            "  unit         Unit tests (-L unit)\n"
            "  integration  Integration tests (-L integration)\n"
            "  e2e          Multi-process E2E tests (-L e2e)\n"
            "  sdk          SDK tests (-L sdk)\n"
            "  perf         Performance benchmarks (optional build, -R perf)\n"
            "  fuzz         Fuzz tests (optional build, -R fuzz)\n"
            "  security     Security tests (optional build, -R security)\n"
            "  all          All tests (default)"
        ),
    )
    parser.add_argument(
        "layer",
        nargs="?",
        default="all",
        help="Test layer to run (default: all)",
    )
    parser.add_argument("--preset", default="default", help="CMake test preset")
    parser.add_argument("--timeout", type=int, default=300, help="Test timeout in seconds")
    parser.add_argument("--parallel", type=int, default=None, help="Parallel test jobs")
    parser.add_argument("--build-dir", default=None, help="Build directory override")
    parser.add_argument("--list", action="store_true", help="List available layers")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.list:
        list_layers()

    layer = args.layer

    if layer == "all":
        # Run everything: all labeled tests + perf/fuzz/security if built
        cmd = ["ctest"]
        if args.build_dir:
            cmd.extend(["--test-dir", args.build_dir])
        if args.timeout:
            cmd.extend(["--timeout", str(args.timeout)])
        if args.parallel:
            cmd.extend(["--parallel", str(args.parallel)])
        if args.verbose:
            cmd.append("--output-on-failure")
        print(f"Running all tests: {' '.join(cmd)}")
        sys.exit(subprocess.call(cmd))

    if layer not in LAYERS:
        print(f"Unknown layer: {layer}", file=sys.stderr)
        print("Use --list to see available layers.", file=sys.stderr)
        sys.exit(1)

    layer_info = LAYERS[layer]

    # For optional layers, print hint if they can't run
    if layer_info["optional"]:
        print(f"Layer '{layer}' is optional ({layer_info['requires_build_flag']}).")
        print(layer_info.get("hint", ""))
        # Still try to run it in case it was built
        print()

    cmd = build_ctest_command(args, layer_info)
    print(f"Running: {' '.join(cmd)}")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
