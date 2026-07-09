"""RailBuddy 命令行入口

用法:
    python -m railbuddy                    # 启动服务
    python -m railbuddy --config path.yaml # 指定配置文件
    python -m railbuddy --once             # 只执行一次抓取（不启动调度器）
    python -m railbuddy --status           # 查看状态
    python -m railbuddy --test-email       # 测试邮件发送
    python -m railbuddy --web              # 启动 Web 管理面板
    python -m railbuddy --web --port 8080  # 指定 Web 端口
"""

import argparse
import sys
import os
import logging

from . import __version__, __app_name__


def main():
    parser = argparse.ArgumentParser(
        prog="railbuddy",
        description=f"{__app_name__} - 城市轨道交通招标信息监控服务 v{__version__}"
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一次抓取并发送，不启动定时调度"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="查看当前状态（数据库统计、各源抓取状态）"
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="发送一封测试邮件（验证邮箱配置）"
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="启动 Web 管理面板（可视化配置数据源、邮箱、调度等）"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5210,
        help="Web 管理面板端口 (默认: 5210，仅 --web 模式有效)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Web 管理面板监听地址 (默认: 0.0.0.0)"
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"{__app_name__} v{__version__}"
    )

    args = parser.parse_args()

    # 延迟导入，避免在 --version 等不需要时加载全部依赖
    from .app import RailBuddyApp
    from .utils.logger import setup_logging
    from .models import BidItem
    from datetime import datetime

    # 状态查看模式
    if args.status:
        setup_logging(level="WARNING")
        app = RailBuddyApp(args.config)
        stats = app.db.get_stats()
        print(f"\n{'='*50}")
        print(f"  RailBuddy 状态")
        print(f"{'='*50}")
        print(f"  数据库: {app.config.db_path}")
        print(f"  总条目: {stats['total_items']}")
        print(f"  已发送: {stats['sent_items']}")
        print(f"  待发送: {stats['unsent_items']}")
        print(f"  监控源: {stats['tracked_sources']}")
        print(f"{'='*50}")

        source_states = app.db.get_all_source_states()
        if source_states:
            print(f"\n  各源抓取状态:")
            for ss in source_states:
                print(f"    {ss['name']}:")
                print(f"      上次抓取: {ss.get('last_fetch_time', '无')}")
                print(f"      抓取状态: {ss.get('last_fetch_status', '未知')}")
                print(f"      条目数量: {ss.get('last_fetch_count', 0)}")
                if ss.get('last_error'):
                    print(f"      错误信息: {ss['last_error']}")

        last_send = app.db.get_last_send_log()
        if last_send:
            print(f"\n  最后一次发送:")
            print(f"    时间: {last_send['sent_at']}")
            print(f"    数量: {last_send['item_count']}")
            print(f"    状态: {last_send['status']}")
        print()
        return

    # 测试邮件模式
    if args.test_email:
        setup_logging(level="INFO")
        app = RailBuddyApp(args.config)
        test_item = BidItem(
            title="[测试] 这是一封来自 RailBuddy 的测试邮件",
            url="https://example.com",
            source="RailBuddy 测试",
            publish_date=datetime.now().strftime("%Y-%m-%d"),
            description="如果您收到了这封邮件，说明邮箱配置正确。",
            category="测试"
        )
        success = app.mailer.send([test_item])
        if success:
            print("测试邮件发送成功！请检查收件箱。")
        else:
            print("测试邮件发送失败！请检查邮箱配置。")
        return

    # 单次执行模式
    if args.once:
        setup_logging(level="INFO")
        app = RailBuddyApp(args.config)
        app.run_task()
        return

    # Web 管理面板模式
    if args.web:
        setup_logging(level="INFO")
        from .web.server import WebServer
        server = WebServer(
            config_path=args.config,
            host=args.host,
            port=args.port
        )
        print(f"\n{'='*50}")
        print(f"  RailBuddy Web 管理面板")
        print(f"  访问地址: http://localhost:{args.port}")
        print(f"  配置文件: {os.path.abspath(args.config)}")
        print(f"{'='*50}\n")
        server.run()
        return

    # 正常启动服务模式
    app = RailBuddyApp(args.config)
    app.run()


if __name__ == "__main__":
    main()
