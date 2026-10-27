"""Deterministic wrappers around the toolchain.

Every module here is a thin, testable shell around one external program
(clang/opt, the mapper, the simulator) or one file format (DFG XML, arch
JSON).  Nothing here talks to an LLM.
"""
