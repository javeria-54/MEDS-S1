#!/usr/bin/env python3
# Copyright 2026 Maktab-e-Digital Systems Lahore.
# SPDX-License-Identifier: Apache-2.0
"""Enforce the MEDS-S1 repository conventions.

This is the anti-entropy tool.  Every rule here exists because breaking it makes
the repository harder for the NEXT contributor to navigate, and no reviewer
reliably catches that by eye across thirty-one parallel projects.

Rules checked (see docs/guidelines/CODING_STANDARD.md for the reasoning):

Exit code is the number of violations, capped at 100.

Usage:
    python3 scripts/check_structure.py
    python3 scripts/check_structure.py --fix-readmes   # stub any missing README
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories that must carry a README explaining what goes in them.
README_DIRS = [
    "rtl", "rtl/core", "rtl/cache", "rtl/fabric", "rtl/peripherals",
    "rtl/socket", "rtl/common", "rtl/generated",
    "verif", "verif/unit", "verif/cosim", "verif/riscof", "verif/formal",
    "verif/conformance",
    "sw", "sw/bsp", "sw/apps", "sw/drivers",
    "gen", "configs", "boards", "extensions", "scripts",
    "docs", "docs/guidelines", "docs/modules", "docs/adr",
]

MODULE_PREFIXES = ("s1_", "meds_s1_", "meds_v_", "tb_")
MAX_LINES = 800
SIZE_WAIVER = {"scripts/gen_project_catalogue.py", "scripts/gen_diagrams.py"}

REQUIRED_CONFIG_KEYS = {"name", "isa", "privilege", "backbone", "memory_map"}
SYNTH_GUARD_IFNDEF = frozenset({"SYNTHESIS",})
SYNTH_GUARD_IFDEF = frozenset({"FORMAL", "SIMULATION", })

class Checker:
    def __init__(self) -> None:
        self.problems: list[tuple[str, str, str]] = []

    def bad(self, rule: str, where: pathlib.Path | str, msg: str) -> None:
        p = where if isinstance(where, str) else str(where.relative_to(ROOT))
        self.problems.append((rule, p, msg))

    # -------------------------------------------------------------------------------------------- S1
    def check_readmes(self, fix: bool) -> None:
        for d in README_DIRS:
            path = ROOT / d
            if not path.is_dir():
                continue
            rd = path / "README.md"
            if rd.exists():
                continue
            if fix:
                rd.write_text(
                    f"# `{d}/`\n\n"
                    "**TODO** — state what lives here, what does *not*, how to add "
                    "something, and the definition of done.\n\n"
                    "See `docs/guidelines/CODING_STANDARD.md` R-D1.\n")
                continue
            self.bad("S1", d, "directory has no README.md (R-D1)")

    # -------------------------------------------------------------------------------------------- S2
    def check_spdx(self) -> None:
        for f in list(ROOT.rglob("*.sv")) + list(ROOT.rglob("*.py")):
            if self._skip(f):
                continue
            head = f.read_text(errors="replace")[:800]
            if "SPDX-License-Identifier" not in head:
                self.bad("S2", f, "missing SPDX-License-Identifier header (R-D2)")

    # --------------------------------------Naming (R-N)---------------------------------------------
    # -------------------------------------------------------------------------------------- S3/S4/S5
    def check_rtl(self) -> None:
        mod_re = re.compile(r"^\s*module\s+([A-Za-z_][\w$]*)", re.M)
        port_re = re.compile(
            r"^\s*(?:input|output|inout)\b[^;]*?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[,)]\s*$")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)          

            mods = mod_re.findall(text)
            if not mods:
                continue                              # package-only file
            if f.stem not in mods:
                self.bad("S3", f, f"no module named '{f.stem}' (found {', '.join(mods)}) (R-N1)")
            for m in mods:
                if not m.startswith(MODULE_PREFIXES):
                    self.bad("S4", f, f"module '{m}' lacks a mandated prefix "
                                      f"{MODULE_PREFIXES} (R-N2)")

            # S5 applies to module PORTS, not to task/function arguments, and
            # only to synthesisable RTL -- a testbench's task signature is not a
            # hardware interface.
            if "verif" in f.parts:
                continue
            depth = 0                                  # task/function nesting
            for line in text.splitlines():
                if re.match(r"\s*(task|function)\b", line):
                    depth += 1
                elif re.match(r"\s*end(task|function)\b", line):
                    depth = max(0, depth - 1)
                if depth:
                    continue
                m = port_re.match(line)
                if not m:
                    continue
                port = m.group(1)
                if port in ("clk_i", "rst_ni"):
                    continue
                if not port.endswith(("_i", "_o", "_io")):
                    self.bad("S5", f, f"port '{port}' lacks _i/_o/_io suffix (R-N3)")

    # -------------------------------------------------------------------------------------------- S6
    def check_dq_naming(self) -> None:
        bad_re = re.compile(r"\b(\w+?)_(next|reg)\b")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)
            for m in bad_re.finditer(text):
                self.bad("S6", f,
                    f"'{m.group(0)}' uses banned suffix '_{m.group(2)}' — "
                    f"use _d (comb next-state) / _q (registered) instead (R-N6)")

    # -------------------------------------------------------------------------------------------- S7
    def check_param_naming(self) -> None:
        # parameter / localparam NAME = value;  (optionally typed: parameter int NAME = ...)
        param_re = re.compile(
            r"\b(?:parameter|localparam)\b\s+(?:\w+\s+)?(?:\[[^\]]*\]\s*)?(\w+)\s*(?:\[[^\]]*\])?\s*="
        )
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for name in param_re.findall(text):
                if name != name.upper():
                    self.bad("S7", f, f"parameter '{name}' must be UPPER_SNAKE (R-N5)")

    def check_signal_naming(self) -> None:
        decl_re = re.compile(
            r"\b(?:logic|wire|reg|input|output|inout)\b\s*(?:signed|unsigned)?\s*"
            r"(?:\[[^\]]*\]\s*)*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:\[[^\]]*\])?\s*[;,=)]"
        )
        # parameter/localparam declarations ko exclude karne ke liye — inke andar
        # bhi 'logic'/'wire' type keyword aa sakta hai jo false-positive banata hai
        param_strip_re = re.compile(
            r"\b(?:parameter|localparam)\b[^;]*;", re.S
        )
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            # param/localparam statements ko same-length spaces se replace kar do
            # (line numbers/positions preserve rehte hain, sirf content nikal jata hai)
            text = param_strip_re.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)

            for names in decl_re.findall(text):
                for name in re.split(r"\s*,\s*", names):
                    name = name.strip()
                    if not name:
                        continue
                    if name != name.lower():
                        self.bad("S7", f, f"signal '{name}' must be lower_snake (R-N5)")

    def check_type_naming(self) -> None:
        # typedef struct/union/logic/... { ... } name_t;   (non-enum typedefs)
        type_re = re.compile(
            r"typedef\s+(?:struct|union)\s+(?:packed\s+)?\b.*?\{(.*?)\}\s*(\w+)\s*;", re.S
        )
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for _body, type_name in type_re.findall(text):
                if not type_name.endswith("_t"):
                    self.bad("S7", f, f"type '{type_name}' must end in _t (R-N5)")

    def check_enum_naming(self) -> None:
        enum_re = re.compile(r"typedef\s+enum\b.*?\{(.*?)\}\s*(\w+)\s*;", re.S)
        value_re = re.compile(r"([A-Za-z_]\w*)\s*(?:=\s*[^,]+)?")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for body, type_name in enum_re.findall(text):
                if not type_name.endswith("_e"):
                    self.bad("S7", f, f"enum type '{type_name}' must end in _e (R-N5)")
                for raw in body.split(","):
                    raw = raw.strip()
                    if not raw:
                        continue
                    vm = value_re.match(raw)
                    if not vm:
                        continue
                    val = vm.group(1)
                    if val != val.upper():
                        self.bad("S7", f, f"enum value '{val}' must be ALL_CAPS (R-N9)")
        
    # ------------------------------------------------------------------------------------------- S8
    def check_clock_reset(self) -> None:
        port_re = re.compile(
            r"^\s*(?:input|output|inout)\b[^;]*?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[,)]\s*$")
        clkrst_re = re.compile(r"(clk|clock|rst|reset)", re.I)
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f) or "verif" in f.parts:
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)
            if not re.search(r"^\s*module\s+", text, re.M):
                continue
            ports, depth = [], 0
            for line in text.splitlines():
                if re.match(r"\s*(task|function)\b", line):
                    depth += 1
                elif re.match(r"\s*end(task|function)\b", line):
                    depth = max(0, depth - 1)
                if depth:
                    continue
                m = port_re.match(line)
                if m:
                    ports.append(m.group(1))
            for p in ports:
                if not clkrst_re.search(p) or p in ("clk_i", "rst_ni"):
                    continue
                self.bad("S8", f, f"port '{p}' — only clk_i/rst_ni allowed "
                                    f"(R-N10); reset ports must be named rst_ni (R-N4)")

    # ------------------------------------------------------------------------------------------- S9
    def check_case_conventions(self) -> None:
        param_re = re.compile(r"\bparameter\b[^=;]*?\b([A-Za-z_]\w*)\s*=")
        type_re = re.compile(r"typedef\s+(?:struct|union)\b.*?\}\s*(\w+)\s*;", re.S)
        # internal signal decl: `logic [..] name ;` — ports end with , or ) not ;
        sig_re = re.compile(
            r"^\s*logic\s*(?:\[[^\]]*\]\s*)*(\w+)\s*(?:\[[^\]]*\])*\s*;", re.M)
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)
            for name in param_re.findall(text):
                if name != name.upper():
                    self.bad("S9", f, f"parameter '{name}' must be UPPER_SNAKE (R-N5)")
            for name in type_re.findall(text):
                if not name.endswith("_t"):
                    self.bad("S9", f, f"typedef '{name}' must end in _t (R-N5)")
            for name in sig_re.findall(text):
                # skip enum-value-style ALL_CAPS constants and already-covered _t types
                if name == name.upper() or name.endswith("_t"):
                    continue
                if name != name.lower():
                    self.bad("S9", f, f"signal '{name}' must be lower_snake (R-N5)")

    # ---------------------------Coding (R-C)-------------------------------------------------------
    # -------------------------------------------------------------------------------------- S10/S11
    def check_style(self) -> None:
        banned = [
            (re.compile(r"\balways\s*@\s*\("), "use always_ff/always_comb/always_latch (R-C1)"),
            (re.compile(r"^\s*reg\s+", re.M), "use `logic`, not `reg` (R-C2)"),
            (re.compile(r"^\s*wire\s+", re.M), "use `logic`, not `wire` (R-C2)"),
            (re.compile(r"\b(?:input|output|inout)\b\s+(?:reg|wire)\b"),
            "port must use `logic`, not `reg`/`wire` (R-C2)"),
            (re.compile(r"\$random\b"), "use $urandom, which is seeded reproducibly (R-V4)"),
        ]
        mem_re = re.compile(r"^\s*logic\s*(?:\[[^\]]*\]\s*)+\w+\s*\[[^\]]*\]\s*;", re.M)
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)
            for rx, msg in banned:
                if rx.search(text):
                    self.bad("S10", f, msg)
            if mem_re.search(text) and f.name not in ("meds_s1_sram.sv",):
                if "verif/" not in str(f):
                    self.bad("S11", f, "memory array declared outside meds_s1_sram "
                                    "(INTERFACES.md section 8, R-C5)")

    # ------------------------------------------------------------------------------------------ S12
    def check_assignment_style(self) -> None:
        # always_comb -> sirf blocking (=)
        # always_ff   -> sirf non-blocking (<=)
        comb_hdr_re = re.compile(r"\balways_comb\b")
        ff_hdr_re = re.compile(r"\balways_ff\b\s*@\s*\([^)]*\)")

        def extract_block(text: str, after_idx: int):
            m = re.search(r"\bbegin\b", text[after_idx:])
            if not m:
                semi = text.find(";", after_idx)
                if semi == -1:
                    return None, None
                return text[after_idx:semi + 1], semi + 1
            begin_pos = after_idx + m.start()
            i = begin_pos + len("begin")
            depth = 1
            tok_re = re.compile(r"\b(begin|end)\b")
            while i < len(text):
                tm = tok_re.search(text, i)
                if not tm:
                    break
                if tm.group(1) == "begin":
                    depth += 1
                else:
                    depth -= 1
                    if depth == 0:
                        return text[begin_pos + len("begin"):tm.start()], tm.end()
                i = tm.end()
            return None, None

        # parenthesized hissa (if/case conditions, ternary etc.) nikaal do
        # taake unke andar ke comparison operators (==, <=, >=) false-positive na banayein
        def strip_parens(s: str) -> str:
            out, depth = [], 0
            for ch in s:
                if ch == "(":
                    depth += 1
                    continue
                if ch == ")":
                    depth = max(0, depth - 1)
                    continue
                if depth == 0:
                    out.append(ch)
            return "".join(out)

        blocking_re = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")
        nonblocking_re = re.compile(r"(?<!<)<=")

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)

            for m in comb_hdr_re.finditer(text):
                body, _ = extract_block(text, m.end())
                if body is None:
                    continue
                clean = strip_parens(body)
                if nonblocking_re.search(clean):
                    self.bad("S12", f, "always_comb mein <= (non-blocking) "
                                        "should not be used only = (R-C1)")

            for m in ff_hdr_re.finditer(text):
                body, _ = extract_block(text, m.end())
                if body is None:
                    continue
                clean = strip_parens(body)
                if blocking_re.search(clean):
                    self.bad("S12", f, "always_ff mein = (blocking) "
                                        "should not be used only <= (R-C1)")

    # ------------------------------------------------------------------------------------------ S13
    def check_case_default(self) -> None:
        comb_hdr_re = re.compile(r"\balways_comb\b")
        case_re = re.compile(r"\b(unique\s+|unique0\s+|priority\s+)?case\b")
        # pehla non-empty statement nikaalne ke liye
        first_stmt_re = re.compile(r"\s*([^;]+;)")

        def extract_block(text: str, after_idx: int):
            m = re.search(r"\bbegin\b", text[after_idx:])
            if not m:
                semi = text.find(";", after_idx)
                if semi == -1:
                    return None
                return text[after_idx:semi + 1]
            begin_pos = after_idx + m.start()
            i = begin_pos + len("begin")
            depth = 1
            tok_re = re.compile(r"\b(begin|end)\b")
            while i < len(text):
                tm = tok_re.search(text, i)
                if not tm:
                    break
                if tm.group(1) == "begin":
                    depth += 1
                else:
                    depth -= 1
                    if depth == 0:
                        return text[begin_pos + len("begin"):tm.start()]
                i = tm.end()
            return None

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            text = self._strip_comments(text)

            for m in comb_hdr_re.finditer(text):
                body = extract_block(text, m.end())
                if body is None or not case_re.search(body):
                    continue  # case wala always_comb hi check karna hai

                cm = case_re.search(body)

                # --- Check 1: case se pehle koi assignment (default) honi chahiye ---
                before_case = body[:cm.start()].strip()
                if not before_case or "=" not in before_case:
                    self.bad("S13", f,
                        "always_comb with case must assign a default before "
                        "the case statement (R-C3)")

                # --- Check 2: case 'unique' honi chahiye ---
                if not cm.group(1) or "unique" not in cm.group(1):
                    self.bad("S13", f,
                        "case inside always_comb must be `unique case`, not "
                        "plain `case` (R-C3)")

    # ------------------------------------------------------------------------------------------ S14
    def check_reset_policy(self) -> None:
        hdr_re = re.compile(r"always_ff\s*@\s*\(([^)]*)\)\s*begin\s*\n\s*([^\n]+)")
        comb_re = re.compile(r"always_comb\s*begin(.*?)\bend\b", re.S)
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
 
            # --- sequential blocks: async assert + active-low + structure ---
            for sens, first_line in hdr_re.findall(text):
                sens_n = re.sub(r"\s+", " ", sens.strip())
                if "rst_ni" not in sens_n:
                    continue                       # no reset — not this rule's concern
                if sens_n != "posedge clk_i or negedge rst_ni":
                    self.bad("S14", f, f"always_ff sensitivity '{sens_n}' must be "
                                        f"'posedge clk_i or negedge rst_ni' — "
                                        f"async assert, active-low (R-C4)")
                    continue
                if not re.match(r"if\s*\(\s*!\s*rst_ni\s*\)", first_line.strip()):
                    self.bad("S14", f, "always_ff with rst_ni: first statement "
                                        "after 'begin' must be 'if (!rst_ni)' (R-C4)")
 
            # --- combinational blocks: reset, if used, must be active-low ---
            for body in comb_re.findall(text):
                if re.search(r"\brst_ni\b", body):
                    # rst_ni must be used as active-low
                    if re.search(r"\bif\s*\(\s*rst_ni\s*\)", body):
                        self.bad("S14", f, "rst_ni used as active-high in always_comb; "
                            "must be active-low using !rst_ni (R-C4)")

    # ------------------------------------------------------------------------------------------ S15
    def check_size(self) -> None:
        for f in list(ROOT.rglob("*.sv")) + list(ROOT.rglob("*.py")):
            if self._skip(f):
                continue
            rel = str(f.relative_to(ROOT))
            if rel in SIZE_WAIVER:
                continue
            n = len(f.read_text(errors="replace").splitlines())
            if n > MAX_LINES:
                self.bad("S15", f, f"{n} lines exceeds the {MAX_LINES}-line limit; "
                                   "split it or add a waiver with a reason (R-C6)")

    # ------------------------------------------------------------------------------------------ S16
    def check_case_keywords(self) -> None:
        casex_re = re.compile(r"\bcasex\b")
        casez_re = re.compile(r"\bcasez\b")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            if casex_re.search(text):
                self.bad("S16", f, "casex is banned outright -- x/z treated as "
                                    "don't-care causes X-optimism; use unique case / "
                                    "unique0 case (R-C11)")
            if casez_re.search(text):
                self.bad("S16", f, "casez found -- permitted only for priority-encoder "
                                    "patterns with explicit reviewer sign-off in the PR "
                                    "description; confirm sign-off exists (R-C11)")

    # ------------------------------------------------------------------------------------------ S17
    def check_magic_numbers(self) -> None:
        sized_re = re.compile(r"\b\d+'s?[bBoOdDhH][0-9a-fA-F_xzXZ?]+\b")
        param_line_re = re.compile(r"\b(?:parameter|localparam)\b")
        allowed = {"0", "1"}                      # trivial constants, not "magic"
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f) or f.name == "s1_pkg.sv":
                continue                          # shared constants live here by design
            text = self._strip_comments(f.read_text(errors="replace"))
            for i, line in enumerate(text.splitlines(), 1):
                if param_line_re.search(line):
                    continue                      # this line IS the named constant
                for lit in sized_re.findall(line):
                    digits = re.sub(r"^\d+'s?[bBoOdDhH]", "", lit).strip("_")
                    if digits in allowed:
                        continue
                    self.bad("S17", f, f"line {i}: sized literal '{lit}' outside a "
                                        f"parameter/localparam -- derive from a named "
                                        f"parameter or $clog2 (R-C7)") 

    # ------------------------------------------------------------------------------------------ S18
    def check_shared_types_in_pkg(self) -> None:
        # Step 1: har file mein struct/union typedefs dhoondo (name -> file)
        type_decl_re = re.compile(
            r"typedef\s+(?:struct|union)\s+(?:packed\s+)?\b.*?\{.*?\}\s*(\w+)\s*;", re.S
        )
        declared_in: dict[str, Path] = {}
        file_texts: dict[Path, str] = {}

        files = [f for f in sorted(ROOT.rglob("*.sv")) if not self._skip(f)]

        for f in files:
            text = self._strip_comments(f.read_text(errors="replace"))
            file_texts[f] = text
            for type_name in type_decl_re.findall(text):
                # agar same naam do jagah declared hai to pehli location record rehti hai
                # (duplicate-declaration apna alag issue hai, yahan scope nahi)
                declared_in.setdefault(type_name, f)

        # Step 2: har declared type ke liye check karo ke kya kisi *dusri* file
        # mein bhi uska naam use ho raha hai (as a usage, not just re-declaration)
        for type_name, decl_file in declared_in.items():
            if decl_file.name == "s1_pkg.sv":
                continue  # already sahi jagah hai

            usage_re = re.compile(r"\b" + re.escape(type_name) + r"\b")
            used_elsewhere = []
            for f, text in file_texts.items():
                if f == decl_file:
                    continue
                if usage_re.search(text):
                    used_elsewhere.append(f)

            if used_elsewhere:
                other_files = ", ".join(str(x) for x in used_elsewhere)
                self.bad("S18", decl_file,
                    f"struct type '{type_name}' is used in other module(s) "
                    f"({other_files}) but declared outside s1_pkg.sv -- shared "
                    f"types must live in s1_pkg.sv (R-C8)")

    # ------------------------------------------------------------------------------------------ S19
    def check_valid_ready(self) -> None:
        assign_re = re.compile(r"assign\s+(\w*valid\w*)\s*=\s*([^;]+);")
        comb_re = re.compile(r"always_comb\s*begin(.*?)\bend\b", re.S)
        comb_assign_re = re.compile(r"(\w*valid\w*)\s*=\s*([^;]+);")
        ready_re = re.compile(r"\bready\w*\b", re.I)
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for name, rhs in assign_re.findall(text):
                if ready_re.search(rhs):
                    self.bad("S19", f, f"'{name}' driven by assign whose RHS "
                                        f"references a 'ready' signal -- valid must not "
                                        f"depend combinationally on ready (R-C10)")
            for body in comb_re.findall(text):
                for name, rhs in comb_assign_re.findall(body):
                    if ready_re.search(rhs):
                        self.bad("S19", f, f"'{name}' assigned inside always_comb from "
                                            f"an expression referencing 'ready' -- "
                                            f"valid must not depend combinationally on "
                                            f"ready (R-C10)")
        
    # ------------------------------------------------------------------------------------------ S20
    def check_wildcard_port_connect(self) -> None:
        wildcard_re = re.compile(r"\.\*")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            if wildcard_re.search(text):
                self.bad("S20", f, "'.*' implicit port connection found -- "
                                    "every port must be connected explicitly, "
                                    "e.g. .clk_i(clk_i) (R-C12)")

    # ------------------------------------------------------------------------------------------ S21
    def check_lint_waiver_config(self) -> None:
        vlt = ROOT / "verif" / "verilator.vlt"
        if not vlt.is_file():
            self.bad("S21", "verif/verilator.vlt", "missing -- required to promote "
                                "WIDTH/WIDTHEXPAND to errors (R-C13, R-C15)")
            return
        text = vlt.read_text(errors="replace")
        if not text.lstrip().startswith("`verilator_config"):
            self.bad("S21", vlt, "`verilator_config` must be the first line (R-L2)")
        for rule, code in (("WIDTH", "R-C13"), ("WIDTHEXPAND", "R-C15")):
            if not re.search(rf"\b{rule}\b", text):
                self.bad("S21", vlt, f"{rule} warning not promoted to error -- "
                                    f"{code} is unenforced")
        for rule, code in (("WIDTH", "R-C13"), ("WIDTHEXPAND", "R-C15"),
                        ("UNOPTFLAT", "R-C21")):
            if not re.search(rf"\b{rule}\b", text):
                self.bad("S21", vlt, f"{rule} not promoted to error -- {code} unenforced")

    # ------------------------------------------------------------------------------------------ S22
    def check_localparam_candidates(self) -> None:
        param_re = re.compile(
            r"\bparameter\b[^=;]*?\b([A-Za-z_]\w*)\s*=\s*([^,;)]+)")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for name, rhs in param_re.findall(text):
                if "$clog2" in rhs or re.search(r"\b[A-Z][A-Z0-9_]*\b", rhs):
                    self.bad("S22", f, f"parameter '{name}' derived from "
                                        f"$clog2/another parameter -- review whether "
                                        f"it should be localparam instead (R-C14)")

    # ------------------------------------------------------------------------------------------ S23
    def check_no_delay(self) -> None:
        # only flags simple numeric/time delays (#5, #10.5, #1ns). Deliberately
        # does NOT flag '#(' -- ambiguous with parameterized instantiation
        # (mod #(.W(8)) i_mod (...)) and can't be told apart by regex.
        delay_re = re.compile(r"#\s*\d+(?:\.\d+)?(?:[a-z]+)?\b")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f) or "verif" in f.parts:
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for i, line in enumerate(text.splitlines(), 1):
                if delay_re.search(line):
                    self.bad("S23", f, f"line {i}: '#' delay in synthesizable "
                                        f"RTL -- confine to verif/ (R-C17)")

    # ------------------------------------------------------------------------------------------ S24
    def check_duplicate_nonblocking(self) -> None:
        block_re = re.compile(r"always_ff\s*@[^;]*?begin(.*?)\bend\b", re.S)
        assign_re = re.compile(r"\b(\w+)(\[[^\]]*\])?\s*<=")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for body in block_re.findall(text):
                seen: dict[str, int] = {}
                for name, brack in assign_re.findall(body):
                    if brack:
                        continue  # bit-sliced target -- overlap needs a real
                                # tool to prove, skip to avoid false positives
                    seen[name] = seen.get(name, 0) + 1
                for name, n in seen.items():
                    if n > 1:
                        self.bad("S24", f, f"'{name}' has {n} whole-signal <= "
                                            f"writes in one always_ff -- later "
                                            f"write silently wins (R-C19)")

    # ------------------------------------------------------------------------------------------ S25
    def check_multibit_boolean(self) -> None:
        decl_re = re.compile(
            r"\b(?:logic|reg|wire|input|output|inout)\b\s*(?:signed|unsigned)?\s*"
            r"(\[[^\]]*\])?\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:\[[^\]]*\])?\s*[;,=)]"
        )
        single_bit_re = re.compile(r"^\[\s*0\s*:\s*0\s*\]$")

        cond_kw_re = re.compile(r"\b(?:if|while)\s*\(")
        cmp_re = re.compile(r"==|!=|<=|>=|<|>")
        reduction_re = re.compile(r"^[&|^~]+\s*\S")
        # bare signal ref, optional leading !, optional [sel] (index or part-select)
        atom_re = re.compile(r"^!*\s*([A-Za-z_]\w*)\s*(?:\[([^\]]*)\])?$")

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))

            # -- local width table --
            multibit: dict[str, bool] = {}
            for rng, names in decl_re.findall(text):
                is_multi = bool(rng) and not single_bit_re.match(rng.strip())
                for name in re.split(r"\s*,\s*", names):
                    name = name.strip()
                    if name:
                        multibit[name] = is_multi

            # -- scan if/while conditions --
            for m in cond_kw_re.finditer(text):
                cond, _end = self._match_paren(text, m.end() - 1)
                if cond is None:
                    continue
                for operand in self._split_top_level(cond):
                    op = operand.strip()
                    if not op or cmp_re.search(op) or reduction_re.match(op):
                        continue                      # already explicit / reduction
                    am = atom_re.match(op)
                    if not am:
                        continue                      # function call etc -- can't tell, skip
                    name, sel = am.group(1), am.group(2)
                    if sel is not None:
                        if ":" not in sel:
                            continue                  # single-bit index sig[3]
                        lo, hi = (s.strip() for s in sel.split(":", 1))
                        if lo == hi:
                            continue                  # [3:3] -- still one bit
                        self.bad("S25", f,
                            f"'{op}' (part-select) used directly as a boolean "
                            f"condition -- compare explicitly, e.g. != '0 (R-C20)")
                        continue
                    if multibit.get(name):
                        self.bad("S25", f,
                            f"'{op}' is a multi-bit signal used directly in a "
                            f"boolean condition -- write '{name} != '0' to make "
                            f"the intent explicit (R-C20)")

    # ------------------------------------------------------------------------------------------ S26
    def check_ansi_ports_order(self) -> None:
        non_ansi_re = re.compile(r"^\s*module\s+\w+\s*\(\s*[A-Za-z_]\w*\s*,")
        port_re = re.compile(
            r"^\s*(?:input|output|inout)\b[^;]*?([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*[,)]\s*$", re.M)
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            if non_ansi_re.search(text):
                self.bad("S26", f, "port list looks Verilog-95 style -- use "
                                    "full ANSI declarations (R-C22)")
            ports = port_re.findall(text)
            clk_idx = next((i for i, p in enumerate(ports) if p == "clk_i"), None)
            rst_idx = next((i for i, p in enumerate(ports) if p == "rst_ni"), None)
            if clk_idx not in (None, 0):
                self.bad("S26", f, "clk_i must be the first port (R-C22)")
            if clk_idx is not None and rst_idx is not None and rst_idx != clk_idx + 1:
                self.bad("S26", f, "rst_ni must immediately follow clk_i (R-C22)")

    # ------------------------------------------------------------------------------------------ S27
    def check_generate_labels(self) -> None:
        genfor_re = re.compile(r"for\s*\(\s*genvar\b[^)]*\)\s*begin\b(?!\s*:)")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            if genfor_re.search(text):
                self.bad("S27", f, "generate-for missing ': label' on begin (R-C23)")

    # ------------------------------------------------------------------------------------------ S28
    def check_manual_sign_handling(self) -> None:
        negate_re = re.compile(r"~\s*[A-Za-z_]\w*(?:\[[^\]]*\])?\s*\+\s*1(?:'[bB]1)?\b")
        negate_alt_re = re.compile(r"-\s*~\s*[A-Za-z_]\w*\b")   # -~x === x+1

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for i, line in enumerate(text.splitlines(), 1):
                if negate_re.search(line) or negate_alt_re.search(line):
                    self.bad("S28", f, f"line {i}: manual two's-complement "
                                        f"negation -- declare the signal "
                                        f"`signed` and use unary '-' instead (R-C24)")

    # ------------------------------------------------------------------------------------------ S29
    def check_hierarchical_refs(self) -> None:
        hier_re = re.compile(
            r"(?<![A-Za-z0-9_$])"
            r"i_[A-Za-z_][A-Za-z0-9_$]*"
            r"(?:\.[A-Za-z_][A-Za-z0-9_$]*)+"
            r"(?![A-Za-z0-9_$])"
        )

        directive_re = re.compile(
            r"^\s*`(?P<kind>ifdef|ifndef|elsif|else|endif)\b"
            r"(?:\s+(?P<name>[A-Za-z_][A-Za-z0-9_$]*))?",
            re.M,
        )

        sva_re = re.compile(
            r"\b(?:assert|assume|cover)\s+property\b"
        )

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f) or "verif" in f.parts:
                continue

            text = self._strip_comments(f.read_text(errors="replace"))
            lines = text.splitlines()
            guarded = [False] * (len(lines) + 1)
            stack: list[bool] = []

            for line_no, line in enumerate(lines, start=1):
                match = directive_re.match(line)

                if match:
                    kind = match.group("kind")
                    name = match.group("name")

                    if kind == "ifndef":
                        is_non_synth_branch = (
                            name in self.SYNTH_GUARD_IFNDEF
                        )
                        stack.append(is_non_synth_branch)

                    elif kind == "ifdef":
                        is_non_synth_branch = (
                            name in self.SYNTH_GUARD_IFDEF
                        )
                        stack.append(is_non_synth_branch)

                    elif kind == "else":
                        if stack:
                            stack[-1] = False

                    elif kind == "elsif":
                        if stack:
                            stack[-1] = False

                    elif kind == "endif":
                        if stack:
                            stack.pop()

                guarded[line_no] = any(stack)

            for match in hier_re.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1

                previous_semicolon = text.rfind(";", 0, match.start())
                current_statement = text[
                    previous_semicolon + 1 : match.start()
                ]

                is_sva = bool(sva_re.search(current_statement))

                if guarded[line_no] and is_sva:
                    continue

                self.bad(
                    "S29",
                    f,
                    f"'{match.group(0)}' is a hierarchical reference into "
                    "an instance -- only allowed inside an SVA that is "
                    "macro-guarded out of synthesis (R-C25)",
                )

    # ------------------------------------------------------------------------------------------ S30
    def check_array_endianness(self) -> None:
        decl_re = re.compile(
            r"\b(?:logic|reg|wire|bit|byte|int|integer|shortint|longint|"
            r"[A-Za-z_]\w*_t)\b\s*(?:signed|unsigned)?\s*(\[[^\]]*\])?\s*"
            r"([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*[;,=)]"
        )
        int_re = re.compile(r"^\d+$")

        def classify(dim):
            if dim is None:
                return None
            inner = dim[1:-1].strip()
            if ":" not in inner:
                return None                    # size-only [16], not a range
            hi, lo = (s.strip() for s in inner.split(":", 1))
            if int_re.match(hi) and int_re.match(lo):
                h, l = int(hi), int(lo)
                return "desc" if h > l else ("asc" if h < l else "equal")
            if lo == "0" and not int_re.match(hi):
                return "desc"                  # [WIDTH-1:0]
            if hi == "0" and not int_re.match(lo):
                return "asc"                   # [0:DEPTH-1]
            return "unknown"

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            for m in decl_re.finditer(text):
                packed, name, unpacked = m.group(1), m.group(2), m.group(3)
                line_no = text.count("\n", 0, m.start()) + 1

                if classify(packed) == "asc":
                    self.bad("S30", f, f"line {line_no}: packed dim {packed} "
                                        f"on '{name}' is ascending -- packed "
                                        f"arrays must be [N-1:0] (R-C26)")
                if classify(unpacked) == "desc":
                    self.bad("S30", f, f"line {line_no}: unpacked dim "
                                        f"{unpacked} on '{name}' is descending "
                                        f"-- unpacked arrays must be [0:N-1] (R-C26)")

    # ------------------------------------------------------------------------------------------ S31
    def check_latch_justification(self) -> None:
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            raw = f.read_text(errors="replace")
            for m in re.finditer(r"always_latch\b", raw):
                line_start = raw.rfind("\n", 0, m.start())
                prev_start = raw.rfind("\n", 0, line_start)
                prev_line = raw[prev_start + 1: line_start] if line_start != -1 else ""
                if "//" not in prev_line:
                    self.bad("S31", f, "always_latch with no justification "
                                        "comment above it (R-C18)")

    # ------------------------------------------------------------------------------------------ S32
    def check_fsm_state_type(self) -> None:
        raw_decl_re = re.compile(
            r"^\s*logic\s*(?:\[[^\]]*\]\s*)+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*;",
            re.M | re.I)
        state_q_name_re = re.compile(r"\w*state\w*_q$", re.I)
        bare_cmp_re = re.compile(r"(\w*state\w*_q)\s*==\s*\d", re.I)
        case_re = re.compile(
            r"(?:unique\s+)?case\s*\(\s*(\w*state\w*_q)\s*\)(.*?)endcase", re.S | re.I)
        label_re = re.compile(r"^\s*([A-Za-z_]\w*)\s*:", re.M)

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))

            # -- raw logic-vector state register --
            for names in raw_decl_re.findall(text):
                for name in re.split(r"\s*,\s*", names):
                    name = name.strip()
                    if state_q_name_re.match(name):
                        self.bad("S32", f, f"'{name}' declared as raw logic "
                                            f"vector — state register must be "
                                            f"a typedef'd enum (R-M1)")

            # -- bare numeric literal comparison --
            for name in bare_cmp_re.findall(text):
                self.bad("S32", f, f"'{name}' compared against a bare numeric "
                                    f"literal — compare via the enum name, not "
                                    f"an integer (R-M1)")

            # -- state VALUES defined via localparam instead of enum members --
            for state_sig, body in case_re.findall(text):
                labels = {m for m in label_re.findall(body) if m.lower() != "default"}
                for label in sorted(labels):
                    lp_re = re.compile(
                        rf"\blocalparam\b[^;]*\b{re.escape(label)}\b\s*=", re.I)
                    if lp_re.search(text):
                        self.bad("S32", f, f"state value '{label}' (used in "
                                            f"case({state_sig})) is declared "
                                            f"via localparam, not as a member "
                                            f"of a typedef'd enum (R-M1)")
    
    # ------------------------------------------------------------------------------------------ S33
    def check_fsm_three_blocks(self) -> None:
        state_reg_re = re.compile(r"(\w*state\w*)_q\s*<=")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            fsm_names = set(state_reg_re.findall(text))
            if not fsm_names:
                continue

            ff_blocks = self._extract_blocks(text, "always_ff")
            comb_blocks = self._extract_blocks(text, "always_comb")

            for fsm in fsm_names:
                q, d = f"{fsm}_q", f"{fsm}_d"

                seq_idx = [i for i, (_, b) in enumerate(ff_blocks)
                           if re.search(rf"\b{q}\s*<=", b)]
                nxt_idx = [i for i, (_, b) in enumerate(comb_blocks)
                           if re.search(rf"\b{d}\s*=", b)]
                out_idx = [i for i, (_, b) in enumerate(comb_blocks)
                           if re.search(r"\w+_o\s*=", b) and re.search(rf"\b{q}\b", b)]

                # --- explicit block-count check (the part you were missing) ---
                distinct_comb = set(nxt_idx) | set(out_idx)
                total_blocks = len(set(seq_idx)) + len(distinct_comb)
                if total_blocks < 3:
                    self.bad("S33", f, f"FSM '{fsm}': found {total_blocks} "
                                        f"block(s) driving this FSM, but exactly "
                                        f"three are required -- one always_ff for "
                                        f"{q}, one always_comb for {d} only, one "
                                        f"always_comb for outputs only (R-M2)")

                # --- sequential block: exactly one, assigns ONLY state_q ---
                if len(seq_idx) != 1:
                    self.bad("S33", f, f"FSM '{fsm}': expected exactly one "
                                        f"always_ff for {q}, found {len(seq_idx)} (R-M2)")
                else:
                    # distinct signal NAMES, not raw '<=' occurrence count --
                    # an if(reset)/else pair both targeting state_q is normal.
                    assigned = set(re.findall(r"(\w+)\s*<=", ff_blocks[seq_idx[0]][1]))
                    extra = assigned - {q}
                    if extra:
                        self.bad("S33", f, f"FSM '{fsm}': sequential block also "
                                            f"assigns {', '.join(sorted(extra))} — "
                                            f"it must do nothing but register the "
                                            f"state (R-M2)")

                # --- next-state block: exactly one, touches state_d, no outputs ---
                if len(nxt_idx) != 1:
                    self.bad("S33", f, f"FSM '{fsm}': expected exactly one "
                                        f"always_comb driving {d}, found {len(nxt_idx)} (R-M2)")
                elif re.search(r"\w+_o\s*=", comb_blocks[nxt_idx[0]][1]):
                    self.bad("S33", f, f"FSM '{fsm}': next-state block also "
                                        f"drives an output port — split it out (R-M2)")

                # --- output block(s): assign outputs, never state_d ---
                for i in out_idx:
                    b = comb_blocks[i][1]
                    if re.search(rf"\b{d}\s*=", b):
                        self.bad("S33", f, f"FSM '{fsm}': output block also "
                                            f"assigns {d} — split it out (R-M2)")

    # ------------------------------------------------------------------------------------------ S34
    def check_fsm_default_case(self) -> None:
        case_re = re.compile(
            r"\bcase\s*\(\s*(\w*state\w*_q)\s*\)(.*?)\bendcase\b",
            re.S | re.I
        )

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue

            text = self._strip_comments(
                f.read_text(errors="replace")
            )

            for match in case_re.finditer(text):
                state_sig = match.group(1)
                body = match.group(2)
                prefix = text[:match.start()]
                prefix = prefix.rstrip()
                if re.search(r"\bunique\s*$", prefix, re.I):
                    continue
                m = re.search(
                    r"\bdefault\s*:\s*(.*?)(?=\n\s*\w+\s*:|\Z)", body, re.S | re.I
                )
                if not m:
                    self.bad(
                        "S34", f, f"case ({state_sig}) has no " f"default branch (R-M3)"
                    )
                    continue
                default_body = m.group(1).strip()
                if not default_body:
                    self.bad(
                        "S34",
                        f, f"case ({state_sig}): default branch must "
                        f"return to a safe state, not be empty " f"(R-M3)"
                    )
                    continue
                if re.search(r"\b'x\b", default_body, re.I):
                    self.bad(
                        "S34",
                        f, f"case ({state_sig}): default branch must "
                        f"return to a safe state, not assign 'x " f"(R-M3)"
                    )

    # ---------------------Formating R-F-----------------------------------------------------------
    # ------------------------------------------------------------------------------------------ S35
    def check_indentation(self) -> None:
        for f in list(ROOT.rglob("*.sv")) + list(ROOT.rglob("*.py")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if "\t" in line:
                    self.bad("S35", f, f"line {i}: tab character found — "
                                        f"2 spaces only, no tabs (R-F1)")
    
    # ------------------------------------------------------------------------------------------ S36
    def check_begin_end(self) -> None:
        bare_begin = re.compile(r"^\s*begin\s*$", re.M)
        split_else = re.compile(r"\bend\s*\n\s*else\b")
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue
            text = self._strip_comments(f.read_text(errors="replace"))
            if bare_begin.search(text):
                self.bad("S36", f, "'begin' alone on its own line — keep it on the "
                                    "line that opens the block (R-F2)")
            if split_else.search(text):
                self.bad("S36", f, "'end' and 'else' on separate lines — use "
                                    "'end else begin' on one line (R-F2)")

    # ------------------------------------------------------------------------------------------ S37
    def check_line_length(self) -> None:
        for f in list(ROOT.rglob("*.sv")) + list(ROOT.rglob("*.py")):
            if self._skip(f):
                continue
            text = f.read_text(errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if len(line) > 120:
                    self.bad("S37", f, f"line {i}: {len(line)} columns "
                                        f"exceeds the 120-column limit (R-F3)")

    # ------------------------------------------------------------------------------------------ S38
    def check_call_parenthesis_spacing(self) -> None:
        call_re = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_$]*)\s+\("
        )
        macro_re = re.compile(
            r"`[A-Za-z_][A-Za-z0-9_$]*\s+\("
        )
        keywords = {
            "if", "else", "for", "foreach", "while", "do", "case", "casex",
            "casez", "randcase", "with", "inside", "assert", "assume", "cover",
            "expect", "wait", "repeat", "forever", "disable", "return",
        }

        for f in sorted(
            list(ROOT.rglob("*.sv")) +
            list(ROOT.rglob("*.svh")) #+
            #list(ROOT.rglob("*.py"))
        ):
            if self._skip(f):
                continue
            text = self._strip_comments(
                f.read_text(errors="replace")
            )
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(
                    r"\.\s*[A-Za-z_][A-Za-z0-9_$]*\s+\(",
                    line
                ):
                    continue
                if re.search(
                    r"\b[A-Za-z_][A-Za-z0-9_$]*\s+#\s*\(",
                    line
                ):
                    continue
                for m in call_re.finditer(line):
                    name = m.group(1)

                    if name.lower() in keywords:
                        continue
                    self.bad(
                        "S38", f, f"line {i}: space before '(' in call "
                        f"'{name}' — remove the space (R-F7)"
                    )
                if macro_re.search(line):
                    self.bad(
                        "S38",
                        f,
                        f"line {i}: space before '(' in macro call "
                        f"(R-F7)"
                    )

    # ------------------------------------------------------------------------------------------ S39
    def check_default_nettype(self) -> None:
        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f) or "verif" in f.parts:
                continue
            text = f.read_text(errors="replace")
            if "`default_nettype none" not in text:
                self.bad("S39", f, "missing `default_nettype none` — without it, "
                                    "the compiler won't catch implicit nets (R-F11)")   
 
    # ------------------------------------------------------------------------------------------ S40
    def check_operator_spacing(self) -> None:
        
        bad_re = re.compile(
            r"(?<![=!<>+\-*/&|^~%])\s{2,}=(?!=)"
            r"|"
            r"(?<![=!<>+\-*/&|^~%])=\s{2,}(?![=])"
        )

        # Plain assignment '=' position
        assign_re = re.compile(
            r"(?<![=!<>+\-*/&|^~%])=(?!=)"
        )

        for f in sorted(ROOT.rglob("*.sv")):
            if self._skip(f):
                continue

            text = self._strip_comments(
                f.read_text(errors="replace")
            )

            lines = text.splitlines()

            for i, line in enumerate(lines):
                if not bad_re.search(line):
                    continue

                stripped = line.strip()

                if re.match(r"^(localparam|parameter)\b", stripped, re.I):
                    continue
                if self._is_alignment_spacing(lines, i):
                    continue

                self.bad(
                    "S40",
                    f,
                    f"line {i + 1}: extra space around '=' — "
                    f"use exactly one space (R-F1)"
                )

    def _is_alignment_spacing(self, lines, index):
        line = lines[index]

        # Current line must contain a plain assignment '='
        m = re.search(
            r"(?<![=!<>+\-*/&|^~%])=(?!=)",
            line
        )

        if not m:
            return False

        eq_pos = m.start()

        # Check nearby lines for an assignment at the same column.
        for j in (index - 1, index + 1):
            if j < 0 or j >= len(lines):
                continue

            other = lines[j]

            other_m = re.search(
                r"(?<![=!<>+\-*/&|^~%])=(?!=)",
                other
            )

            if not other_m:
                continue

            # Same '=' column means intentional alignment.
            if other_m.start() == eq_pos:
                return True

        return False
    # ---------------------VERIFICATION------------------------------------------------------------
    # ------------------------------------------------------------------------------------------ S41
    def check_testbenches(self) -> None:
        modules = {f.stem for f in ROOT.rglob("rtl/**/*.sv")}
        for tb in sorted((ROOT / "verif" / "unit").glob("*.sv")):
            if not tb.name.startswith("tb_"):
                self.bad("S41", tb, "unit testbench must be named tb_<module>.sv (R-V1)")
                continue
            dut = tb.stem[3:]
            if dut not in modules:
                self.bad("S41", tb, f"no RTL module '{dut}' for this testbench (R-V1)")

    # ------------------------------------------------------------------------------------------ S42
    def check_configs(self) -> None:
        try:
            import yaml
        except ImportError:
            return
        cfgdir = ROOT / "configs"
        if not cfgdir.is_dir():
            return
        found = list(cfgdir.glob("*.yaml"))
        if not found:
            self.bad("S42", "configs", "no configuration files (SPEC section 5.3)")
        for f in sorted(found):
            try:
                data = yaml.safe_load(f.read_text()) or {}
            except Exception as e:
                self.bad("S42", f, f"YAML does not parse: {e}")
                continue
            missing = REQUIRED_CONFIG_KEYS - set(data)
            if missing:
                self.bad("S42", f, f"missing required keys: {', '.join(sorted(missing))}")

    @staticmethod
    def _skip(f: pathlib.Path) -> bool:
        parts = set(f.parts)
        return bool(parts & {".git", "build", "__pycache__", "ext", "node_modules"}) \
            or "rtl/generated" in str(f)

    @staticmethod
    def _strip_comments(text: str) -> str:
        # remove /* block */ comments, then // line comments — replace with
        # spaces (not empty) so line/column positions stay roughly intact
        text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
        text = re.sub(r"//[^\n]*", "", text)
        return text

    @staticmethod
    def _extract_blocks(text: str, keyword: str) -> list[tuple[str, str]]:
        """Returns list of (header, body) for every `keyword @(...)? begin...end`."""
        out = []
        for m in re.finditer(rf"{keyword}\s*(@\s*\([^)]*\))?\s*begin\b", text):
            header = m.group(1) or ""
            depth, i = 1, m.end()
            while depth > 0 and i < len(text):
                nxt = re.search(r"\bbegin\b|\bend\b", text[i:])
                if not nxt:
                    break
                depth += 1 if nxt.group(0) == "begin" else -1
                i += nxt.end()
            out.append((header, text[m.end():i]))
        return out
    
    @staticmethod
    def _match_paren(text: str, open_idx: int):
        """text[open_idx] must be '(' -- returns (inner_content, index_after_close)."""
        depth = 0
        for i in range(open_idx, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1:i], i + 1
        return None, None

    @staticmethod
    def _split_top_level(cond: str) -> list[str]:
        """Split on top-level && / || only -- nested (...)/[...] ke andar wale ignore."""
        parts, depth, buf, i = [], 0, [], 0
        while i < len(cond):
            ch = cond[i]
            if ch in "([":
                depth += 1
                buf.append(ch)
            elif ch in ")]":
                depth -= 1
                buf.append(ch)
            elif depth == 0 and cond[i:i + 2] in ("&&", "||"):
                parts.append("".join(buf))
                buf = []
                i += 1
            else:
                buf.append(ch)
            i += 1
        parts.append("".join(buf))
        return parts

    def report(self) -> int:
        if not self.problems:
            print("structure check: OK")
            return 0
        by_rule: dict[str, list] = {}
        for rule, path, msg in self.problems:
            by_rule.setdefault(rule, []).append((path, msg))
        print(f"structure check: {len(self.problems)} violation(s)\n")
        for rule in sorted(by_rule):
            print(f"  [{rule}]")
            for path, msg in sorted(by_rule[rule]):
                print(f"    {path}: {msg}")
            print()
        print("See docs/guidelines/CODING_STANDARD.md for the rule that each code maps to.")
        return min(len(self.problems), 100)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-readmes", action="store_true",
                    help="create a stub README.md in any directory missing one")
    args = ap.parse_args()

    c = Checker()
    c.check_readmes(args.fix_readmes)
    c.check_spdx()
    c.check_rtl()
    c.check_style()
    c.check_testbenches()
    c.check_configs()
    c.check_size()
    c.check_dq_naming()
    c.check_enum_naming()
    c.check_case_default()
    c.check_clock_reset()
    c.check_case_conventions()
    c.check_assignment_style()
    c.check_multibit_boolean()
    #c.check_magic_numbers()
    c.check_valid_ready()
    #c.check_indentation()
    c.check_begin_end()
    c.check_line_length()
    c.check_default_nettype()
    c.check_operator_spacing()
    c.check_reset_policy()
    c.check_wildcard_port_connect()
    #c.check_lint_waiver_config()
    c.check_localparam_candidates()
    c.check_no_delay()
    c.check_duplicate_nonblocking()
    c.check_ansi_ports_order()
    c.check_generate_labels()
    c.check_hierarchical_refs()
    c.check_latch_justification()
    c.check_fsm_state_type()
    c.check_fsm_three_blocks()
    c.check_fsm_default_case()
    c.check_param_naming()
    c.check_signal_naming() 
    c.check_type_naming()
    c.check_manual_sign_handling()
    c.check_array_endianness()
    c.check_call_parenthesis_spacing()
    c.check_case_keywords()
    c.check_shared_types_in_pkg()
    
    return c.report()

if __name__ == "__main__":
    sys.exit(main())
