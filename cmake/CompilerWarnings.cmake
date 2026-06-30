function(apply_project_warnings target_name)
    if(NOT ENABLE_WARNINGS)
        return()
    endif()

    target_compile_options(${target_name} PRIVATE -Wall -Wextra -Wpedantic)
endfunction()
