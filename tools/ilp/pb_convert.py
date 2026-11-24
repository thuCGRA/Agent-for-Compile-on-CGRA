#!/usr/bin/env python3
"""Make the auto-generated polybench stubs acceptable to the morpher DFG pass.

The pass demands that every load/store address be a *single* GEP rooted at a
named global array.  Two shapes break it:

  int **A;  A[i][k]      -> pointer-to-pointer, no named array   (rejected)
  int A[512]; A[i*n+k]   -> the index is hoisted, giving a two-level GEP (rejected)

Both are fixed by:
  int **A;      ->  int A[512];
  A[i][k]       ->  A[(i)+(k)]        # one affine GEP, rooted at @A

`n` is also pinned to a literal so the stub compiles without a `*N` dereference.
These kernels are only used to *shape* a DFG (node/edge counts); they are not
used for simulation, so collapsing the row stride is acceptable here.
"""
import re, sys, pathlib

ARR = 512

def convert(src: str) -> str:
    out = []
    for line in src.splitlines():
        m = re.match(r'^\s*(?:volatile\s+|const\s+)*int\s*\*{1,2}\s*([A-Za-z_]\w*)\s*;\s*$', line)
        if m:
            name = m.group(1)
            out.append("volatile int * N;" if name == "N" else "int %s[%d];" % (name, ARR))
            continue
        line = re.sub(r'int\s+n\s*=\s*\*N\s*;', 'int n = 20;', line)
        mm = re.match(r'^(\s*)int\s+([ijk])\s*;\s*$', line)
        if mm:
            out.append("%sint %s = 0;" % (mm.group(1), mm.group(2)))
            continue
        out.append(line)
    src = "\n".join(out) + "\n"
    src = re.sub(r'\b([A-Za-z_]\w*)\s*\[\s*([A-Za-z_]\w*)\s*\]\s*\[\s*([A-Za-z_]\w*)\s*\]',
                 lambda m: "%s[(%s)+(%s)]" % (m.group(1), m.group(2), m.group(3)), src)
    return src

if __name__ == "__main__":
    for p in sys.argv[1:]:
        path = pathlib.Path(p)
        path.write_text(convert(path.read_text()))
        print("converted", path)
