# -*- coding: utf-8 -*-
"""
Token 管理模块
- JWS 签名 → user_token
- user_token → access_token (per space)
- 团队列表获取与缓存
全部通过 JWS 鉴权完成，不依赖外部 JSON 文件。
"""
import base64
import json
import os
import re
import time
import logging
from typing import Optional, List, Dict, Any

import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.backends import default_backend

from .config import JWSConfig, APIConfig

logger = logging.getLogger(__name__)


class TokenManager:
    """JWS Token 管理器 —— 自动签发 / 刷新 / 缓存"""

    # 用于规范化团队名称的正则：只保留中文汉字
    _HAN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

    def __init__(self, jws_config: JWSConfig, api_config: APIConfig):
        self.jws = jws_config
        self.api = api_config

        # ── user_token 缓存 ──
        self._user_token: Optional[str] = None
        self._user_token_exp: float = 0.0

        # ── 团队列表缓存 ──
        self._team_list_cache: Optional[List[dict]] = None
        self._team_list_cached_at: float = 0.0

        # ── HTTP Session ──
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._ua(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
        })

    # ─────────────── 工具方法 ───────────────

    @staticmethod
    def _ua() -> str:
        try:
            import platform
            return (f"SMH-Uploader/2.0 "
                    f"({platform.system()} {platform.release()}; "
                    f"Python {platform.python_version()})")
        except Exception:
            return "SMH-Uploader/2.0"

    @staticmethod
    def _b64url(data: bytes) -> bytes:
        """URL-safe Base64 编码（无填充）"""
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    @classmethod
    def norm_name(cls, name: str) -> str:
        """规范化团队名称：只保留汉字，便于模糊匹配"""
        if not name:
            return ""
        return "".join(cls._HAN_RE.findall(str(name)))

    # ─────────────── JWS 签名 ───────────────

    def _load_private_key(self) -> RSAPrivateKey:
        key_file = self.jws.private_key_file
        if not os.path.exists(key_file):
            raise FileNotFoundError(f"私钥文件不存在: {key_file}")
        with open(key_file, "rb") as f:
            pk = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
        if not isinstance(pk, RSAPrivateKey):
            raise RuntimeError("私钥必须是 RSA 格式（RS256 要求）")
        return pk

    def _build_jws(self) -> str:
        """构建 RS256 JWS Token"""
        header = {"alg": "RS256"}
        payload = {
            "exp": int(time.time()) + self.jws.token_ttl_seconds,
            "countryCode": self.jws.country_code,
            "phoneNumber": self.jws.phone_number,
            "type": "user",
        }
        h = self._b64url(json.dumps(header, separators=(",", ":")).encode())
        p = self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = h + b"." + p

        pk = self._load_private_key()
        sig = pk.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return (signing_input + b"." + self._b64url(sig)).decode()

    # ─────────────── user_token ───────────────

    def ensure_user_token(self, *, force: bool = False) -> str:
        """获取 / 自动刷新 user_token"""
        if not force and self._user_token and time.time() < self._user_token_exp - 5:
            return self._user_token

        jws_token = self._build_jws()
        url = self.api.url_user_token()
        params = {
            "auth_type": "jws",
            "app_id": self.jws.app_id,
            "jws_token": jws_token,
        }
        body = {
            "countryCode": self.jws.country_code,
            "phoneNumber": self.jws.phone_number,
            "type": "user",
        }

        logger.info("正在获取 user_token (app_id=%s) ...", self.jws.app_id)
        resp = self._session.post(url, params=params, json=body, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"获取 user_token 失败: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        token = data.get("userToken")
        if not token:
            raise RuntimeError("响应中缺少 userToken 字段")

        expires_in = data.get("expiresIn", self.jws.token_ttl_seconds)
        self._user_token = token
        self._user_token_exp = time.time() + max(60, expires_in - 10)
        logger.info("✔ user_token 获取成功 (有效 %ds)", expires_in)
        return self._user_token

    # ─────────────── access_token (per space) ───────────────

    def get_access_token(self, space_id: str, org_id: str,
                         library_id: str) -> Optional[str]:
        """获取指定团队空间的 access_token"""
        user_token = self.ensure_user_token()
        url = self.api.url_space_token(org_id, space_id)
        params = {
            "user_token": user_token,
            "library_id": library_id,
            "libraryId": library_id,  # 兼容不同网关
        }

        try:
            resp = self._session.post(url, params=params, json={}, timeout=15)
            if resp.status_code == 200:
                at = resp.json().get("accessToken")
                if at:
                    logger.debug("✔ access_token 获取成功 (space=%s)", space_id)
                    return at
                logger.warning("响应中缺少 accessToken 字段")
            else:
                logger.error("获取 access_token 失败: %s %s",
                             resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("获取 access_token 异常: %s", e)
        return None

    # ─────────────── 团队列表 ───────────────

    def fetch_team_list(self, *, with_path: bool = True) -> List[dict]:
        """从 API 在线获取团队列表"""
        user_token = self.ensure_user_token()
        url = self.api.url_team_tree(self.jws.org_id)
        params = {
            "user_token": user_token,
            "with_path": "true" if with_path else "false",
            "check_permission": "1",
            "check_management_permission": "0",
        }

        logger.info("正在获取团队列表 (org_id=%s) ...", self.jws.org_id)
        resp = self._session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"获取团队列表失败: {resp.status_code} {resp.text[:200]}")

        teams = resp.json().get("children", [])
        if not isinstance(teams, list):
            raise RuntimeError("团队列表格式异常")

        logger.info("✔ 成功获取 %d 个顶级团队", len(teams))
        return teams

    def get_cached_team_list(self, *, max_age: int = 120) -> List[dict]:
        """带缓存的团队列表"""
        if self._team_list_cache and (time.time() - self._team_list_cached_at <= max_age):
            return self._team_list_cache
        self._team_list_cache = self.fetch_team_list()
        self._team_list_cached_at = time.time()
        return self._team_list_cache

    def flatten_teams(self) -> List[Dict[str, Any]]:
        """
        将树形团队列表展平为列表，每项包含:
          id, name, original_name, spaceId, orgId
        递归遍历所有子节点。
        """
        raw_teams = self.get_cached_team_list()
        flat: List[Dict[str, Any]] = []

        def _walk(node: dict):
            team_id = node.get("teamId") or node.get("id")
            name = node.get("name") or node.get("teamName")
            original_name = node.get("original_name") or name
            space_id = node.get("spaceId") or node.get("space_id")
            org_id = node.get("orgId") or node.get("org_id") or self.jws.org_id

            if space_id and name:
                flat.append({
                    "id": team_id,
                    "name": str(name),
                    "original_name": str(original_name),
                    "spaceId": str(space_id),
                    "orgId": str(org_id),
                })

            for child in (node.get("children") or []):
                if isinstance(child, dict):
                    _walk(child)

        if isinstance(raw_teams, list):
            for n in raw_teams:
                if isinstance(n, dict):
                    _walk(n)
        elif isinstance(raw_teams, dict):
            _walk(raw_teams)

        if not flat:
            raise RuntimeError("在线团队列表为空，请检查 ORG_ID / 权限 / 账号")

        logger.info("✔ 展平后共 %d 个团队", len(flat))
        return flat

    # ─────────────── 缓存控制 ───────────────

    def refresh(self):
        """强制刷新所有缓存"""
        self._user_token = None
        self._user_token_exp = 0.0
        self._team_list_cache = None
        self._team_list_cached_at = 0.0
        self.ensure_user_token(force=True)
        logger.info("✔ 所有缓存已刷新")
