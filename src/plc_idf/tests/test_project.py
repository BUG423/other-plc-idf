"""Offline text-project contracts, with negative inputs and no PLC access."""

import csv
import io
import json
from pathlib import Path

import pytest

from plc_idf.project import (
    IMPORT_FIELDS, MANIFEST, check_project, create_project, render_import_table,
    write_import_tables,
)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "hello_plc"
    result = create_project(root)
    assert result.ok, result.as_dict()
    return root


def edit_manifest(root: Path, edit):
    path = root / MANIFEST
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value, indent=2))


def edit_csv(root: Path, filename: str, edit):
    path = root / filename
    reader = csv.DictReader(io.StringIO(path.read_text()))
    headers, rows = reader.fieldnames, list(reader)
    edit(rows)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, headers)
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(text.getvalue())


def codes(root):
    return {item.code for item in check_project(root).diagnostics}


def test_scaffold_has_pinned_contract_and_true_capability_labels(project):
    result = check_project(project / MANIFEST)
    assert result.ok
    assert result.manifest["target"]["model"] == "Easy521-0808TN"
    assert result.manifest["toolchain"]["autoshop"] == "4.11.0.5"
    assert result.manifest["task"]["cycle_ms"] == 5
    assert result.manifest["variable_map"] == {"g_iScanCounter": "D700", "g_iHeartbeat": "D701"}
    assert set(result.source_hashes) == {MANIFEST, "MAIN_body.st", "MAIN_variables.csv", "modbus_register_map.csv"}
    assert result.as_dict()["compiled"] is False
    assert result.as_dict()["native_project_created"] is False
    assert result.as_dict()["validation"] == "offline-static-check"
    assert len(list(project.glob("*.hcp"))) == 0


def test_scaffold_import_headers_match_existing_real_asset(project):
    repo_root = Path(__file__).resolve().parents[3]
    existing = (repo_root / "plc/easy521/autoshop_global_import.csv").read_text(encoding="utf-8-sig")
    assert next(csv.reader(io.StringIO(existing))) == list(IMPORT_FIELDS)
    text = (project / "autoshop_global_import_utf16.csv").read_text(encoding="utf-16")
    assert text.splitlines() == (project / "autoshop_global_import.csv").read_text().splitlines()
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1][8] == "D700"
    assert (project / "autoshop_global_import_utf16.csv").read_bytes().startswith(b"\xff\xfe")


def test_init_refuses_overwrite_and_preserves_original(project):
    original = (project / "MAIN_body.st").read_bytes()
    with pytest.raises(FileExistsError):
        create_project(project)
    assert (project / "MAIN_body.st").read_bytes() == original


@pytest.mark.parametrize("name,cycle", [("bad name", 5), ("", 5), ("ok", 0), ("ok", True)])
def test_bad_creation_arguments_have_no_partial_files(tmp_path, name, cycle):
    root = tmp_path / "project"
    with pytest.raises(ValueError):
        create_project(root, name=name, cycle_ms=cycle)
    assert not root.exists()


def test_init_accepts_existing_empty_directory(tmp_path):
    assert create_project(tmp_path, name="empty").ok


@pytest.mark.parametrize("change,expected", [
    (lambda m: m.update(schema_version=True), "E_MANIFEST"),
    (lambda m: m["target"].update(model="OtherPLC"), "E_TARGET"),
    (lambda m: m["toolchain"].pop("autoshop"), "E_TOOLCHAIN"),
    (lambda m: m["task"].update(cycle_ms=False), "E_TASK"),
    (lambda m: m["task"].update(source="/etc/passwd"), "E_PATH"),
    (lambda m: m["task"].update(source="../outside.st"), "E_PATH"),
    (lambda m: m["task"].update(source="missing.st"), "E_FILE"),
    (lambda m: m.update(variable_map=[]), "E_MAPPING"),
    (lambda m: m["variable_map"].update(unknown="D900"), "E_VARIABLE_MISSING"),
    (lambda m: m["variable_map"].update(G_ISCANCOUNTER="D900"), "E_MAPPING_DUPLICATE"),
    (lambda m: m["variable_map"].update(g_iHeartbeat="D700"), "E_ADDRESS_CONFLICT"),
    (lambda m: m["variable_map"].update(g_iHeartbeat="Y0"), "E_ADDRESS"),
    (lambda m: m["variable_map"].update(g_iHeartbeat="D8000"), "E_ADDRESS"),
])
def test_manifest_bad_inputs(project, change, expected):
    edit_manifest(project, change)
    assert expected in codes(project)
    diagnostic = next(item for item in check_project(project).diagnostics if item.code == expected)
    assert diagnostic.line >= 1
    assert diagnostic.column >= 1


@pytest.mark.parametrize("text", ["[", "[]", '{"name":"a","name":"b"}', "null"])
def test_invalid_manifest_is_a_diagnostic_not_traceback(project, text):
    (project / MANIFEST).write_text(text)
    assert not check_project(project).ok


def test_symlink_source_cannot_escape_project(project, tmp_path):
    outside = tmp_path / "outside.st"
    outside.write_text("secret")
    (project / "MAIN_body.st").unlink()
    (project / "MAIN_body.st").symlink_to(outside)
    result = check_project(project)
    assert "E_PATH" in {item.code for item in result.diagnostics}
    assert "MAIN_body.st" not in result.source_hashes


def test_lreal_in_variable_csv_has_file_and_row(project):
    edit_csv(project, "MAIN_variables.csv", lambda rows: rows[1].update(data_type="LREAL"))
    item = next(item for item in check_project(project).diagnostics if item.code == "E_TYPE")
    assert item.path == "MAIN_variables.csv"
    assert item.line == 3
    assert "LREAL" in item.message


@pytest.mark.parametrize("change,expected", [
    (lambda rows: rows.append(dict(rows[0], name="G_ISCANCOUNTER")), "E_VARIABLE_DUPLICATE"),
    (lambda rows: rows[0].update(name="IF"), "E_VARIABLE_NAME"),
    (lambda rows: rows[0].update(scope="FB instance"), "E_SCOPE"),
    (lambda rows: rows[0].update(initial_value="32768"), "E_INITIAL_VALUE"),
    (lambda rows: rows[0].update(power_failure_retain="false"), "E_RETAIN"),
])
def test_variable_errors(project, change, expected):
    edit_csv(project, "MAIN_variables.csv", change)
    assert expected in codes(project)


def test_32bit_address_span_overlap_is_detected(project):
    edit_csv(project, "MAIN_variables.csv", lambda rows: rows[0].update(data_type="DINT"))
    assert "E_ADDRESS_CONFLICT" in codes(project)
    edit_manifest(project, lambda m: m["variable_map"].update(g_iScanCounter="D7999"))
    assert "E_ADDRESS" in codes(project)


@pytest.mark.parametrize("change,expected", [
    (lambda rows: rows[0].update(logical_value="unknown"), "E_VARIABLE_MISSING"),
    (lambda rows: rows[0].update(holding_registers="700-701"), "E_REGISTER_RANGE"),
    (lambda rows: rows[0].update(register_count="garbage"), "E_REGISTER_RANGE"),
    (lambda rows: rows[0].update(holding_registers="7999-8000", register_count="2"), "E_REGISTER_RANGE"),
    (lambda rows: rows[1].update(holding_registers="700"), "E_REGISTER_CONFLICT"),
    (lambda rows: rows[0].update(direction="physical_output"), "E_REGISTER_DIRECTION"),
    (lambda rows: rows[0].update(encoding="IEEE-754 binary32 big-endian ABCD"), "E_REGISTER_ENCODING"),
    (lambda rows: rows.pop(), "E_REGISTER_MISSING"),
    (lambda rows: rows.append(dict(rows[0])), "E_REGISTER_DUPLICATE"),
])
def test_register_contract_errors(project, change, expected):
    edit_csv(project, "modbus_register_map.csv", change)
    assert expected in codes(project)


def test_csv_header_and_row_mismatches(project):
    p = project / "MAIN_variables.csv"
    p.write_text("wrong,header\na,b\n")
    assert "E_CSV_HEADER" in codes(project)
    p.write_text("scope,name,data_type,initial_value,power_failure_retain,description\nGlobal,foo\n")
    assert "E_CSV_ROW" in codes(project)


def test_csv_blank_lines_keep_error_position_and_bad_quotes_fail(project):
    p = project / "MAIN_variables.csv"
    header = "scope,name,data_type,initial_value,power_failure_retain,description\n"
    p.write_text(header + "\nGlobal,missing_columns\n")
    item = next(item for item in check_project(project).diagnostics if item.code == "E_CSV_ROW")
    assert item.line == 3
    p.write_text(header + 'Global,foo,INT,0,No,"unclosed\n')
    assert "E_CSV" in codes(project)


@pytest.mark.parametrize("source,expected,line,column", [
    ("(* ok *)\nIF LREAL > 0 THEN\nEND_IF;", "E_ST_TYPE", 2, 4),
    ("\n  missing := 1;", "E_ST_VARIABLE", 2, 3),
    ("IF TRUE THEN\n g_iHeartbeat := 1;", "E_ST_BLOCK", 1, 1),
    ("END_IF;", "E_ST_BLOCK", 1, 1),
    ("(* unclosed", "E_ST_COMMENT", 1, 1),
    ("g_iHeartbeat := 'bad", "E_ST_STRING", 1, 17),
    ("Y0 := TRUE;", "E_ST_IO", 1, 1),
    ("D7999 := 1;", "E_ST_ADDRESS", 1, 1),
    ("VAR x : INT; END_VAR", "E_ST_BODY", 1, 1),
    ("g_iHeartbeat := [1];", "E_ST_TOKEN", 1, 17),
    ("g_iHeartbeat();", "E_ST_CALL", 1, 1),
    ("g_iHeartbeat := ABS(1;", "E_ST_PAREN", 1, 20),
    ("g_iHeartbeat := 1);", "E_ST_PAREN", 1, 18),
])
def test_source_errors_have_exact_position(project, source, expected, line, column):
    (project / "MAIN_body.st").write_text(source)
    item = next(item for item in check_project(project).diagnostics if item.code == expected)
    assert (item.path, item.line, item.column) == ("MAIN_body.st", line, column)


def test_nested_comments_and_exponent_literals_do_not_create_fake_variables(project):
    (project / "MAIN_body.st").write_text("(* LREAL unknown (* nested *) *)\n// Y0 := TRUE\ng_iHeartbeat := ABS(-1e-3);\n")
    assert check_project(project).ok


def test_check_does_not_modify_any_project_file(project):
    before = {p.name: p.read_bytes() for p in project.iterdir()}
    result = check_project(project)
    assert result.ok
    assert before == {p.name: p.read_bytes() for p in project.iterdir()}


def test_source_hash_changes_when_st_changes(project):
    first = check_project(project).source_hashes["MAIN_body.st"]
    with (project / "MAIN_body.st").open("a") as out:
        out.write("\n(* change *)\n")
    assert check_project(project).source_hashes["MAIN_body.st"] != first


def test_import_regeneration_uses_updated_mapping(project):
    edit_manifest(project, lambda m: m["variable_map"].update(g_iHeartbeat="D702"))
    edit_csv(project, "modbus_register_map.csv", lambda rows: rows[1].update(holding_registers="702"))
    write_import_tables(project)
    assert "D702" in render_import_table(project)
    assert "D702" in (project / "autoshop_global_import_utf16.csv").read_text(encoding="utf-16")


def test_import_generation_normalizes_checked_csv_whitespace(project):
    edit_csv(project, "MAIN_variables.csv", lambda rows: rows[0].update(name=" g_iScanCounter ", data_type=" INT "))
    rows = list(csv.reader(io.StringIO(render_import_table(project))))
    assert rows[1][1] == "g_iScanCounter"
    assert rows[1][2] == "INT"
    assert rows[1][8] == "D700"


def test_real_mapping_width_and_declared_encoding_can_be_checked(project):
    edit_csv(project, "MAIN_variables.csv", lambda rows: rows[1].update(data_type="REAL", initial_value="0.0"))
    edit_csv(project, "modbus_register_map.csv", lambda rows: rows[1].update(
        holding_registers="701-702", register_count="2", encoding="IEEE-754 binary32 word-order-unverified"))
    assert check_project(project).ok


def test_import_generation_refuses_bad_project_and_symlink_outputs(project, tmp_path):
    outside = tmp_path / "do_not_change.csv"
    outside.write_text("original")
    (project / "autoshop_global_import.csv").unlink()
    (project / "autoshop_global_import.csv").symlink_to(outside)
    with pytest.raises(ValueError, match="regular files"):
        write_import_tables(project)
    assert outside.read_text() == "original"
    (project / "MAIN_body.st").write_text("broken := 1;")
    with pytest.raises(ValueError, match="project check failed"):
        render_import_table(project)
