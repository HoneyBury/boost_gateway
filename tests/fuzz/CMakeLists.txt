# Boost Gateway — Fuzz Testing Targets
#
# 构建方式：
#   cmake -B build_fuzz -DBOOST_BUILD_FUZZ=ON
#   cmake --build build_fuzz
#
# 每个 fuzz target 都可以独立运行：
#   build_fuzz/tests/fuzz/fuzz_protocol_codec -max_total_time=60
#   build_fuzz/tests/fuzz/fuzz_fragment_assembler -max_total_time=60
#
# 崩溃文件自动保存在运行目录的 crashes/ 子目录中。

option(BOOST_BUILD_FUZZ "Build fuzz targets" OFF)

if(BOOST_BUILD_FUZZ)
    # ── 编译器 / 链接器 sanitizer 标志 ──────────────────────────────
    set(FUZZ_COMPILE_OPTIONS
        -fsanitize=fuzzer,address,undefined
        -g
        -O1
        -fno-omit-frame-pointer
    )
    set(FUZZ_LINK_OPTIONS
        -fsanitize=fuzzer,address,undefined
    )

    # ── Helper: add_fuzz_target ─────────────────────────────────────
    function(add_fuzz_target target_name source_file)
        add_executable(${target_name} ${source_file})
        target_include_directories(${target_name}
            PRIVATE
                "${CMAKE_SOURCE_DIR}/include"
        )
        target_link_libraries(${target_name}
            PRIVATE
                project_net
                project_app
                project_boost_asio
        )
        target_compile_options(${target_name}
            PRIVATE
                ${FUZZ_COMPILE_OPTIONS}
        )
        target_link_options(${target_name}
            PRIVATE
                ${FUZZ_LINK_OPTIONS}
        )
        apply_project_warnings(${target_name})
    endfunction()

    # ── Fuzz targets ────────────────────────────────────────────────

    add_fuzz_target(fuzz_protocol_codec
        fuzz_protocol_codec.cpp
    )

    add_fuzz_target(fuzz_fragment_assembler
        fuzz_fragment_assembler.cpp
    )

    message(STATUS "Fuzz targets are enabled (BOOST_BUILD_FUZZ=ON)")
    message(STATUS "  fuzz_protocol_codec     — protocol codec decode/encode round-trip")
    message(STATUS "  fuzz_fragment_assembler — FragmentAssembler feed() with arbitrary sequences")

else()
    # 如果 fuzz 未启用，创建一个空目标避免 "missing source" 警告
    add_executable(fuzz_stub EXCLUDE_FROM_ALL
        "${CMAKE_CURRENT_SOURCE_DIR}/../unit/hello_test.cpp"
    )
    target_link_libraries(fuzz_stub
        PRIVATE
            GTest::gtest_main
    )
endif()
