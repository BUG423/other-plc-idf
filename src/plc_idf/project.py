"""Portable text projects and a deliberately limited, offline LiteST checker.

This module never imports a device or Windows backend.  Passing this check does
not compile ST, validate timing, or create an AutoShop native project.  The v1
profile is a scalar, memory-only MAIN body; the vendor compiler remains the
authority for syntax and execution semantics.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from plc_idf import __version__

MANIFEST = "plcidf.json"
PROFILE = "easy521-memory-only-v1"
VARIABLE_FIELDS = (
    "scope", "name", "data_type", "initial_value", "power_failure_retain", "description"
)
REGISTER_FIELDS = (
    "direction", "logical_value", "holding_registers", "register_count", "encoding",
    "unit_or_range", "notes"
)
IMPORT_FIELDS = (
    "序号", "变量名", "数据类型", "隐藏初始值", "初始值", "掉电保持", "网络公开", "注释",
    "软元件地址", ""
)
TYPE_WORDS = {"BOOL": 0, "INT": 1, "UINT": 1, "WORD": 1, "DINT": 2,
              "UDINT": 2, "DWORD": 2, "REAL": 2}
REGISTER_ENCODINGS = {
    "INT": {"signed 16-bit integer"},
    "UINT": {"unsigned 16-bit integer"}, "WORD": {"unsigned 16-bit integer"},
    "DINT": {"signed 32-bit integer word-order-unverified"},
    "UDINT": {"unsigned 32-bit integer word-order-unverified"},
    "DWORD": {"unsigned 32-bit integer word-order-unverified"},
    "REAL": {"IEEE-754 binary32 word-order-unverified",
             "IEEE-754 binary32 big-endian ABCD", "IEEE-754 binary32 word-swapped CDAB"},
}
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
KEYWORDS = set("""IF THEN ELSIF ELSE END_IF CASE OF END_CASE FOR TO BY DO END_FOR
    WHILE END_WHILE REPEAT UNTIL END_REPEAT RETURN EXIT TRUE FALSE AND OR XOR
    NOT MOD""".split())
BUILTINS = {"ABS", "MIN", "MAX", "LIMIT", "INT_TO_REAL", "REAL_TO_INT"}
DECLARATIONS = {"PROGRAM", "END_PROGRAM", "VAR", "END_VAR", "VAR_GLOBAL",
                "VAR_INPUT", "VAR_OUTPUT", "FUNCTION", "FUNCTION_BLOCK"}
UNSUPPORTED_TYPES = {"LREAL", "LINT", "ULINT", "LWORD", "STRING", "WSTRING",
                     "ARRAY", "STRUCT", "TIME", "DATE", "TIME_OF_DAY", "DATE_AND_TIME"}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    path: str
    line: int = 1
    column: int = 1
    severity: str = "error"


@dataclass
class ProjectCheck:
    root: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)
    manifest: dict | None = None

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)

    def as_dict(self) -> dict:
        return {"schema_version": 1, "ok": self.ok, "root": self.root,
                "validation": "offline-static-check", "compiled": False,
                "native_project_created": False,
                "profile": PROFILE, "source_hashes": self.source_hashes,
                "limitations": ["Not a complete ST parser or compiler",
                                "No native AutoShop project synchronization",
                                "No hardware, cycle-time or runtime validation"],
                "diagnostics": [asdict(item) for item in self.diagnostics]}


def _csv_text(headers: tuple[str, ...], rows: list[list[str]]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()


def create_project(path: str | Path, *, name: str | None = None,
                   cycle_ms: int = 5) -> ProjectCheck:
    """Create a new/empty directory; refuse to overwrite any existing files.

    Use for both ``new <path>`` and ``init [path]``.  Derived AutoShop tables are
    templates only: no binary .hcp/.ST container or Windows project is created.
    """
    root = Path(path).expanduser().resolve()
    project_name = name if name is not None else root.name
    if not isinstance(project_name, str) or not NAME.fullmatch(project_name):
        raise ValueError("project name must match [A-Za-z_][A-Za-z_0-9]*")
    if type(cycle_ms) is not int or cycle_ms <= 0:
        raise ValueError("cycle_ms must be a positive integer")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(f"refusing to overwrite nonempty project directory: {root}")
    manifest = {
        "schema_version": 1, "name": project_name, "profile": PROFILE,
        "target": {"manufacturer": "Inovance", "model": "Easy521-0808TN",
                   "dialect": "AutoShop LiteST"},
        "toolchain": {"plcidf": __version__, "autoshop": "4.11.0.5"},
        "task": {"name": "MAIN", "cycle_ms": cycle_ms, "source": "MAIN_body.st",
                 "variables": "MAIN_variables.csv"},
        "register_map": "modbus_register_map.csv",
        "variable_map": {"g_iScanCounter": "D700", "g_iHeartbeat": "D701"},
    }
    variables = [
        ["Global", "g_iScanCounter", "INT", "0", "No", "Memory-only scan counter"],
        ["Global", "g_iHeartbeat", "INT", "0", "No", "Memory-only heartbeat (0/1)"],
    ]
    registers = [
        ["output", "g_iScanCounter", "700", "1", "signed 16-bit integer", "0..999",
         "D700 internal memory; not a physical output"],
        ["output", "g_iHeartbeat", "701", "1", "signed 16-bit integer", "0..1",
         "D701 internal memory; not a physical output"],
    ]
    source = """(* MAIN body only. Internal D memory; no physical I/O or network commands.
   Configure MAIN's constant cycle in AutoShop to match plcidf.json.
   Heartbeat toggles every 1000 scans; actual timing requires runtime evidence. *)
IF g_iScanCounter >= 999 THEN
    g_iScanCounter := 0;
    IF g_iHeartbeat = 0 THEN
        g_iHeartbeat := 1;
    ELSE
        g_iHeartbeat := 0;
    END_IF;
ELSE
    g_iScanCounter := g_iScanCounter + 1;
END_IF;
"""
    readme = """# PLC-IDF text project

This is a memory-only Easy521 MAIN example, not an AutoShop native project.
Edit MAIN_body.st, MAIN_variables.csv and the mappings in plcidf.json together.
`check_project()` checks the small scalar memory-only profile offline; passing
does not prove full ST syntax, vendor compilation, download or runtime timing.
`output` in the register map means a value produced by the application, not an
electrical output. Only D700/D701 internal registers are mapped by this example.

The two autoshop_global_import*.csv files are derived variable import templates
using the ten-column header from plc/easy521/autoshop_global_import.csv. The
UTF-16 file has a BOM for AutoShop 4.11 Unicode import. Regenerate them with
`write_import_tables(path)` after editing variables or address mappings.
The native AutoShop .ST files use binary containers; do not overwrite them with
MAIN_body.st. Native project creation/source synchronization is not implemented
by this scaffold. Import/body insertion and the configured MAIN task cycle
still require a separately verified AutoShop integration.
"""
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "MAIN_body.st").write_text(source, encoding="utf-8")
    (root / "MAIN_variables.csv").write_text(_csv_text(VARIABLE_FIELDS, variables),
                                              encoding="utf-8", newline="")
    (root / "modbus_register_map.csv").write_text(_csv_text(REGISTER_FIELDS, registers),
                                                 encoding="utf-8", newline="")
    (root / "README.md").write_text(readme, encoding="utf-8")
    write_import_tables(root)
    return check_project(root)


def _position(text: str, offset: int) -> tuple[int, int]:
    return text.count("\n", 0, offset) + 1, offset - text.rfind("\n", 0, offset)


class _Checker:
    def __init__(self, root: Path):
        self.root = root
        self.result = ProjectCheck(str(root))
        self.manifest_text = ""

    def error(self, code: str, message: str, path: str = MANIFEST,
              line: int = 1, column: int = 1) -> None:
        self.result.diagnostics.append(Diagnostic(code, message, path, line, column))

    def manifest_error(self, code: str, message: str, key: str) -> None:
        offset = self.manifest_text.find(json.dumps(key))
        line, col = _position(self.manifest_text, max(0, offset))
        self.error(code, message, line=line, column=col)

    def read(self, relative: object) -> str | None:
        if not isinstance(relative, str) or not relative:
            self.error("E_PATH", "project file path must be a nonempty relative string")
            return None
        path = self.root / relative
        if path.is_absolute() and Path(relative).is_absolute():
            self.error("E_PATH", "absolute project paths are not allowed", relative)
            return None
        if not path.resolve().is_relative_to(self.root):
            self.error("E_PATH", "project file must remain inside project root", relative)
            return None
        try:
            data = path.read_bytes()
            text = data.decode("utf-8-sig")
        except (OSError, UnicodeError) as exc:
            self.error("E_FILE", str(exc), relative)
            return None
        self.result.source_hashes[relative] = hashlib.sha256(data).hexdigest()
        return text

    def rows(self, path: object, headers: tuple[str, ...]) -> list[tuple[int, dict]]:
        text = self.read(path)
        if text is None:
            return []
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            if next(reader, None) != list(headers):
                self.error("E_CSV_HEADER", "expected columns: " + ",".join(headers), str(path))
                return []
            rows = []
            while True:
                line = reader.line_num + 1
                row = next(reader, None)
                if row is None:
                    break
                if not row:
                    continue
                if len(row) != len(headers):
                    self.error("E_CSV_ROW", "row has wrong number of columns", str(path), line)
                    continue
                rows.append((line, {key: value.strip() for key, value in zip(headers, row)}))
            return rows
        except csv.Error as exc:
            self.error("E_CSV", str(exc), str(path), max(1, reader.line_num))
            return []

    def run(self) -> ProjectCheck:
        text = self.read(MANIFEST)
        if text is None:
            return self.result
        self.manifest_text = text
        try:
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError(f"duplicate manifest key: {key}")
                    obj[key] = value
                return obj
            manifest = json.loads(text, object_pairs_hook=unique)
        except json.JSONDecodeError as exc:
            self.error("E_MANIFEST_JSON", exc.msg, line=exc.lineno, column=exc.colno)
            return self.result
        except ValueError as exc:
            self.error("E_MANIFEST_JSON", str(exc))
            return self.result
        if not isinstance(manifest, dict):
            self.error("E_MANIFEST", "manifest must be a JSON object")
            return self.result
        self.result.manifest = manifest
        expected = {"schema_version": 1, "profile": PROFILE}
        for key, value in expected.items():
            if type(manifest.get(key)) is not type(value) or manifest.get(key) != value:
                self.manifest_error("E_MANIFEST", f"{key} must be {value!r}", key)
        if not isinstance(manifest.get("name"), str) or not NAME.fullmatch(manifest["name"]):
            self.manifest_error("E_MANIFEST", "invalid project name", "name")
        target = {"manufacturer": "Inovance", "model": "Easy521-0808TN",
                  "dialect": "AutoShop LiteST"}
        if manifest.get("target") != target:
            self.manifest_error("E_TARGET", "supported target is Inovance Easy521-0808TN / AutoShop LiteST", "target")
        tools = manifest.get("toolchain")
        if not isinstance(tools, dict) or any(not isinstance(tools.get(key), str) or not tools[key]
                                             for key in ("plcidf", "autoshop")):
            self.manifest_error("E_TOOLCHAIN", "pin nonempty plcidf and autoshop version strings", "toolchain")
        task = manifest.get("task")
        if not isinstance(task, dict):
            self.manifest_error("E_TASK", "one MAIN task object is required", "task")
            return self.result
        if task.get("name") != "MAIN" or type(task.get("cycle_ms")) is not int or task["cycle_ms"] <= 0:
            self.manifest_error("E_TASK", "MAIN requires a positive integer cycle_ms (configuration intent, not measured timing)", "task")
        variable_rows = self.rows(task.get("variables"), VARIABLE_FIELDS)
        variables: dict[str, tuple[int, dict]] = {}
        for line, row in variable_rows:
            name, dtype = row["name"], row["data_type"].upper()
            if not NAME.fullmatch(name) or name.upper() in KEYWORDS | DECLARATIONS | set(TYPE_WORDS):
                self.error("E_VARIABLE_NAME", f"invalid or reserved variable name: {name}", str(task.get("variables")), line)
            if name.casefold() in variables:
                self.error("E_VARIABLE_DUPLICATE", f"duplicate variable (case-insensitive): {name}", str(task.get("variables")), line)
            else:
                variables[name.casefold()] = (line, row)
            if row["scope"] != "Global":
                self.error("E_SCOPE", "v1 memory-only profile supports Global scalar variables", str(task.get("variables")), line)
            if dtype not in TYPE_WORDS:
                message = "Easy521 LiteST does not support LREAL" if dtype == "LREAL" else f"type is outside this checker profile: {dtype}"
                self.error("E_TYPE", message, str(task.get("variables")), line)
            if row["power_failure_retain"] not in {"Yes", "No"}:
                self.error("E_RETAIN", "power_failure_retain must be Yes or No", str(task.get("variables")), line)
            self.initial_value(row, str(task.get("variables")), line)
        mappings = manifest.get("variable_map")
        if not isinstance(mappings, dict):
            self.manifest_error("E_MAPPING", "variable_map must map variable names to D addresses", "variable_map")
            mappings = {}
        locations: dict[str, tuple[int, int]] = {}
        occupied: dict[int, str] = {}
        normalized_mappings = set()
        for name, address in mappings.items():
            key = name.casefold()
            if key in normalized_mappings:
                self.manifest_error("E_MAPPING_DUPLICATE", f"duplicate variable mapping: {name}", name)
            normalized_mappings.add(key)
            if key not in variables:
                self.manifest_error("E_VARIABLE_MISSING", f"mapping references undeclared variable: {name}", name)
                continue
            dtype = variables[key][1]["data_type"].upper()
            width = TYPE_WORDS.get(dtype, 0)
            match = re.fullmatch(r"D(0|[1-9][0-9]*)", address) if isinstance(address, str) else None
            if match is None or not width:
                self.manifest_error("E_ADDRESS", f"{name}: v1 mapped values require a 16/32-bit scalar and D0..D7999 address", name)
                continue
            start = int(match[1])
            if start + width > 8000:
                self.manifest_error("E_ADDRESS", f"{name}: address span exceeds D7999", name)
                continue
            locations[key] = (start, width)
            for address_index in range(start, start + width):
                if address_index in occupied:
                    self.manifest_error("E_ADDRESS_CONFLICT", f"D{address_index} overlaps {occupied[address_index]} and {name}", name)
                occupied[address_index] = name
        self.registers(manifest.get("register_map"), variables, locations)
        source = self.read(task.get("source"))
        if source is not None:
            self.source(source, str(task.get("source")), variables, occupied)
        return self.result

    def initial_value(self, row: dict, path: str, line: int) -> None:
        value, dtype = row["initial_value"], row["data_type"].upper()
        limits = {"INT": (-32768, 32767), "UINT": (0, 65535), "WORD": (0, 65535),
                  "DINT": (-2147483648, 2147483647), "UDINT": (0, 4294967295),
                  "DWORD": (0, 4294967295)}
        try:
            if dtype == "BOOL" and value.upper() not in {"TRUE", "FALSE"}:
                raise ValueError
            if dtype in limits and not limits[dtype][0] <= int(value) <= limits[dtype][1]:
                raise ValueError
            if dtype == "REAL" and (not math.isfinite(float(value)) or abs(float(value)) > 3.402823466e38):
                raise ValueError
        except (ValueError, OverflowError):
            self.error("E_INITIAL_VALUE", f"invalid {dtype} initial value: {value}", path, line)

    def registers(self, path: object, variables: dict, locations: dict) -> None:
        rows = self.rows(path, REGISTER_FIELDS)
        seen, occupied = set(), {}
        for line, row in rows:
            name = row["logical_value"].casefold()
            if name in seen:
                self.error("E_REGISTER_DUPLICATE", f"duplicate logical_value: {row['logical_value']}", str(path), line)
            seen.add(name)
            if name not in variables:
                self.error("E_VARIABLE_MISSING", f"register references undeclared variable: {row['logical_value']}", str(path), line)
            else:
                dtype = variables[name][1]["data_type"].upper()
                if row["encoding"] not in REGISTER_ENCODINGS.get(dtype, set()):
                    self.error("E_REGISTER_ENCODING", f"encoding disagrees with {dtype} profile for {row['logical_value']}", str(path), line)
            match = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", row["holding_registers"])
            try:
                if match is None:
                    raise ValueError
                start = int(match[1])
                count = int(row["register_count"])
                end = int(match[2]) if match[2] else start
                if count <= 0 or end - start + 1 != count or start < 0 or end >= 8000:
                    raise ValueError
            except ValueError:
                self.error("E_REGISTER_RANGE", "invalid D0..D7999 range or register_count", str(path), line)
                continue
            if locations.get(name) != (start, count):
                self.error("E_REGISTER_MAPPING", f"range/type width disagrees with variable_map: {row['logical_value']}", str(path), line)
            if row["direction"] not in {"input", "output"}:
                self.error("E_REGISTER_DIRECTION", "direction must be input or output", str(path), line)
            for index in range(start, end + 1):
                if index in occupied:
                    self.error("E_REGISTER_CONFLICT", f"register {index} overlaps row {occupied[index]}", str(path), line)
                occupied[index] = line
        for name in locations.keys() - seen:
            self.error("E_REGISTER_MISSING", f"mapped variable absent from register contract: {name}", str(path))

    def source(self, text: str, path: str, variables: dict, occupied: dict) -> None:
        # Replace comments/string literals with spaces, preserving all line/column
        # positions. Nested block comments and IEC doubled quotes are understood.
        chars, index = list(text), 0
        while index < len(text):
            start = index
            if text.startswith("//", index):
                end = text.find("\n", index)
                index = len(text) if end == -1 else end
            elif text.startswith("(*", index):
                depth, index = 1, index + 2
                while index < len(text) and depth:
                    if text.startswith("(*", index):
                        depth, index = depth + 1, index + 2
                    elif text.startswith("*)", index):
                        depth, index = depth - 1, index + 2
                    else:
                        index += 1
                if depth:
                    self.error("E_ST_COMMENT", "unterminated block comment", path, *_position(text, start))
            elif text[index] in {"'", '"'}:
                quote, index = text[index], index + 1
                while index < len(text):
                    if text[index] == quote:
                        index += 1
                        if index < len(text) and text[index] == quote:
                            index += 1
                            continue
                        break
                    index += 1
                else:
                    self.error("E_ST_STRING", "unterminated string", path, *_position(text, start))
                self.error("E_ST_PROFILE", "string values are outside the scalar memory-only profile", path, *_position(text, start))
            else:
                index += 1
                continue
            for pos in range(start, index):
                if chars[pos] != "\n":
                    chars[pos] = " "
        clean = "".join(chars)
        token_pattern = r"[A-Za-z_][A-Za-z_0-9]*|(?:[0-9]+\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?|:=|<=|>=|<>|[^\s]"
        tokens = list(re.finditer(token_pattern, clean))
        stack: list[tuple[str, re.Match]] = []
        parens: list[re.Match] = []
        opening = {"IF": "END_IF", "CASE": "END_CASE", "FOR": "END_FOR",
                   "WHILE": "END_WHILE", "REPEAT": "END_REPEAT"}
        closers = set(opening.values())
        for idx, match in enumerate(tokens):
            value, upper = match[0], match[0].upper()
            line, column = _position(text, match.start())
            if value == "(":
                parens.append(match)
            elif value == ")":
                if parens:
                    parens.pop()
                else:
                    self.error("E_ST_PAREN", "unmatched closing parenthesis", path, line, column)
            if upper in opening:
                stack.append((opening[upper], match))
            elif upper in closers:
                if not stack or stack[-1][0] != upper:
                    self.error("E_ST_BLOCK", f"unmatched {upper}", path, line, column)
                else:
                    stack.pop()
            if upper in UNSUPPORTED_TYPES:
                self.error("E_ST_TYPE", "LREAL is unsupported by Easy521 LiteST" if upper == "LREAL" else f"outside checker type profile: {upper}", path, line, column)
            elif upper in DECLARATIONS or upper in TYPE_WORDS:
                self.error("E_ST_BODY", "source must be a MAIN body; declare variables in the CSV", path, line, column)
            elif NAME.fullmatch(value) and upper not in KEYWORDS | BUILTINS:
                direct = re.fullmatch(r"D([0-9]+)", upper)
                if direct:
                    if int(direct[1]) not in occupied:
                        self.error("E_ST_ADDRESS", f"direct register is not declared in variable_map: {value}", path, line, column)
                elif re.fullmatch(r"[XYM][0-9]+", upper):
                    self.error("E_ST_IO", f"{value} is outside the memory-only D profile", path, line, column)
                elif value.casefold() not in variables:
                    self.error("E_ST_VARIABLE", f"undeclared variable or unsupported instruction: {value}", path, line, column)
            elif not NAME.fullmatch(value) and not value[0].isdigit() and value not in {
                    ":=", "<=", ">=", "<>", ";", ":", ",", "(", ")", "+", "-", "*", "/", "=", "<", ">"}:
                self.error("E_ST_TOKEN", f"token outside checker profile: {value}", path, line, column)
            # A variable followed by '(' is a function-block invocation rather
            # than the scalar value supported by this checker.
            if value.casefold() in variables and idx + 1 < len(tokens) and tokens[idx + 1][0] == "(":
                self.error("E_ST_CALL", "function-block instances are outside the scalar profile", path, line, column)
        for expected, match in stack:
            self.error("E_ST_BLOCK", f"missing {expected}", path, *_position(text, match.start()))
        for match in parens:
            self.error("E_ST_PAREN", "unclosed parenthesis", path, *_position(text, match.start()))


def check_project(path: str | Path) -> ProjectCheck:
    """Return structured diagnostics; this function has no external side effects."""
    root = Path(path).expanduser().resolve()
    if root.is_file():
        if root.name != MANIFEST:
            result = ProjectCheck(str(root.parent))
            result.diagnostics.append(Diagnostic("E_PATH", f"expected directory or {MANIFEST}", str(root)))
            return result
        root = root.parent
    return _Checker(root).run()


def render_import_table(path: str | Path) -> str:
    """Render the existing AutoShop ten-column import format; no native writes."""
    result = check_project(path)
    if not result.ok:
        raise ValueError("project check failed; resolve diagnostics before generating import tables")
    root = Path(result.root)
    manifest = result.manifest
    assert manifest is not None
    reader = csv.DictReader(io.StringIO((root / manifest["task"]["variables"]).read_text(encoding="utf-8-sig")))
    rows = [{key: value.strip() for key, value in row.items()} for row in reader]
    mappings = {key.casefold(): value for key, value in manifest["variable_map"].items()}
    output = []
    for index, row in enumerate(rows, 1):
        initial = row["initial_value"].strip()
        if row["data_type"].upper() == "BOOL":
            initial = "ON" if initial.upper() == "TRUE" else "OFF"
        output.append([str(index), row["name"], row["data_type"].upper(), "", initial,
                       "保持" if row["power_failure_retain"] == "Yes" else "不保持", "私有",
                       row["description"], mappings.get(row["name"].casefold(), ""), ""])
    return _csv_text(IMPORT_FIELDS, output)


def write_import_tables(path: str | Path) -> list[Path]:
    """Regenerate only the two derived CSVs in the text project directory."""
    root = Path(path).expanduser().resolve()
    if root.is_file():
        root = root.parent
    text = render_import_table(root)
    paths = [root / "autoshop_global_import.csv", root / "autoshop_global_import_utf16.csv"]
    # Avoid following a pre-existing link when regenerating derived output.
    if any(item.is_symlink() or item.exists() and not item.is_file() for item in paths):
        raise ValueError("derived import paths must be regular files, not symlinks/directories")
    for item, encoding in zip(paths, ("utf-8", "utf-16")):
        item.write_text(text, encoding=encoding, newline="")
    return paths
