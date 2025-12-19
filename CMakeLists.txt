cmake_minimum_required(VERSION 3.16.2)
project(LID)
set(CMAKE_CXX_STANDARD 17)

option(USE_MULTI_THREAD "Use multi-thread" ON)

if(USE_MULTI_THREAD)
    add_compile_definitions(MULTI_THREAD)
endif()

if(MSVC)
    set(CMAKE_CXX_FLAGS "/arch:AVX2 /W1 /EHsc")
elseif (CMAKE_CXX_COMPILER_ID STREQUAL "Intel")
    set(CMAKE_CXX_FLAGS "-xHost")
else()
    # clang and gcc
    include(CheckCXXCompilerFlag)
    check_cxx_compiler_flag("-march=native" HAS_MARCH_NATIVE)
    if (HAS_MARCH_NATIVE)
        add_compile_options(-march=native)
    endif ()
    # add_compile_options(-Wall -Wextra)
    add_compile_options(-O3 -Wall -Wextra)
endif()

add_library(pgm_index INTERFACE)
target_include_directories(pgm_index INTERFACE indexes/PGM-index/include)

# Dependencies
find_package(Eigen3 REQUIRED NO_MODULE)
find_package(Snappy REQUIRED)
if(POLICY CMP0167)
    cmake_policy(SET CMP0167 NEW)
endif()
find_package(Boost REQUIRED COMPONENTS serialization iostreams)

set(_gmp_prefixes
    $ENV{CONDA_PREFIX}
    /usr/local
    /usr
)
list(REMOVE_DUPLICATES _gmp_prefixes)

find_path(GMP_INCLUDE_DIR
    NAMES gmp.h
    HINTS ${_gmp_prefixes}
    PATH_SUFFIXES include
)
find_path(GMPXX_INCLUDE_DIR
    NAMES gmpxx.h
    HINTS ${_gmp_prefixes}
    PATH_SUFFIXES include
)
find_library(GMP_LIBRARY
    NAMES gmp
    HINTS ${_gmp_prefixes}
    PATH_SUFFIXES lib lib64
)
find_library(GMPXX_LIBRARY
    NAMES gmpxx
    HINTS ${_gmp_prefixes}
    PATH_SUFFIXES lib lib64
)

if(NOT GMP_INCLUDE_DIR OR NOT GMP_LIBRARY)
    message(FATAL_ERROR "GMP headers or library not found. Set CONDA_PREFIX or install GMP.")
endif()

set(GMP_INCLUDE_DIRS ${GMP_INCLUDE_DIR})
set(GMP_LIBRARIES ${GMP_LIBRARY})

if(GMPXX_INCLUDE_DIR)
    list(APPEND GMP_INCLUDE_DIRS ${GMPXX_INCLUDE_DIR})
endif()
if(GMPXX_LIBRARY)
    set(GMPXX_LIBRARIES ${GMPXX_LIBRARY})
else()
    set(GMPXX_LIBRARIES "")
endif()

# Header include directories
include_directories(libraries/LeCo/headers)

# Subdirectories
add_subdirectory(libraries/LeCo/src)
add_subdirectory(libraries/LeCo/thirdparty/fsst)

# .c/cpp document
aux_source_directory(libraries/LeCo/experiments SRC_LIST)
add_library(leco ${SRC_LIST})
target_include_directories(leco
    PUBLIC ${Boost_INCLUDE_DIRS} ${GMP_INCLUDE_DIRS}
)
target_link_libraries(leco
    docs
    Snappy::snappy
    fsst
    ${Boost_LIBRARIES}
    Eigen3::Eigen
    ${GMP_LIBRARIES}
    ${GMPXX_LIBRARIES}
)

add_executable(prepare_ycsb gen_ycsb_workloads.cpp)
add_executable(HYBRID-LID run_ycsb_experiments.cpp)
add_executable(MULTI-HYBRID-LID run_multi_threaded_ycsb.cpp)
add_executable(LID run_microbenchmark.cpp)

target_link_libraries(LID
    PRIVATE pgm_index
    PRIVATE leco
)
if (APPLE)
    # OpenMP disabled on Mac since m1 use osx-arm64 library
    # PGM-index requires x86
    set_target_properties(LID PROPERTIES OSX_ARCHITECTURES x86_64)
    set_target_properties(HYBRID-LID PROPERTIES OSX_ARCHITECTURES x86_64)
    set_target_properties(MULTI-HYBRID-LID PROPERTIES OSX_ARCHITECTURES x86_64)
    # POPCNT is required by ALEX
    target_compile_options(LID PRIVATE -march=x86-64-v2)
    target_compile_options(HYBRID-LID PRIVATE -march=x86-64-v2)
    target_compile_options(MULTI-HYBRID-LID PRIVATE -march=x86-64-v2)
else()
    find_package(OpenMP)
    if (OpenMP_CXX_FOUND)
        message(STATUS "OpenMP found")
        target_link_libraries(pgm_index INTERFACE OpenMP::OpenMP_CXX)
        target_link_libraries(HYBRID-LID OpenMP::OpenMP_CXX leco)
        target_link_libraries(MULTI-HYBRID-LID OpenMP::OpenMP_CXX leco)
    else()
        message(FATAL_ERROR "Openmp not found!")
        target_link_libraries(HYBRID-LID
            PRIVATE leco
        )
        target_link_libraries(MULTI-HYBRID-LID
            PRIVATE leco
        )
    endif ()
endif()
