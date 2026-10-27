#!/usr/bin/env python3
"""Apply (idempotently) the mapper --dump-placement patch.

The mapper lives in toolchain/vendor/, which is git-ignored, so the change is
kept here as a script instead of a commit.  Run it after a fresh checkout or
after re-syncing the vendor tree, then rebuild with scripts/rebuild_mapper.sh.
"""

import io
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAPPER = os.path.join(ROOT, "toolchain", "vendor", "mapper")
UTIL = os.path.join(MAPPER, "include", "morpher", "util", "util.h")
COMPILER = os.path.join(MAPPER, "src", "CGRA_xml_compiler.cpp")

FIELD = '    std::string dump_placement_file = ""; // optional: dump final placement (any method)\n'
FIELD_ANCHOR = '    std::string report_file = ""; // machine readable result\n'
OPTION = '        .add_option<std::string>("", "--dump-placement", "dump final placement json", "")\n'
OPTION_ANCHOR = '        .add_option<std::string>("", "--report", "machine readable report json", "")\n'
READ = '    ret.dump_placement_file = args.get_option<std::string>("--dump-placement");\n'
READ_ANCHOR = '    ret.report_file = args.get_option<std::string>("--report");\n'

DUMP_BLOCK = (
    '\n\t\t\tif (!args.dump_placement_file.empty())\n'
    '\t\t\t{\n'
    '\t\t\t\tnlohmann::json rep;\n'
    '\t\t\t\trep["ok"] = true;\n'
    '\t\t\t\trep["ii"] = II;\n'
    '\t\t\t\trep["mapping_method"] = mapper->getMappingMethodName();\n'
    '\t\t\t\tnlohmann::json placement = nlohmann::json::array();\n'
    '\t\t\t\tfor (DFGNode &n : tempDFG.nodeList)\n'
    '\t\t\t\t{\n'
    '\t\t\t\t\tif (n.rootDP == NULL)\n'
    '\t\t\t\t\t\tcontinue;\n'
    '\t\t\t\t\tPE *pe = n.rootDP->getPE();\n'
    '\t\t\t\t\tnlohmann::json e;\n'
    '\t\t\t\t\te["node"] = n.idx;\n'
    '\t\t\t\t\te["op"] = n.op;\n'
    '\t\t\t\t\te["x"] = pe->X;\n'
    '\t\t\t\t\te["y"] = pe->Y;\n'
    '\t\t\t\t\te["t"] = pe->T;\n'
    '\t\t\t\t\te["lat"] = n.rootDP->getLat();\n'
    '\t\t\t\t\tplacement.push_back(e);\n'
    '\t\t\t\t}\n'
    '\t\t\t\trep["placement"] = placement;\n'
    '\t\t\t\tstd::ofstream dump(args.dump_placement_file.c_str());\n'
    '\t\t\t\tif (dump.is_open())\n'
    '\t\t\t\t{\n'
    '\t\t\t\t\tdump << rep.dump(2) << "\\n";\n'
    '\t\t\t\t\tdump.close();\n'
    '\t\t\t\t\tstd::cout << "[dump] placement -> " << args.dump_placement_file << "\\n";\n'
    '\t\t\t\t}\n'
    '\t\t\t\telse\n'
    '\t\t\t\t{\n'
    '\t\t\t\t\tstd::cerr << "[dump] cannot write " << args.dump_placement_file << "\\n";\n'
    '\t\t\t\t}\n'
    '\t\t\t}\n'
)
COMPILER_ANCHOR = (
    '\t\t\tif(PEType == "HyCUBE_4REG"){\n'
    '\t\t\t\tstd::cout << "\\nPrinting HyCUBE Binary...\\n";\n'
    '\t\t\t\tmapper->printHyCUBEBinary(tempCGRA);\n'
    '\t\t\t}\n'
)


def patch(path, anchors):
    text = io.open(path, encoding="utf-8", errors="replace").read()
    changed = False
    for needle, anchor, payload in anchors:
        if needle in text:
            continue
        if anchor not in text:
            raise SystemExit("anchor not found in %s: %r" % (path, anchor[:60]))
        text = text.replace(anchor, anchor + payload, 1)
        changed = True
    if changed:
        io.open(path, "w", encoding="utf-8", newline="").write(text)
    return changed


def main():
    util_changed = patch(UTIL, [
        (FIELD.strip(), FIELD_ANCHOR, FIELD.replace(FIELD_ANCHOR, "", 1)),
        ("--dump-placement", OPTION_ANCHOR, OPTION),
        ('dump_placement_file = args.get_option', READ_ANCHOR, READ),
    ])
    comp_changed = patch(COMPILER, [
        ("dump_placement_file", COMPILER_ANCHOR, DUMP_BLOCK),
    ])
    print("util.h:", "patched" if util_changed else "already patched")
    print("CGRA_xml_compiler.cpp:", "patched" if comp_changed else "already patched")


if __name__ == "__main__":
    main()
