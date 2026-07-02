import difflib
import pytest
import debgraph
import pathlib
import os
from . import make_fixture
import subprocess
import tempfile


def test_parser():
    tests = [
        [
            "apt-transport-https (= 3.2.0)",
            {"name": "apt-transport-https", "op": "=", "version": "3.2.0"},
        ],
        ["libappstream5 (= 1)", {"name": "libappstream5", "op": "=", "version": "1"}],
    ]
    for s, expected in tests:
        assert debgraph.Package._parse_package_ref(s).__dict__ == expected


def _assert_files_equal(file1, file2):
    diff = list(difflib.unified_diff(file1.readlines(), file2.readlines()))
    assert diff == [], "".join(diff)


def _test_fixture(fixture: str, *extra_args: str):
    with tempfile.TemporaryDirectory() as temp_dir:
        in_fixture, out_fixtures = make_fixture.get_fixtures(fixture)

        with open(in_fixture, "r") as in_file:
            s = in_file.read()

        for expected_fixture, _ in out_fixtures:
            actual_fixture = os.path.join(temp_dir, os.path.split(expected_fixture)[1])

            debgraph.run_debgraph(
                debgraph.Options(
                    use_fixed_dates=True,
                    override_input_stream=s,
                    argv=[actual_fixture, *extra_args],
                )
            )

            with open(actual_fixture, "r") as actual, open(
                expected_fixture, "r"
            ) as expected:
                _assert_files_equal(actual, expected)


def test_wsl():
    _test_fixture("wsl")
    _test_fixture("wsl_long", "--long")


def test_version():
    e = None
    try:
        debgraph.run_debgraph(debgraph.Options(argv=["--version"]))
    except debgraph.DebgraphError as actual:
        e = actual

    assert e != None and e.message == "Debgraph 0.2.0"
