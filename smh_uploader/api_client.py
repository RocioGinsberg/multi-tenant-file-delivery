# -*- coding: utf-8 -*-
"""
API 客户端模块
封装腾讯 SMH RESTful 文件/目录操作，包括：
- 目录创建/删除
- 文件上传三阶段流程 (presign → COS PUT → confirm)
- 202 hash 协商
- 文件删除/移动
"""
import hashlib
import logging
from typing import Dict, Any, Optional

import aiohttp
import aiofiles
from urllib.parse import urlencode

from .config import APIConfig

logger = logging.getLogger(__name__)


class APIClient:
    """腾讯 SMH API 客户端"""

    def __init__(self, api_config: APIConfig, library_id: str):
        self.api = api_config
        self.library_id = library_id

    # ─────────────── 工具方法 ───────────────

    @staticmethod
    def _params(access_token: str, **extra) -> Dict[str, str]:
        """构建通用请求参数"""
        p = {"access_token": access_token}
        p.update(extra)
        return p

    @staticmethod
    async def calculate_sha256(file_path: str, *, full_file: bool = False) -> str:
        """计算文件 SHA256（前 64KB 或全文件）"""
        h = hashlib.sha256()
        async with aiofiles.open(file_path, "rb") as f:
            if full_file:
                while chunk := await f.read(8192):
                    h.update(chunk)
            else:
                chunk = await f.read(65536)  # 前 64KB
                h.update(chunk)
        return h.hexdigest()

    # ─────────────── 目录操作 ───────────────

    async def create_directory(self, session: aiohttp.ClientSession,
                               space_id: str, path: str, access_token: str,
                               conflict_strategy: str = "ask") -> bool:
        """
        创建目录。
        Returns True 表示创建成功或目录已存在。
        """
        url = self.api.url_directory(self.library_id, space_id, path)
        params = self._params(
            access_token,
            conflict_resolution_strategy=conflict_strategy,
        )
        async with session.put(f"{url}?{urlencode(params)}", json={}) as resp:
            if resp.status in (201, 409):
                return True
            text = await resp.text()
            logger.warning("创建目录失败: %s → %s %s", path, resp.status, text[:200])
            return False

    async def delete_directory(self, session: aiohttp.ClientSession,
                               space_id: str, path: str, access_token: str,
                               permanent: bool = False) -> bool:
        url = self.api.url_directory(self.library_id, space_id, path)
        params = self._params(access_token)
        if permanent:
            params["permanent"] = "1"
        async with session.delete(f"{url}?{urlencode(params)}") as resp:
            return resp.status in (200, 204)

    # ─────────────── 上传授权（含 202 hash 协商） ───────────────

    async def get_upload_auth(self, session: aiohttp.ClientSession,
                              space_id: str, remote_path: str, file_size: int,
                              local_path: str, access_token: str,
                              conflict_strategy: str = "overwrite") -> Dict[str, Any]:
        """
        三阶段上传授权：
          阶段 1：空 payload → 可能返回 202
          阶段 2：带 beginningHash → 可能返回 202
          阶段 3：带 fullHash + beginningHash → 最终授权
        """
        url = self.api.url_file(self.library_id, space_id, remote_path)
        params = self._params(
            access_token,
            conflict_resolution_strategy=conflict_strategy,
            filesize=str(file_size),
            domainTag="0",
        )
        full_url = f"{url}?{urlencode(params)}"

        # ── 阶段 1：无 hash ──
        async with session.put(full_url, json={}) as resp:
            if resp.status in (200, 201):
                return self._tag_instant(await resp.json())
            if resp.status != 202:
                text = await resp.text()
                raise RuntimeError(f"上传授权失败(阶段1): {resp.status} {text[:200]}")

        # ── 阶段 2：前 64KB hash ──
        beginning_hash = await self.calculate_sha256(local_path, full_file=False)
        payload2 = {"beginningHash": beginning_hash, "size": str(file_size)}

        async with session.put(full_url, json=payload2) as resp:
            if resp.status in (200, 201):
                return self._tag_instant(await resp.json())
            if resp.status != 202:
                text = await resp.text()
                raise RuntimeError(f"上传授权失败(阶段2): {resp.status} {text[:200]}")

        # ── 阶段 3：全文件 hash ──
        full_hash = await self.calculate_sha256(local_path, full_file=True)
        payload3 = {
            "fullHash": full_hash,
            "beginningHash": beginning_hash,
            "size": str(file_size),
        }

        async with session.put(full_url, json=payload3) as resp:
            if resp.status in (200, 201):
                return self._tag_instant(await resp.json())
            text = await resp.text()
            raise RuntimeError(f"上传授权失败(阶段3): {resp.status} {text[:200]}")

    @staticmethod
    def _tag_instant(result: dict) -> dict:
        """如果响应中没有 domain 字段，标记为秒传"""
        if "domain" not in result:
            result["isInstantUpload"] = True
        return result

    # ─────────────── COS 上传 ───────────────

    @staticmethod
    async def upload_to_cos(session: aiohttp.ClientSession,
                            local_path: str, auth_data: Dict[str, Any],
                            chunk_size: int = 1024 * 1024) -> None:
        """流式上传文件到 COS"""
        domain = auth_data.get("domain")
        path = auth_data.get("path")
        headers = auth_data.get("headers", {})
        if not (domain and path):
            raise RuntimeError("上传授权缺少 domain/path 字段")

        upload_url = f"https://{domain}{path}"

        async def _file_iter():
            async with aiofiles.open(local_path, "rb") as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        async with session.put(upload_url, headers=headers, data=_file_iter()) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"COS 上传失败: {resp.status} {text[:200]}")

    # ─────────────── 确认上传 ───────────────

    async def confirm_upload(self, session: aiohttp.ClientSession,
                             space_id: str, confirm_key: str,
                             access_token: str,
                             conflict_strategy: str = "overwrite") -> Dict[str, Any]:
        """确认文件上传完成"""
        url = self.api.url_file(self.library_id, space_id, confirm_key)
        params = self._params(
            access_token,
            confirm="",
            conflict_resolution_strategy=conflict_strategy,
        )
        async with session.post(f"{url}?{urlencode(params)}") as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"确认上传失败: {resp.status} {text[:200]}")
            return await resp.json()

    # ─────────────── 文件删除 ───────────────

    async def delete_file(self, session: aiohttp.ClientSession,
                          space_id: str, file_path: str,
                          access_token: str,
                          permanent: bool = False) -> bool:
        url = self.api.url_file(self.library_id, space_id, file_path)
        params = self._params(access_token)
        if permanent:
            params["permanent"] = "1"
        async with session.delete(f"{url}?{urlencode(params)}") as resp:
            return resp.status in (200, 204)

    # ─────────────── 文件移动 ───────────────

    async def move_file(self, session: aiohttp.ClientSession,
                        space_id: str, from_path: str, to_path: str,
                        access_token: str,
                        conflict_strategy: str = "ask") -> Dict[str, Any]:
        url = self.api.url_file(self.library_id, space_id, from_path)
        params = self._params(
            access_token,
            conflict_resolution_strategy=conflict_strategy,
        )
        payload = {"to": to_path}
        async with session.post(f"{url}/move?{urlencode(params)}",
                                json=payload) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"移动文件失败: {resp.status} {text[:200]}")
            return await resp.json()
