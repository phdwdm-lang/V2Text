"""
小红书视频 → 视频分析稿

链路：
  小红书链接
    ├── [并行] 百炼 ASR (paraformer-v1)  → 语音转写
    └── [并行] qwen3-vl-plus 视频理解      → 画面结构分析 + 截图候选列表
         （注：视频理解 API 不含音频，ASR 独立处理）
    ↓
  合并 → <标题>-视频分析稿.md (Obsidian 落库)
    ↓
  [后处理] 解析截图候选列表时间段
    └── ffmpeg -ss <时间> 按需精确取帧  → 只存需要的截图
"""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from dashscope.audio.asr.transcription import Transcription
from openai import OpenAI


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

VL_MODEL = "qwen3-vl-plus"
VL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VL_FPS = 1  # 操作演示类视频 1fps 足够

TERM_REPLACEMENTS: List[Tuple[str, str]] = [
    (r"CLOLOLOD", "Claude"),
    (r"COUDOD", "Claude"),
    (r"CLOLOUD", "Claude"),
    (r"CCOOLLD", "Claude"),
    (r"cloud dx", "Codex"),
    (r"cloud x", "Codex"),
    (r"cloud code", "Claude Code"),
    (r"cloud cl", "Claude CLI"),
    (r"code xx", "Codex"),
    (r"code x", "Codex"),
    (r"German I", "Gemini"),
    (r"Gerald", "Gemini"),
    (r"gma sign", "gemini design"),
    (r"SPG", "SVG"),
    (r"Malcom", "Markdown"),
    (r"APIK", "API Key"),
    (r"Crosse", "Cursor"),
]

PHRASE_REPLACEMENTS: List[Tuple[str, str]] = [
    ("Hello,大家好。", "Hello，大家好。"),
    ("Hello, 大家好。", "Hello，大家好。"),
    ("两个skill", "两个 Skill"),
    ("一个skill", "一个 Skill"),
    ("这个skill", "这个 Skill"),
    ("主agent", "主 Agent"),
    ("模型的agent", "模型的 Agent"),
    ("做SPG的图标", "做 SVG 图标"),
    ("UIUX", "UI/UX"),
]

VIDEO_UNDERSTANDING_PROMPT = """你现在要分析的是一条小红书视频。请不要只做逐字稿总结，而是把"视频里讲了什么"和"视频里做了什么操作"一起整理出来。

注意：你看到的是视频画面帧，没有音频信息。语音转写内容会单独提供，请专注于画面内容分析。

你的任务目标：
1. 准确总结这条视频的核心主题、目标和结论。
2. 尽可能记录视频中的具体操作过程（根据画面变化推断）。
3. 识别并提取视频中出现的工具、产品、网站、平台、模型、命令、路径、按钮、页面切换、代码操作。
4. 为后续改写成知乎图文内容提供可复用素材。
5. 识别适合截图的关键画面时间段。

分析要求：
- 要结合画面、字幕、页面变化一起理解。
- 如果画面中出现了操作演示，要记录"做了什么动作、在哪个页面、使用了什么工具、动作的目的是什么"。
- 如果视频中出现了链接、网站、产品名、命令、快捷键、仓库名、Skill 名称、模型名称等，请尽量提取。
- 如果某个专有名词看不清，不要瞎猜，标注为"待确认"，并说明原因。
- 对关键事实尽量给出证据等级：画面可确认 / 多模态推断 / 待确认。

请严格按以下 Markdown 结构输出（不要省略任何节标题）：

## 一句话总结
用 1 到 2 句话说明这条视频在讲什么。

## 内容定位
- 内容类型：（工具演示 / 工具更新 / 经验分享 / 教程 / 观点表达）
- 主轴：
- 更偏讲解 / 更偏演示 / 二者结合：
- 目标读者：

## 核心内容总结
用 3 到 6 条要点总结视频最重要的信息（适合后续改写成知乎小标题或段落）。

## 视频操作时间线
| 时间段 | 当前画面 / 页面 | 具体操作 | 涉及工具 / 产品 | 这一步的目的 | 备注 |
| --- | --- | --- | --- | --- | --- |

## 关键信息提取

### 产品 / 工具 / 平台
（名称、类型、作用、证据等级）

### 网站 / 仓库 / 链接
（名称、地址、用途、证据等级）

### 快捷键 / 命令 / 路径
（内容、出现场景、作用、证据等级）

### 作者建议 / 判断 / 结论

## 截图候选列表

**填写规则（严格执行）**：
- 时间段：必须填写精确秒数，格式为 `<秒数>s`（如 `32s`、`78s`），不要填区间，不要填 `00:32` 格式
- 截图类型：只能从以下选项中选一个：`操作步骤` / `界面全局` / `关键结果` / `前后对比` / `工具界面`
- 用途说明：一句话说清这张图在知乎文章里要说明什么，避免写"演示界面"这类模糊描述
- 是否必须：满足以下任一条件则填"必须"：①界面发生明显跳转或变化；②出现点击/输入等关键操作的结果；③出现前后对比效果；④出现工具名称/命令/路径等关键文字信息

如果视频中有值得截图的内容，**至少填写 3 行**，操作演示型视频应填写 5 行以上。如果视频确实没有值得截图的画面（如纯口播），填写"无"。

| 精确时间（秒） | 截图类型 | 用途说明 | 是否必须 |
| --- | --- | --- | --- |

## 知乎图文最值得强调的 3 个点
- 点 1
- 点 2
- 点 3

## 不确定信息
列出所有无法 100% 确认、但可能影响成稿准确性的内容，每条注明原因。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将公开小红书视频链接转为视频分析稿，并保存到 Obsidian。",
    )
    parser.add_argument("--url", required=True, help="小红书分享链接")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Obsidian 输出根目录，脚本会在其下自动创建 <标题>-视频转写/ 子目录",
    )
    parser.add_argument(
        "--cache-dir",
        default=".tmp/video_analysis",
        help="中间结果缓存目录",
    )
    parser.add_argument(
        "--skip-screenshots",
        action="store_true",
        help="跳过按需截图（仅生成分析稿，不取帧）",
    )
    return parser.parse_args()


def _load_env_file() -> None:
    """从 Skill 根目录的 .env 文件加载环境变量（不覆盖已有值）"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def get_dashscope_api_key() -> str:
    _load_env_file()

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if api_key:
        return api_key

    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, _ = winreg.QueryValueEx(key, "DASHSCOPE_API_KEY")
                if value and value.strip():
                    return value.strip()
        except OSError:
            pass

    raise RuntimeError(
        "未找到 DASHSCOPE_API_KEY。\n"
        "请在 Skill 根目录创建 .env 文件并填入 API Key，"
        "参考 .env.example 模板。"
    )


def get_with_retries(
    url: str,
    *,
    headers: Dict[str, str] | None = None,
    timeout: int = 30,
    attempts: int = 4,
    sleep_seconds: float = 2.0,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(sleep_seconds * attempt)
    raise RuntimeError(f"请求失败：{url}\n原因：{last_error}") from last_error


def fetch_note_metadata(url: str) -> Dict:
    response = get_with_retries(url, headers={"User-Agent": USER_AGENT})
    marker = "window.__INITIAL_STATE__="
    start = response.text.find(marker)
    if start == -1:
        raise RuntimeError("页面中未找到 __INITIAL_STATE__。")

    payload = response.text[start + len(marker):]
    end = payload.find("</script>")
    if end == -1:
        raise RuntimeError("页面中未找到 __INITIAL_STATE__ 结束标记。")

    state_payload = payload[:end]
    state_payload = re.sub(r":undefined([,}])", r":null\1", state_payload)
    state = json.loads(state_payload)

    note_state = state["note"]
    note_id = note_state.get("currentNoteId") or next(
        iter(note_state["noteDetailMap"].keys())
    )
    note = note_state["noteDetailMap"][note_id]["note"]
    h264_streams = note["video"]["media"]["stream"]["h264"]

    return {
        "note_id": note_id,
        "title": note["title"].strip(),
        "author": note["user"]["nickname"].strip(),
        "desc": note.get("desc", "").strip(),
        "url": url,
        "video_url": h264_streams[0]["masterUrl"],
        "duration_seconds": int(round(note["video"]["media"]["video"]["duration"])),
        "publish_time_ms": int(note["time"]),
        "ip_location": note.get("ipLocation", ""),
        "tags": [item["name"] for item in note.get("tagList", []) if item.get("name")],
    }


def transcribe_video(video_url: str, api_key: str) -> Dict:
    """百炼 ASR：音频转写"""
    response = Transcription.call(
        model=Transcription.Models.paraformer_v1,
        file_urls=[video_url],
        api_key=api_key,
        disfluency_removal_enabled=True,
        timestamp_alignment_enabled=True,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"百炼转写失败，status_code={response.status_code}，message={response.message}"
        )
    result = response.output["results"][0]
    transcription_url = result["transcription_url"]
    return get_with_retries(transcription_url, timeout=60, attempts=6, sleep_seconds=3.0).json()


def understand_video(video_url: str, api_key: str) -> str:
    """qwen3-vl-plus：视频画面理解（不含音频）"""
    client = OpenAI(api_key=api_key, base_url=VL_BASE_URL)
    completion = client.chat.completions.create(
        model=VL_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                    "fps": VL_FPS,
                },
                {"type": "text", "text": VIDEO_UNDERSTANDING_PROMPT},
            ],
        }],
    )
    return completion.choices[0].message.content or ""


def parse_screenshot_candidates(vl_analysis: str) -> List[Dict]:
    """
    从视频理解输出中解析截图候选列表。
    匹配 Markdown 表格行，格式：| 时间段 | 截图类型 | 重点 | 是否必须 |
    时间段支持：00:32、00:32-00:38、32s、32-38s 等常见写法。
    返回列表，每项：{"start_sec": int, "label": str, "required": bool}
    """
    candidates = []
    in_table = False
    time_pattern = re.compile(
        r"(\d{1,2}):(\d{2})(?:-\d{1,2}:\d{2})?|(\d+)s?(?:-\d+s?)?"
    )
    TABLE_HEADERS = {"时间段", "精确时间（秒）", "精确时间", "---", ":---", "---:"}
    for line in vl_analysis.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in stripped.split("|") if c.strip()]
        if not cells:
            continue
        if cells[0] in TABLE_HEADERS or all(c.startswith("-") for c in cells):
            in_table = True
            continue
        if not in_table:
            continue
        if len(cells) < 2:
            continue
        if cells[0].lower() in ("无", "n/a", "-"):
            continue

        time_cell = cells[0]
        label = cells[2] if len(cells) > 2 else cells[1]
        required = len(cells) > 3 and "必须" in cells[3]

        m = time_pattern.search(time_cell)
        if not m:
            continue
        if m.group(3) is not None:
            start_sec = int(m.group(3))
        elif m.group(1) is not None:
            start_sec = int(m.group(1)) * 60 + int(m.group(2))
        else:
            continue

        candidates.append({
            "start_sec": start_sec,
            "label": label,
            "required": required,
        })

    return candidates


def extract_frame_at(video_url: str, second: int, output_path: Path) -> bool:
    """ffmpeg 精确 seek 取单帧，存为 output_path"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-ss", str(second),
        "-i", video_url,
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
        "-y",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        print(f"  [警告] 取帧失败 {second}s：{result.stderr[-200:]}")
        return False
    return True


def extract_candidate_frames(
    video_url: str,
    candidates: List[Dict],
    screenshots_dir: Path,
) -> List[Path]:
    """
    根据截图候选列表按需取帧。
    文件名格式：frame_<序号>.jpg（纯数字+英文，避免特殊字符导致路径问题）
    返回成功生成的文件路径列表。
    """
    if not candidates:
        return []

    screenshots_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []

    for idx, item in enumerate(candidates, 1):
        sec = item["start_sec"]
        label = item.get("label", "")
        filename = f"frame_{idx:03d}.jpg"
        output_path = screenshots_dir / filename
        print(f"  取帧 {sec}s → {filename}（{label}）")
        if extract_frame_at(video_url, sec, output_path):
            saved.append(output_path)

    return saved


def clean_text(text: str) -> str:
    value = text
    for pattern, replacement in TERM_REPLACEMENTS:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    for source, target in PHRASE_REPLACEMENTS:
        value = value.replace(source, target)
    value = re.sub(r"\s+", " ", value)
    value = value.replace(",大家好", "，大家好")
    value = value.replace(".然后", "。然后")
    value = value.replace(".但是", "。但是")
    value = value.replace(".这个", "。这个")
    value = value.replace(".那", "。那")
    value = re.sub(r"([A-Za-z])，", r"\1,", value)
    value = re.sub(r"([A-Za-z])。", r"\1.", value)
    value = re.sub(r"([A-Za-z])：", r"\1:", value)
    value = value.replace(" ,", ",").replace(" .", ".")
    return value.strip()


def extract_key_transcript_segments(
    sentences: List[Dict],
    *,
    max_chars: int = 2000,
) -> str:
    """
    从 ASR 句子列表中提取关键片段。
    策略：优先保留问句/感叹句，以及长度 ≥ 20 字的句子（通常包含具体信息）；
    累计字符数超过 max_chars 时截止。
    完整转写存入 .tmp 缓存，不进入分析稿正文。
    """
    key_sentences = []
    total = 0
    for s in sentences:
        text = clean_text(str(s.get("text", "")))
        if not text:
            continue
        is_key = (
            text.endswith(("？", "！"))
            or len(text) >= 20
            or any(kw in text for kw in ["如何", "怎么", "注意", "关键", "重要", "推荐", "建议", "安装", "使用"])
        )
        if is_key:
            key_sentences.append(text)
            total += len(text)
            if total >= max_chars:
                key_sentences.append("……（更多内容见 .tmp 缓存中的完整逐字稿）")
                break
    return "\n\n".join(key_sentences)


def build_full_transcript(sentences: List[Dict]) -> str:
    """构建完整逐字稿文本，仅用于 .tmp 缓存"""
    paragraphs = []
    current: List[str] = []
    current_length = 0

    for sentence in sentences:
        text = clean_text(str(sentence.get("text", "")))
        if not text:
            continue
        current.append(text)
        current_length += len(text)
        should_break = current_length >= 140 or len(current) >= 4
        if text.endswith(("？", "！")) and current_length >= 70:
            should_break = True
        if should_break:
            paragraphs.append("".join(current))
            current = []
            current_length = 0

    if current:
        paragraphs.append("".join(current))

    return "\n\n".join(paragraphs)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', " ", name)
    name = re.sub(r"[\U00010000-\U0010ffff]", "", name, flags=re.UNICODE)
    name = re.sub(r"[\U0001f000-\U0001f9ff]", "", name, flags=re.UNICODE)
    name = re.sub(r" +", " ", name)
    return name.strip()


def format_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")


def format_duration(seconds: int) -> str:
    minutes, remaining = divmod(seconds, 60)
    return f"{minutes}分{remaining:02d}秒"


def build_analysis_markdown(
    metadata: Dict,
    transcript: Dict,
    vl_analysis: str,
    screenshots_dir: Path,
) -> str:
    transcript_item = transcript["transcripts"][0]
    sentences = transcript_item.get("sentences", [])

    publish_date = format_date(int(metadata["publish_time_ms"]))
    created_date = datetime.now().strftime("%Y-%m-%d")
    duration = format_duration(int(metadata["duration_seconds"]))

    tags = ["content", "视频分析", "知乎"]
    if "ai编程" in metadata.get("tags", []):
        tags.append("ai编程")
    if "vibecoding" in metadata.get("tags", []):
        tags.append("vibecoding")

    frontmatter_lines = [
        "---",
        "type: content",
        "format: analysis",
        "status: draft",
        'area: "[[写作]]"',
        f"created: {created_date}",
        f'source: "{metadata["url"]}"',
        f'author: "{metadata["author"]}"',
        f'duration: "{duration}"',
        'screenshots_dir: "截图/"',
        f"tags: [{', '.join(tags)}]",
        "---",
    ]

    key_segments = extract_key_transcript_segments(sentences)

    lines = [
        "\n".join(frontmatter_lines),
        "",
        f"# {metadata['title']}｜视频分析稿",
        "",
        "> [!info]",
        f"> 来源标题：{metadata['title']}",
        f"> 作者：{metadata['author']}",
        f"> 发布时间：{publish_date}",
        f"> 视频时长：{duration}",
        f"> 原始链接：{metadata['url']}",
        f"> 处理链路：小红书链接解析 → 百炼 ASR + qwen3-vl-plus 视频理解 → 合并分析稿",
        "",
        "---",
        "",
        "## 视频画面分析（qwen3-vl-plus）",
        "",
        vl_analysis.strip(),
        "",
        "---",
        "",
        "## 关键语音片段（百炼 ASR）",
        "",
        "> 说明：以下为 ASR 关键片段提取，完整逐字稿见 `.tmp` 缓存。",
        "",
        key_segments,
        "",
        "---",
        "",
        "## 后续加工方向",
        "",
        "- 基于本分析稿生成知乎图文工作稿（使用 xhs-video-to-zhihu skill）",
        "- 工作稿中标注配图任务定义（截图时间段 + 生成图类型）",
        f"- 截图素材：`截图/` 子目录下（文件名格式：frame_<序号>.jpg）",
    ]

    return "\n".join(lines).rstrip() + "\n"


def save_outputs(
    metadata: Dict,
    transcript: Dict,
    vl_analysis: str,
    analysis_md: str,
    output_dir: Path,
    cache_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    base_name = sanitize_filename(str(metadata["title"]))

    # 落库：视频分析稿
    note_path = output_dir / f"{base_name}-视频分析稿.md"
    note_path.write_text(analysis_md, encoding="utf-8")

    # 缓存：ASR 原始 JSON
    asr_json_path = cache_dir / f"{base_name}-百炼ASR.json"
    asr_json_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 缓存：完整逐字稿文本
    transcript_item = transcript["transcripts"][0]
    full_transcript = build_full_transcript(transcript_item.get("sentences", []))
    full_txt_path = cache_dir / f"{base_name}-完整逐字稿.txt"
    full_txt_path.write_text(full_transcript, encoding="utf-8")

    # 缓存：视频理解原始输出
    vl_raw_path = cache_dir / f"{base_name}-视频理解.md"
    vl_raw_path.write_text(vl_analysis, encoding="utf-8")

    return note_path


def main() -> None:
    args = parse_args()
    api_key = get_dashscope_api_key()

    print("正在解析小红书链接...")
    metadata = fetch_note_metadata(args.url)
    video_url = str(metadata["video_url"])
    base_name = sanitize_filename(str(metadata["title"]))

    cache_dir = Path(args.cache_dir)
    output_dir = Path(args.output_dir) / f"{base_name}-视频转写"
    screenshots_dir = output_dir / "截图"

    print(f"标题：{metadata['title']}")
    print(f"时长：{format_duration(int(metadata['duration_seconds']))}")
    print("开始并行处理（ASR 转写 + 视频画面理解）...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_asr = executor.submit(transcribe_video, video_url, api_key)
        future_vl = executor.submit(understand_video, video_url, api_key)

        transcript = future_asr.result()
        print("✓ ASR 转写完成")

        vl_analysis = future_vl.result()
        print("✓ 视频画面理解完成")

    print("正在合并生成视频分析稿...")
    analysis_md = build_analysis_markdown(
        metadata, transcript, vl_analysis, screenshots_dir
    )

    note_path = save_outputs(
        metadata=metadata,
        transcript=transcript,
        vl_analysis=vl_analysis,
        analysis_md=analysis_md,
        output_dir=output_dir,
        cache_dir=cache_dir,
    )

    print(f"✓ 视频分析稿已生成：{note_path}")

    if not args.skip_screenshots:
        candidates = parse_screenshot_candidates(vl_analysis)
        if candidates:
            print(f"\n正在按需取帧（共 {len(candidates)} 个截图候选）...")
            saved = extract_candidate_frames(video_url, candidates, screenshots_dir)
            print(f"✓ 截图完成（{len(saved)}/{len(candidates)} 张）：{screenshots_dir}")
        else:
            print("\n视频理解输出中未解析到截图候选，跳过截图。")
    else:
        print("\n已跳过截图（--skip-screenshots）。")

    print(f"\n  缓存目录：{cache_dir}")


if __name__ == "__main__":
    main()
