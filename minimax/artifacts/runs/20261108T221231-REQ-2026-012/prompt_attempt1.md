你是一名严格的 Python 后端工程师，遵循“最小改动 + 不破坏既有契约”的工程准则。
本次以**整文件模式**交付：对每个需要改动的文件，输出一个完整可运行的新版本。

要求：
1. 每个文件一个代码块，格式严格为 ```` ```file path=<仓库相对路径> ````，块内是该文件的**完整内容**；
2. 只输出需要改动的文件，未改动的文件不要输出；
3. 不得修改 tests/** 下任何文件；保持既有公开签名与返回结构（只允许新增可选参数或字段）；
4. 金额计算必须整数分、不得引入浮点误差；
5. 代码必须自洽可导入，不要写省略号、伪代码或 TODO。

# 任务
REQ-2026-012 · 退款结果暴露剩余实付（规格 v2 追加项）

## 需求原文
规格已更新到 v2，追加 SPEC-011-06：退款结果需要暴露剩余实付金额 remaining_paid_cents。请沿用 REQ-2026-011 的上下文与已保存状态继续实现，不得回退已完成的 SPEC-011-01 ~ 05，也不得修改任何测试文件。

## 必须实现的规格条目（来自 seekdb 全文检索，版本 2026-11-08T10:30+08:00）
- SPEC-011-06（spec/REQ-2026-011.md，版本 2026-11-08T10:30+08:00）

## 上下文包（含目标文件全文；补丁必须基于真实内容构造 hunk，行号与缩进以它为准）
# 任务上下文包 REQ-2026-012 · 退款结果暴露剩余实付（规格 v2 追加项）

- 生成时间：2026-11-08T22:12:32+08:00
- 规格版本：2026-11-08T10:30+08:00
- 召回证据：21 个片段

## 0. 目标文件全文

> 生成补丁必须基于下面这些真实内容构造 hunk，不要臆测行号或省略上下文。

### `src/settlement/models.py`（4730 字节）

```python
"""结算领域模型。

金额字段一律是 int 分，字段名统一以 ``_cents`` 结尾，避免单位歧义。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderItem:
    """订单行项目。"""

    line_no: int
    sku: str
    category: str
    unit_price_cents: int
    qty: int

    @property
    def gross_cents(self) -> int:
        return self.unit_price_cents * self.qty


@dataclass(frozen=True)
class Coupon:
    """优惠券。当前仅支持满减（amount）与折扣（percent）两种。"""

    code: str
    type: str
    value: int
    min_spend_cents: int = 0
    excluded_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "excluded_categories",
            tuple(str(category) for category in self.excluded_categories),
        )

    def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
            off = gross_cents * (100 - self.value) // 100
            return max(0, min(off, gross_cents))
        raise ValueError(f"unknown coupon type: {self.type}")


@dataclass(frozen=True)
class Order:
    """订单。"""

    order_id: str
    items: tuple[OrderItem, ...]
    coupon: Coupon | None = None
    platform_subsidy_cents: int = 0
    shipping_fee_cents: int = 0

    @property
    def gross_cents(self) -> int:
        return sum(i.gross_cents for i in self.items)

    def item(self, line_no: int) -> OrderItem:
        for it in self.items:
            if it.line_no == line_no:
                return it
        raise KeyError(f"line_no {line_no} not found")

    @property
    def line_nos(self) -> tuple[int, ...]:
        return tuple(i.line_no for i in self.items)


@dataclass
class SettledLine:
    """结算后的行明细。"""

    line_no: int
    sku: str
    category: str
    gross_cents: int
    discount_cents: int
    paid_cents: int


@dataclass
class Settlement:
    """整单结算结果。"""

    order_id: str
    gross_cents: int
    coupon_discount_cents: int
    subsidy_cents: int
    shipping_fee_cents: int
    paid_total_cents: int
    lines: list[SettledLine] = field(default_factory=list)
    unallocated_discount_cents: int = 0

    @property
    def discount_total_cents(self) -> int:
        return self.coupon_discount_cents + self.subsidy_cents

    def line(self, line_no: int) -> SettledLine:
        for ln in self.lines:
            if ln.line_no == line_no:
                return ln
        raise KeyError(f"line_no {line_no} not found")

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "gross_cents": self.gross_cents,
            "coupon_discount_cents": self.coupon_discount_cents,
            "subsidy_cents": self.subsidy_cents,
            "shipping_fee_cents": self.shipping_fee_cents,
            "discount_total_cents": self.discount_total_cents,
            "paid_total_cents": self.paid_total_cents,
            "unallocated_discount_cents": self.unallocated_discount_cents,
            "lines": [
                {
                    "line_no": ln.line_no,
                    "sku": ln.sku,
                    "category": ln.category,
                    "gross_cents": ln.gross_cents,
                    "discount_cents": ln.discount_cents,
                    "paid_cents": ln.paid_cents,
                }
                for ln in self.lines
            ],
        }


@dataclass
class RefundLine:
    """退款明细行。"""

    line_no: int
    sku: str
    refund_cents: int


@dataclass
class RefundResult:
    """退款结果。"""

    order_id: str
    returned_line_nos: tuple[int, ...]
    lines: list[RefundLine] = field(default_factory=list)
    shipping_refund_cents: int = 0
    remaining_paid_cents: int = 0

    @property
    def refund_total_cents(self) -> int:
        return sum(ln.refund_cents for ln in self.lines) + self.shipping_refund_cents

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "returned_line_nos": list(self.returned_line_nos),
            "lines": [
                {"line_no": ln.line_no, "sku": ln.sku, "refund_cents": ln.refund_cents}
                for ln in self.lines
            ],
            "shipping_refund_cents": self.shipping_refund_cents,
            "remaining_paid_cents": self.remaining_paid_cents,
            "refund_total_cents": self.refund_total_cents,
        }

```

### `src/settlement/engine.py`（5097 字节）

```python
"""结算与退款主流程。"""

from __future__ import annotations

from typing import Iterable, Sequence

from .allocation import allocate, verify_total
from .errors import NegativeAllocationError, RefundError
from .models import (
    Coupon,
    Order,
    OrderItem,
    RefundLine,
    RefundResult,
    SettledLine,
    Settlement,
)
from .validators import validate_order, validate_refund_amounts, validate_return_lines


def build_order(payload: dict) -> Order:
    """从 JSON 字典构造订单（不做业务校验，校验在 engine 入口做）。"""
    items = tuple(
        OrderItem(
            line_no=int(raw["line_no"]),
            sku=str(raw["sku"]),
            category=str(raw.get("category", "default")),
            unit_price_cents=int(raw["unit_price_cents"]),
            qty=int(raw.get("qty", 1)),
        )
        for raw in payload["items"]
    )
    coupon_raw = payload.get("coupon")
    coupon = (
        Coupon(
            code=str(coupon_raw["code"]),
            type=str(coupon_raw["type"]),
            value=int(coupon_raw["value"]),
            min_spend_cents=int(coupon_raw.get("min_spend_cents", 0)),
            excluded_categories=tuple(
                str(category)
                for category in coupon_raw.get("excluded_categories", [])
            ),
        )
        if coupon_raw
        else None
    )
    return Order(
        order_id=str(payload["order_id"]),
        items=items,
        coupon=coupon,
        platform_subsidy_cents=int(payload.get("platform_subsidy_cents", 0)),
        shipping_fee_cents=int(payload.get("shipping_fee_cents", 0)),
    )


def settle(order: Order) -> Settlement:
    """结算整单：计算每行实付金额。

    待分摊总额 = 优惠券金额 + 平台补贴。优惠券排除的类目不参与分摊。
    """
    validate_order(order)
    gross_total = order.gross_cents

    coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
    subsidy = order.platform_subsidy_cents
    to_allocate = coupon_discount + subsidy
    excluded_categories = set(
        order.coupon.excluded_categories if order.coupon else ()
    )

    basis = [
        (it.line_no, it.gross_cents)
        for it in order.items
        if it.gross_cents > 0 and it.category not in excluded_categories
    ]
    allocated = {it.line_no: 0 for it in order.items}
    unallocated_discount = 0
    if basis:
        allocations = allocate(to_allocate, basis)
        allocated.update(allocations)
        verify_total(to_allocate, allocations)
    else:
        unallocated_discount = to_allocate

    lines = []
    for it in order.items:
        discount = allocated[it.line_no]
        paid = it.gross_cents - discount
        if paid < 0:
            raise NegativeAllocationError(
                "分摊后行实付金额不能为负",
                {
                    "order_id": order.order_id,
                    "line_no": it.line_no,
                    "paid_cents": paid,
                },
            )
        lines.append(
            SettledLine(
                line_no=it.line_no,
                sku=it.sku,
                category=it.category,
                gross_cents=it.gross_cents,
                discount_cents=discount,
                paid_cents=paid,
            )
        )

    return Settlement(
        order_id=order.order_id,
        gross_cents=gross_total,
        coupon_discount_cents=coupon_discount,
        subsidy_cents=subsidy,
        shipping_fee_cents=order.shipping_fee_cents,
        paid_total_cents=(
            sum(ln.paid_cents for ln in lines)
            + order.shipping_fee_cents
            - unallocated_discount
        ),
        lines=lines,
        unallocated_discount_cents=unallocated_discount,
    )


def refund(
    order: Order,
    settlement: Settlement,
    returned_line_nos: Sequence[int] | Iterable[int],
) -> RefundResult:
    """按已结算结果计算部分退货的退款金额。"""
    returned = validate_return_lines(order, returned_line_nos)
    lines = [
        RefundLine(
            line_no=ln,
            sku=order.item(ln).sku,
            refund_cents=settlement.line(ln).paid_cents,
        )
        for ln in returned
    ]
    if not lines:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})

    validate_refund_amounts(order.order_id, lines)
    returned_set = set(returned)
    remaining_paid = sum(
        settlement.line(ln).paid_cents
        for ln in order.line_nos
        if ln not in returned_set
    )
    shipping = order.shipping_fee_cents if remaining_paid == 0 else 0
    return RefundResult(
        order_id=order.order_id,
        returned_line_nos=tuple(returned),
        lines=lines,
        shipping_refund_cents=shipping,
        remaining_paid_cents=remaining_paid,
    )


def preview_refund(
    order: Order, returned_line_nos: Sequence[int] | Iterable[int]
) -> RefundResult:
    """只读预览退款结果：内部完成结算与退款计算。"""
    return refund(order, settle(order), returned_line_nos)

```

## 1. 规格条目

### SPEC-011-06 · REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-06 退款结果暴露剩余实付
- 来源：`spec/REQ-2026-011.md`（版本 2026-11-08T10:30+08:00）
- 片段：`REQ-2026-011#11-3e42313464ed`

```text
REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-06 退款结果暴露剩余实付
### SPEC-011-06 退款结果暴露剩余实付

- `RefundResult` 新增字段 `remaining_paid_cents`：本次退货动作后，未退货行的实付金额合计
  （整单全退时为 0）。
- `to_dict()` 新增键 `remaining_paid_cents`，紧随 `shipping_refund_cents` 之后，其余键顺序不变。
- CLI `refund` / `preview` 输出必须包含该字段（由 `to_dict()` 透出）。
- 该字段是只读派生值：不得参与 `refund_total_cents` 计算，也不得写入订单状态。
- 边界：只在 `refund()` 内基于 `settlement.lines` 计算，禁止二次读取订单原价推算。
```

## 2. 相关代码

### `src/settlement/engine.py` :: `refund`
- 片段：`engine#3-288d4a54aebf`（score=4.5057）

```python
def refund(
    order: Order,
    settlement: Settlement,
    returned_line_nos: Sequence[int] | Iterable[int],
) -> RefundResult:
    """按已结算结果计算部分退货的退款金额。"""
    returned = validate_return_lines(order, returned_line_nos)
    lines = [
        RefundLine(
            line_no=ln,
            sku=order.item(ln).sku,
            refund_cents=settlement.line(ln).paid_cents,
        )
        for ln in returned
    ]
    if not lines:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})

    validate_refund_amounts(order.order_id, lines)
    returned_set = set(returned)
    remaining_paid = sum(
        settlement.line(ln).paid_cents
        for ln in order.line_nos
        if ln not in returned_set
    )
    shipping = order.shipping_fee_cents if remaining_paid == 0 else 0
    return RefundResult(
        order_id=order.order_id,
        returned_line_nos=tuple(returned),
        lines=lines,
        shipping_refund_cents=shipping,
        remaining_paid_cents=remaining_paid,
    )
```

### `src/settlement/engine.py` :: `settle`
- 片段：`engine#2-e3ab57cf8213`（score=4.1925）

```python
def settle(order: Order) -> Settlement:
    """结算整单：计算每行实付金额。

    待分摊总额 = 优惠券金额 + 平台补贴。优惠券排除的类目不参与分摊。
    """
    validate_order(order)
    gross_total = order.gross_cents

    coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
    subsidy = order.platform_subsidy_cents
    to_allocate = coupon_discount + subsidy
    excluded_categories = set(
        order.coupon.excluded_categories if order.coupon else ()
    )

    basis = [
        (it.line_no, it.gross_cents)
        for it in order.items
        if it.gross_cents > 0 and it.category not in excluded_categories
    ]
    allocated = {it.line_no: 0 for it in order.items}
    unallocated_discount = 0
    if basis:
        allocations = allocate(to_allocate, basis)
        allocated.update(allocations)
        verify_total(to_allocate, allocations)
    else:
        unallocated_discount = to_allocate

    lines = []
    for it in order.items:
        discount = allocated[it.line_no]
        paid = it.gross_cents - discount
        if paid < 0:
            raise NegativeAllocationError(
                "分摊后行实付金额不能为负",
                {
                    "order_id": order.order_id,
                    "line_no": it.line_no,
                    "paid_cents": paid,
                },
            )
        lines.append(
            SettledLine(
                line_no=it.line_no,
                sk
...（已截断）
```

### `src/settlement/models.py` :: `RefundResult`
- 片段：`models#16-6214bf24c599`（score=3.5792）

```python
class RefundResult:
    """退款结果。"""

    order_id: str
    returned_line_nos: tuple[int, ...]
    lines: list[RefundLine] = field(default_factory=list)
    shipping_refund_cents: int = 0
    remaining_paid_cents: int = 0

    @property
    def refund_total_cents(self) -> int:
        return sum(ln.refund_cents for ln in self.lines) + self.shipping_refund_cents

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "returned_line_nos": list(self.returned_line_nos),
            "lines": [
                {"line_no": ln.line_no, "sku": ln.sku, "refund_cents": ln.refund_cents}
                for ln in self.lines
            ],
            "shipping_refund_cents": self.shipping_refund_cents,
            "remaining_paid_cents": self.remaining_paid_cents,
            "refund_total_cents": self.refund_total_cents,
        }
```

### `src/settlement/models.py` :: `Coupon`
- 片段：`models#3-c05930907f8e`（score=3.066）

```python
class Coupon:
    """优惠券。当前仅支持满减（amount）与折扣（percent）两种。"""

    code: str
    type: str
    value: int
    min_spend_cents: int = 0
    excluded_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "excluded_categories",
            tuple(str(category) for category in self.excluded_categories),
        )

    def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
            off = gross_cents * (100 - self.value) // 100
            return max(0, min(off, gross_cents))
        raise ValueError(f"unknown coupon type: {self.type}")
```

### `src/settlement/engine.py` :: `engine`
- 片段：`engine#0-3966ce264755`（score=1.1333）

```python
"""结算与退款主流程。"""

from __future__ import annotations

from typing import Iterable, Sequence

from .allocation import allocate, verify_total
from .errors import NegativeAllocationError, RefundError
from .models import (
    Coupon,
    Order,
    OrderItem,
    RefundLine,
    RefundResult,
    SettledLine,
    Settlement,
)
from .validators import validate_order, validate_refund_amounts, validate_return_lines


def build_order(payload: dict) -> Order:
    """从 JSON 字典构造订单（不做业务校验，校验在 engine 入口做）。"""
    items = tuple(
        OrderItem(
            line_no=int(raw["line_no"]),
```

### `src/settlement/engine.py` :: `build_order`
- 片段：`engine#1-8f6276b082cb`（score=1.1333）

```python
def build_order(payload: dict) -> Order:
    """从 JSON 字典构造订单（不做业务校验，校验在 engine 入口做）。"""
    items = tuple(
        OrderItem(
            line_no=int(raw["line_no"]),
            sku=str(raw["sku"]),
            category=str(raw.get("category", "default")),
            unit_price_cents=int(raw["unit_price_cents"]),
            qty=int(raw.get("qty", 1)),
        )
        for raw in payload["items"]
    )
    coupon_raw = payload.get("coupon")
    coupon = (
        Coupon(
            code=str(coupon_raw["code"]),
            type=str(coupon_raw["type"]),
            value=int(coupon_raw["value"]),
            min_spend_cents=int(coupon_raw.get("min_spend_cents", 0)),
            excluded_categories=tuple(
                str(category)
                for category in coupon_raw.get("excluded_categories", [])
            ),
        )
        if coupon_raw
        else None
    )
    return Order(
        order_id=str(payload["order_id"]),
        items=items,
        coupon=coupon,
        platform_subsidy_cents=int(payload.get("platform_subsidy_cents", 0)),
        shipping_fee_cents=int(payload.get("shipping_fee_cents", 0)),
    )
```

### `src/settlement/engine.py` :: `preview_refund`
- 片段：`engine#4-6d14a80c843e`（score=0.8833）

```python
def preview_refund(
    order: Order, returned_line_nos: Sequence[int] | Iterable[int]
) -> RefundResult:
    """只读预览退款结果：内部完成结算与退款计算。"""
    return refund(order, settle(order), returned_line_nos)
```

### `src/settlement/models.py` :: `Coupon.discount_cents`
- 片段：`models#5-0009c391ec1c`（score=0.7833）

```python
def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
            off = gross_cents * (100 - self.value) // 100
            return max(0, min(off, gross_cents))
        raise ValueError(f"unknown coupon type: {self.type}")
```

## 3. 验证测试

### `tests/acceptance/test_req_2026_012.py` :: `test_spec_011_06_remaining_paid_exposed`
- 片段：`test_req_2026_012#2-454d81a44ae6`（score=3.8128）

```python
def test_spec_011_06_remaining_paid_exposed():
    """SPEC-011-06：部分退货后暴露剩余实付，等于未退货行实付合计。"""
    order = load("order_standard.json")
    result = refund(order, settle(order), [1, 2])
    assert result.remaining_paid_cents == 4500
    assert result.to_dict()["remaining_paid_cents"] == 4500
```

### `tests/acceptance/test_req_2026_012.py` :: `test_spec_011_06_cli_output_contains_field`
- 片段：`test_req_2026_012#5-55e39e2d5440`（score=3.7426）

```python
def test_spec_011_06_cli_output_contains_field():
    """SPEC-011-06：CLI 输出包含 remaining_paid_cents，且不影响退款总额。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "settlement.cli",
            "refund",
            "--order",
            str(FIXTURES / "order_standard.json"),
            "--return",
            "1,2",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["remaining_paid_cents"] == 4500
    assert payload["refund_total_cents"] == 90000 + 18000
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_02_tail_order_fixture`
- 片段：`test_req_2026_011#7-1f836dffaf82`（score=3.3574）

```python
def test_spec_011_02_tail_order_fixture():
    """SPEC-011-02：端到端验证尾差轮转后的行实付，行和等于整单实付。"""
    result = settle(load("order_tail.json"))
    assert [ln.discount_cents for ln in result.lines] == [3334, 3334, 3333]
    assert [ln.paid_cents for ln in result.lines] == [6666, 6666, 6667]
    assert result.paid_total_cents == 30000 - 10001
```

### `tests/acceptance/test_req_2026_012.py` :: `test_spec_011_06_full_zero_paid_order`
- 片段：`test_req_2026_012#3-ae3848977d11`（score=3.0915）

```python
def test_spec_011_06_full_zero_paid_order():
    """SPEC-011-06 边界：优惠全额抵扣的订单，退 1、2 后剩余实付为 0。"""
    order = load("order_fullpay.json")
    result = refund(order, settle(order), [1, 2])
    assert result.remaining_paid_cents == 0
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_01_excluded_category_gets_no_allocation`
- 片段：`test_req_2026_011#3-d2ee1ae25eee`（score=3.0298）

```python
def test_spec_011_01_excluded_category_gets_no_allocation():
    """SPEC-011-01：被排除类目分摊额为 0，份额按剩余行基数重分配，整单口径不变。"""
    result = settle(load("order_excluded_gift.json"))
    got = [(ln.discount_cents, ln.paid_cents) for ln in result.lines]
    assert got == [(10417, 89583), (2083, 17917), (0, 5000)]
    assert result.paid_total_cents == 112500 + 1200
    assert result.unallocated_discount_cents == 0
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_01_percent_coupon_respects_exclusions`
- 片段：`test_req_2026_011#5-e004a99df535`（score=2.9936）

```python
def test_spec_011_01_percent_coupon_respects_exclusions():
    """SPEC-011-01：折扣券（percent）与满减共用同一套类目排除语义。"""
    payload = raw("order_excluded_gift.json")
    payload["coupon"] = {
        "code": "CPN_PCT",
        "type": "percent",
        "value": 88,
        "min_spend_cents": 0,
        "excluded_categories": ["gift"],
    }
    result = settle(build_order(payload))
    assert result.coupon_discount_cents == 15000
    # 待分摊额 = 券 15000 + 平台补贴 2500 = 17500，全部落在未排除的两行上
    assert [(ln.discount_cents, ln.paid_cents) for ln in result.lines] == [
        (14584, 85416),
        (2916, 17084),
        (0, 5000),
    ]
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_01_all_categories_excluded_keeps_gross`
- 片段：`test_req_2026_011#4-05283d09787a`（score=2.866）

```python
def test_spec_011_01_all_categories_excluded_keeps_gross():
    """SPEC-011-01 边界：全部行被排除时不抛异常，待分摊额记为整单未分摊优惠。"""
    payload = raw("order_excluded_gift.json")
    payload["coupon"]["excluded_categories"] = ["digital", "book", "gift"]
    result = settle(build_order(payload))
    assert [ln.discount_cents for ln in result.lines] == [0, 0, 0]
    assert [ln.paid_cents for ln in result.lines] == [100000, 20000, 5000]
    assert result.unallocated_discount_cents == 10000 + 2500
    assert result.paid_total_cents == 125000 - 12500 + 1200
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_05_preview_service_and_cli`
- 片段：`test_req_2026_011#13-69526c5bae49`（score=2.8511）

```python
def test_spec_011_05_preview_service_and_cli():
    """SPEC-011-05：服务层与 CLI 都提供预览入口，且只读无副作用。"""
    assert (
        service.preview_refund_file(FIXTURES / "order_excluded_gift.json", [3])["refund_total_cents"]
        == 5000
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "settlement.cli",
            "preview",
            "--order",
            str(FIXTURES / "order_excluded_gift.json"),
            "--return",
            "1,3",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["refund_total_cents"] == 89583 + 5000
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_03_shipping_not_refunded_when_remaining_paid_left`
- 片段：`test_req_2026_011#9-65396fce96ec`（score=2.3333）

```python
def test_spec_011_03_shipping_not_refunded_when_remaining_paid_left():
    """SPEC-011-03：仍有剩余实付时不退运费（与基线行为一致）。"""
    order = load("order_standard.json")
    result = refund(order, settle(order), [1, 2])
    assert result.shipping_refund_cents == 0
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_04_negative_allocation_raises`
- 片段：`test_req_2026_011#10-6942f591b5fd`（score=2.1333）

```python
def test_spec_011_04_negative_allocation_raises():
    """SPEC-011-04：分摊后行实付为负必须显式失败（E_NEG_ALLOC），不得静默夹断。"""
    with pytest.raises(NegativeAllocationError) as exc:
        settle(load("order_negative.json"))
    assert exc.value.code == "E_NEG_ALLOC"
    assert exc.value.detail["line_no"] == 2
    assert exc.value.detail["paid_cents"] == -30000
```

### `tests/acceptance/test_req_2026_012.py` :: `test_spec_011_06_all_returned_is_zero`
- 片段：`test_req_2026_012#4-1cbfc8b6d50f`（score=2.1）

```python
def test_spec_011_06_all_returned_is_zero():
    """SPEC-011-06 边界：整单全退时剩余实付为 0。"""
    order = load("order_standard.json")
    assert refund(order, settle(order), [1, 2, 3]).remaining_paid_cents == 0
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_03_shipping_refunded_when_remaining_paid_is_zero`
- 片段：`test_req_2026_011#8-3779bffc852b`（score=1.7333）

```python
def test_spec_011_03_shipping_refunded_when_remaining_paid_is_zero():
    """SPEC-011-03：退货后剩余实付为 0 时退还全额运费。"""
    order = load("order_fullpay.json")
    settlement = settle(order)
    assert [ln.paid_cents for ln in settlement.lines] == [0, 0, 0]
    result = refund(order, settlement, [1, 2])
    assert result.shipping_refund_cents == 1200
    assert result.refund_total_cents == 1200
```

## 4. 任务状态

- 已完成：SPEC-011-01 已实现（验收用例通过）（继承自 REQ-2026-011）; SPEC-011-02 已实现（验收用例通过）（继承自 REQ-2026-011）; SPEC-011-03 已实现（验收用例通过）（继承自 REQ-2026-011）; SPEC-011-04 已实现（验收用例通过）（继承自 REQ-2026-011）; SPEC-011-05 已实现（验收用例通过）（继承自 REQ-2026-011）
- 待办：无
- 已知遗留：BACKLOG-311 多次部分退货的累积对账未覆盖; BACKLOG-312 折扣券 + 类目排除缺少专项用例

### 上游任务交接（PowerContext 恢复）

```text
任务：REQ-2026-011 · 退款结算：类目排除分摊与尾差规则升级
状态：done　规格版本：2026-11-07T20:00+08:00　迭代：3
补丁：f26f7cd8bdbd　测试：{"ok": true, "returncode": 0, "passed": 28, "failed": 0, "duration_s": 5.14, "tail": "............................                                             [100%]\n28 passed in 3.07s"}
已完成：SPEC-011-01 已实现（验收用例通过）; SPEC-011-02 已实现（验收用例通过）; SPEC-011-03 已实现（验收用例通过）; SPEC-011-04 已实现（验收用例通过）; SPEC-011-05 已实现（验收用例通过）
待办：无
遗留：BACKLOG-311 多次部分退货的累积对账未覆盖; BACKLOG-312 折扣券 + 类目排除缺少专项用例
证据片段：30 条
落库后端：powercontext
保存时间：2026-11-08T22:12:16+0800
```

## 5. 检索留痕

- [spec] `退款结果暴露剩余实付（规格 v2 追加项） 规格已更新到 v2，追加 SPEC-011-06：退款结果需要暴露剩余实付金额 remaining_paid_cents。请沿用 REQ-2026-011 的上下文与已保存状态继续实现，不得回退已完成的 SPEC-011-01 ~ 05，也不得修改任何测试文件。` → REQ-2026-011#11-3e42313464ed, REQ-2026-011#6-d1c897e446b5, REQ-2026-011#1-c62dc96441ec
- [spec] `SPEC-011-06` → REQ-2026-011#16-ef8a2021d6fc, REQ-2026-011#11-3e42313464ed, REQ-2026-011#1-c62dc96441ec
- [code] `models 退款结果暴露剩余实付（规格 v2 追加项）` → __init__#0-67a77019c70b, models#3-c05930907f8e, allocation#0-59b02402f88e
- [code] `src/settlement/models.py 规格已更新到 v2，追加 SPEC-011-06：退款结果需要暴露剩余实付金额 remaining_paid_cents。请沿用 REQ-2026-011 的上下文与已保存状态继续实现，不得回退已完成的 SPEC-011-01 ~ 05，也不得修改任何测试文件。` → engine#3-288d4a54aebf, engine#2-e3ab57cf8213, allocation#0-59b02402f88e
- [code] `engine 退款结果暴露剩余实付（规格 v2 追加项）` → __init__#0-67a77019c70b, allocation#0-59b02402f88e, engine#0-3966ce264755
- [code] `src/settlement/engine.py 规格已更新到 v2，追加 SPEC-011-06：退款结果需要暴露剩余实付金额 remaining_paid_cents。请沿用 REQ-2026-011 的上下文与已保存状态继续实现，不得回退已完成的 SPEC-011-01 ~ 05，也不得修改任何测试文件。` → engine#3-288d4a54aebf, engine#2-e3ab57cf8213, allocation#0-59b02402f88e
- [test] `SPEC-011-06 退款结果暴露剩余实付（规格 v2 追加项）` → test_req_2026_012#2-454d81a44ae6, test_req_2026_011#9-65396fce96ec, test_req_2026_011#10-6942f591b5fd
- [test] `规格已更新到 v2，追加 SPEC-011-06：退款结果需要暴露剩余实付金额 remaining_paid_cents。请沿用 REQ-2026-011 的上下文与已保存状态继续实现，不得回退已完成的 SPEC-011-01 ~ 05，也不得修改任何测试文件。` → test_req_2026_012#2-454d81a44ae6, test_req_2026_012#5-55e39e2d5440, test_req_2026_011#7-1f836dffaf82


## 验证方式
实现后必须让 `python -m pytest -q tests/test_allocation.py tests/test_engine.py tests/test_service_cli.py tests/acceptance/test_req_2026_011.py tests/acceptance/test_req_2026_012.py` 全绿，
其中 `tests/acceptance/` 是本任务的验收用例，`tests/test_*.py` 是必须保持通过的回归用例。

请按 ```file path=... 的整文件格式输出需要改动的文件。