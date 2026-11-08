"""业务命令行入口。

用法：
    python -m settlement.cli settle --order tests/fixtures/order_standard.json
    python -m settlement.cli refund --order tests/fixtures/order_standard.json --return 1,2
    python -m settlement.cli preview --order tests/fixtures/order_standard.json --return 1,2

输出统一为 JSON（stdout），错误为 {"error_code": ..., "message": ...} 且退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import service
from .errors import SettlementError


def _parse_lines(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="settlement", description="订单退款结算引擎 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_settle = sub.add_parser("settle", help="结算整单，输出每行实付金额")
    p_settle.add_argument("--order", required=True, help="订单 JSON 路径")

    p_refund = sub.add_parser("refund", help="计算部分退货的退款金额")
    p_refund.add_argument("--order", required=True, help="订单 JSON 路径")
    p_refund.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")

    p_preview = sub.add_parser("preview", help="只读预览退款金额")
    p_preview.add_argument("--order", required=True, help="订单 JSON 路径")
    p_preview.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "settle":
            result = service.settle_file(args.order)
        elif args.command == "refund":
            result = service.refund_file(args.order, _parse_lines(args.returned))
        else:
            result = service.preview_refund_file(
                args.order, _parse_lines(args.returned)
            )
    except SettlementError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
