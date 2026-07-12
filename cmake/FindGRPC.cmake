# FindGRPC.cmake — Find gRPC and protoc for gRPC C++ code generation.
#
# This module provides:
#   find_grpc_cpp_plugin(<var>)   — Locates grpc_cpp_plugin executable
#   find_protoc(<var>)             — Locates protoc executable
#
# It delegates to the official FindProtobuf and gRPC CONFIG targets
# when available, and provides fallback PATH searches for environments
# where gRPC was installed via a package manager.
#
# Provided targets (when gRPC CMake config is found):
#   gRPC::grpc++
#   gRPC::grpc
#   protobuf::protoc
#
# Usage from CMakeLists.txt:
#   find_package(gRPC CONFIG QUIET)
#   if(gRPC_FOUND)
#     include(cmake/FindGRPC.cmake)
#     find_grpc_cpp_plugin(GRPC_CPP_PLUGIN)
#     find_protoc(PROTOC)
#   endif()
#
# On success, sets:
#   GRPC_FOUND          — TRUE if gRPC and protoc are available
#   GRPC_CPP_PLUGIN     — Path to grpc_cpp_plugin
#   PROTOC              — Path to protoc
#   PROTOBUF_INCLUDE_DIR — Path to the protobuf include directory for --proto_path

# Guard against multiple includes
if(DEFINED GRPC_FOUND_AND_INCLUDED)
  return()
endif()
set(GRPC_FOUND_AND_INCLUDED TRUE)

# -------------------------------------------------------------------
# find_grpc_cpp_plugin
# -------------------------------------------------------------------
function(find_grpc_cpp_plugin _out_var)
  if(TARGET gRPC::grpc_cpp_plugin)
    set(${_out_var} "$<TARGET_FILE:gRPC::grpc_cpp_plugin>" PARENT_SCOPE)
    return()
  endif()

  if(GRPC_CPP_PLUGIN)
    set(${_out_var} "${GRPC_CPP_PLUGIN}" PARENT_SCOPE)
    return()
  endif()

  # Fallback: search PATH manually
  find_program(GRPC_CPP_PLUGIN
    NAMES grpc_cpp_plugin
    DOC "Path to the gRPC C++ plugin for protoc"
  )

  if(GRPC_CPP_PLUGIN)
    set(${_out_var} "${GRPC_CPP_PLUGIN}" PARENT_SCOPE)
    message(STATUS "FindGRPC: grpc_cpp_plugin found at ${GRPC_CPP_PLUGIN}")
  else()
    message(WARNING "FindGRPC: grpc_cpp_plugin not found. "
                    "Set GRPC_CPP_PLUGIN or install gRPC via vcpkg/conan.")
    set(${_out_var} "" PARENT_SCOPE)
  endif()
endfunction()

# -------------------------------------------------------------------
# find_protoc
# -------------------------------------------------------------------
function(find_protoc _out_var)
  if(TARGET protobuf::protoc)
    # Some Conan protobuf build modules search PATH before their package
    # directory and can therefore bind this target to a system protoc.  Keep
    # generated code ABI-compatible with the Conan protobuf runtime.
    if(DEFINED protobuf_PACKAGE_FOLDER_RELEASE AND
       EXISTS "${protobuf_PACKAGE_FOLDER_RELEASE}/bin/protoc")
      set_property(TARGET protobuf::protoc PROPERTY IMPORTED_LOCATION
        "${protobuf_PACKAGE_FOLDER_RELEASE}/bin/protoc")
    endif()
    set(${_out_var} "$<TARGET_FILE:protobuf::protoc>" PARENT_SCOPE)
    return()
  endif()

  if(PROTOC)
    set(${_out_var} "${PROTOC}" PARENT_SCOPE)
    return()
  endif()

  find_program(PROTOC
    NAMES protoc
    DOC "Path to the protobuf compiler (protoc)"
  )

  if(PROTOC)
    set(${_out_var} "${PROTOC}" PARENT_SCOPE)
    message(STATUS "FindGRPC: protoc found at ${PROTOC}")
  else()
    message(WARNING "FindGRPC: protoc not found. "
                    "Set PROTOC or install protobuf via vcpkg/conan.")
    set(${_out_var} "" PARENT_SCOPE)
  endif()
endfunction()

# -------------------------------------------------------------------
# Ensure PROTOBUF_INCLUDE_DIR is set
# -------------------------------------------------------------------
if(NOT PROTOBUF_INCLUDE_DIR)
  if(TARGET protobuf::libprotobuf)
    get_target_property(_inc protobuf::libprotobuf INTERFACE_INCLUDE_DIRECTORIES)
    if(_inc)
      set(PROTOBUF_INCLUDE_DIR "${_inc}" CACHE PATH "Protobuf include directory")
    endif()
  endif()
endif()

if(NOT PROTOBUF_INCLUDE_DIR)
  # Common install location
  foreach(_dir
      "${CMAKE_SOURCE_DIR}/third_party/protobuf/src"
      "/usr/local/include"
      "/usr/include"
      "$ENV{VCPKG_ROOT}/installed/$ENV{VCPKG_TARGET_TRIPLET}/include"
    )
    if(EXISTS "${_dir}/google/protobuf/descriptor.proto")
      set(PROTOBUF_INCLUDE_DIR "${_dir}" CACHE PATH "Protobuf include directory")
      break()
    endif()
  endforeach()
endif()

if(PROTOBUF_INCLUDE_DIR)
  message(STATUS "FindGRPC: PROTOBUF_INCLUDE_DIR = ${PROTOBUF_INCLUDE_DIR}")
endif()

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
if(TARGET gRPC::grpc++ AND TARGET protobuf::libprotobuf AND PROTOC AND GRPC_CPP_PLUGIN)
  set(GRPC_FOUND TRUE PARENT_SCOPE)
  message(STATUS "FindGRPC: gRPC C++ stack fully resolved")
else()
  message(STATUS "FindGRPC: gRPC C++ stack partially resolved "
                 "(set BOOST_BUILD_GRPC=OFF or install missing components)")
endif()
