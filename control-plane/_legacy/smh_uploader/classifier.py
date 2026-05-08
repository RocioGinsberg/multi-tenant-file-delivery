# -*- coding: utf-8 -*-
"""
文件分类器
- 输出 CSV 中的本地路径为相对路径（相对于 workspace）
- 幂等：多次运行同一目录，产出完全相同的 CSV
用法:
    python classifier.py                          # 使用 .env 中的 WORKSPACE_PATH
    python classifier.py /path/to/workspace       # 显式指定工作目录
"""
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from fuzzywuzzy import process

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# 异常定义
# ─────────────────────────────────────────────────────────────────────

class TeamValidationError(Exception):
    def __init__(self, filename: str, team: str, suggested: Optional[str] = None):
        self.filename = filename
        self.team = team
        self.suggested = suggested
        msg = f"文件 '{filename}' 中的团队名 '{team}' 不在有效列表中"
        if suggested:
            msg += f"，建议: '{suggested}'"
        super().__init__(msg)

class UnknownTaskError(Exception):
    def __init__(self, filename: str, task: str, description: str):
        self.filename = filename
        self.task = task
        self.description = description
        super().__init__(f"文件 '{filename}' 的任务 '{task}' 未在配置中找到")

# ─────────────────────────────────────────────────────────────────────
# 核心分类器
# ─────────────────────────────────────────────────────────────────────

class FileClassifier:
    """
    纯逻辑文件分类器。
    - folder_path:      待分类的工作目录
    - task_config_json:  任务分类规则 JSON
    - team_list_json:    团队列表 JSON
    - output_csv:        输出 CSV 路径（默认与 task_config 同目录）
    """

    # 匹配 "01. ", "12. ", "1. " 等数字编号前缀
    _NUMBER_PREFIX_RE = re.compile(r"^\d+\.\s*")

    def __init__(
        self,
        folder_path: str | Path,
        task_config_json: str | Path = "task_classification.json",
        team_list_json: str | Path = "current_teamlist.json",
        output_csv: str | Path = "分类结果.csv",
        *,
        recursive: bool = True,
    ):
        self.folder = Path(folder_path).expanduser().resolve()
        if not self.folder.is_dir():
            raise NotADirectoryError(f"目录不存在: {self.folder}")

        self.recursive = recursive
        self.output_csv = Path(output_csv)

        # ── 加载配置 ──
        self._load_task_config(task_config_json)
        self.valid_teams = self._load_team_list(team_list_json)
        self.team_name_mapping = self._load_team_mapping(team_list_json)

        # ── 构建去编号 → 原始团队名的索引（用于匹配） ──
        # 例如: {"酷动": "01. 酷动", "中恒": "12. 中恒", ...}
        self._stripped_team_index: Dict[str, str] = {}
        for name in self.valid_teams:
            stripped = self._strip_number_prefix(name)
            if stripped and stripped != name:
                self._stripped_team_index[stripped] = name

    # ─────────────── 配置加载 ───────────────

    def _load_task_config(self, path: str | Path) -> None:
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"任务配置文件不存在: {p}")
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.team_aliases: Dict[str, str] = cfg.get("team_aliases", {})
        self.task_to_info: Dict[str, dict] = cfg["task_classification"]
        self.description_mapping: Dict[str, str] = cfg.get("description_mapping", {})

        mcfg = cfg.get("mapping_config", {})
        self.fuzzy_enabled: bool = mcfg.get("enable_fuzzy_match", True)
        self.fuzzy_threshold: int = mcfg.get("fuzzy_threshold", 70)
        self.desc_fuzzy_threshold: int = mcfg.get("description_mapping_fuzzy_threshold", 70)

        self.suffix_priority: Dict[str, str] = cfg.get("suffix_priority", {})
        self.suffix_fallback: Dict[str, str] = cfg.get("suffix_fallback", {})
        self.ignored_filenames: set = set(cfg.get("ignored_filenames", []))

    @staticmethod
    def _load_team_list(path: str | Path) -> List[str]:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"团队列表文件不存在: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        teams = [t.get("name", "") for t in data if t.get("name")]
        if not teams:
            raise ValueError(f"在 {p} 中未找到有效团队")
        return teams

    @staticmethod
    def _load_team_mapping(path: str | Path) -> Dict[str, str]:
        """name → original_name 映射"""
        mapping: Dict[str, str] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t in data:
                if "name" in t and "original_name" in t:
                    mapping[t["name"]] = t["original_name"]
        except Exception as e:
            logger.warning("加载团队映射失败 (%s)，将使用原始名称", e)
        return mapping

    # ─────────────── 文本 / 匹配工具 ───────────────

    @staticmethod
    def normalize_text(text: str) -> str:
        return "".join(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))

    @classmethod
    def _strip_number_prefix(cls, name: str) -> str:
        """去除团队名中的数字编号前缀，如 '12. 中恒' → '中恒'"""
        return cls._NUMBER_PREFIX_RE.sub("", name).strip()

    @staticmethod
    def fuzzy_match(text: str, candidates: List[str], threshold: int = 60) -> Optional[str]:
        result = process.extractOne(text, candidates)
        if result and result[1] >= threshold:
            return result[0]
        return None

    # ─────────────── 分类逻辑 ───────────────

    def _classify_description(self, description: str, ext: str) -> str:
        """根据描述文本和后缀判断任务名称"""
        # 1. 后缀优先级
        if ext in self.suffix_priority:
            return self.suffix_priority[ext]

        # 2. 描述 → description_mapping → 模糊匹配任务名
        if self.description_mapping:
            keys = list(self.description_mapping.keys())
            hit = process.extractOne(description, keys)
            if hit and hit[1] >= self.desc_fuzzy_threshold:
                mapped = self.description_mapping[hit[0]]
                if self.fuzzy_enabled:
                    task = self.fuzzy_match(mapped, list(self.task_to_info.keys()),
                                           self.fuzzy_threshold)
                    if task:
                        return task
                return mapped

        # 3. 直接模糊匹配任务名
        task = self.fuzzy_match(description, list(self.task_to_info.keys()),
                                self.desc_fuzzy_threshold)
        if task:
            return task

        return self.suffix_fallback.get(ext, "未知文件类型")

    def _validate_team(self, filename: str, team: str) -> str:
        """
        验证团队名称。匹配优先级：
          1. team_aliases 别名映射
          2. 精确匹配 valid_teams
          3. 去编号前缀后精确匹配（'中恒' → '12. 中恒'）
          4. 模糊匹配（fallback）
        """
        if not team:
            return team

        # 1. 别名映射
        canonical = self.team_aliases.get(team, team)

        # 2. 精确匹配
        if canonical in self.valid_teams:
            return canonical

        # 3. ★ 去编号匹配：文件名中的 '中恒' 匹配 valid_teams 中的 '12. 中恒'
        if canonical in self._stripped_team_index:
            matched = self._stripped_team_index[canonical]
            logger.debug("团队名去编号匹配: '%s' → '%s'", canonical, matched)
            return matched

        # 4. 模糊匹配（仅用于报错建议）
        suggested = self.fuzzy_match(canonical, self.valid_teams, 70)
        raise TeamValidationError(filename, team, suggested)

    def _validate_task(self, filename: str, task_name: str, description: str) -> str:
        unknown_tags = {"未知文件类型", "Excel 文件", "CSV 文件", "PDF 文件",
                        "文本文件", "JSON 文件", "XML 文件"}
        if not task_name or task_name in unknown_tags:
            raise UnknownTaskError(filename, task_name, description)
        return task_name

    # ─────────────── 单文件解析 ───────────────

    def parse_file(self, file_path: Path) -> Optional[Dict[str, str]]:
        """
        解析单个文件 → dict 或 None（跳过）。
        本地路径输出为相对于 self.folder 的相对路径。
        """
        base = file_path.stem
        ext = file_path.suffix.lower()

        # 拆分文件名：最后一段当团队名
        parts = [p.strip() for p in re.split(r"\s*[-—–'-]\s*", base)]
        team = parts[-1] if len(parts) >= 2 else ""
        desc_candidate = " ".join(parts[:-1]).strip() if len(parts) >= 2 else (parts[0] if parts else "")

        # 验证团队
        try:
            validated_team = self._validate_team(file_path.name, team)
        except TeamValidationError as e:
            logger.warning("⏭️  %s", e)
            return None

        # 分类任务
        description = self.normalize_text(desc_candidate)
        task_name = self._classify_description(description, ext)

        try:
            validated_task = self._validate_task(file_path.name, task_name, desc_candidate)
        except UnknownTaskError as e:
            logger.warning("⏭️  %s", e)
            return None

        # 构建上传路径
        info = self.task_to_info.get(validated_task, {})
        if isinstance(info, dict):
            category = info.get("category", "未知类别")
            task = info.get("task", "")
        else:
            category, task = "未知类别", validated_task

        if task.strip():
            upload_path = f"{category}/{task}" if category != "未知类别" else task
        else:
            upload_path = category if category != "未知类别" else "未分类"

        # 团队名映射到 original_name（即网盘侧名称）
        final_team = self.team_name_mapping.get(validated_team, validated_team)

        # ★ 本地路径改为相对路径
        try:
            rel_path = file_path.resolve().relative_to(self.folder)
        except ValueError:
            rel_path = file_path  # fallback

        return {
            "团队": final_team,
            "文件名": base + ext,
            "任务": task,
            "上传路径": upload_path,
            "本地路径": str(rel_path),
        }

    # ─────────────── 批量分类（幂等） ───────────────

    def classify(self) -> str:
        """
        扫描目录 → 分类 → 写入 CSV。
        幂等：同一目录内容不变时，多次运行产出相同 CSV。
        返回输出 CSV 的路径。
        """
        # 收集文件（排序保证幂等）
        iterator = self.folder.rglob("*") if self.recursive else self.folder.iterdir()
        files = sorted(
            [p for p in iterator if p.is_file() and p.name not in self.ignored_filenames],
            key=lambda p: str(p),
        )

        logger.info("🔍 开始处理 %d 个文件 (workspace: %s)", len(files), self.folder)

        results = []
        skipped = 0
        errors = []

        for fp in files:
            try:
                info = self.parse_file(fp)
                if info is not None:
                    results.append(info)
                else:
                    skipped += 1
            except Exception as e:
                errors.append({"file": fp.name, "error": str(e)})
                skipped += 1

        if not results:
            logger.warning("⚠️  没有成功分类的文件")
            return ""

        # 输出 CSV（固定列顺序 + 排序 → 幂等）
        df = pd.DataFrame(results)
        col_order = ["团队", "文件名", "任务", "上传路径", "本地路径"]
        cols = [c for c in col_order if c in df.columns]
        cols += [c for c in df.columns if c not in col_order]
        df = df[cols].sort_values(by=["团队", "任务", "上传路径", "文件名"], ascending=True)

        out = self.output_csv.resolve()
        df.to_csv(out, index=False, encoding="utf-8-sig")

        logger.info("✅ 成功分类 %d 个文件，跳过 %d 个", len(results), skipped)
        logger.info("📄 结果: %s", out)

        if errors:
            logger.info("❌ 错误 (%d):", len(errors))
            for e in errors[:10]:
                logger.info("   - %s: %s", e["file"], e["error"])

        return str(out)

# ─────────────────────────────────────────────────────────────────────
# 命令行入口
# ─────────────────────────────────────────────────────────────────────

def main():
    """
    用法:
        python classifier.py [workspace_path]
    不传参数时从 .env 中读取 WORKSPACE_PATH。
    配置文件 (task_classification.json / current_teamlist.json) 默认在
    workspace 的父目录中查找。
    """
    # 确定 workspace
    if len(sys.argv) > 1:
        workspace = sys.argv[1]
    else:
        workspace = os.getenv("WORKSPACE_PATH", "")
    if not workspace:
        print("用法: python classifier.py [workspace_path]")
        print("或在 .env 中设置 WORKSPACE_PATH")
        sys.exit(1)

    workspace = Path(workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"❌ 目录不存在: {workspace}")
        sys.exit(1)

    # 配置文件：workspace 的父目录
    config_dir = workspace.parent
    task_config = config_dir / "task_classification.json"
    team_list = config_dir / "current_teamlist.json"
    output_csv = config_dir / "分类结果.csv"

    # 允许通过环境变量覆盖
    task_config = Path(os.getenv("TASK_CONFIG_PATH", str(task_config)))
    team_list = Path(os.getenv("TEAM_LIST_PATH", str(team_list)))
    output_csv = Path(os.getenv("OUTPUT_CSV_PATH", str(output_csv)))

    logger.info("📂 Workspace:  %s", workspace)
    logger.info("📋 任务配置:   %s", task_config)
    logger.info("👥 团队列表:   %s", team_list)
    logger.info("📄 输出 CSV:   %s", output_csv)

    classifier = FileClassifier(
        folder_path=workspace,
        task_config_json=task_config,
        team_list_json=team_list,
        output_csv=output_csv,
        recursive=True,
    )

    result = classifier.classify()
    if result:
        logger.info("🎉 分类完成: %s", result)
    else:
        logger.warning("⚠️  无可分类文件")
        sys.exit(1)

if __name__ == "__main__":
    main()
