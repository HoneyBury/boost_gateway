# Internal protobuf and experimental gRPC code generation.

if(NOT BOOST_BUILD_RAFT_PROTOBUF AND NOT BOOST_BUILD_GRPC)
  message(STATUS "Proto: all generated protocol targets are disabled")
  return()
endif()

find_package(Protobuf CONFIG REQUIRED)

include(${CMAKE_SOURCE_DIR}/cmake/FindGRPC.cmake)
find_protoc(PROTOC)
if(NOT PROTOC)
  message(FATAL_ERROR "Proto: protoc is required for generated protocol targets")
endif()

set(PROTO_ROOT_DIR "${CMAKE_SOURCE_DIR}/proto")
set(PROTO_V3_DIR "${PROTO_ROOT_DIR}/v3")
set(GENERATED_DIR "${CMAKE_BINARY_DIR}/generated/proto")
file(MAKE_DIRECTORY "${GENERATED_DIR}")

if(BOOST_BUILD_RAFT_PROTOBUF)
  set(RAFT_PROTO "${PROTO_V3_DIR}/raft.proto")
  set(RAFT_PB_CC "${GENERATED_DIR}/raft.pb.cc")
  set(RAFT_PB_H "${GENERATED_DIR}/raft.pb.h")

  add_custom_command(
    OUTPUT "${RAFT_PB_CC}" "${RAFT_PB_H}"
    COMMAND ${PROTOC}
      --proto_path="${PROTO_V3_DIR}"
      --cpp_out="${GENERATED_DIR}"
      "${RAFT_PROTO}"
    DEPENDS "${RAFT_PROTO}"
    COMMENT "protoc: raft.pb.cc"
  )
  add_custom_target(generate_v3_raft_proto DEPENDS "${RAFT_PB_CC}" "${RAFT_PB_H}")

  add_library(project_raft_proto STATIC "${RAFT_PB_CC}")
  add_dependencies(project_raft_proto generate_v3_raft_proto)
  target_include_directories(project_raft_proto
    PUBLIC
      $<BUILD_INTERFACE:${GENERATED_DIR}>
      $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
  )
  if(TARGET protobuf::libprotobuf-lite)
    target_link_libraries(project_raft_proto PUBLIC protobuf::libprotobuf-lite)
  else()
    target_link_libraries(project_raft_proto PUBLIC protobuf::libprotobuf)
  endif()
  message(STATUS "Proto: internal Raft protobuf target ready (${GENERATED_DIR})")
endif()

if(NOT BOOST_BUILD_GRPC)
  message(STATUS "Proto: BOOST_BUILD_GRPC=OFF - skipping experimental gRPC generation")
  return()
endif()

find_package(gRPC CONFIG REQUIRED)
find_grpc_cpp_plugin(GRPC_CPP_PLUGIN)
if(NOT GRPC_CPP_PLUGIN)
  message(FATAL_ERROR "Proto: grpc_cpp_plugin is required when BOOST_BUILD_GRPC=ON")
endif()

# gateway.proto is intentionally self-contained. The per-domain schemas preserve
# duplicate package message names and are validated separately.
set(V3_PROTO_FILES "${PROTO_V3_DIR}/gateway.proto")
set(PROTOBUF_GENERATED_SOURCES "")
set(GRPC_GENERATED_SOURCES "")

foreach(_proto ${V3_PROTO_FILES})
  get_filename_component(_proto_name ${_proto} NAME_WE)
  set(_pb_cc "${GENERATED_DIR}/${_proto_name}.pb.cc")
  set(_pb_h "${GENERATED_DIR}/${_proto_name}.pb.h")
  list(APPEND PROTOBUF_GENERATED_SOURCES "${_pb_cc}")
  add_custom_command(
    OUTPUT "${_pb_cc}" "${_pb_h}"
    COMMAND ${PROTOC}
      --proto_path="${PROTO_V3_DIR}"
      --cpp_out="${GENERATED_DIR}"
      "${_proto}"
    DEPENDS "${_proto}"
    COMMENT "protoc: ${_proto_name}.pb.cc"
  )

  set(_grpc_cc "${GENERATED_DIR}/${_proto_name}.grpc.pb.cc")
  set(_grpc_h "${GENERATED_DIR}/${_proto_name}.grpc.pb.h")
  list(APPEND GRPC_GENERATED_SOURCES "${_grpc_cc}")
  add_custom_command(
    OUTPUT "${_grpc_cc}" "${_grpc_h}"
    COMMAND ${PROTOC}
      --proto_path="${PROTO_V3_DIR}"
      --grpc_out="${GENERATED_DIR}"
      --plugin=protoc-gen-grpc="${GRPC_CPP_PLUGIN}"
      "${_proto}"
    DEPENDS "${_proto}"
    COMMENT "protoc: ${_proto_name}.grpc.pb.cc"
  )
endforeach()

add_custom_target(generate_v3_proto DEPENDS ${PROTOBUF_GENERATED_SOURCES})
add_custom_target(generate_v3_grpc DEPENDS ${GRPC_GENERATED_SOURCES})

add_library(project_proto STATIC ${PROTOBUF_GENERATED_SOURCES} ${GRPC_GENERATED_SOURCES})
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

message(STATUS "Proto: experimental project_proto target ready (${GENERATED_DIR})")
