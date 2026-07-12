# Proto/CMakeLists.txt — Protocol Buffers and gRPC code generation.
#
# Enabled only when BOOST_BUILD_GRPC=ON.
# Generated C++ stubs are placed under ${CMAKE_BINARY_DIR}/generated/proto/
# and exposed via the `project_proto` interface target.

if(NOT BOOST_BUILD_GRPC)
  message(STATUS "Proto: BOOST_BUILD_GRPC=OFF — skipping proto/gRPC code generation")
  return()
endif()

# ── Find dependencies ──────────────────────────────────────────────────────
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)

include(${CMAKE_SOURCE_DIR}/cmake/FindGRPC.cmake)
find_protoc(PROTOC)
find_grpc_cpp_plugin(GRPC_CPP_PLUGIN)

if(NOT PROTOC OR NOT GRPC_CPP_PLUGIN)
  message(FATAL_ERROR "Proto: protoc and grpc_cpp_plugin are required when BOOST_BUILD_GRPC=ON")
endif()

# ── Source & output directories ────────────────────────────────────────────
set(PROTO_ROOT_DIR "${CMAKE_SOURCE_DIR}/proto")
set(PROTO_V3_DIR   "${PROTO_ROOT_DIR}/v3")
set(GENERATED_DIR  "${CMAKE_BINARY_DIR}/generated/proto")

file(MAKE_DIRECTORY "${GENERATED_DIR}")

# ── Select the canonical gRPC schema ───────────────────────────────────────
# gateway.proto is intentionally self-contained.  The per-domain proto files
# currently preserve the typed-envelope contract and reuse the same package
# message names, so compiling them together would produce duplicate C++ types.
set(V3_PROTO_FILES "${PROTO_V3_DIR}/gateway.proto")

message(STATUS "Proto: generating canonical schema ${V3_PROTO_FILES}")

# ── Protobuf C++ generation (all v3 protos) ───────────────────────────────
# protobuf_generate is provided by find_package(Protobuf CONFIG) in CMake >= 3.14.
# protobuf_generate_grpc is available in CMake >= 3.19 with gRPC config.

set(PROTOBUF_GENERATED_SOURCES "")
set(GRPC_GENERATED_SOURCES "")

# We invoke protoc directly via add_custom_command for precise control over
# include paths and plugin location.

# ── 1. Protobuf C++ stubs ──────────────────────────────────────────────
foreach(_proto ${V3_PROTO_FILES})
  get_filename_component(_proto_name ${_proto} NAME_WE)
  set(_pb_cc "${GENERATED_DIR}/${_proto_name}.pb.cc")
  set(_pb_h  "${GENERATED_DIR}/${_proto_name}.pb.h")

  list(APPEND PROTOBUF_GENERATED_SOURCES "${_pb_cc}")

  add_custom_command(
    OUTPUT  "${_pb_cc}" "${_pb_h}"
    COMMAND ${PROTOC}
      --proto_path="${PROTO_V3_DIR}"
      --cpp_out="${GENERATED_DIR}"
      "${_proto}"
    DEPENDS "${_proto}"
    COMMENT "protoc: ${_proto_name}.pb.cc"
  )
endforeach()

# ── 2. gRPC service stubs ─────────────────────────────────────────────────
foreach(_proto ${V3_PROTO_FILES})
  get_filename_component(_proto_name ${_proto} NAME_WE)
  set(_grpc_cc "${GENERATED_DIR}/${_proto_name}.grpc.pb.cc")
  set(_grpc_h  "${GENERATED_DIR}/${_proto_name}.grpc.pb.h")

  list(APPEND GRPC_GENERATED_SOURCES "${_grpc_cc}")

  add_custom_command(
    OUTPUT  "${_grpc_cc}" "${_grpc_h}"
    COMMAND ${PROTOC}
      --proto_path="${PROTO_V3_DIR}"
      --grpc_out="${GENERATED_DIR}"
      --plugin=protoc-gen-grpc="${GRPC_CPP_PLUGIN}"
      "${_proto}"
    DEPENDS "${_proto}"
    COMMENT "protoc: ${_proto_name}.grpc.pb.cc"
  )
endforeach()

# ── 3. Interface library exposing generated headers ────────────────────────
# Create custom targets for generated code so downstream targets can depend on them.
add_custom_target(generate_v3_proto
  DEPENDS ${PROTOBUF_GENERATED_SOURCES}
  COMMENT "Generate v3 protobuf C++ stubs"
)

add_custom_target(generate_v3_grpc
  DEPENDS ${GRPC_GENERATED_SOURCES}
  COMMENT "Generate v3 gRPC C++ stubs"
)

add_library(project_proto STATIC
  ${PROTOBUF_GENERATED_SOURCES}
  ${GRPC_GENERATED_SOURCES}
)
add_dependencies(project_proto generate_v3_grpc generate_v3_proto)

target_include_directories(project_proto
  PUBLIC
    $<BUILD_INTERFACE:${GENERATED_DIR}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
)

target_link_libraries(project_proto
  PUBLIC
    protobuf::libprotobuf
    gRPC::grpc++
    gRPC::grpc
)

if(TARGET gRPC::grpc++_reflection)
  target_link_libraries(project_proto PUBLIC gRPC::grpc++_reflection)
endif()

message(STATUS "Proto: project_proto interface target ready (${GENERATED_DIR})")
