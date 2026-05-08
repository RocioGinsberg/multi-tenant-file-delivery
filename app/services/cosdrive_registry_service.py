"""
CosDrive 分类注册表管理服务。
负责配置校验、草稿管理、发布、回滚。

新增文件，放置于 app/services/cosdrive_registry_service.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..repos import cosdrive_repo

logger = logging.getLogger("portal.cosdrive_registry")

# 注册表 config_json 必须包含的顶级 key
_REQUIRED_KEYS = {
    "team_aliases",
    "task_classification",
    "description_mapping",
    "mapping_config",
    "suffix_priority",
    "suffix_fallback",
    "ignored_filenames",
}


def validate_config(config: Dict[str, Any]) -> tuple[bool, List[str], List[str]]:
    """
    校验注册表配置合法性。
    返回: (valid, errors, warnings)
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. 必填 key
    missing = _REQUIRED_KEYS - set(config.keys())
    if missing:
        errors.append(f"缺少必填配置项: {', '.join(sorted(missing))}")

    # 2. task_classification 结构
    tc = config.get("task_classification", {})
    if not isinstance(tc, dict) or not tc:
        errors.append("task_classification 不能为空")
    else:
        for task_name, info in tc.items():
            if isinstance(info, dict):
                if not info.get("category"):
                    errors.append(f"task_classification['{task_name}'] 缺少 category")
            # 允许非 dict 值（向后兼容简写）

    # 3. description_mapping 引用的 task 必须存在
    dm = config.get("description_mapping", {})
    for desc, mapped_task in dm.items():
        if mapped_task not in tc:
            warnings.append(
                f"description_mapping['{desc}'] → '{mapped_task}' "
                f"不在 task_classification 中（可能依赖模糊匹配）"
            )

    # 4. suffix_priority 引用合法性
    sp = config.get("suffix_priority", {})
    for ext, task in sp.items():
        if task not in tc:
            errors.append(f"suffix_priority['{ext}'] → '{task}' 不在 task_classification 中")

    # 5. suffix_fallback 检查
    sf = config.get("suffix_fallback", {})
    if not isinstance(sf, dict):
        errors.append("suffix_fallback 必须是 dict")

    # 6. mapping_config 阈值检查
    mc = config.get("mapping_config", {})
    for key in ("fuzzy_threshold", "description_mapping_fuzzy_threshold"):
        val = mc.get(key)
        if val is not None:
            if not isinstance(val, (int, float)) or val < 0 or val > 100:
                errors.append(f"mapping_config.{key} 必须在 0-100 之间")

    # 7. team_aliases 重复别名检查
    ta = config.get("team_aliases", {})
    if not isinstance(ta, dict):
        errors.append("team_aliases 必须是 dict")
    else:
        seen_targets = {}
        for alias, target in ta.items():
            if target in seen_targets:
                warnings.append(
                    f"team_aliases 中 '{alias}' 和 '{seen_targets[target]}' "
                    f"都映射到 '{target}'"
                )
            seen_targets[target] = alias

    # 8. ignored_filenames 类型检查
    ign = config.get("ignored_filenames", [])
    if not isinstance(ign, list):
        errors.append("ignored_filenames 必须是 list")

    valid = len(errors) == 0
    return valid, errors, warnings


def get_current_published() -> dict | None:
    return cosdrive_repo.get_published_registry()


def get_version(version_id: str) -> dict | None:
    return cosdrive_repo.get_registry_version(version_id)


def list_versions(limit: int = 50) -> list[dict]:
    return cosdrive_repo.list_registry_versions(limit)


def save_draft(config: dict, user: str) -> dict:
    """保存草稿前先校验"""
    valid, errors, warnings = validate_config(config)
    if not valid:
        raise ValueError(f"配置校验失败: {'; '.join(errors)}")
    return cosdrive_repo.save_draft_registry(config, user)


def publish(version_id: str, user: str) -> dict:
    """发布指定版本"""
    version = cosdrive_repo.get_registry_version(version_id)
    if not version:
        raise ValueError(f"版本 {version_id} 不存在")

    # 发布前再次校验
    config = version["config_json"]
    if isinstance(config, str):
        import json
        config = json.loads(config)
    valid, errors, _ = validate_config(config)
    if not valid:
        raise ValueError(f"发布校验失败: {'; '.join(errors)}")

    result = cosdrive_repo.publish_registry(version_id, user)
    if not result:
        raise ValueError("发布失败")

    logger.info("注册表版本 %s (v%d) 已发布 by %s",
                version_id, result["version_no"], user)
    return result


def rollback(target_version_id: str, user: str) -> dict:
    result = cosdrive_repo.rollback_registry(target_version_id, user)
    if not result:
        raise ValueError(f"回滚目标版本 {target_version_id} 不存在")

    logger.info("注册表已回滚到版本 %s → 新版本 v%d by %s",
                target_version_id, result["version_no"], user)
    return result
