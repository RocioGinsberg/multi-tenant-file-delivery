# -*- coding: utf-8 -*-
"""
配置管理模块
所有参数从环境变量（.env）读取，不依赖外部 JSON 文件。
"""
import os
from dataclasses import dataclass, field
from typing import Dict, Any
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────── JWS 鉴权配置 ───────────────────────────

@dataclass
class JWSConfig:
    """JWS 鉴权所需参数（全部来自 .env）"""
    app_id: str
    private_key_file: str
    org_id: str
    library_id: str
    country_code: str
    phone_number: str
    token_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "JWSConfig":
        missing = []
        for var in ("APP_ID", "PRIVATE_KEY_FILE", "ORG_ID", "LIBRARY_ID", "PHONE_NUMBER"):
            if not os.getenv(var):
                missing.append(var)
        if missing:
            raise RuntimeError(f"缺少必需的环境变量: {', '.join(missing)}")

        return cls(
            app_id=os.environ["APP_ID"],
            private_key_file=os.environ["PRIVATE_KEY_FILE"],
            org_id=os.environ["ORG_ID"],
            library_id=os.environ["LIBRARY_ID"],
            country_code=os.getenv("COUNTRY_CODE", "+86"),
            phone_number=os.environ["PHONE_NUMBER"],
            token_ttl_seconds=int(os.getenv("TOKEN_TTL_SECONDS", "300")),
        )


# ─────────────────────────── API 端点配置 ───────────────────────────

@dataclass
class APIConfig:
    """API 端点地址（与腾讯 SMH 服务端点对齐）"""
    base_public: str = "https://api.tencentsmh.cn"
    base_app: str = "https://api.tencentsmh.cn/api/v1"

    # ── 公共 API ──
    def url_user_token(self) -> str:
        return f"{self.base_public}/user/v1/token"

    def url_team_tree(self, org_id: str) -> str:
        return f"{self.base_public}/user/v1/team/{org_id}/"

    def url_space_token(self, org_id: str, space_id: str) -> str:
        return f"{self.base_public}/user/v1/space/{org_id}/token/{space_id}"

    # ── RESTful 文件/目录操作 ──
    def url_directory(self, library_id: str, space_id: str, path: str) -> str:
        return f"{self.base_app}/directory/{library_id}/{space_id}/{quote(path)}"

    def url_file(self, library_id: str, space_id: str, path: str) -> str:
        return f"{self.base_app}/file/{library_id}/{space_id}/{quote(path)}"


# ─────────────────────────── 上传行为配置 ───────────────────────────

@dataclass
class UploadConfig:
    """并发与策略配置"""
    max_team_concurrent: int = 3
    max_file_concurrent: int = 5
    conflict_strategy: str = "overwrite"        # overwrite / rename / refuse / ask
    auto_adjust_concurrent: bool = True

    @classmethod
    def from_env(cls) -> "UploadConfig":
        return cls(
            max_team_concurrent=int(os.getenv("MAX_TEAM_CONCURRENT", "3")),
            max_file_concurrent=int(os.getenv("MAX_FILE_CONCURRENT", "5")),
            conflict_strategy=os.getenv("CONFLICT_STRATEGY", "overwrite"),
            auto_adjust_concurrent=os.getenv("AUTO_ADJUST_CONCURRENT", "true").lower() == "true",
        )


# ─────────────────────────── 工作目录配置 ───────────────────────────

@dataclass
class WorkspaceConfig:
    """工作目录配置：用于将 CSV 中的相对路径解析为绝对路径"""
    workspace_path: str  # 文件所在根目录

    @classmethod
    def from_env(cls) -> "WorkspaceConfig":
        wp = os.getenv("WORKSPACE_PATH", "")
        if not wp:
            raise RuntimeError(
                "缺少环境变量 WORKSPACE_PATH（分类结果 CSV 中本地路径的基准目录）"
            )
        wp = os.path.expanduser(wp)
        if not os.path.isdir(wp):
            raise RuntimeError(f"WORKSPACE_PATH 目录不存在: {wp}")
        return cls(workspace_path=os.path.abspath(wp))

    def resolve(self, relative_path: str) -> str:
        """将相对路径解析为绝对路径"""
        return os.path.join(self.workspace_path, relative_path)


# ─────────────────────────── 全局配置入口 ───────────────────────────

class Config:
    """组合所有子配置"""

    def __init__(self):
        self.jws = JWSConfig.from_env()
        self.api = APIConfig()
        self.upload = UploadConfig.from_env()
        self.workspace = WorkspaceConfig.from_env()

    def summary(self) -> Dict[str, Any]:
        """脱敏摘要，用于日志"""
        phone = self.jws.phone_number
        masked_phone = phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "****"
        return {
            "app_id": self.jws.app_id,
            "org_id": self.jws.org_id,
            "library_id": self.jws.library_id,
            "phone": masked_phone,
            "workspace": self.workspace.workspace_path,
            "team_concurrent": self.upload.max_team_concurrent,
            "file_concurrent": self.upload.max_file_concurrent,
            "conflict_strategy": self.upload.conflict_strategy,
        }
