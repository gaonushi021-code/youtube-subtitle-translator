# youtube_translate.py — 使用说明

YouTube 英文字幕提取 + 说话人识别 + 中文翻译工具。输入一个 YouTube 链接，自动生成双语对照文件，适合播客、访谈、教程类视频的阅读和存档。

---

## 环境依赖

| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| Python 3.10+ | 系统自带或 brew | 运行脚本 |
| yt-dlp | `brew install yt-dlp` | 提取 YouTube 字幕 |
| openai | `pip install openai` | 调用 DeepSeek API |
| DeepSeek API Key | [platform.deepseek.com](https://platform.deepseek.com) | 翻译 + 说话人识别 |

**配置 API Key（一次性）：**

```bash
echo 'export DEEPSEEK_API_KEY="sk-xxxx"' >> ~/.zshrc
source ~/.zshrc
```

---

## 每次运行方式

打开终端，进入脚本所在目录，然后运行：

```bash
cd ~/Desktop/Cursor/字幕处理
python3 youtube_translate.py "YouTube链接"
```

API Key 已保存在系统环境变量中，无需每次传入。运行完成后，当前目录下会生成三个文件：`.srt`、`.md`、`.html`，用浏览器打开 `.html` 即可左右对照阅读。

---

## 更多用法示例

```bash
# 最简用法（默认输出到当前目录，生成 srt + md + html 三种格式）
python3 youtube_translate.py "https://www.youtube.com/watch?v=xxxxx"

# 指定说话人名字（访谈类视频推荐）
python3 youtube_translate.py "https://..." --speakers "Lenny" "Cat Woo"

# 指定输出目录
python3 youtube_translate.py "https://..." --output-dir ~/Desktop/播客

# 只生成 HTML 对照文件
python3 youtube_translate.py "https://..." --format html
```

---

## 全部参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `url` | 必填 | YouTube 视频链接 |
| `--api-key` | 读取环境变量 | DeepSeek API Key，也可用环境变量 `DEEPSEEK_API_KEY` |
| `--speakers A B` | `Host Guest` | 两位说话人名字，主持人在前，嘉宾在后 |
| `--output-dir` | `.`（当前目录）| 输出文件存放路径 |
| `--format` | `both` | 输出格式：`srt` / `md` / `html` / `both`（both = 三种全部） |
| `--min-chars` | `200` | 段落最小字符数（达到此长度且遇到句末才截断） |
| `--max-chars` | `500` | 段落最大字符数（超过此长度强制截断） |

---

## 输出文件说明

每次运行会在 `--output-dir` 下生成以视频标题命名的文件：

### `*_bilingual.html` — 左右对照阅读页（主要输出）

用浏览器打开，左栏英文 + 右栏中文，功能包括：

- **同步滚动**：拖动任意一侧，另一侧按比例跟随
- **段落高亮**：滚动时自动高亮当前阅读位置（两侧同步）
- **说话人标签**：主持人蓝色徽章，嘉宾橙色徽章
- **时间戳**：每段左侧显示视频时间（可对照原视频查看）
- **阅读进度条**：顶部渐变进度条 + 右上角段落计数
- **返回顶部按钮**：滚动后右下角浮现
- **暗色主题**：护眼设计，适合长时间阅读
- **移动端适配**：小屏时上下布局

### `*_bilingual.md` — Markdown 格式

适合导入 Obsidian、Notion 等笔记工具。按说话人分组，每段含时间戳、英文原文、中文译文。

### `*_bilingual.srt` — 双语字幕文件

可加载到视频播放器（如 IINA、VLC）使用，每条字幕显示英文原文和中文译文。

---

## 核心功能说明

### 字幕去重

YouTube 自动字幕采用"滚动"格式：每条 SRT 条目包含 2-3 行，但头部几行和上一条末尾重复。脚本在解析阶段自动检测并丢弃重复行，只保留新出现的内容，避免"Over the past few months Over the past few months"这类重复文字。

### 段落合并

原始字幕每条通常只有 5-15 个词，不适合阅读。脚本将碎片字幕按以下规则合并为完整段落：

- 积累文字直到**遇到句末标点（.!?）且超过 min-chars**，然后截断
- 超过 max-chars 时**强制截断**，防止单段过长
- 自动修复**断词问题**：若上段末尾是字母且下段开头是小写字母，直接拼接（不加空格）

### 说话人识别

利用 DeepSeek 大模型，根据**对话上下文**推断说话人：
- 主持人特征：提问、引导话题、简短过渡
- 嘉宾特征：给出长回答、分享经验和专业观点

适用场景：
- 双人访谈（一问一答）
- 主持人 + 多位嘉宾的合集视频（统一标记为 Guest）
- 单人演讲（所有段落会被标记为同一人）

### 翻译质量

使用 `deepseek-chat` 模型，说话人识别与翻译在同一次 API 调用中完成，模型有完整上下文，翻译连贯自然。每批 8 段，按批次处理，避免超出 token 限制。

---

## 常见问题

**Q: 视频没有英文字幕怎么办？**  
脚本会先尝试正式字幕，再尝试自动生成字幕（auto-subs）。若两者都没有，会提示 `❌ 未找到英文字幕`。部分视频只有非英语字幕，暂不支持。

**Q: 说话人识别准确率如何？**  
对于标准访谈格式（一问一答）准确率较高。对于多人圆桌、声音混杂、或说话风格相近的视频，准确率会下降——毕竟只靠文字语境，没有声纹分析。

**Q: `--speakers` 参数写什么名字都行吗？**  
是的，可以写真实人名（如 `--speakers "Lenny Rachitsky" "Cat Woo"`），也可以写通用名（如 `--speakers 主持人 嘉宾`）。名字会出现在说话人标签和 HTML 徽章里。

**Q: HTML 文件需要联网才能打开吗？**  
字体从 Google Fonts 加载，断网时会 fallback 到系统字体（苹方/微软雅黑）。其余所有功能完全离线可用。

---

## 文件结构

```
字幕处理/
├── youtube_translate.py          # 主脚本
├── README.md                     # 本说明文档
└── [视频标题]_bilingual.html     # 左右对照阅读页（输出示例）
└── [视频标题]_bilingual.md       # Markdown 格式（输出示例）
└── [视频标题]_bilingual.srt      # 双语字幕（输出示例）
```
