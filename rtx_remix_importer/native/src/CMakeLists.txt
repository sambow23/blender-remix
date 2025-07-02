cmake_minimum_required(VERSION 3.16)
project(remix_native_core CXX)

# --- Find Blender's Python Library ---
set(BLENDER_PYTHON_DIR "" CACHE PATH "Path to Blender's Python directory")
if(NOT BLENDER_PYTHON_DIR)
    message(FATAL_ERROR "BLENDER_PYTHON_DIR is not set. Please specify: -DBLENDER_PYTHON_DIR=/path/to/blender/python")
endif()
set(PYTHON_LIBRARY "${BLENDER_PYTHON_DIR}/lib/libpython3.11.a")
if(NOT EXISTS ${PYTHON_LIBRARY})
    message(FATAL_ERROR "Python library not found at ${PYTHON_LIBRARY}")
endif()

# --- Find System Python Headers ---
find_package(Python3 3.11 REQUIRED COMPONENTS Development)

# --- Manually Specify OpenUSD Paths ---
set(USD_INCLUDE_DIR "/usr/include/pxr")
set(USD_LIBRARY "/usr/lib/libusd_ms.so")
if(NOT EXISTS ${USD_INCLUDE_DIR})
    message(FATAL_ERROR "OpenUSD include directory not found at ${USD_INCLUDE_DIR}")
endif()
if(NOT EXISTS ${USD_LIBRARY})
    message(FATAL_ERROR "OpenUSD library not found at ${USD_LIBRARY}")
endif()

# --- Create the Native Extension ---
add_library(remix_native MODULE main.cpp)

# Link against Python and USD libraries
target_include_directories(remix_native PRIVATE
    ${Python3_INCLUDE_DIRS}
    ${USD_INCLUDE_DIR}
)
target_link_libraries(remix_native PRIVATE
    ${PYTHON_LIBRARY}
    ${USD_LIBRARY}
)

# Output the module to the addon root directory (two levels up from src)
set_target_properties(remix_native PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/../.."
    PREFIX ""
)

# Print build information
message(STATUS "Building RTX Remix Native Module")
message(STATUS "Blender Python Directory: ${BLENDER_PYTHON_DIR}")
message(STATUS "Output Directory: ${CMAKE_SOURCE_DIR}/../..")
message(STATUS "USD Include Directory: ${USD_INCLUDE_DIR}")
message(STATUS "USD Library: ${USD_LIBRARY}")

# Installation (optional)
install(TARGETS remix_native
    LIBRARY DESTINATION "${CMAKE_SOURCE_DIR}/../.."
) 