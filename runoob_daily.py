from __future__ import annotations

import argparse
import datetime as dt
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


@dataclass(frozen=True)
class LlmConfig:
    api_base: str
    api_key: str
    model: str
    timeout: int
    max_input_chars: int


TRUE_VALUES = {"1", "true", "yes", "on"}


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
        "你是一个技术教程摘要助手。"
        "请把给定的教程摘录压缩成适合手机通知阅读的中文摘要。"
        "要求：100到140字；最多3段；保留核心概念、用途和一个关键注意点；"
        "不要写空话，不要输出 Markdown 标题。"
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

    summary = normalize_generated_text(str(content))
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
    )


def render_message(item: KnowledgePoint) -> tuple[str, str]:
    title = f"今日知识点：{item.point_title}"
    body = (
        f"专题：{item.tutorial_title}\n"
        f"链接：{item.point_url}\n\n"
        f"{item.summary}"
    )
    return title, body


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
    return None


def push_message(session: requests.Session, provider: str, title: str, body: str) -> None:
    provider = provider.lower()
    if provider == "bark":
        push_bark(session, title, body)
        return
    if provider == "serverchan":
        push_serverchan(session, title, body)
        return
    if provider == "pushplus":
        push_pushplus(session, title, body)
        return
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
        help="启用 AI 摘要。需要同时配置 LLM_SUMMARY_ENABLED 和相关 API 环境变量。",
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

        push_message(session, provider, title, body)
        print(f"推送成功，渠道: {provider}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
