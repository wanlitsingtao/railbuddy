"""HTML 邮件发送器 - 发送格式化的招标信息邮件"""

import smtplib
import socket
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr, formatdate, make_msgid
from datetime import datetime
from typing import List, Optional, Tuple

from .models import BidItem

logger = logging.getLogger(__name__)


# 常见邮箱提供商的 SMTP 配置
EMAIL_PROVIDER_PRESETS = {
    "qq.com": {
        "label": "QQ邮箱",
        "smtp_server": "smtp.qq.com",
        "smtp_port": 465,
        "use_ssl": True,
        "alt_port": 587,
        "note": "QQ邮箱需使用「授权码」而非登录密码。请在 QQ邮箱 → 设置 → 账户 → POP3/SMTP 服务 中生成。"
    },
    "163.com": {
        "label": "163邮箱",
        "smtp_server": "smtp.163.com",
        "smtp_port": 465,
        "use_ssl": True,
        "alt_port": 25,
        "note": "163邮箱需在 设置 → POP3/SMTP/IMAP 中开启 SMTP 服务，并使用「客户端授权密码」。"
    },
    "gmail.com": {
        "label": "Gmail",
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "use_ssl": False,
        "alt_port": 465,
        "note": "Gmail 需开启「两步验证」后生成「应用专用密码」使用。"
    },
    "outlook.com": {
        "label": "Outlook/Hotmail",
        "smtp_server": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "use_ssl": False,
        "alt_port": 587,
        "note": ""
    },
    "sina.com": {
        "label": "新浪邮箱",
        "smtp_server": "smtp.sina.com",
        "smtp_port": 465,
        "use_ssl": True,
        "alt_port": 587,
        "note": ""
    },
    "126.com": {
        "label": "126邮箱",
        "smtp_server": "smtp.126.com",
        "smtp_port": 465,
        "use_ssl": True,
        "alt_port": 25,
        "note": ""
    },
    "yeah.net": {
        "label": "Yeah邮箱",
        "smtp_server": "smtp.yeah.net",
        "smtp_port": 465,
        "use_ssl": True,
        "alt_port": 25,
        "note": ""
    },
    "aliyun.com": {
        "label": "阿里企业邮箱",
        "smtp_server": "smtp.aliyun.com",
        "smtp_port": 465,
        "use_ssl": True,
        "alt_port": 25,
        "note": ""
    },
}


def get_provider_hint(sender_email: str) -> Optional[dict]:
    """根据发件邮箱域名返回对应提供商的 SMTP 配置和提示"""
    if not sender_email or "@" not in sender_email:
        return None
    domain = sender_email.split("@")[-1].lower().strip()
    return EMAIL_PROVIDER_PRESETS.get(domain)


def diagnose_smtp(smtp_server: str, smtp_port: int, use_ssl: bool, sender: str, password: str,
                  timeout: int = 10) -> Tuple[bool, str]:
    """SMTP 连接诊断，尝试发送 HELO 和登录

    Returns:
        (success, detail_message)
    """
    results = []
    connection_ok = False

    # 步骤1：DNS 解析
    try:
        ip = socket.getaddrinfo(smtp_server, smtp_port)
        results.append(f"✓ DNS 解析成功: {smtp_server} -> {ip[0][4][0]}")
        connection_ok = True
    except socket.gaierror:
        results.append(f"✗ DNS 解析失败: {smtp_server}，请检查 SMTP 服务器地址是否正确")
        return False, "\n".join(results)

    # 步骤2：TCP 连接 + SMTP 协议
    if connection_ok:
        try:
            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=timeout)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=timeout)
                server.ehlo()
                if server.has_extn("starttls"):
                    server.starttls()
                    results.append("✓ STARTTLS 协商成功")
                    server.ehlo()

            results.append(f"✓ SMTP 连接成功 ({smtp_server}:{smtp_port}, {'SSL' if use_ssl else 'STARTTLS'})")

            # 步骤3：登录
            try:
                server.login(sender, password)
                results.append(f"✓ 登录成功 ({sender})")
                server.quit()
                return True, "\n".join(results)
            except smtplib.SMTPAuthenticationError as e:
                err_code = e.smtp_code if hasattr(e, "smtp_code") else ""
                err_msg = e.smtp_error.decode() if isinstance(getattr(e, "smtp_error", None), bytes) else str(e)
                results.append(f"✗ 登录失败 [{err_code}]: {err_msg}")

                # 针对 QQ 邮箱的特别提示
                provider = get_provider_hint(sender)
                if provider:
                    results.append(f"\n💡 {provider['label']}提示：{provider['note']}")
                else:
                    results.append("\n💡 常见原因：")
                    results.append("  1) QQ/163/126 邮箱需要使用「授权码」而非登录密码")
                    results.append("  2) 请确认已在邮箱设置中开启了 SMTP 服务")
                    results.append("  3) 如有设备锁/安全验证，请先通过验证")
                server.quit()
                return False, "\n".join(results)

        except smtplib.SMTPServerDisconnected as e:
            results.append(f"✗ 服务器断开连接: {e}")
            results.append("\n💡 可能原因：端口或加密方式不匹配")
            results.append(f"  当前配置: {smtp_server}:{smtp_port}, SSL={'是' if use_ssl else '否(STARTTLS)'}")
            if use_ssl:
                results.append(f"  可尝试改用端口 587 + STARTTLS（关闭SSL开关）")
            else:
                results.append(f"  可尝试改用端口 465 + SSL（开启SSL开关）")
            return False, "\n".join(results)

        except (socket.timeout, TimeoutError) as e:
            results.append(f"✗ 连接超时: {e}")
            return False, "\n".join(results)

        except Exception as e:
            results.append(f"✗ 连接异常: {type(e).__name__}: {e}")
            return False, "\n".join(results)

    return False, "\n".join(results)


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

    def diagnose(self) -> Tuple[bool, str]:
        """连接诊断，返回 (成功与否, 详细信息)"""
        return diagnose_smtp(
            self.smtp_server, self.smtp_port, self.use_ssl,
            self.sender, self.password
        )

    def send(self, items: List["BidItem"]) -> bool:
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
            msg["From"] = formataddr(("RailBuddy 监控", self.sender))
            msg["To"] = self.receiver
            msg["Date"] = formatdate(localtime=True)
            msg["Message-Id"] = make_msgid(domain="qq.com")
            msg["X-Mailer"] = "RailBuddy/1.0"

            # 纯文本回退
            plain_text = self._build_plain_text(items)
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            # 发送
            with self._get_smtp_connection() as server:
                server.login(self.sender, self.password)
                recipients = [r.strip() for r in self.receiver.split(",") if r.strip()]
                server.sendmail(self.sender, recipients, msg.as_string())

            logger.info(f"邮件发送成功: {len(items)} 个项目 -> {len(recipients)} 个收件人 ({', '.join(recipients)})")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"邮件认证失败: {e}", exc_info=True)
            provider = get_provider_hint(self.sender)
            hint = f"（{provider['label']}需使用授权码而非登录密码）" if provider else "（请检查是否使用了授权码而非登录密码）"
            logger.error(f"提示: {hint}")
            return False

        except smtplib.SMTPServerDisconnected as e:
            logger.error(f"SMTP 服务器断开连接: {e}", exc_info=True)
            return False

        except Exception as e:
            logger.error(f"邮件发送失败: {e}", exc_info=True)
            return False

    @staticmethod
    def _build_subject(count: int) -> str:
        """构建邮件主题"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"[RailBuddy] 城轨招标监控 - 发现 {count} 个新项目 ({now})"

    def _get_smtp_connection(self):
        """获取 SMTP 连接"""
        if self.use_ssl:
            return smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
            return server

    def _build_html_content(self, items: List["BidItem"]) -> str:
        """构建 HTML 邮件正文"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 按类别分组
        categories = {}
        for item in items:
            cat = item.category or "其他"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        # 类别排序：招标、中标 排在最前，其他类别按字母顺序排在后面
        priority = {"招标": 0, "中标": 1}
        sorted_cats = sorted(
            categories.keys(),
            key=lambda c: (0 if c in priority else 1, priority.get(c, 99), c)
        )

        # 构建各分类的表格
        category_sections = []
        for cat in sorted_cats:
            cat_items = categories[cat]
            rows = []
            for idx, item in enumerate(cat_items, 1):
                rows.append(self._build_item_row(item, idx))

            # 优先级类别加特殊样式
            is_priority = cat in priority
            section_class = "category-section category-priority" if is_priority else "category-section"
            title_class = "category-title category-title-priority" if is_priority else "category-title"

            category_sections.append(f"""
            <div class="{section_class}">
                <h3 class="{title_class}">{cat} ({len(cat_items)})</h3>
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
        .category-section.category-priority {{
            background: #fff8e1;
            border-left: 4px solid #ff9800;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 28px;
        }}
        .category-title {{
            color: #1a73e8;
            font-size: 16px;
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 6px;
            margin: 0 0 12px 0;
        }}
        .category-title-priority {{
            color: #e65100;
            border-bottom-color: #ffb74d;
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
    def _build_item_row(item: "BidItem", idx: int) -> str:
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

    def _build_plain_text(self, items: List["BidItem"]) -> str:
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
