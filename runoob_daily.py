from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

HOME_URL = "https://www.runoob.com/"
DEFAULT_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class LinkCandidate:
    title: str
    url: str


@dataclass(frozen=True)
class KnowledgePoint:
    tutorial_title: str
    tutorial_url: str
    point_title: str
    point_url: str
    summary: str
    source_text: str
    summary_style: str = "plain"


@dataclass(frozen=True)
class LlmConfig:
    api_base: str
    api_key: str
    model: str
    timeout: int
    max_input_chars: int


@dataclass(frozen=True)
class WechatMpConfig:
    app_id: str
    app_secret: str
    thumb_media_id: str
    author: str
    mode: str
    title_prefix: str
    need_open_comment: int
    only_fans_can_comment: int


TRUE_VALUES = {"1", "true", "yes", "on"}
CARD_LABELS = ("一句话：", "核心点：", "今天试试：")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_runoob_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith("runoob.com")


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    cleaned = parsed._replace(fragment="")
    return cleaned.geturl()


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    if not stripped:
        return default
    return int(stripped)


def compact_text(value: str) -> str:
    return clean_text(value.replace("\n", " "))


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1].rstrip() + "…"


def truncate_utf8_bytes(value: str, limit: int) -> str:
    if len(value.encode("utf-8")) <= limit:
        return value
    if limit <= 0:
        return ""

    result: list[str] = []
    used = 0
    for char in value:
        size = len(char.encode("utf-8"))
        if used + size > limit:
            break
        result.append(char)
        used += size

    truncated = "".join(result).rstrip()
    if truncated != value and len("…".encode("utf-8")) <= limit:
        while truncated and len((truncated + "…").encode("utf-8")) > limit:
            truncated = truncated[:-1].rstrip()
        truncated += "…"
    return truncated


def build_session(timeout: int) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def fetch_html(session: requests.Session, url: str) -> tuple[str, str]:
    response = session.get(url, timeout=session.request_timeout)  # type: ignore[attr-defined]
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    return response.text, response.url


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def directory_prefix(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if "/" not in path.rstrip("/"):
        return "/"
    prefix = path.rsplit("/", 1)[0]
    return prefix if prefix.endswith("/") else f"{prefix}/"


def choose_for_date(items: list[LinkCandidate], when: dt.date, salt: int = 0) -> LinkCandidate:
    if not items:
        raise ValueError("候选列表为空，无法选取内容。")
    index = (when.toordinal() + salt) % len(items)
    return items[index]


def dedupe_links(items: Iterable[LinkCandidate]) -> list[LinkCandidate]:
    seen: set[str] = set()
    result: list[LinkCandidate] = []
    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
    return result


def looks_like_tutorial_entry(text: str, url: str) -> bool:
    lowered = text.lower()
    if not is_runoob_url(url):
        return False
    if any(keyword in lowered for keyword in ("测验", "手册", "本地书签", "笔记", "考试")):
        return False
    return "学习" in text or "教程" in text or "guide" in lowered


def shorten_title(text: str) -> str:
    cleaned = clean_text(text)
    cleaned = cleaned.replace("〖", "").replace("〗", "")
    if "学习 " in cleaned:
        cleaned = cleaned.split("学习 ", 1)[1]
    if " " in cleaned and len(cleaned) > 28:
        cleaned = cleaned.split(" ", 1)[0]
    return cleaned[:40] or "菜鸟教程"


def discover_tutorial_links(
    soup: BeautifulSoup, base_url: str, topic_hint: str | None
) -> list[LinkCandidate]:
    candidates: list[LinkCandidate] = []
    hint = topic_hint.lower() if topic_hint else None

    for anchor in soup.select("a[href]"):
        raw_text = clean_text(anchor.get_text(" ", strip=True))
        if not raw_text:
            continue
        href = normalize_url(urljoin(base_url, anchor["href"]))
        if not looks_like_tutorial_entry(raw_text, href):
            continue
        if hint and hint not in raw_text.lower() and hint not in href.lower():
            continue
        candidates.append(LinkCandidate(title=shorten_title(raw_text), url=href))

    return sorted(dedupe_links(candidates), key=lambda item: item.url)


def looks_like_chapter(title: str, href: str, tutorial_url: str) -> bool:
    if href == tutorial_url:
        return False
    if not is_runoob_url(href):
        return False
    parsed = urlparse(href)
    if not parsed.path.endswith(".html"):
        return False
    lowered = title.lower()
    if any(keyword in lowered for keyword in ("运行实例", "测验", "上一页", "下一页", "笔记")):
        return False
    return bool(clean_text(title))


def discover_chapter_links(soup: BeautifulSoup, tutorial_url: str) -> list[LinkCandidate]:
    tutorial_url = normalize_url(tutorial_url)
    tutorial_path = urlparse(tutorial_url).path
    same_directory = directory_prefix(tutorial_url)
    section_root = "/" + tutorial_path.strip("/").split("/", 1)[0] + "/"
    candidates: list[LinkCandidate] = []

    for anchor in soup.select("a[href]"):
        title = clean_text(anchor.get_text(" ", strip=True))
        if not title:
            continue
        href = normalize_url(urljoin(tutorial_url, anchor["href"]))
        path = urlparse(href).path
        if not looks_like_chapter(title, href, tutorial_url):
            continue
        if path.startswith(same_directory):
            candidates.append(LinkCandidate(title=title[:60], url=href))

    links = dedupe_links(candidates)
    if len(links) >= 3:
        return links

    fallback: list[LinkCandidate] = []
    for anchor in soup.select("a[href]"):
        title = clean_text(anchor.get_text(" ", strip=True))
        if not title:
            continue
        href = normalize_url(urljoin(tutorial_url, anchor["href"]))
        path = urlparse(href).path
        if not looks_like_chapter(title, href, tutorial_url):
            continue
        if path.startswith(section_root):
            fallback.append(LinkCandidate(title=title[:60], url=href))

    return dedupe_links(fallback)


def pick_content_root(soup: BeautifulSoup) -> Tag:
    selectors = (
        "article",
        "#content",
        ".article-intro",
        ".article",
        ".content",
        ".entry",
        ".tutorial-content",
        ".main",
        "main",
    )
    for selector in selectors:
        for node in soup.select(selector):
            if isinstance(node, Tag):
                text = clean_text(node.get_text(" ", strip=True))
                if len(text) >= 120:
                    return node
    if soup.body and isinstance(soup.body, Tag):
        return soup.body
    return soup


def collect_text_blocks(
    root: Tag,
    *,
    max_blocks: int | None,
    max_chars: int | None,
    per_block_limit: int,
) -> list[str]:
    blocks: list[str] = []
    current_chars = 0

    for node in root.select("p, li, h2, h3"):
        text = clean_text(node.get_text(" ", strip=True))
        if not is_useful_block(text):
            continue
        text = trim_block(text, limit=per_block_limit)
        if blocks and text == blocks[-1]:
            continue

        projected = current_chars + len(text)
        if max_chars is not None and projected > max_chars:
            remaining = max_chars - current_chars
            if remaining < 30:
                break
            text = trim_block(text, limit=remaining)
            projected = current_chars + len(text)

        blocks.append(text)
        current_chars = projected + 2

        if max_blocks is not None and len(blocks) >= max_blocks:
            break

    return blocks


def is_useful_block(text: str) -> bool:
    if len(text) < 10:
        return False
    bad_tokens = ("AI 思考中", "写笔记", "上一篇", "下一篇", "运行实例", "返回顶部")
    return not any(token in text for token in bad_tokens)


def trim_block(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def extract_title(soup: BeautifulSoup) -> str:
    site_title_fragments = ("学的不仅是技术", "菜鸟教程 --")
    content_root = pick_content_root(soup)

    for selector in ("h1", ".article-intro h1", ".content h1"):
        for node in content_root.select(selector):
            title = clean_text(node.get_text(" ", strip=True))
            if title and not any(fragment in title for fragment in site_title_fragments):
                return title

    for node in soup.select("h1"):
        title = clean_text(node.get_text(" ", strip=True))
        if title and not any(fragment in title for fragment in site_title_fragments):
            return title

    if soup.title and soup.title.string:
        return clean_text(soup.title.string.split("|", 1)[0])
    return "菜鸟教程知识点"


def extract_summary(soup: BeautifulSoup, max_blocks: int) -> str:
    root = pick_content_root(soup)
    blocks = collect_text_blocks(
        root,
        max_blocks=max_blocks,
        max_chars=None,
        per_block_limit=160,
    )

    if blocks:
        return "\n\n".join(blocks)

    fallback = trim_block(clean_text(root.get_text(" ", strip=True)), limit=320)
    if fallback:
        return fallback
    raise ValueError("没有从页面中提取到可用正文。")


def extract_source_text(soup: BeautifulSoup, max_chars: int) -> str:
    root = pick_content_root(soup)
    blocks = collect_text_blocks(
        root,
        max_blocks=12,
        max_chars=max_chars,
        per_block_limit=320,
    )
    if blocks:
        return "\n".join(blocks)
    return trim_block(clean_text(root.get_text(" ", strip=True)), limit=max_chars)


def normalize_generated_text(text: str) -> str:
    paragraphs = [clean_text(part) for part in text.replace("\r\n", "\n").split("\n")]
    paragraphs = [part for part in paragraphs if part]
    return "\n\n".join(paragraphs[:3])


def strip_known_label(text: str) -> str:
    return re.sub(
        r"^(一句话|核心点|今天试试|今日行动|行动建议|马上试试|应用场景|one line|core point|try today)\s*[:：-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def normalize_card_summary(text: str) -> str:
    raw_lines = [clean_text(line.lstrip("-*• ")) for line in text.replace("\r\n", "\n").split("\n")]
    lines = [strip_known_label(line) for line in raw_lines if clean_text(line)]
    normalized: list[str] = []
    for label, line in zip(CARD_LABELS, lines[:3]):
        if not line:
            continue
        normalized.append(f"{label}{line}")
    return "\n".join(normalized)


def resolve_wechat_mp_config() -> WechatMpConfig | None:
    app_id = os.getenv("WECHAT_APP_ID", "").strip()
    app_secret = os.getenv("WECHAT_APP_SECRET", "").strip()
    thumb_media_id = os.getenv("WECHAT_THUMB_MEDIA_ID", "").strip()
    mode = os.getenv("WECHAT_MP_MODE", "draft").strip().lower() or "draft"
    author = os.getenv("WECHAT_AUTHOR", "").strip()
    title_prefix = os.getenv("WECHAT_TITLE_PREFIX", "晨读｜").strip()
    need_open_comment = env_int("WECHAT_NEED_OPEN_COMMENT", 0)
    only_fans_can_comment = env_int("WECHAT_ONLY_FANS_CAN_COMMENT", 0)

    if not any((app_id, app_secret, thumb_media_id)):
        return None
    if not app_id or not app_secret or not thumb_media_id:
        raise ValueError("微信公众号配置不完整，至少需要 WECHAT_APP_ID、WECHAT_APP_SECRET、WECHAT_THUMB_MEDIA_ID。")
    if mode not in {"draft", "publish"}:
        raise ValueError("WECHAT_MP_MODE 仅支持 draft 或 publish。")

    return WechatMpConfig(
        app_id=app_id,
        app_secret=app_secret,
        thumb_media_id=thumb_media_id,
        author=author,
        mode=mode,
        title_prefix=title_prefix,
        need_open_comment=1 if need_open_comment else 0,
        only_fans_can_comment=1 if only_fans_can_comment else 0,
    )


def resolve_llm_config(timeout: int) -> LlmConfig | None:
    if not env_flag("LLM_SUMMARY_ENABLED"):
        return None

    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    api_base = os.getenv("LLM_API_BASE", "https://api.openai.com/v1").strip()
    llm_timeout = env_int("LLM_TIMEOUT", max(timeout, DEFAULT_TIMEOUT))
    max_input_chars = env_int("LLM_SUMMARY_MAX_INPUT_CHARS", 2800)

    if not api_key or not model or not api_base:
        print("AI 摘要已启用，但 LLM 配置不完整，回退普通摘要。", file=sys.stderr)
        return None

    return LlmConfig(
        api_base=api_base.rstrip("/"),
        api_key=api_key,
        model=model,
        timeout=llm_timeout,
        max_input_chars=max_input_chars,
    )


def summarize_with_llm(
    session: requests.Session,
    config: LlmConfig,
    item: KnowledgePoint,
) -> str:
    endpoint = f"{config.api_base}/chat/completions"
    prompt = (
        "你是技术晨读卡片编辑。"
        "请把给定的教程摘录改写成适合手机通知阅读的中文晨读卡片。"
        "输出必须严格为 3 行，且只能使用下面 3 个字段名，不要额外解释：\n"
        "一句话：...\n"
        "核心点：...\n"
        "今天试试：...\n"
        "要求：总字数控制在 80 到 120 字；"
        "第一行概括这是什么和有什么用；"
        "第二行提炼最重要的机制、规则或注意点；"
        "第三行给一个今天就能上手的小动作；"
        "避免空话、套话、Markdown 标题和编号。"
    )
    payload = {
        "model": config.model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"专题：{item.tutorial_title}\n"
                    f"知识点：{item.point_title}\n"
                    f"链接：{item.point_url}\n\n"
                    f"内容摘录：\n{item.source_text}"
                ),
            },
        ],
    }
    response = session.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.timeout,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        content = "\n".join(parts)

    summary = normalize_card_summary(str(content))
    if not summary:
        raise ValueError("LLM 未返回有效摘要。")
    return summary


def maybe_apply_llm_summary(
    session: requests.Session,
    config: LlmConfig | None,
    item: KnowledgePoint,
) -> KnowledgePoint:
    if config is None:
        return item

    try:
        summary = summarize_with_llm(session, config, item)
        return KnowledgePoint(
            tutorial_title=item.tutorial_title,
            tutorial_url=item.tutorial_url,
            point_title=item.point_title,
            point_url=item.point_url,
            summary=summary,
            source_text=item.source_text,
            summary_style="card",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"AI 摘要失败，回退普通摘要: {exc}", file=sys.stderr)
        return item


def resolve_today_knowledge(
    session: requests.Session,
    when: dt.date,
    root_url: str | None,
    topic_hint: str | None,
    max_blocks: int,
    source_max_chars: int,
) -> KnowledgePoint:
    if root_url:
        tutorial_url = normalize_url(root_url)
        tutorial_title = "指定专题"
    else:
        home_html, canonical_home = fetch_html(session, HOME_URL)
        tutorial_links = discover_tutorial_links(
            soup_from_html(home_html), canonical_home, topic_hint
        )
        if not tutorial_links:
            raise ValueError("未能从菜鸟教程首页识别出专题入口，请设置 RUNOOB_ROOT_URL。")
        tutorial = choose_for_date(tutorial_links, when)
        tutorial_url = tutorial.url
        tutorial_title = tutorial.title

    tutorial_html, tutorial_url = fetch_html(session, tutorial_url)
    tutorial_soup = soup_from_html(tutorial_html)
    parsed_tutorial_title = extract_title(tutorial_soup)
    if parsed_tutorial_title:
        tutorial_title = parsed_tutorial_title

    chapter_links = discover_chapter_links(tutorial_soup, tutorial_url)
    chapter = choose_for_date(chapter_links, when, salt=len(tutorial_title)) if chapter_links else LinkCandidate(
        title=tutorial_title, url=tutorial_url
    )

    chapter_html, chapter_url = fetch_html(session, chapter.url)
    chapter_soup = soup_from_html(chapter_html)
    point_title = extract_title(chapter_soup)
    summary = extract_summary(chapter_soup, max_blocks=max_blocks)
    source_text = extract_source_text(chapter_soup, max_chars=source_max_chars)

    return KnowledgePoint(
        tutorial_title=tutorial_title,
        tutorial_url=tutorial_url,
        point_title=point_title or chapter.title,
        point_url=chapter_url,
        summary=summary,
        source_text=source_text,
        summary_style="plain",
    )


def render_message(item: KnowledgePoint) -> tuple[str, str]:
    title_prefix = "今日晨读" if item.summary_style == "card" else "今日知识点"
    title = f"{title_prefix}：{item.point_title}"
    if item.summary_style == "card":
        body = (
            f"专题：{item.tutorial_title}\n\n"
            f"{item.summary}\n\n"
            f"原文：{item.point_url}"
        )
    else:
        body = (
            f"专题：{item.tutorial_title}\n"
            f"原文：{item.point_url}\n\n"
            f"{item.summary}"
        )
    return title, body


def build_wechat_digest(item: KnowledgePoint) -> str:
    parts = [strip_known_label(line) for line in item.summary.splitlines() if clean_text(line)]
    digest = compact_text("；".join(part for part in parts[:2] if part))
    digest = digest or compact_text(item.point_title)
    return truncate_utf8_bytes(digest, 100)


def build_wechat_title(item: KnowledgePoint, config: WechatMpConfig) -> str:
    return truncate_text(f"{config.title_prefix}{item.point_title}", 64)


def build_wechat_html(item: KnowledgePoint, config: WechatMpConfig) -> str:
    today = dt.date.today().strftime("%Y-%m-%d")
    safe_title = html.escape(item.point_title)
    safe_topic = html.escape(item.tutorial_title)
    safe_url = html.escape(item.point_url, quote=True)
    summary_lines = [clean_text(line) for line in item.summary.splitlines() if clean_text(line)]

    paragraphs = [
        '<section style="font-size:16px;line-height:1.8;color:#222;">',
        f'<p><strong>日期：</strong>{today}</p>',
        f'<p><strong>专题：</strong>{safe_topic}</p>',
        f'<h2 style="margin:1.2em 0 0.6em;font-size:24px;">{safe_title}</h2>',
    ]

    for line in summary_lines:
        if "：" in line:
            label, content = line.split("：", 1)
            safe_label = html.escape(label)
            safe_content = html.escape(content)
            paragraphs.append(f'<p><strong>{safe_label}：</strong>{safe_content}</p>')
        else:
            paragraphs.append(f"<p>{html.escape(line)}</p>")

    paragraphs.extend(
        [
            '<hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0;">',
            f'<p><a href="{safe_url}">阅读原文</a></p>',
            '<p style="color:#888;font-size:14px;">内容来源：菜鸟教程，本文由脚本自动整理生成。</p>',
            "</section>",
        ]
    )
    return "".join(paragraphs)


def parse_wechat_response(response: requests.Response, action: str) -> dict:
    data = response.json()
    errcode = data.get("errcode", 0)
    if errcode not in (0, None):
        errmsg = data.get("errmsg", "unknown error")
        raise ValueError(f"微信公众号{action}失败: {errcode} {errmsg}")
    return data


def post_wechat_json(
    session: requests.Session,
    endpoint: str,
    *,
    params: dict[str, str],
    payload: dict,
) -> requests.Response:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = session.post(
        endpoint,
        params=params,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=session.request_timeout,  # type: ignore[attr-defined]
    )
    response.raise_for_status()
    return response


def get_wechat_access_token(session: requests.Session, config: WechatMpConfig) -> str:
    endpoint = "https://api.weixin.qq.com/cgi-bin/token"
    response = session.get(
        endpoint,
        params={
            "grant_type": "client_credential",
            "appid": config.app_id,
            "secret": config.app_secret,
        },
        timeout=session.request_timeout,  # type: ignore[attr-defined]
    )
    response.raise_for_status()
    data = parse_wechat_response(response, "获取 access_token")
    access_token = data.get("access_token")
    if not access_token:
        raise ValueError("微信公众号 access_token 返回为空。")
    return str(access_token)


def create_wechat_draft(
    session: requests.Session,
    access_token: str,
    config: WechatMpConfig,
    item: KnowledgePoint,
) -> str:
    endpoint = "https://api.weixin.qq.com/cgi-bin/draft/add"
    article = {
        "title": build_wechat_title(item, config),
        "author": truncate_text(config.author, 8),
        "digest": build_wechat_digest(item),
        "content": build_wechat_html(item, config),
        "content_source_url": item.point_url,
        "thumb_media_id": config.thumb_media_id,
        "need_open_comment": config.need_open_comment,
        "only_fans_can_comment": config.only_fans_can_comment,
    }
    if not article["author"]:
        article.pop("author")

    variants: list[tuple[dict, str | None]] = [(article, None)]
    digestless = dict(article)
    digestless.pop("digest", None)
    variants.append((digestless, "微信公众号草稿摘要过长，已自动去掉 digest 后重试成功。"))

    authorless = dict(digestless)
    authorless.pop("author", None)
    variants.append((authorless, "微信公众号草稿摘要或作者过长，已自动精简字段后重试成功。"))

    last_error: ValueError | None = None
    for index, (payload_article, success_message) in enumerate(variants):
        response = post_wechat_json(
            session,
            endpoint,
            params={"access_token": access_token},
            payload={"articles": [payload_article]},
        )
        data = response.json()
        errcode = data.get("errcode", 0)
        errmsg = str(data.get("errmsg", ""))
        if errcode in (0, None):
            media_id = data.get("media_id")
            if not media_id:
                raise ValueError("微信公众号草稿创建成功，但未返回 media_id。")
            if success_message:
                print(success_message, file=sys.stderr)
            return str(media_id)

        last_error = ValueError(f"微信公众号新增草稿失败: {errcode} {errmsg}")
        retryable = (
            (errcode == 45004 and "description size out of limit" in errmsg)
            or (errcode == 45110 and "author size out of limit" in errmsg)
        )
        if retryable and index < len(variants) - 1:
            continue
        raise last_error

    if last_error is not None:
        raise last_error
    raise ValueError("微信公众号新增草稿失败：未知错误。")


def submit_wechat_publish(
    session: requests.Session,
    access_token: str,
    media_id: str,
) -> tuple[str, str]:
    endpoint = "https://api.weixin.qq.com/cgi-bin/freepublish/submit"
    response = post_wechat_json(
        session,
        endpoint,
        params={"access_token": access_token},
        payload={"media_id": media_id},
    )
    data = parse_wechat_response(response, "提交发布")
    publish_id = str(data.get("publish_id", ""))
    msg_data_id = str(data.get("msg_data_id", ""))
    return publish_id, msg_data_id


def push_wechat_mp(session: requests.Session, item: KnowledgePoint) -> str:
    config = resolve_wechat_mp_config()
    if config is None:
        raise ValueError("未提供微信公众号配置。")

    access_token = get_wechat_access_token(session, config)
    media_id = create_wechat_draft(session, access_token, config, item)
    if config.mode == "draft":
        return f"推送成功，渠道: wechat_mp（草稿已创建，media_id: {media_id}）"

    publish_id, msg_data_id = submit_wechat_publish(session, access_token, media_id)
    suffix = f"，msg_data_id: {msg_data_id}" if msg_data_id else ""
    return f"推送成功，渠道: wechat_mp（发布任务已提交，publish_id: {publish_id}{suffix}）"


def push_bark(session: requests.Session, title: str, body: str) -> requests.Response:
    base_url = os.getenv("BARK_PUSH_URL", "").strip()
    device_key = os.getenv("BARK_DEVICE_KEY", "").strip()
    if base_url:
        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/push"):
            endpoint = f"{endpoint}/push"
    elif device_key:
        endpoint = f"https://api.day.app/{device_key}/push"
    else:
        raise ValueError("未提供 Bark 配置。")

    response = session.post(
        endpoint,
        json={
            "title": title,
            "body": body,
            "group": os.getenv("BARK_GROUP", "runoob-daily"),
            "isArchive": "1",
        },
        timeout=session.request_timeout,  # type: ignore[attr-defined]
    )
    response.raise_for_status()
    return response


def push_serverchan(session: requests.Session, title: str, body: str) -> requests.Response:
    send_key = (
        os.getenv("SERVERCHAN_SENDKEY", "").strip()
        or os.getenv("SERVERCHAN_KEY", "").strip()
    )
    if not send_key:
        raise ValueError("未提供 Server酱 SendKey。")
    endpoint = f"https://sctapi.ftqq.com/{send_key}.send"
    response = session.post(
        endpoint,
        data={"title": title, "desp": body},
        timeout=session.request_timeout,  # type: ignore[attr-defined]
    )
    response.raise_for_status()
    return response


def push_pushplus(session: requests.Session, title: str, body: str) -> requests.Response:
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        raise ValueError("未提供 PushPlus token。")
    endpoint = "https://www.pushplus.plus/send"
    response = session.post(
        endpoint,
        json={"token": token, "title": title, "content": body, "template": "txt"},
        timeout=session.request_timeout,  # type: ignore[attr-defined]
    )
    response.raise_for_status()
    return response


def detect_push_provider() -> str | None:
    explicit = os.getenv("PUSH_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("BARK_PUSH_URL") or os.getenv("BARK_DEVICE_KEY"):
        return "bark"
    if os.getenv("SERVERCHAN_SENDKEY") or os.getenv("SERVERCHAN_KEY"):
        return "serverchan"
    if os.getenv("PUSHPLUS_TOKEN"):
        return "pushplus"
    if resolve_wechat_mp_config() is not None:
        return "wechat_mp"
    return None


def push_message(
    session: requests.Session,
    provider: str,
    title: str,
    body: str,
    item: KnowledgePoint,
) -> str:
    provider = provider.lower()
    if provider == "bark":
        push_bark(session, title, body)
        return "推送成功，渠道: bark"
    if provider == "serverchan":
        push_serverchan(session, title, body)
        return "推送成功，渠道: serverchan"
    if provider == "pushplus":
        push_pushplus(session, title, body)
        return "推送成功，渠道: pushplus"
    if provider == "wechat_mp":
        return push_wechat_mp(session, item)
    raise ValueError(f"不支持的推送渠道: {provider}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取菜鸟教程并推送每日知识点。")
    parser.add_argument(
        "--date",
        help="用于选择内容的日期，格式 YYYY-MM-DD。默认使用今天。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印内容，不执行消息推送。",
    )
    parser.add_argument(
        "--root-url",
        default=os.getenv("RUNOOB_ROOT_URL", "").strip() or None,
        help="固定专题入口，例如 https://www.runoob.com/python3/python3-tutorial.html",
    )
    parser.add_argument(
        "--topic-hint",
        default=os.getenv("RUNOOB_TOPIC_HINT", "").strip() or None,
        help="首页自动发现专题时使用的关键词过滤，例如 python、sql、git。",
    )
    parser.add_argument(
        "--max-blocks",
        type=int,
        default=env_int("RUNOOB_MAX_BLOCKS", 4),
        help="摘要最多提取多少段正文，默认 4。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=env_int("RUNOOB_TIMEOUT", DEFAULT_TIMEOUT),
        help="网络请求超时时间（秒），默认 20。",
    )
    parser.add_argument(
        "--llm-summary",
        action="store_true",
        help="启用 AI 晨读卡片。需要同时配置 LLM_SUMMARY_ENABLED 和相关 API 环境变量。",
    )
    return parser.parse_args()


def resolve_date(raw_date: str | None) -> dt.date:
    if not raw_date:
        return dt.date.today()
    return dt.datetime.strptime(raw_date, "%Y-%m-%d").date()


def main() -> int:
    args = parse_args()
    try:
        when = resolve_date(args.date)
        session = build_session(timeout=args.timeout)
        if args.llm_summary:
            os.environ["LLM_SUMMARY_ENABLED"] = "1"
        llm_config = resolve_llm_config(timeout=args.timeout)
        item = resolve_today_knowledge(
            session=session,
            when=when,
            root_url=args.root_url,
            topic_hint=args.topic_hint,
            max_blocks=max(1, args.max_blocks),
            source_max_chars=llm_config.max_input_chars if llm_config else 2800,
        )
        item = maybe_apply_llm_summary(session, llm_config, item)
        title, body = render_message(item)
        print(title)
        print()
        print(body)
        print()

        if args.dry_run:
            print("dry-run: 未执行推送")
            return 0

        provider = detect_push_provider()
        if not provider:
            print("未检测到推送配置，已输出内容但未发送。", file=sys.stderr)
            return 0

        status = push_message(session, provider, title, body, item)
        print(status)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
