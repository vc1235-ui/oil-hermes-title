"""CLI 参数解析的回归测试。

活体验证时发现的 bug：``--session`` 只挂在父 parser 上，
``hermes oil-title lock --session X`` 会被 argparse 拒绝（README/SKILL 里就是这么写的）。
父 parser 与每个子命令都要接受它，且子命令不能把父级已解析的值清空。
"""

from __future__ import annotations

import argparse

from oht_cli import _setup_argparse


def _parser():
    parser = argparse.ArgumentParser(prog="hermes")
    subs = parser.add_subparsers(dest="command")
    oht = subs.add_parser("oil-title")
    _setup_argparse(oht)
    return parser


def test_session_before_subcommand():
    args = _parser().parse_args(["oil-title", "--session", "S1", "lock"])
    assert args.session == "S1"
    assert args.oil_title_command == "lock"


def test_session_after_subcommand():
    args = _parser().parse_args(["oil-title", "lock", "--session", "S2"])
    assert args.session == "S2"
    assert args.oil_title_command == "lock"


def test_session_default_and_other_flags():
    args = _parser().parse_args(["oil-title", "status"])
    assert args.session == ""
    args = _parser().parse_args(["oil-title", "usage", "--days", "3", "--json"])
    assert (args.days, args.json) == (3, True)
    args = _parser().parse_args(["oil-title", "rename", "--session", "S3", "新", "标题"])
    assert (args.session, args.title) == ("S3", ["新", "标题"])
    args = _parser().parse_args(["oil-title", "preview"])
    assert args.json is False
