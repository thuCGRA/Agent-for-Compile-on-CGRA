#!/usr/bin/env bash
# Rebuild the patched mapper (--dump-placement) from toolchain/vendor/mapper.
# The vendor tree is git-ignored; scripts/patch_mapper_dump.py re-applies the
# source patch, this script only compiles it.  GCC 7.5 (cgra-tc) is required.
set -euo pipefail
cd "$(dirname "$0")/.."
source /home/steve/miniconda3/etc/profile.d/conda.sh
conda activate cgra-tc

python3 scripts/patch_mapper_dump.py

cmake -S toolchain/vendor/mapper -B build/mapper \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER="$CONDA_PREFIX/bin/x86_64-conda-linux-gnu-gcc" \
  -DCMAKE_C_FLAGS="-B/usr/bin -O2"
cmake --build build/mapper -j"$(nproc)"
cp build/mapper/src/cgra_xml_mapper toolchain/out/cgra_xml_mapper
echo "rebuilt toolchain/out/cgra_xml_mapper"
