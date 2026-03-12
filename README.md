# 每日菜鸟教程推送

这个项目会每天抓取菜鸟教程的一个知识点，并通过 Bark、Server酱 或 PushPlus 推送到手机。也支持可选的 AI 晨读卡片模式，把正文压缩成更适合通知阅读的三段短卡片。

## 运行方式

### 本地测试

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python runoob_daily.py --dry-run
```

如果你只想固定推送某个专题，优先配置 `RUNOOB_ROOT_URL`。例如：

```bash
python runoob_daily.py --dry-run --root-url https://www.runoob.com/python3/python3-tutorial.html
```

如果要测试 AI 晨读卡片版：

```bash
set LLM_SUMMARY_ENABLED=1
set LLM_API_BASE=https://api.openai.com/v1
set LLM_API_KEY=你的密钥
set LLM_MODEL=gpt-4.1-mini
python runoob_daily.py --dry-run --llm-summary
```

### GitHub Actions 定时运行

工作流文件已经放在 `.github/workflows/daily-runoob.yml`。

默认 Cron 是 `0 0 * * *`，对应中国标准时间每天 `08:00`。GitHub Actions 使用 UTC，调度可能会有几分钟延迟。

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中添加以下 Secrets：

- `PUSH_PROVIDER`：`serverchan`、`bark` 或 `pushplus`
- `SERVERCHAN_SENDKEY`：使用 Server酱 时必填
- `BARK_DEVICE_KEY` 或 `BARK_PUSH_URL`：使用 Bark 时至少填一个
- `PUSHPLUS_TOKEN`：使用 PushPlus 时必填
- `RUNOOB_ROOT_URL`：推荐填写，例如 `https://www.runoob.com/python3/python3-tutorial.html`
- `RUNOOB_TOPIC_HINT`：可选。如果不指定 `RUNOOB_ROOT_URL`，脚本会从首页自动发现教程入口，再用关键词过滤
- `RUNOOB_MAX_BLOCKS`：可选，默认 `4`
- `RUNOOB_TIMEOUT`：可选，默认 `20`
- `LLM_SUMMARY_ENABLED`：可选，填 `1` 时启用 AI 晨读卡片
- `LLM_API_BASE`：可选，默认 `https://api.openai.com/v1`
- `LLM_API_KEY`：启用 AI 摘要时必填
- `LLM_MODEL`：启用 AI 摘要时必填，例如 `gpt-4.1-mini`
- `LLM_TIMEOUT`：可选，默认 `40`
- `LLM_SUMMARY_MAX_INPUT_CHARS`：可选，默认 `2800`

## 脚本行为

- 优先使用 `RUNOOB_ROOT_URL` 作为某个专题的入口页。
- 如果没有设置 `RUNOOB_ROOT_URL`，脚本会访问首页并自动识别教程入口。
- 通过日期来稳定选择当天内容，不依赖本地状态文件，所以适合 GitHub Actions 这种无状态运行环境。
- 如果没有检测到推送配置，脚本会打印内容但不会报错退出。
- 如果启用了 AI 晨读卡片但接口失败，脚本会自动回退到普通摘要，不会中断当天任务。

## AI 晨读卡片接入

脚本调用的是 OpenAI 兼容的 `chat/completions` 接口，所以不绑定某一家服务。

- 如果你用 OpenAI，保留默认 `LLM_API_BASE=https://api.openai.com/v1` 即可。
- 如果你用其他兼容服务，只需要改 `LLM_API_BASE`、`LLM_API_KEY` 和 `LLM_MODEL`。
- 开启后，推送会变成三行卡片：`一句话`、`核心点`、`今天试试`。
- AI 卡片是可选增强层，不配置这些变量时，脚本仍然按原始正文摘录推送。

## 推送渠道说明

- Bark 官方项目：<https://github.com/Finb/Bark>
- Server酱 Turbo：<https://ftqq.com/>
- PushPlus 文档：<https://www.pushplus.plus/doc/guide/api.html>
