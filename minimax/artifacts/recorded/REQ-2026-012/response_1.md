按 SPEC-011-06 只做最小增量：在 RefundResult 上暴露 remaining_paid_cents，并在 refund() 里用未退货行实付合计填充。

1. models.RefundResult 新增 remaining_paid_cents（默认 0），to_dict() 紧随 shipping_refund_cents 输出；
2. engine.refund 复用已算出的 remaining_paid，避免二次读取订单原价；
3. 未改动退款总额计算、未修改任何测试文件，SPEC-011-01 ~ 05 的行为保持不变。

```diff
diff --git a/src/settlement/engine.py b/src/settlement/engine.py
index ac0db18..d1ab760 100644
--- a/src/settlement/engine.py
+++ b/src/settlement/engine.py
@@ -141,6 +141,7 @@ def refund(
         returned_line_nos=tuple(returned),
         lines=lines,
         shipping_refund_cents=shipping,
+        remaining_paid_cents=remaining_paid,
     )
 
 
diff --git a/src/settlement/models.py b/src/settlement/models.py
index 3f0f3bf..d4b5bac 100644
--- a/src/settlement/models.py
+++ b/src/settlement/models.py
@@ -147,6 +147,7 @@ class RefundResult:
     returned_line_nos: tuple[int, ...]
     lines: list[RefundLine] = field(default_factory=list)
     shipping_refund_cents: int = 0
+    remaining_paid_cents: int = 0
 
     @property
     def refund_total_cents(self) -> int:
@@ -161,5 +162,6 @@ class RefundResult:
                 for ln in self.lines
             ],
             "shipping_refund_cents": self.shipping_refund_cents,
+            "remaining_paid_cents": self.remaining_paid_cents,
             "refund_total_cents": self.refund_total_cents,
         }
```
