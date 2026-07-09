"""HTML 邮件发送器 - 发送格式化的招标信息邮件"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
from typing import List, Optional

from .models import BidItem

logger = logging.getLogger(__name__)


class MailSender:
    """邮件发送器

    发送 HTML 格式邮件，包含：
    - 招标信息表格（标题、来源、日期、类别）
    - 可点击的详情链接
    - 内容摘要
    """

    def __init__(self, config: dict):
        self.smtp_server: str = config.get("smtp_server", "")
        self.smtp_port: int = config.get("smtp_port", 465)
        self.use_ssl: bool = config.get("use_ssl", True)
        self.sender: str = config.get("sender", "")
        self.password: str = config.get("password", "")
        self.receiver: str = config.get("receiver", "")

    def send(self, items: List[BidItem]) -> bool:
        """发送邮件

        Args:
            items: 要发送的条目列表

        Returns:
            True 发送成功，False 失败
        """
        if not items:
            logger.info("没有新项目，跳过发送邮件")
            return True

        try:
            # 构建邮件
            subject = self._build_subject(len(items))
            html_content = self._build_html_content(items)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = Header(self._format_sender(), "utf-8")
            msg["To"] = Header(self.receiver, "utf-8")

            # 纯文本回退
            plain_text = self._build_plain_text(items)
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            # 发送
            with self._get_smtp_connection() as server:
                server.login(self.sender, self.password)
                recipients = [r.strip() for r in self.receiver.split(",")]
                server.sendmail(self.sender, recipients, msg.as_string())

            logger.info(f"邮件发送成功: {len(items)} 个项目 -> {self.receiver}")
            return True

        except Exception as e:
            logger.error(f"邮件发送失败: {e}", exc_info=True)
            return False

    def _get_smtp_connection(self):
        """获取 SMTP 连接"""
        if self.use_ssl:
            return smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
            server.starttls()
            return server

    def _format_sender(self) -> str:
        """格式化发件人显示名"""
        return f"RailBuddy 监控 <{self.sender}>"

    @staticmethod
    def _build_subject(count: int) -> str:
        """构建邮件主题"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"[RailBuddy] 城轨招标监控 - 发现 {count} 个新项目 ({now})"

    def _build_html_content(self, items: List[BidItem]) -> str:
        """构建 HTML 邮件正文"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 按类别分组
        categories = {}
        for item in items:
            cat = item.category or "其他"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        # 构建各分类的表格
        category_sections = []
        for cat, cat_items in categories.items():
            rows = []
            for idx, item in enumerate(cat_items, 1):
                rows.append(self._build_item_row(item, idx))

            category_sections.append(f"""
            <div class="category-section">
                <h3 class="category-title">{cat} ({len(cat_items)})</h3>
                <table class="item-table">
                    <thead>
                        <tr>
                            <th class="col-num">#</th>
                            <th class="col-title">标题</th>
                            <th class="col-source">来源</th>
                            <th class="col-date">日期</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            </div>
            """)

        return f"""\
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
            margin: 0; padding: 0;
            background-color: #f5f5f5;
            color: #333;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background: linear-gradient(135deg, #1a73e8, #0d47a1);
            color: white;
            padding: 24px 30px;
            border-radius: 8px 8px 0 0;
        }}
        .header h1 {{
            margin: 0 0 8px 0;
            font-size: 22px;
        }}
        .header .meta {{
            font-size: 13px;
            opacity: 0.9;
        }}
        .content {{
            background: white;
            padding: 24px 30px;
            border-radius: 0 0 8px 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .summary {{
            background: #e8f0fe;
            border-left: 4px solid #1a73e8;
            padding: 12px 16px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .category-section {{
            margin-bottom: 24px;
        }}
        .category-title {{
            color: #1a73e8;
            font-size: 16px;
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 6px;
            margin: 0 0 12px 0;
        }}
        .item-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        .item-table th {{
            background: #f0f4f8;
            color: #555;
            font-weight: 600;
            padding: 8px 10px;
            text-align: left;
            border-bottom: 2px solid #ddd;
        }}
        .item-table td {{
            padding: 10px;
            border-bottom: 1px solid #eee;
            vertical-align: top;
        }}
        .item-table tr:hover {{
            background: #fafafa;
        }}
        .col-num {{ width: 36px; text-align: center; color: #999; }}
        .col-title {{ }}
        .col-source {{ width: 140px; color: #666; }}
        .col-date {{ width: 100px; color: #666; white-space: nowrap; }}
        .item-title a {{
            color: #1a73e8;
            text-decoration: none;
            font-weight: 500;
        }}
        .item-title a:hover {{
            text-decoration: underline;
        }}
        .item-desc {{
            color: #777;
            font-size: 13px;
            margin-top: 4px;
            line-height: 1.5;
        }}
        .item-link {{
            color: #999;
            font-size: 12px;
            margin-top: 4px;
            word-break: break-all;
        }}
        .footer {{
            text-align: center;
            padding: 16px;
            color: #999;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>城市轨道交通招标信息监控报告</h1>
            <div class="meta">
                生成时间：{now_str} ｜
                新增项目：{len(items)} 个 ｜
                数据源：{len(set(i.source for i in items))} 个
            </div>
        </div>
        <div class="content">
            <div class="summary">
                本报告由 RailBuddy 自动监控系统生成。以下为最新抓取到的城市轨道交通招标相关信息，
                点击标题可查看详情。
            </div>
            {''.join(category_sections)}
        </div>
        <div class="footer">
            本邮件由 RailBuddy 自动监控系统发送，请勿直接回复<br>
            Powered by RailBuddy v1.0
        </div>
    </div>
</body>
</html>
"""

    @staticmethod
    def _build_item_row(item: BidItem, idx: int) -> str:
        """构建单条目的 HTML 表格行"""
        # 标题（带链接）
        title_html = (
            f'<div class="item-title">'
            f'<a href="{item.url}" target="_blank">{item.title}</a>'
            f"</div>"
        )

        # 摘要
        if item.description:
            desc = item.description[:200] + ("..." if len(item.description) > 200 else "")
            title_html += f'<div class="item-desc">{desc}</div>'

        # 链接地址
        title_html += f'<div class="item-link">{item.url}</div>'

        # 日期
        date_str = item.publish_date or "未知"

        return f"""
            <tr>
                <td class="col-num">{idx}</td>
                <td class="col-title">{title_html}</td>
                <td class="col-source">{item.source}</td>
                <td class="col-date">{date_str}</td>
            </tr>
        """

    def _build_plain_text(self, items: List[BidItem]) -> str:
        """构建纯文本邮件正文（回退用）"""
        lines = [
            "=" * 60,
            "  城市轨道交通招标信息监控报告",
            f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  新增项目: {len(items)} 个",
            "=" * 60,
            "",
        ]

        for idx, item in enumerate(items, 1):
            lines.append(f"[{idx}] {item.title}")
            lines.append(f"    来源: {item.source}")
            lines.append(f"    日期: {item.publish_date or '未知'}")
            lines.append(f"    链接: {item.url}")
            if item.description:
                desc = item.description[:200] + ("..." if len(item.description) > 200 else "")
                lines.append(f"    摘要: {desc}")
            if item.category:
                lines.append(f"    类别: {item.category}")
            lines.append("")
            lines.append("-" * 50)
            lines.append("")

        lines.append("=" * 60)
        lines.append("  本邮件由 RailBuddy 自动监控系统发送，请勿直接回复")
        lines.append("=" * 60)
        return "\n".join(lines)
