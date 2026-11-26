#!/usr/bin/env python3
"""Allow single-step routes: a 0-hop, 0-register edge stays inside one PE."""
import os
import sys

F = os.path.expanduser("~/acc-thu-ilp/tools/ilp/routes_to_path.py")
s = open(F, encoding="utf-8").read()
old = '''        if len(steps) < 2:
            raise ValueError("edge %s->%s has a single step" % (r["src"], r["dst"]))'''
new = '''        if not steps:
            raise ValueError("edge %s->%s has no steps" % (r["src"], r["dst"]))'''
if old not in s:
    sys.exit("anchor not found")
s = s.replace(old, new)
open(F, "w", encoding="utf-8").write(s)
print("routes_to_path: single-step allowed")