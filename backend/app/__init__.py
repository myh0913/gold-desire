"""gold-desire 后端应用包。

分层约定（依赖方向自上而下，禁止反向 import）：

- ``core``         配置、日志、缓存、异常、中间件等横切关注点
- ``db``           SQLAlchemy 引擎 / 会话 / 声明基类
- ``models``       ORM 模型（仅被 repositories 使用）
- ``schemas``      Pydantic 请求/响应契约
- ``repositories`` 唯一 DB 读写出口
- ``services``     业务编排
- ``api``          路由层（SHALL NOT 直接访问 ORM 或上游数据源）
"""

__version__ = "0.1.0"
