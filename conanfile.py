from conan import ConanFile
from conan.tools.cmake import CMakeDeps, CMakeToolchain, cmake_layout


class BoostGatewayConan(ConanFile):
    name = "boost_gateway_workspace"
    version = "0.1"
    package_type = "application"

    # Conan >=2.0, <2.9 to avoid CMakeDeps header-only library regression in 2.30+
    # See: docs/performance-baseline.md P1 fmt header_only=False workaround
    conan_version = ">=2.0.0,<2.9.0"

    settings = "os", "arch", "compiler", "build_type"
    options = {
        "with_grpc": [True, False],
        "with_raft_protobuf": [True, False],
        "with_sqlite": [True, False],
    }
    default_options = {
        "&:with_grpc": False,
        "&:with_raft_protobuf": True,
        "&:with_sqlite": False,
        "fmt/*:header_only": False,
        "spdlog/*:header_only": False,
        "openssl/*:shared": False,
    }

    def requirements(self):
        self.requires("boost/1.86.0")
        self.requires("fmt/11.2.0")
        self.requires("spdlog/1.15.3")
        self.requires("nlohmann_json/3.12.0")
        self.requires("openssl/3.3.2")
        self.requires("hiredis/1.2.0")
        if bool(self.options.get_safe("with_sqlite")):
            self.requires("sqlite3/3.46.1")
        self.requires("gtest/1.15.0")
        if bool(self.options.get_safe("with_raft_protobuf")) or bool(self.options.get_safe("with_grpc")):
            self.requires("protobuf/5.27.0")
        if bool(self.options.get_safe("with_grpc")):
            self.requires("grpc/1.67.1")

    def configure(self):
        if self.options.get_safe("with_grpc") is None:
            self.options.with_grpc = False
        if self.options.get_safe("with_raft_protobuf") is None:
            self.options.with_raft_protobuf = True
        if self.options.get_safe("with_sqlite") is None:
            self.options.with_sqlite = False

    def layout(self):
        cmake_layout(self)

    def generate(self):
        deps = CMakeDeps(self)
        deps.generate()

        tc = CMakeToolchain(self)
        tc.variables["BOOST_DEPENDENCY_PROVIDER"] = "conan"
        tc.variables["BOOST_BUILD_GRPC"] = bool(self.options.get_safe("with_grpc"))
        tc.variables["BOOST_BUILD_RAFT_PROTOBUF"] = bool(
            self.options.get_safe("with_raft_protobuf")
        )
        tc.variables["BOOST_BUILD_SQLITE"] = bool(self.options.get_safe("with_sqlite"))
        tc.generate()
