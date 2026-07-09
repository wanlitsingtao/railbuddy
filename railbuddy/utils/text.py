"""文本处理工具模块"""

import re
import hashlib
from datetime import datetime
from typing import Optional


def generate_item_id(url: str, title: str = "") -> str:
    """根据 URL 和标题生成唯一 ID（MD5 哈希）

    用于去重：同一 URL + 标题组合始终生成相同 ID
    """
    content = url.strip().lower()
    if title:
        content += "|" + title.strip()
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def extract_date(text: str) -> Optional[str]:
    """从文本中提取日期字符串

    支持格式：
        2024-01-15 / 2024/01/15 / 2024.01.15
        2024年01月15日
        2024-1-5
    返回标准化格式 YYYY-MM-DD，无法识别返回 None
    """
    if not text:
        return None

    text = text.strip()

    # YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        y, m, d = match.groups()
        try:
            dt = datetime(int(y), int(m), int(d))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # YYYY年MM月DD日
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if match:
        y, m, d = match.groups()
        try:
            dt = datetime(int(y), int(m), int(d))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def parse_date_to_iso(date_str: str) -> Optional[str]:
    """将日期字符串转为 ISO 格式（用于数据库比较）

    输入支持 extract_date 能识别的所有格式
    """
    normalized = extract_date(date_str)
    if normalized:
        return f"{normalized}T00:00:00"
    return None


def clean_url(url: str, prefix: str = "") -> str:
    """处理相对 URL，拼接为完整 URL

    Args:
        url: 原始链接（可能是相对路径或绝对路径）
        prefix: 站点前缀（如 http://www.example.com）
    """
    from urllib.parse import urljoin

    url = url.strip()
    if not url:
        return ""

    # 绝对 URL 直接返回
    if url.startswith("http://") or url.startswith("https://"):
        return url

    if url.startswith("//"):
        return "https:" + url

    # 使用 urljoin 正确处理相对路径（包括 ./ ../ 等）
    if prefix:
        # 确保 prefix 以 / 结尾，使 urljoin 正确处理
        base = prefix if prefix.endswith("/") else prefix + "/"
        return urljoin(base, url)

    return url


def truncate_text(text: str, max_len: int = 500) -> str:
    """截断文本到指定长度，添加省略号"""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def html_to_plain_text(html: str) -> str:
    """简单地将 HTML 转为纯文本（去除标签）"""
    if not html:
        return ""
    # 去除 script 和 style
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # 去除所有标签
    text = re.sub(r"<[^>]+>", " ", html)
    # 合并多余空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_title(title: str) -> str:
    """清理标题文本

    - 去除前缀标签如 [设备]、[服务]、[施工]、[物资] 等
    - 去除多余空白和换行
    - 去除前后的项目符号（•、◆等）
    """
    if not title:
        return ""
    # 去除前缀方括号标签 [设备] [服务] 等
    title = re.sub(r"^\s*\[.{1,6}\]\s*", "", title)
    # 去除前后的项目符号
    title = re.sub(r"^[•·◆●○◇◇※\s]+", "", title)
    # 合并多余空白
    title = re.sub(r"\s+", " ", title).strip()
    return title
