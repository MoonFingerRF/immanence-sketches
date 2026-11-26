# immanence-sketches: run embedded pieces for their golden frames and check each frame against the
# reference. The library is this repository's root (an ESP-IDF component: CMakeLists.txt there).
cmake_minimum_required(VERSION 3.16)
# In your own project point this at your checkout, or use idf_component.yml (see README.md).
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../..")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(immanence_serial)
