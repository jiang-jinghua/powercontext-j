按 SPEC-011-01 ~ SPEC-011-05 实现，改动集中在分摊、结算、校验与 CLI：

1. allocation 增加 policy="spread" 的尾差逐行轮转，并新增 allocate_with_exclusions 按类目收缩分摊基数；
2. engine.settle 接入类目排除，负实付抛 E_NEG_ALLOC，并记录 unallocated_discount_cents；
3. engine.refund 改为"剩余实付为 0 才退运费"，退款明细负值抛 E_NEG_REFUND；
4. engine.preview_refund / service.preview_refund_file / CLI preview 提供只读预览；
5. 未修改任何测试文件，公开签名保持向后兼容。

```diff
diff --git a/src/settlement/allocation.py b/src/settlement/allocation.py
index 433b4da..4309ab4 100644
--- a/src/settlement/allocation.py
+++ b/src/settlement/allocation.py
@@ -1,10 +1,13 @@
 """金额分摊。
 
-规则（基线版本）：
+规则（REQ-2026-011 之后的版本）：
 1. 以各行应付金额为权重，按比例把待分摊总额拆到行上；
 2. 比例计算使用 :class:`fractions.Fraction`，保证不引入浮点误差；
-3. 每行先向下取整，剩余的分（尾差）一次性给"权重最大"的行，
-   权重相同则给 line_no 最小的行。
+3. 每行先向下取整；尾差（0 ≤ 尾差 < 行数）按 ``policy`` 分配：
+   - ``spread``（默认，SPEC-011-02）：按"权重降序、line_no 升序"逐行 +1 分，单行最多 1 分；
+   - ``largest``：全部给权重最大的单行，仅保留给历史调用方做对照，业务链路不再使用；
+4. 类目排除（SPEC-011-01）在调用侧通过缩小 ``basis`` 实现：
+   被排除行不进 basis，其分摊额恒为 0，份额由剩余行按比例吸收。
 
 该模块被 engine.settle 复用，退款金额完全由这里的分摊结果推导，
 因此分摊正确性直接决定退款正确性。
@@ -17,6 +20,8 @@ from typing import Iterable, Sequence
 
 from .errors import AllocationError
 
+POLICIES = ("spread", "largest")
+
 
 def normalize_basis(basis: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
     """校验并规范化分摊基数：[(line_no, weight_cents)]。"""
@@ -37,13 +42,36 @@ def normalize_basis(basis: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
     return rows
 
 
-def allocate(total_cents: int, basis: Sequence[tuple[int, int]]) -> dict[int, int]:
+def _distribute_remainder(floors: dict[int, int], rows: list[tuple[int, int]], remainder: int, policy: str) -> None:
+    """把尾差分给各行（原地修改 floors）。"""
+    if not remainder:
+        return
+    if policy == "largest":
+        target = max(rows, key=lambda r: (r[1], -r[0]))[0]
+        floors[target] += remainder
+        return
+    if policy == "spread":
+        # 权重降序；权重相同按 line_no 升序，逐行 1 分
+        for line_no, _ in sorted(rows, key=lambda r: (-r[1], r[0]))[:remainder]:
+            floors[line_no] += 1
+        return
+    raise AllocationError("未知的尾差分配策略", {"policy": policy})
+
+
+def allocate(
+    total_cents: int,
+    basis: Sequence[tuple[int, int]],
+    *,
+    policy: str = "spread",
+) -> dict[int, int]:
     """把 ``total_cents`` 按 ``basis`` 权重分摊，返回 {line_no: 分摊额}。
 
     返回值的和严格等于 ``total_cents``。
     """
     if total_cents < 0:
         raise AllocationError("待分摊金额不能为负", {"total_cents": total_cents})
+    if policy not in POLICIES:
+        raise AllocationError("未知的尾差分配策略", {"policy": policy})
     rows = normalize_basis(basis)
     weight_sum = sum(w for _, w in rows)
 
@@ -53,13 +81,41 @@ def allocate(total_cents: int, basis: Sequence[tuple[int, int]]) -> dict[int, in
         floors[line_no] = exact.numerator // exact.denominator
 
     remainder = total_cents - sum(floors.values())
-    if remainder:
-        # 尾差：全部给权重最大的行（权重相同取 line_no 小者）
-        target = max(rows, key=lambda r: (r[1], -r[0]))[0]
-        floors[target] += remainder
+    _distribute_remainder(floors, rows, remainder, policy)
     return floors
 
 
+def allocate_with_exclusions(
+    total_cents: int,
+    entries: Sequence[tuple[int, str, int]],
+    excluded_categories: Iterable[str] = (),
+    *,
+    policy: str = "spread",
+) -> tuple[dict[int, int], int]:
+    """按类目排除后分摊（SPEC-011-01）。
+
+    Args:
+        total_cents: 待分摊总额（优惠券 + 平台补贴）。
+        entries: [(line_no, category, gross_cents)]。
+        excluded_categories: 不参与分摊的类目集合。
+
+    Returns:
+        ``(allocated, unallocated)``：``allocated`` 覆盖所有行（被排除行与无基数行为 0），
+        ``unallocated`` 为无法分摊到行的金额（全部行被排除时等于 ``total_cents``）。
+    """
+    excluded = {c for c in excluded_categories}
+    allocated = {line_no: 0 for line_no, _, _ in entries}
+    if total_cents == 0:
+        return allocated, 0
+    basis = [(ln, gross) for ln, cat, gross in entries if cat not in excluded and gross > 0]
+    if not basis:
+        return allocated, total_cents
+    part = allocate(total_cents, basis, policy=policy)
+    allocated.update(part)
+    unallocated = total_cents - sum(allocated.values())
+    return allocated, unallocated
+
+
 def verify_total(total_cents: int, allocated: dict[int, int]) -> None:
     """自检：分摊结果必须与待分摊金额一致。"""
     got = sum(allocated.values())
diff --git a/src/settlement/cli.py b/src/settlement/cli.py
index 8793286..4462c18 100644
--- a/src/settlement/cli.py
+++ b/src/settlement/cli.py
@@ -34,6 +34,10 @@ def build_parser() -> argparse.ArgumentParser:
     p_refund.add_argument("--order", required=True, help="订单 JSON 路径")
     p_refund.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")
 
+    p_preview = sub.add_parser("preview", help="退款预览：结算 + 退款一次算完，只读不落库")
+    p_preview.add_argument("--order", required=True, help="订单 JSON 路径")
+    p_preview.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")
+
     return parser
 
 
@@ -42,6 +46,8 @@ def main(argv: list[str] | None = None) -> int:
     try:
         if args.command == "settle":
             result = service.settle_file(args.order)
+        elif args.command == "preview":
+            result = service.preview_refund_file(args.order, _parse_lines(args.returned))
         else:
             result = service.refund_file(args.order, _parse_lines(args.returned))
     except SettlementError as exc:
diff --git a/src/settlement/engine.py b/src/settlement/engine.py
index 22693b4..ac0db18 100644
--- a/src/settlement/engine.py
+++ b/src/settlement/engine.py
@@ -4,8 +4,8 @@ from __future__ import annotations
 
 from typing import Iterable, Sequence
 
-from .allocation import allocate, verify_total
-from .errors import RefundError
+from .allocation import allocate_with_exclusions
+from .errors import NegativeAllocationError, RefundError
 from .models import (
     Coupon,
     Order,
@@ -15,7 +15,7 @@ from .models import (
     SettledLine,
     Settlement,
 )
-from .validators import validate_order, validate_return_lines
+from .validators import validate_order, validate_refund_amounts, validate_return_lines
 
 
 def build_order(payload: dict) -> Order:
@@ -37,6 +37,7 @@ def build_order(payload: dict) -> Order:
             type=str(coupon_raw["type"]),
             value=int(coupon_raw["value"]),
             min_spend_cents=int(coupon_raw.get("min_spend_cents", 0)),
+            excluded_categories=tuple(coupon_raw.get("excluded_categories") or ()),
         )
         if coupon_raw
         else None
@@ -53,7 +54,8 @@ def build_order(payload: dict) -> Order:
 def settle(order: Order) -> Settlement:
     """结算整单：计算每行实付金额。
 
-    待分摊总额 = 优惠券金额 + 平台补贴，按行应付金额比例分摊。
+    待分摊总额 = 优惠券金额 + 平台补贴；按行应付金额比例分摊到"未被排除类目"的行，
+    尾差按 SPEC-011-02 逐行轮转。被排除行分摊额恒为 0。
     """
     validate_order(order)
     gross_total = order.gross_cents
@@ -61,9 +63,13 @@ def settle(order: Order) -> Settlement:
     coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
     subsidy = order.platform_subsidy_cents
     to_allocate = coupon_discount + subsidy
+    excluded = order.coupon.excluded_categories if order.coupon else ()
 
-    allocated = allocate(to_allocate, [(it.line_no, it.gross_cents) for it in order.items])
-    verify_total(to_allocate, allocated)
+    allocated, unallocated = allocate_with_exclusions(
+        to_allocate,
+        [(it.line_no, it.category, it.gross_cents) for it in order.items],
+        excluded,
+    )
 
     lines = [
         SettledLine(
@@ -76,13 +82,27 @@ def settle(order: Order) -> Settlement:
         )
         for it in order.items
     ]
+    for ln in lines:
+        if ln.paid_cents < 0:
+            raise NegativeAllocationError(
+                "分摊后行实付为负，拒绝结算",
+                {
+                    "order_id": order.order_id,
+                    "line_no": ln.line_no,
+                    "gross_cents": ln.gross_cents,
+                    "discount_cents": ln.discount_cents,
+                    "paid_cents": ln.paid_cents,
+                },
+            )
+
     return Settlement(
         order_id=order.order_id,
         gross_cents=gross_total,
         coupon_discount_cents=coupon_discount,
         subsidy_cents=subsidy,
         shipping_fee_cents=order.shipping_fee_cents,
-        paid_total_cents=sum(ln.paid_cents for ln in lines) + order.shipping_fee_cents,
+        paid_total_cents=sum(ln.paid_cents for ln in lines) + order.shipping_fee_cents - unallocated,
+        unallocated_discount_cents=unallocated,
         lines=lines,
     )
 
@@ -94,9 +114,9 @@ def refund(
 ) -> RefundResult:
     """按已结算结果计算部分退货的退款金额。
 
-    规则（基线版本）：
+    规则（SPEC-011-03 起）：
     - 退货行的退款额 = 该行实付金额；
-    - 运费仅在"整单全部退货"时退还。
+    - 剩余实付（未退货行实付合计）为 0 时退还全额运费，否则不退（全退场景天然满足）。
     """
     returned = validate_return_lines(order, returned_line_nos)
     lines = [
@@ -107,13 +127,23 @@ def refund(
         )
         for ln in returned
     ]
-    all_returned = set(returned) == set(order.line_nos)
-    shipping = order.shipping_fee_cents if all_returned else 0
     if not lines:
         raise RefundError("退货行不能为空", {"order_id": order.order_id})
+    validate_refund_amounts(order.order_id, lines)
+
+    returned_set = set(returned)
+    remaining_paid = sum(
+        line.paid_cents for line in settlement.lines if line.line_no not in returned_set
+    )
+    shipping = order.shipping_fee_cents if remaining_paid == 0 else 0
     return RefundResult(
         order_id=order.order_id,
         returned_line_nos=tuple(returned),
         lines=lines,
         shipping_refund_cents=shipping,
     )
+
+
+def preview_refund(order: Order, returned_line_nos: Sequence[int] | Iterable[int]) -> RefundResult:
+    """退款预览（SPEC-011-05）：内部完成结算 + 退款，只读、无副作用。"""
+    return refund(order, settle(order), returned_line_nos)
diff --git a/src/settlement/errors.py b/src/settlement/errors.py
index 10cbdcd..b18be22 100644
--- a/src/settlement/errors.py
+++ b/src/settlement/errors.py
@@ -42,3 +42,21 @@ class RefundError(SettlementError):
     """退款流程不成立（例如退货行号不存在）。"""
 
     code = "E_REFUND"
+
+
+class RefundAmountError(SettlementError):
+    """金额为负：分摊或退款结果出现负值，属于规则冲突，必须显式失败。"""
+
+    code = "E_AMOUNT_NEGATIVE"
+
+
+class NegativeAllocationError(RefundAmountError):
+    """分摊后行实付为负（SPEC-011-04）。"""
+
+    code = "E_NEG_ALLOC"
+
+
+class NegativeRefundError(RefundAmountError):
+    """退款明细金额为负（SPEC-011-04）。"""
+
+    code = "E_NEG_REFUND"
diff --git a/src/settlement/models.py b/src/settlement/models.py
index b4deb35..3f0f3bf 100644
--- a/src/settlement/models.py
+++ b/src/settlement/models.py
@@ -31,6 +31,7 @@ class Coupon:
     type: str
     value: int
     min_spend_cents: int = 0
+    excluded_categories: tuple[str, ...] = ()
 
     def discount_cents(self, gross_cents: int) -> int:
         """按券面规则计算优惠金额（不含分摊）。"""
@@ -92,6 +93,7 @@ class Settlement:
     subsidy_cents: int
     shipping_fee_cents: int
     paid_total_cents: int
+    unallocated_discount_cents: int = 0
     lines: list[SettledLine] = field(default_factory=list)
 
     @property
@@ -113,6 +115,7 @@ class Settlement:
             "shipping_fee_cents": self.shipping_fee_cents,
             "discount_total_cents": self.discount_total_cents,
             "paid_total_cents": self.paid_total_cents,
+            "unallocated_discount_cents": self.unallocated_discount_cents,
             "lines": [
                 {
                     "line_no": ln.line_no,
diff --git a/src/settlement/service.py b/src/settlement/service.py
index 678a49e..787c73c 100644
--- a/src/settlement/service.py
+++ b/src/settlement/service.py
@@ -41,3 +41,8 @@ def refund_file(path: str | Path, returned_line_nos: Sequence[int]) -> dict:
     order = load_order(path)
     settlement = settle_order(order)
     return refund_order(order, settlement, returned_line_nos).to_dict()
+
+
+def preview_refund_file(path: str | Path, returned_line_nos: Sequence[int]) -> dict:
+    """退款预览：不依赖调用方先持有 Settlement（SPEC-011-05）。"""
+    return engine.preview_refund(load_order(path), returned_line_nos).to_dict()
diff --git a/src/settlement/validators.py b/src/settlement/validators.py
index 49b629c..325f81b 100644
--- a/src/settlement/validators.py
+++ b/src/settlement/validators.py
@@ -7,8 +7,8 @@ from __future__ import annotations
 
 from typing import Iterable, Sequence
 
-from .errors import RefundError, ValidationError
-from .models import Order
+from .errors import NegativeRefundError, RefundError, ValidationError
+from .models import Order, RefundLine
 
 
 def validate_order(order: Order) -> None:
@@ -58,3 +58,18 @@ def validate_return_lines(order: Order, returned_line_nos: Sequence[int] | Itera
     if not result:
         raise RefundError("退货行不能为空", {"order_id": order.order_id})
     return result
+
+
+def validate_refund_amounts(order_id: str, lines: Sequence[RefundLine]) -> None:
+    """退款金额校验（SPEC-011-04）：出现负值必须显式失败，禁止静默夹断。"""
+    for ln in lines:
+        if ln.refund_cents < 0:
+            raise NegativeRefundError(
+                "退款金额为负，拒绝生成退款单",
+                {
+                    "order_id": order_id,
+                    "line_no": ln.line_no,
+                    "sku": ln.sku,
+                    "refund_cents": ln.refund_cents,
+                },
+            )
```
