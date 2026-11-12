# Neverdeth (NeverDead PC Recompilation)
# SDK boilerplate lives in generated/rexglue.cmake.

cmake_minimum_required(VERSION 3.25)
project(neverdead LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    file(REAL_PATH "${CMAKE_CURRENT_SOURCE_DIR}" NEVERDEAD_REAL_SOURCE_DIR)
    add_compile_options(
        -Wno-argument-outside-range
        "-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=."
        "-ffile-prefix-map=${NEVERDEAD_REAL_SOURCE_DIR}=."
    )
endif()

include(generated/rexglue.cmake)

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang" AND DEFINED rexglue_DIR)
    get_filename_component(_REXGLUE_PREFIX "${rexglue_DIR}/../../.." ABSOLUTE)
    file(TO_NATIVE_PATH "${_REXGLUE_PREFIX}" _REXGLUE_PREFIX_NATIVE)
    add_compile_options(
        "-ffile-prefix-map=${_REXGLUE_PREFIX}=rexglue-sdk"
        "-ffile-prefix-map=${_REXGLUE_PREFIX_NATIVE}=rexglue-sdk"
    )
endif()

# Host and instrumentation sources
set(NEVERDEAD_SOURCES
    src/main.cpp
    # Vectored exception handler logger (writes to crash_diag.log without altering SDK flow)
    src/crash_diag.cpp
    # Mid-ASM and runtime hook modules
    src/nd_runtime_patches.cpp
    src/nd_dxgi_present.cpp
    src/nd_d3d12_pso.cpp
    src/nd_shader_osd.cpp
    src/nd_frame_pacer.cpp
    src/nd_config_ui.cpp
    src/nd_setup_wizard.cpp
    src/nd_diag_pm4.cpp
    # Guest-to-host virtual address translator used by hooks
    src/nd_guest_mem.cpp
    # Win32 application icon resource
    src/neverdeth.rc
)

if(WIN32)
    add_executable(neverdead WIN32 ${NEVERDEAD_SOURCES})
else()
    add_executable(neverdead ${NEVERDEAD_SOURCES})
endif()

set_target_properties(neverdead PROPERTIES OUTPUT_NAME "Neverdeth")
target_compile_definitions(neverdead PRIVATE NEVERDETH_VERSION_STRING="v0.1.1-alpha")

if(MSVC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    # Emit a fixed-base .map file so host PCs in crash_diag.log resolve directly to symbols.
    target_link_options(neverdead PRIVATE "-Wl,/MAP" "-Wl,/DYNAMICBASE:NO")
endif()
if(WIN32)
    target_link_libraries(neverdead PRIVATE dbghelp)
endif()

rexglue_setup_target(neverdead GPU_PLUGINS xenos)

# Patch generated translation units after codegen and before compiling neverdead_recomp
# so Ninja does not run file patching concurrently with compilation.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_custom_target(neverdead_generated_hooks
    COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_SOURCE_DIR}/tools/apply_generated_hooks.py"
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
    COMMENT "Applying generated code hooks to recompiled translation units"
    VERBATIM
)
add_dependencies(neverdead_recomp neverdead_generated_hooks)
add_dependencies(neverdead_generated_hooks neverdead_codegen)
add_dependencies(neverdead neverdead_generated_hooks)

# Force-include nd_hooks.h on Clang as a fallback for TUs without forward declarations;
# compatible with the precompiled header.
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(neverdead PRIVATE
        "-include" "${CMAKE_CURRENT_SOURCE_DIR}/src/nd_hooks.h")
endif()
