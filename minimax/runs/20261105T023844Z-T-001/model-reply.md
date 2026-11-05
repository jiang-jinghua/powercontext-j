```diff
--- a/src/order_service/money.py
+++ b/src/order_service/money.py
@@ -1,6 +1,6 @@
 """Exact money helpers for the order service."""
 
-from decimal import ROUND_HALF_EVEN, Decimal
+from decimal import ROUND_HALF_UP, Decimal
 
 CENT = Decimal("0.01")
 
@@ -11,5 +11,5 @@
 
 
 def round_money(value: object) -> Decimal:
-    """Round an amount to cents using the platform default rounding mode."""
-    return money(value).quantize(CENT, rounding=ROUND_HALF_EVEN)
+    """Round an amount to cents with half-up rounding (SPEC-PRC-002)."""
+    return money(value).quantize(CENT, rounding=ROUND_HALF_UP)
--- a/src/order_service/promotion.py
+++ b/src/order_service/promotion.py
@@ -1,5 +1,6 @@
 """Coupon registry and coupon application."""
 
+from dataclasses import dataclass
 from decimal import Decimal
 
 from .money import money
@@ -13,10 +14,28 @@
     """Raised when a known coupon does not apply to this order."""
 
 
-COUPONS: dict[str, object] = {}
+@dataclass(frozen=True)
+class CouponRule:
+    """A fixed-amount coupon judged on the goods subtotal."""
+
+    code: str
+    threshold: Decimal
+    amount: Decimal
+
+    def apply(self, goods: Decimal) -> Decimal:
+        if goods < self.threshold:
+            raise CouponNotApplicableError(
+                f"{self.code} requires goods of at least {self.threshold}, got {goods}"
+            )
+        return self.amount
 
 
-def resolve_coupon(code: str) -> object:
+COUPONS: dict[str, CouponRule] = {
+    "SAVE30": CouponRule(code="SAVE30", threshold=money("200"), amount=money("30")),
+}
+
+
+def resolve_coupon(code: str) -> CouponRule:
     """Look up a coupon rule by code."""
     try:
         return COUPONS[code]
```