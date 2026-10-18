"""Restricted source preprocessing contracts; no GUI/device or vendor executable."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('prepare_vendor_st', Path(__file__).parents[1] / 'prepare_vendor_st.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def declaration(values=None):
    return json.dumps({'schema': prepare.SOURCE_SCHEMA, 'globals': values or {'COUNTER': 'DINT'}}).encode()


def inputs(tmp_path, body='COUNTER := COUNTER + 1;\n', declarations=None):
    source, globals_path = tmp_path / 'source.st', tmp_path / 'globals.json'
    source.write_text(body)
    globals_path.write_bytes(declaration(declarations))
    return source, globals_path, tmp_path / 'intermediate'


def test_prepares_fresh_bound_container_and_empty_cl(tmp_path):
    source, globals_path, output = inputs(tmp_path)
    result = prepare.prepare_vendor_st(source, globals_path, output)
    assert result['ok'] is True and result['status'] == 'prepared'
    assert result['scope'] == 'vendor-main-intermediate-only'
    assert result['source_sha256'] == prepare.sha(source.read_bytes())
    assert result['globals_sha256'] == prepare.sha(globals_path.read_bytes())
    assert (output / 'MAIN.st').read_bytes().startswith(prepare.HEADER)
    assert (output / 'MAIN.cl').read_bytes() == b''
    assert result['files'][0]['sha256'] == prepare.sha((output / 'MAIN.st').read_bytes())


def test_known_container_byte_vector_matches_vendor_reader():
    assert prepare.encode_sttemp('FUNCTION')[32:] == bytes.fromhex('a9c1a3b2bebdc0af')


def test_comment_pseudo_addresses_and_declarations_are_ignored():
    body = "(* M8002 %QX0.0 FUNCTION 'D100' (* nested *) *)\nCOUNTER := COUNTER + 1; // X0 VAR_GLOBAL\n"
    result = prepare.intermediate_text(body, {'COUNTER': 'DINT'})
    assert body.splitlines()[0] in result


def test_string_pseudo_address_is_not_misreported_as_device():
    with pytest.raises(ValueError, match='String literals'):
        prepare.intermediate_text("COUNTER := 'M8002 %IX0.0 FUNCTION';", {'COUNTER': 'DINT'})


def test_real_exponents_and_bool_control_are_accepted():
    body = 'IF ENABLE THEN VALUE := VALUE + 1.0E-3; ELSE VALUE := -0.5; END_IF;'
    text = prepare.intermediate_text(body, {'ENABLE': 'BOOL', 'VALUE': 'REAL'})
    assert 'VALUE:REAL;' in text and '\r\n' in text


@pytest.mark.parametrize('body', [
    'M8002 := TRUE;', '%QX0.0 := TRUE;', 'COUNTER := UNKNOWN + 1;',
    'COUNTER := ABS(COUNTER);', 'PROGRAM MAIN COUNTER := 1; END_PROGRAM',
    'VAR x:DINT; END_VAR COUNTER := 1;', 'COUNTER[0] := 1;',
    'COUNTER.field := 1;', 'COUNTER := 16#FF;', '(* not closed', "COUNTER := 'not closed",
])
def test_unsupported_inputs_fail_before_creating_output(tmp_path, body):
    source, globals_path, output = inputs(tmp_path, body)
    result = prepare.prepare_vendor_st(source, globals_path, output)
    assert result['ok'] is False and not output.exists()


@pytest.mark.parametrize('values', [
    {'COUNTER':'STRING'}, {'COUNTER':'DINT','counter':'DINT'}, {'M8002':'BOOL'},
    {'MAIN':'DINT'}, {'_HC_FN':'DINT'}, {'BAD;NAME':'DINT'}, {'COUNTER':True},
])
def test_unsupported_or_ambiguous_globals_are_rejected(values):
    with pytest.raises(ValueError):
        prepare.parse_globals(declaration(values))


def test_duplicate_json_keys_are_rejected():
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        prepare.parse_globals(b'{"schema":"plcidf.vendor-main-source.v1","globals":{"X":"DINT","X":"BOOL"}}')


def test_existing_output_is_never_overwritten(tmp_path):
    source, globals_path, output = inputs(tmp_path)
    output.mkdir(); sentinel = output / 'MAIN.st'; sentinel.write_bytes(b'keep')
    result = prepare.prepare_vendor_st(source, globals_path, output)
    assert result['ok'] is False and sentinel.read_bytes() == b'keep'


def test_symlink_input_is_rejected(tmp_path):
    source, globals_path, output = inputs(tmp_path)
    alias = tmp_path / 'alias.st'; alias.symlink_to(source)
    result = prepare.prepare_vendor_st(alias, globals_path, output)
    assert result['ok'] is False and not output.exists()


def test_source_change_during_preparation_does_not_report_prepared(tmp_path, monkeypatch):
    source, globals_path, output = inputs(tmp_path)
    original = prepare.read_file
    calls = 0
    def changed(path):
        nonlocal calls
        if Path(path) == source:
            calls += 1
            if calls > 1:
                source.write_text('COUNTER := 9;')
        return original(path)
    monkeypatch.setattr(prepare, 'read_file', changed)
    result = prepare.prepare_vendor_st(source, globals_path, output)
    assert result['ok'] is False
    assert 'changed' in result['diagnostics'][0]['message']


def test_implicit_statement_list_runtime_declaration_precedes_globals():
    text = prepare.intermediate_text('COUNTER := COUNTER + 1; COUNTER := COUNTER + 2;', {'COUNTER':'DINT'})
    assert text.startswith('FUNCTION _HC_EMPTYFUC : VOID\r\nEND_FUNCTION\r\nVAR_GLOBAL\r\n')
    assert text.count('FUNCTION _HC_EMPTYFUC : VOID') == 1


def test_mixed_type_if_receives_implicit_runtime_declaration():
    variables = {'FLAG':'BOOL','SMALL':'INT','COUNTER':'DINT','VALUE':'REAL'}
    body = 'FLAG := NOT FLAG; SMALL := SMALL + 1; COUNTER := COUNTER + 1; VALUE := VALUE + 0.5; IF VALUE > 10 THEN FLAG := FALSE; END_IF;'
    text = prepare.intermediate_text(body, variables)
    assert 'FUNCTION _HC_EMPTYFUC : VOID\r\nEND_FUNCTION\r\n' in text
    for name, type_name in variables.items():
        assert name + ':' + type_name + ';' in text
