# No REQUIRES: the main component then depends on every component in the build, the library
# included, whatever its directory is called.
idf_component_register(SRCS "main.cpp" INCLUDE_DIRS "." "pieces")
# Same rule as the library: no fused multiply-add, no fast-math (bit-exact drawing).
target_compile_options(${COMPONENT_LIB} PRIVATE -ffp-contract=off -fno-fast-math)
