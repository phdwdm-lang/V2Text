# 小红书视频转知乎图文

一个 AI Skill，将小红书视频自动转换为适合知乎发布的中文图文。

## 它做什么？

给它一条小红书视频链接（或已有的分析稿），它会自动完成：

1. **视频解析**：语音转写（ASR）+ 视频画面理解（多模态大模型），生成结构化的视频分析稿
2. **内容改写**：将分析稿改写为适合知乎发布的中文图文，保留你的个人表达风格
3. **配图处理**：从视频中提取关键帧截图，必要时生成信息图
4. **导出发布**：输出 `.docx` 文件，可直接导入知乎编辑器发布

适用场景：视频转图文、跨平台内容复用、AI 工具分享改写、vibecoding 经验整理、操作演示总结。

## 安装

```bash
npx skills add <你的GitHub用户名>/xhs-video-to-zhihu
```

> 需要 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 或其他支持 Skills 的 AI 编程工具。

## 首次配置

### 1. 百炼 API Key（必填）

脚本使用阿里云百炼的 ASR 和多模态视频理解能力，**需要一个支持多模态模型（qwen-vl 系列）的 API Key**。

1. 打开 [阿里云百炼控制台](https://dashscope.console.aliyun.com/)
2. 左侧菜单 → **API-KEY 管理** → **创建新的 API-KEY**
3. 确保账号已开通 **通义千问 VL（视觉语言）模型** 的调用权限（百炼控制台 → 模型广场 → 搜索 `qwen-vl` → 开通）

> ⚠️ 普通文本模型的 Key 也能用于 ASR 转写，但**视频画面理解**需要多模态模型权限。

将 `.env.example` 复制为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

```
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

### 2. 输出目录

首次使用时，AI 会询问你希望将产出文件存放在哪个目录，并自动写入 `.env` 的 `OUTPUT_DIR` 字段。也可以提前手动配置：

```
OUTPUT_DIR=你的输出目录路径
```

### 3. 系统工具

| 工具 | 用途 | Windows | macOS |
|---|---|---|---|
| Python 3.10+ | 运行脚本 | 系统自带或官网安装 | `brew install python` |
| ffmpeg | 视频取帧截图 | `winget install ffmpeg` | `brew install ffmpeg` |
| Pandoc | md → docx 转换 | `winget install JohnMacFarlane.Pandoc` | `brew install pandoc` |

安装 Python 依赖：

```bash
pip install -r scripts/requirements.txt
```

## 使用

在 Claude Code 中直接提供小红书视频链接即可触发工作流：

```
把这条小红书视频转成知乎图文：https://www.xiaohongshu.com/discovery/item/...
```

也支持直接提供已有的分析稿或逐字稿，跳过视频解析步骤。

## 产出

每次执行会在输出目录下生成：

```
<标题>-视频转写/
  ├── <标题>-视频分析稿.md      # 结构化视频分析
  ├── <标题>-知乎图文工作稿.md    # 改写中间稿
  ├── <标题>-知乎图文最终版.md    # 可发布的最终版
  ├── <标题>-知乎发布版.docx     # 导入知乎编辑器用
  └── 截图/                     # 视频关键帧截图
```

## 个人表达风格

`references/个人表达风格.md` 用于让改写稿贴近作者本人的写法。当前文件中的风格是我（作者）的个人风格，你可以根据自己的写作习惯修改这个文件。

**如何生成你自己的风格文件**：

1. 准备 3-5 篇你已发布的知乎文章（或其他平台的长文）
2. 让 AI 阅读这些文章，提炼出你的写作特征（句式偏好、叙述视角、情绪表达方式等）
3. 按照现有 `个人表达风格.md` 的结构整理成文件，替换原有内容

风格文件不是必须的——没有它 Skill 也能正常运行，只是改写稿会更偏通用知乎风格。

## 工作流详情

完整的 10 步工作流说明见 [SKILL.md](SKILL.md)。

## License

MIT
