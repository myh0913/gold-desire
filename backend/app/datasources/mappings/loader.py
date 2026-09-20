"""YAML 映射加载器：让非 Python 开发者「丢一个 YAML 文件」即可接入新源。

- 读取 ``mappings/defs/`` 下的 ``*.yaml`` / ``*.yml``，转换为
  :class:`CapabilityMapping` 并注册；
- 与 Python 注册的映射 **合并**：冲突时 Python 优先（``register_mapping``
  默认不覆盖），保证代码内映射始终是权威定义。

YAML 格式（每文件一份映射）::

    source_id: demo_http
    capability: daily_bars
    record_path: data.items
    # 表格型响应（表头数组 + 行数组）才需要：
    # columns_path: data.fields
    # 一行展开为多行（如个股 → 所属多个题材）才需要：
    # explode_path: plates[]
    notes: 示例源日线
    static:
      currency: CNY
    fields:
      - target: code
        source: symbol
        transform: normalize_code
      - target: volume_shares
        source: vol
        transform: lots_to_shares
      - target: trade_date
        context: args.date      # 取数参数中的目标交易日
        transform: str_to_date
      - target: pre_close
        source: prev_close
        required: false         # 源缺失时留空，而非拒绝整批记录
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.datasources.mappings.dsl import REQUIRED, CapabilityMapping, FieldMap, MappingError
from app.datasources.mappings.registry import register_mapping

__all__ = ["DEFAULT_DEFS_DIR", "load_yaml_mappings", "parse_mapping_doc"]

DEFAULT_DEFS_DIR: Path = Path(__file__).resolve().parent / "defs"


def parse_mapping_doc(doc: Any, *, origin: str = "<doc>") -> CapabilityMapping:
    """把单个 YAML/JSON 文档解析为 :class:`CapabilityMapping`。

    Raises:
        MappingError: 文档结构非法（缺字段、字段项非对象等）。
    """
    if not isinstance(doc, dict):
        raise MappingError(f"{origin}: 映射文档必须是对象（mapping）")

    source_id = doc.get("source_id")
    capability = doc.get("capability")
    if not isinstance(source_id, str) or not source_id:
        raise MappingError(f"{origin}: 缺少 source_id")
    if not isinstance(capability, str) or not capability:
        raise MappingError(f"{origin}: 缺少 capability")

    raw_fields = doc.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise MappingError(f"{origin}: fields 必须是非空列表")

    fields: list[FieldMap] = []
    for item in raw_fields:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            raise MappingError(f"{origin}: fields 每项须为对象且含 target")
        fields.append(
            FieldMap(
                target=item["target"],
                source=item.get("source"),
                transform=str(item.get("transform", "identity")),
                default=item.get("default", REQUIRED),
                required=bool(item.get("required", True)),
                context=item.get("context"),
            )
        )

    static = doc.get("static") or {}
    if not isinstance(static, dict):
        raise MappingError(f"{origin}: static 必须是对象")

    record_path = doc.get("record_path")
    if record_path is not None and not isinstance(record_path, str):
        raise MappingError(f"{origin}: record_path 必须是字符串或省略")

    columns_path = doc.get("columns_path")
    if columns_path is not None and not isinstance(columns_path, str):
        raise MappingError(f"{origin}: columns_path 必须是字符串或省略")

    explode_path = doc.get("explode_path")
    if explode_path is not None and not isinstance(explode_path, str):
        raise MappingError(f"{origin}: explode_path 必须是字符串或省略")

    return CapabilityMapping(
        source_id=source_id,
        capability=capability,
        record_path=record_path,
        fields=tuple(fields),
        static=static,
        columns_path=columns_path,
        explode_path=explode_path,
        notes=str(doc.get("notes", "")),
    )


def load_yaml_mappings(directory: Path | None = None) -> list[CapabilityMapping]:
    """加载目录下全部 YAML 映射并注册（Python 已注册者优先，不覆盖）。

    Args:
        directory: 映射定义目录；缺省为 ``mappings/defs``。

    Returns:
        本次成功注册的映射列表（不含因冲突被跳过的）。

    Raises:
        MappingError: pyyaml 未安装，或某个 YAML 文档结构非法。
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 依赖已在 pyproject 声明
        raise MappingError("加载 YAML 映射需要 pyyaml，请先安装：pip install pyyaml") from exc

    target_dir = directory or DEFAULT_DEFS_DIR
    if not target_dir.is_dir():
        return []

    loaded: list[CapabilityMapping] = []
    for path in sorted(target_dir.glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if doc is None:
            continue
        mapping = parse_mapping_doc(doc, origin=path.name)
        registered = register_mapping(mapping)
        if registered is mapping:
            loaded.append(mapping)
    return loaded
