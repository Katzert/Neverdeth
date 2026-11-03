# neverdead - ReXGlue Recompiled Project
#
# SDK boilerplate lives in generated/rexglue.cmake.

cmake_minimum_required(VERSION 3.25)
project(neverdead LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    add_compile_options(-Wno-argument-outside-range)
endif()

# M3.1/M3.3 (swe_native_3d_v2): permite compilar un arbol recompilado distinto
# del que dejo el ultimo codegen (p.ej. generated/neverdead_default_v2) sin
# tocar la cadena v1.  Default = comportamiento actual (v1 intacto).
#   NEVERDEAD_REXGLUE_CMAKE : include de ReXGlue (v1: generated/rexglue.cmake,
#                             v2: tools/rexglue_v2.cmake)
#   NEVERDEAD_GENERATED_DIR : arbol recompilado que parchea la cadena de
#                             apply_*.py (vacio = el del rexglue.cmake incluido)
set(NEVERDEAD_REXGLUE_CMAKE "generated/rexglue.cmake" CACHE STRING
    "Include de ReXGlue (v1: generated/rexglue.cmake | v2: tools/rexglue_v2.cmake)")
set(NEVERDEAD_GENERATED_DIR "" CACHE PATH
    "Arbol recompilado a compilar/parchear (vacio = el que fija el rexglue.cmake)")
if(NEVERDEAD_GENERATED_DIR)
    set(ENV{NEVERDEAD_GENERATED_DIR} "${NEVERDEAD_GENERATED_DIR}")
endif()
include(${NEVERDEAD_REXGLUE_CMAKE})

# Sources
set(NEVERDEAD_SOURCES
    src/main.cpp
    # DIAG(neverdead-crash-diag): VEH observador de AV (adoptado de 360tools).
    # Solo loguea a crash_diag.log; no altera el manejo del SDK.
    src/crash_diag.cpp
    # Hooks mid-ASM: instrumentacion de VdInitializeEngines / VdSwap.
    # Las llamadas las inyecta el codegen (config/mid_asm_hooks.toml).
    src/nd_hooks.cpp
    # Traductor guest VA -> host para esos hooks.
    src/nd_guest_mem.cpp
)

if(WIN32)
    add_executable(neverdead WIN32 ${NEVERDEAD_SOURCES})
else()
    add_executable(neverdead ${NEVERDEAD_SOURCES})
endif()

if(MSVC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    # DIAG: map file para resolver frames host (sin PDB) a sub_XXXXXXXX.
    # Con el driver clang++ (GNU-style) los flags del linker van via -Wl,.
    # DYNAMICBASE:NO => base fija = preferred del .map, asi los PCs de
    # crash_diag.log se resuelven directamente contra neverdead.map.
    target_link_options(neverdead PRIVATE "-Wl,/MAP" "-Wl,/DYNAMICBASE:NO")
endif()
if(WIN32)
    target_link_libraries(neverdead PRIVATE dbghelp)
endif()

rexglue_setup_target(neverdead GPU_PLUGINS xenos)

# El codegen emite `extern void NdVdInitHook();` en la TU que contiene el hook
# (verificado en neverdead_recomp.7.cpp:22681). Se mantiene -include como red de
# seguridad para TUs/versiones que no lo emitan; convive con la PCH (probado).
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(neverdead PRIVATE
        "-include" "${CMAKE_CURRENT_SOURCE_DIR}/src/nd_hooks.h")
endif()
