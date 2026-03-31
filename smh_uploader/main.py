# -*- coding: utf-8 -*-
"""
主入口
用法:
  python -m smh_uploader run                   # 一条龙: teams → classify → upload
  python -m smh_uploader teams                 # 拉取团队列表 → 保存 JSON
  python -m smh_uploader classify              # 文件分类 → 生成 CSV
  python -m smh_uploader upload [CSV文件]      # 批量上传
  python -m smh_uploader                       # 交互模式
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .config import JWSConfig, APIConfig, Config
from .token_manager import TokenManager
from .api_client import APIClient
from .uploader import BatchUploader
from .classifier import FileClassifier

# ─────────────── 日志配置 ───────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("upload_log.txt", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────── 路径辅助 ───────────────

def _resolve_paths():
    """
    解析公共路径配置，返回 (workspace, config_dir, team_list, task_config, output_csv)。
    优先使用环境变量，其次按约定推导。
    """
    workspace_raw = os.getenv("WORKSPACE_PATH", "")
    if not workspace_raw:
        raise RuntimeError("请在 .env 中设置 WORKSPACE_PATH")

    workspace = Path(workspace_raw).expanduser().resolve()
    config_dir = workspace.parent

    team_list = Path(os.getenv(
        "TEAM_LIST_PATH", str(config_dir / "current_teamlist.json")))
    task_config = Path(os.getenv(
        "TASK_CONFIG_PATH", str(config_dir / "task_classification.json")))
    output_csv = Path(os.getenv(
        "OUTPUT_CSV_PATH", str(config_dir / "分类结果.csv")))

    return workspace, config_dir, team_list, task_config, output_csv


# ─────────────── 子命令: teams ───────────────

async def cmd_teams() -> str:
    """
    从 API 实时拉取团队列表 → 保存为 JSON 文件 → 打印树形结构。
    仅需要 JWS 配置，不需要 WORKSPACE_PATH（独立运行时）。
    返回保存的 JSON 文件路径。
    """
    logger.info("=" * 60)
    logger.info("步骤 1/3: 拉取团队列表")
    logger.info("=" * 60)

    jws = JWSConfig.from_env()
    api = APIConfig()
    tm = TokenManager(jws, api)

    # ── 展平团队 → 保存 JSON ──
    flat_teams = tm.flatten_teams()

    # 确定保存路径
    try:
        _, _, team_list_path, _, _ = _resolve_paths()
    except RuntimeError:
        # WORKSPACE_PATH 未设置时的 fallback
        team_list_path = Path(os.getenv(
            "TEAM_LIST_PATH", "./current_teamlist.json"))

    team_list_path = team_list_path.resolve()
    with open(team_list_path, "w", encoding="utf-8") as f:
        json.dump(flat_teams, f, ensure_ascii=False, indent=2)
    logger.info("✔ 团队列表已保存: %s (%d 个团队)", team_list_path, len(flat_teams))

    # ── 打印树形结构（从原始 API 数据） ──
    raw_teams = tm.get_cached_team_list()

    def _print_tree(node: dict, indent: int = 0):
        name = node.get("name") or node.get("teamName", "未命名")
        space = node.get("spaceId") or node.get("space_id", "-")
        logger.info("%s├─ %s (space: %s)", "  " * indent, name, space)
        for child in node.get("children", []):
            _print_tree(child, indent + 1)

    logger.info("\n📁 团队结构 (%d 个顶级):\n", len(raw_teams))
    for t in raw_teams:
        _print_tree(t)

    return str(team_list_path)


# ─────────────── 子命令: classify ───────────────

async def cmd_classify() -> str:
    """
    读取团队 JSON + 任务配置 → 扫描 workspace → 输出 CSV。
    返回生成的 CSV 文件路径。
    """
    logger.info("=" * 60)
    logger.info("步骤 2/3: 文件分类")
    logger.info("=" * 60)

    workspace, _, team_list, task_config, output_csv = _resolve_paths()

    if not team_list.exists():
        logger.error("❌ 团队列表不存在: %s", team_list)
        logger.error("   请先运行: python -m smh_uploader teams")
        return ""

    if not task_config.exists():
        logger.error("❌ 任务配置不存在: %s", task_config)
        return ""

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
    return result or ""


# ─────────────── 子命令: upload ───────────────

async def cmd_upload(csv_file: str = "") -> dict:
    """批量上传文件。返回上传结果摘要。"""
    logger.info("=" * 60)
    logger.info("步骤 3/3: 批量上传")
    logger.info("=" * 60)

    if not csv_file:
        _, _, _, _, output_csv = _resolve_paths()
        csv_file = str(output_csv)

    if not Path(csv_file).exists():
        logger.error("❌ CSV 不存在: %s", csv_file)
        logger.error("   请先运行: python -m smh_uploader classify")
        return {}

    cfg = Config()
    logger.info("配置摘要: %s", cfg.summary())

    tm = TokenManager(cfg.jws, cfg.api)
    api = APIClient(cfg.api, cfg.jws.library_id)
    uploader = BatchUploader(cfg, tm, api)

    results = await uploader.run(csv_file)

    # 输出失败明细
    failed = []
    for team_results in results["results_by_team"].values():
        if isinstance(team_results, list):
            for r in team_results:
                if isinstance(r, dict) and not r.get("success"):
                    failed.append(r)

    if failed:
        logger.info("\n❌ 失败文件 (%d 个):", len(failed))
        for r in failed[:20]:
            m = r.get("mapping")
            if m:
                logger.info("  - %s/%s: %s", m.team_name, m.file_name,
                            r.get("error", "未知"))
        if len(failed) > 20:
            logger.info("  ... 还有 %d 个", len(failed) - 20)

    logger.info("\n✅ 上传任务完成")
    return results


# ─────────────── 子命令: run（一条龙） ───────────────

async def cmd_run():
    """一条龙执行: teams → classify → upload"""
    logger.info("\n" + "🚀" * 20)
    logger.info("一条龙模式: teams → classify → upload")
    logger.info("🚀" * 20 + "\n")

    # 1. 拉取团队列表
    team_json = await cmd_teams()
    if not team_json:
        logger.error("❌ 拉取团队列表失败，终止")
        return

    # 2. 文件分类
    csv_file = await cmd_classify()
    if not csv_file:
        logger.error("❌ 分类失败或无文件，终止")
        return

    # 3. 确认上传
    print(f"\n📋 分类结果: {csv_file}")
    confirm = input("确认开始上传? (y/n，默认 y): ").strip().lower()
    if confirm and confirm not in ("y", "yes", "是"):
        logger.info("⏸️  已取消上传")
        return

    # 4. 上传
    await cmd_upload(csv_file)


# ─────────────── 交互模式 ───────────────

async def interactive():
    print("\n" + "=" * 60)
    print("腾讯企业网盘批量操作工具 v2.0 (JWS 鉴权)")
    print("=" * 60)
    print("\n推荐流程: teams → classify → upload")
    print()
    print("  1. 一条龙执行 (teams → classify → upload)")
    print("  2. 拉取团队列表 (teams)")
    print("  3. 文件分类 (classify)")
    print("  4. 批量上传 (upload)")
    print("  5. 退出")

    choice = input("\n请输入 (1-5): ").strip()

    if choice == "1":
        await cmd_run()
    elif choice == "2":
        await cmd_teams()
    elif choice == "3":
        await cmd_classify()
    elif choice == "4":
        csv_file = input("CSV 文件路径 (回车使用默认): ").strip()
        await cmd_upload(csv_file)
    elif choice == "5":
        print("再见！")
    else:
        print("无效选项")


# ─────────────── CLI 入口 ───────────────

def _usage():
    print("用法:")
    print("  python -m smh_uploader run                  # 一条龙 (推荐)")
    print("  python -m smh_uploader teams                # 拉取团队列表")
    print("  python -m smh_uploader classify             # 文件分类")
    print("  python -m smh_uploader upload [CSV文件]     # 批量上传")
    print("  python -m smh_uploader                      # 交互模式")


async def main():
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode == "run":
            await cmd_run()
        elif mode == "teams":
            await cmd_teams()
        elif mode == "classify":
            await cmd_classify()
        elif mode == "upload":
            csv_file = sys.argv[2] if len(sys.argv) > 2 else ""
            await cmd_upload(csv_file)
        elif mode in ("help", "--help", "-h"):
            _usage()
        else:
            _usage()
    else:
        await interactive()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\n用户中断")
    except Exception as e:
        logger.error("程序失败: %s", e)
        import traceback
        traceback.print_exc()
        sys.exit(1)
