# Native host runner: the same C++ VM the ESP32 runs, built for this computer.
#   make -C host            -> host/build/immanence-vm
#   immanence-vm program.pdeb FRAMES [--clock EPOCH TZ] [--dump frames.grey] [--arena BYTES]
# -ffp-contract=off and no -ffast-math are required: they keep the VM bit-exact with the reference.
CXX ?= c++
CXXFLAGS ?= -O2
FLAGS = -std=c++17 -Wall -Wextra -ffp-contract=off -fno-fast-math
SRC = vm_run.cpp $(wildcard ../src/*.cpp)

build/immanence-vm: $(SRC) $(wildcard ../src/*.h)
	@mkdir -p build
	$(CXX) $(FLAGS) $(CXXFLAGS) -o $@ $(SRC)

clean:
	rm -rf build
.PHONY: clean
