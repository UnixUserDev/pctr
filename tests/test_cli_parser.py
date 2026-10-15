"""CLI parser tests — the CLI must import and build its parser on Linux."""

import sys


def test_import_without_pywinauto():
    # On Linux, pywinauto is absent; importing cli must still succeed.
    import pctr.cli as cli
    assert cli is not None


def test_parser_builds_and_has_backend_global():
    import pctr.cli as cli
    ap = cli.build_parser()
    args = ap.parse_args(["--backend", "x11", "size"])
    assert args.backend == "x11"
    assert args.cmd == "size"


def test_parser_input_backend_is_not_global_backend():
    import pctr.cli as cli
    ap = cli.build_parser()
    # The per-command input option must not clobber the platform --backend.
    args = ap.parse_args(["--backend", "x11", "desktop", "list"])
    assert args.backend == "x11"

    args = ap.parse_args(["move", "--x", "1", "--y", "2", "--input-backend", "serial-hid"])
    assert args.input_backend == "serial-hid"


def test_all_subcommands_parse_help():
    import pctr.cli as cli
    ap = cli.build_parser()
    for action in ap._subparsers._group_actions:
        for name, sub in action.choices.items():
            assert sub is not None
