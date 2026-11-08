"""订单退款结算引擎。

模块职责：
- models    领域模型（订单 / 行项目 / 优惠券 / 结算结果）
- allocation 金额分摊（按权重比例 + 尾差处理）
- engine    结算与退款预览主流程
- validators 输入与结果校验
- service   面向调用方的服务层（加载 JSON、输出可序列化结果）
- cli       命令行入口（settle / refund）
"""

__all__ = ["__version__"]
__version__ = "1.4.0"
