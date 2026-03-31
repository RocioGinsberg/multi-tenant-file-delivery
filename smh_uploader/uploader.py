# -*- coding: utf-8 -*-
"""
批量上传编排模块
- CSV 数据加载与团队匹配
- 嵌套并发：团队级 × 文件级
- 目录自动创建（去重缓存）
- 完整的统计与错误报告
"""
import asyncio
import csv
import os
import time
import logging
import threading
from dataclasses import dataclass
from collections import defaultdict
from typing import List, Dict, Any, Optional

import aiohttp

from .config import Config
from .token_manager import TokenManager
from .api_client import APIClient

logger = logging.getLogger(__name__)


# ─────────────── 数据结构 ───────────────

@dataclass
class TeamInfo:
    """团队信息"""
    id: Any
    name: str
    original_name: str
    space_id: str
    org_id: str


@dataclass
class UploadMapping:
    """单个文件的上传映射"""
    local_path: str
    remote_path: str
    space_id: str
    team_name: str
    file_name: str
    task_type: str = ""


# ─────────────── 上传器 ───────────────

class BatchUploader:
    """嵌套并发批量上传器"""

    def __init__(self, config: Config, token_manager: TokenManager,
                 api_client: APIClient):
        self.config = config
        self.tm = token_manager
        self.api = api_client
        self.upload_cfg = config.upload

        # 目录创建去重缓存
        self._created_dirs: Dict[str, set] = defaultdict(set)
        self._dir_lock = threading.Lock()

        # 统计计数器
        self._stats_lock = threading.Lock()
        self.total_teams = 0
        self.completed_teams = 0
        self.total_files = 0
        self.completed_files = 0
        self.failed_files = 0

    def _inc(self, *, ok: int = 0, fail: int = 0, teams: int = 0):
        with self._stats_lock:
            self.completed_files += ok
            self.failed_files += fail
            self.completed_teams += teams

    # ─────────────── CSV / 团队数据 ───────────────

    @staticmethod
    def _col(row: dict, *names: str) -> str:
        """从 CSV 行中取第一个匹配的列值"""
        for n in names:
            v = row.get(n)
            if v is not None:
                return str(v).strip()
        return ""

    @staticmethod
    def _strip(s: str) -> str:
        return s.strip().strip("/")

    def load_csv(self, csv_path: str) -> List[Dict[str, str]]:
        """加载 CSV 文件（兼容 BOM）"""
        logger.info("正在读取 CSV: %s", csv_path)
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise RuntimeError(f"CSV 为空: {csv_path}")
        logger.info("✔ 读取到 %d 行", len(rows))
        return rows

    def load_teams(self) -> List[TeamInfo]:
        """从 API 获取展平后的团队列表"""
        flat = self.tm.flatten_teams()
        return [
            TeamInfo(
                id=t["id"],
                name=t["name"],
                original_name=t["original_name"],
                space_id=t["spaceId"],
                org_id=t["orgId"],
            )
            for t in flat
        ]

    def group_by_team(self, csv_rows: List[Dict[str, str]],
                      teams: List[TeamInfo]) -> Dict[str, List[UploadMapping]]:
        """
        将 CSV 行按团队分组，返回 {original_name: [UploadMapping, ...]}。
        CSV 中的 本地路径 支持两种格式：
          - 相对路径 → 通过 WORKSPACE_PATH 解析为绝对路径
          - 绝对路径 → 直接使用（向后兼容）
        匹配策略：精确 original_name → 精确 name → 汉字模糊匹配
        """
        # 构建多种索引
        norm = TokenManager.norm_name
        by_original: Dict[str, TeamInfo] = {}
        by_name: Dict[str, TeamInfo] = {}
        by_han: Dict[str, TeamInfo] = {}
        for t in teams:
            key_orig = t.original_name.replace("\u3000", " ").strip().lower()
            key_name = t.name.replace("\u3000", " ").strip().lower()
            by_original[key_orig] = t
            by_name[key_name] = t
            han_key = norm(t.original_name) or norm(t.name)
            if han_key:
                by_han[han_key] = t

        workspace = self.config.workspace
        grouped: Dict[str, List[UploadMapping]] = defaultdict(list)
        miss = 0

        for row in csv_rows:
            team_raw = self._col(row, "团队", "团队名称", "team", "Team", "team_name")
            file_name = self._col(row, "文件名", "filename", "name")
            task_type = self._col(row, "任务", "task")
            upload_path = self._col(row, "上传路径", "path", "目标路径", "aimed_path",
                                    "drive_dir")
            local_path = self._col(row, "本地路径", "local_path")

            if not (team_raw and file_name):
                logger.warning("CSV 行缺少团队/文件名，跳过: %s", row)
                continue

            # 团队匹配
            team_key = team_raw.replace("\u3000", " ").strip().lower()
            team = (by_original.get(team_key)
                    or by_name.get(team_key)
                    or by_han.get(norm(team_raw)))

            if not team:
                miss += 1
                logger.warning("团队名未匹配: '%s' (文件: %s)", team_raw, file_name)
                continue

            # ★ 解析本地路径：相对路径通过 WORKSPACE_PATH 解析
            lp = local_path or file_name
            if not os.path.isabs(lp):
                lp = workspace.resolve(lp)
            else:
                lp = os.path.abspath(lp)

            if not os.path.exists(lp):
                logger.warning("本地文件不存在，跳过: %s", lp)
                continue

            # 远程路径
            up = self._strip(upload_path)
            fn = file_name.lstrip("/")
            remote = f"{up}/{fn}" if up else fn

            grouped[team.original_name].append(UploadMapping(
                local_path=lp,
                remote_path=remote,
                space_id=team.space_id,
                team_name=team.original_name,
                file_name=file_name,
                task_type=task_type,
            ))

        if miss:
            logger.warning("共 %d 行未匹配到团队", miss)

        logger.info("✔ 分组完成，涉及 %d 个团队", len(grouped))
        for tn, files in grouped.items():
            logger.info("  - %s: %d 个文件", tn, len(files))
        return grouped

    # ─────────────── 目录创建 ───────────────

    async def _ensure_dir(self, session: aiohttp.ClientSession,
                          space_id: str, dir_path: str,
                          access_token: str) -> bool:
        """递归创建远程目录（带去重缓存）"""
        if not dir_path:
            return True

        parts = [p for p in dir_path.split("/") if p]
        current = ""

        for part in parts:
            current = f"{current}/{part}" if current else part

            with self._dir_lock:
                if current in self._created_dirs[space_id]:
                    continue

            ok = await self.api.create_directory(
                session, space_id, current, access_token, "ask"
            )
            if ok:
                with self._dir_lock:
                    self._created_dirs[space_id].add(current)
            else:
                return False
        return True

    # ─────────────── 单文件上传 ───────────────

    async def _upload_one(self, session: aiohttp.ClientSession,
                          mapping: UploadMapping, access_token: str,
                          prefix: str = "") -> Dict[str, Any]:
        """上传单个文件（完整流程）"""
        try:
            file_size = os.path.getsize(mapping.local_path)

            # 1. 确保目录存在
            dir_path = "/".join(mapping.remote_path.split("/")[:-1])
            if dir_path:
                ok = await self._ensure_dir(session, mapping.space_id,
                                            dir_path, access_token)
                if not ok:
                    raise RuntimeError(f"创建目录失败: {dir_path}")

            # 2. 获取上传授权（含 202 hash 协商）
            auth = await self.api.get_upload_auth(
                session, mapping.space_id, mapping.remote_path,
                file_size, mapping.local_path, access_token,
                self.upload_cfg.conflict_strategy,
            )

            # 3. 秒传
            if auth.get("isInstantUpload"):
                self._inc(ok=1)
                logger.info("✔ %s[%d/%d] 秒传: %s/%s",
                            prefix, self.completed_files, self.total_files,
                            mapping.team_name, mapping.file_name)
                return {"success": True, "mapping": mapping, "instant": True}

            # 4. 上传到 COS
            await self.api.upload_to_cos(session, mapping.local_path, auth)

            # 5. 确认
            result = await self.api.confirm_upload(
                session, mapping.space_id, auth["confirmKey"],
                access_token, self.upload_cfg.conflict_strategy,
            )

            self._inc(ok=1)
            logger.info("✔ %s[%d/%d] 上传成功: %s/%s",
                        prefix, self.completed_files, self.total_files,
                        mapping.team_name, mapping.file_name)
            return {"success": True, "mapping": mapping, "result": result}

        except Exception as e:
            self._inc(fail=1)
            logger.error("✗ %s上传失败: %s/%s — %s",
                         prefix, mapping.team_name, mapping.file_name, e)
            return {"success": False, "mapping": mapping, "error": str(e)}

    # ─────────────── 团队级上传 ───────────────

    def _file_concurrent(self, count: int) -> int:
        """根据文件数动态计算文件级并发数"""
        if not self.upload_cfg.auto_adjust_concurrent:
            return self.upload_cfg.max_file_concurrent
        if count >= 20:
            return min(10, count)
        if count >= 10:
            return min(8, count)
        if count >= 5:
            return min(5, count)
        return min(3, count)

    async def _upload_team(self, team_name: str,
                           mappings: List[UploadMapping],
                           team: TeamInfo,
                           idx: int) -> List[Dict[str, Any]]:
        """上传一个团队的所有文件"""
        prefix = f"[团队{idx + 1}] "
        logger.info("\n%s%s", prefix, "=" * 50)
        logger.info("%s开始上传: %s (%d 个文件)", prefix, team_name, len(mappings))

        # 获取 access_token
        at = self.tm.get_access_token(
            team.space_id, team.org_id, self.config.jws.library_id
        )
        if not at:
            logger.error("%s无法获取 access_token，跳过", prefix)
            return [{"success": False, "mapping": m,
                     "error": "access_token 获取失败"} for m in mappings]

        fc = self._file_concurrent(len(mappings))
        logger.info("%s文件并发数: %d", prefix, fc)
        sem = asyncio.Semaphore(fc)

        async def _guarded(m: UploadMapping):
            async with sem:
                return await self._upload_one(session, m, at, prefix)

        connector = aiohttp.TCPConnector(limit=30, limit_per_host=15)
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        async with aiohttp.ClientSession(connector=connector,
                                         timeout=timeout) as session:
            t0 = time.time()
            raw = await asyncio.gather(
                *[_guarded(m) for m in mappings], return_exceptions=True
            )
            elapsed = time.time() - t0

        # 处理结果
        results = []
        for i, r in enumerate(raw):
            if isinstance(r, Exception):
                results.append({"success": False, "mapping": mappings[i],
                                "error": str(r)})
                self._inc(fail=1)
            else:
                results.append(r)

        ok_cnt = sum(1 for r in results if r["success"])
        self._inc(teams=1)
        logger.info("%s完成: 成功 %d/%d，耗时 %.1fs",
                    prefix, ok_cnt, len(results), elapsed)
        return results

    # ─────────────── 批量上传入口 ───────────────

    async def run(self, csv_path: str) -> Dict[str, Any]:
        """
        端到端批量上传。
        1. 读 CSV  2. 获取团队列表  3. 按团队分组  4. 嵌套并发上传
        """
        t0 = time.time()
        logger.info("🚀 开始批量上传（JWS 鉴权 + 动态团队列表）")

        # 数据准备
        csv_rows = self.load_csv(csv_path)
        teams = self.load_teams()
        grouped = self.group_by_team(csv_rows, teams)

        # 初始化统计
        self.total_teams = len(grouped)
        self.total_files = sum(len(v) for v in grouped.values())
        self.completed_teams = self.completed_files = self.failed_files = 0

        # 团队索引
        team_map = {}
        for t in teams:
            team_map[t.original_name] = t
            team_map[t.name] = t

        # 团队并发
        tc = self.upload_cfg.max_team_concurrent
        if self.upload_cfg.auto_adjust_concurrent:
            n = len(grouped)
            tc = min(6, n) if n >= 10 else (min(4, n) if n >= 5 else min(3, n))

        logger.info("\n📊 任务概览:")
        logger.info("  📁 团队: %d 个", self.total_teams)
        logger.info("  📄 文件: %d 个", self.total_files)
        logger.info("  🔄 团队并发: %d", tc)

        team_sem = asyncio.Semaphore(tc)

        async def _team_task(item):
            name, files, i = item
            async with team_sem:
                t = team_map.get(name)
                if not t:
                    return [{"success": False, "mapping": m,
                             "error": "团队信息缺失"} for m in files]
                return await self._upload_team(name, files, t, i)

        items = [(name, files, i)
                 for i, (name, files) in enumerate(grouped.items())]
        all_raw = await asyncio.gather(
            *[_team_task(it) for it in items], return_exceptions=True
        )

        # 汇总
        by_team = {}
        for i, r in enumerate(all_raw):
            tn = items[i][0]
            by_team[tn] = r if not isinstance(r, Exception) else []

        elapsed = time.time() - t0
        speed = self.completed_files / elapsed if elapsed > 0 else 0

        logger.info("\n" + "=" * 60)
        logger.info("🎉 批量上传完成!")
        logger.info("=" * 60)
        logger.info("  ✅ 成功: %d", self.completed_files)
        logger.info("  ❌ 失败: %d", self.failed_files)
        logger.info("  📁 团队: %d/%d", self.completed_teams, self.total_teams)
        logger.info("  ⏱ 耗时: %.1fs (%.1f 文件/s)", elapsed, speed)
        logger.info("  🔐 鉴权: JWS 动态")

        return {
            "total_teams": self.total_teams,
            "completed_teams": self.completed_teams,
            "total_files": self.total_files,
            "successful_files": self.completed_files,
            "failed_files": self.failed_files,
            "total_time": elapsed,
            "average_speed": speed,
            "results_by_team": by_team,
        }
