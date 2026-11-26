# ESP-IDF component (idf.py): add this repository under components/, or depend on it from
# idf_component.yml (see examples/esp-idf-serial). The VM is plain C++17; -ffp-contract=off and no
# -ffast-math keep it bit-exact (src/pde_fp.h enforces the first from the source as well).
file(GLOB IMMANENCE_SRCS "${CMAKE_CURRENT_LIST_DIR}/src/*.cpp")
idf_component_register(SRCS ${IMMANENCE_SRCS} INCLUDE_DIRS "src")
target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++17 -ffp-contract=off -fno-fast-math)
