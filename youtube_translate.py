#!/usr/bin/env python3
"""
YouTube 字幕提取 + 说话人识别 + 中文翻译工具
用法: python3 youtube_translate.py <YouTube链接> --speakers Host Guest
"""

import subprocess
import sys
import os
import re
import tempfile
import glob
import argparse
from openai import OpenAI


# ── 字幕提取 ──────────────────────────────────────────────────────────────────

def extract_subtitles(url: str, tmpdir: str) -> str | None:
    base = os.path.join(tmpdir, "subtitle")
    for auto in (False, True):
        cmd = [
            "yt-dlp", "--skip-download",
            "--convert-subs", "srt",
            "--sub-lang", "en,en-US,en-GB",
            "-o", base,
            "--write-auto-subs" if auto else "--write-subs",
            url,
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        files = glob.glob(os.path.join(tmpdir, "*.srt"))
        if files:
            return files[0]
    return None


def srt_to_segments(srt_path: str) -> list[dict]:
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(
        r"(\d+)\n"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3}) --> (\d{2}:\d{2}:\d{2}[,\.]\d{3})\n"
        r"((?:(?!\d+\n\d{2}:\d{2}).+\n?)+)",
        re.MULTILINE,
    )
    segments = []
    prev_lines: list[str] = []
    for m in pattern.finditer(content):
        raw = re.sub(r"<[^>]+>", "", m.group(4)).strip()
        if not raw:
            continue
        # YouTube 滚动字幕：每个条目包含多行，开头几行与上一条目结尾重复
        # 只保留本条目中未出现在上一条目的新行
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        new_lines = []
        skip = len(prev_lines)
        for line in lines:
            if skip > 0 and prev_lines and line.lower() == prev_lines[-skip].lower():
                skip -= 1
            else:
                skip = 0
                new_lines.append(line)
        prev_lines = lines
        text = " ".join(new_lines)
        if text:
            segments.append({
                "index": m.group(1),
                "time_start": m.group(2),
                "time_end": m.group(3),
                "text": text,
            })
    return segments


# ── 段落合并 ──────────────────────────────────────────────────────────────────

def segments_to_paragraphs(segments: list[dict],
                            min_chars: int = 200,
                            max_chars: int = 500) -> list[dict]:
    """
    合并碎片字幕为完整段落，同时修复断词问题。
    逻辑：积累文字直到遇到句末标点且超过 min_chars，或超过 max_chars 强制截断。
    """
    paragraphs: list[dict] = []
    buf: list[str] = []
    time_start: str | None = None
    time_end: str | None = None
    idx = 1

    for i, seg in enumerate(segments):
        if time_start is None:
            time_start = seg["time_start"]
        time_end = seg["time_end"]

        # YouTube 滚动字幕去重：新段文字已出现在缓冲末尾则跳过
        combined_so_far = " ".join(buf).lower()
        seg_text_lower = seg["text"].lower()
        if combined_so_far and seg_text_lower in combined_so_far:
            continue

        # 断词修复：上段末尾是字母且本段开头是小写，直接拼接不加空格
        if buf and buf[-1][-1].isalpha() and seg["text"] and seg["text"][0].islower():
            buf[-1] = buf[-1] + seg["text"]
        else:
            buf.append(seg["text"])

        combined = " ".join(buf)
        is_last = (i == len(segments) - 1)
        ends_sentence = combined.rstrip()[-1] in ".!?"

        if is_last or (ends_sentence and len(combined) >= min_chars) or len(combined) >= max_chars:
            paragraphs.append({
                "index": str(idx),
                "time_start": time_start,
                "time_end": time_end,
                "text": combined,
                "speaker": None,
            })
            idx += 1
            buf = []
            time_start = None

    return paragraphs


def batch_paragraphs(paragraphs: list[dict], batch_size: int = 8) -> list[list[dict]]:
    return [paragraphs[i : i + batch_size] for i in range(0, len(paragraphs), batch_size)]


# ── 说话人识别 + 翻译（合并为一次 API 调用）────────────────────────────────────

def identify_and_translate_batch(client: OpenAI,
                                  paragraphs: list[dict],
                                  speaker_a: str,
                                  speaker_b: str) -> list[dict]:
    """
    一次 API 调用同时完成两件事：
    1. 根据上下文（问答模式、语气、内容）判断每段的说话人
    2. 将英文翻译为中文

    识别逻辑依据：
    - 主持人（speaker_a）通常提问、引导话题、做简短过渡
    - 嘉宾（speaker_b）通常给出较长的回答、分享经验和观点
    """
    numbered = "\n\n".join(
        f"[{i+1}]\n{p['text']}" for i, p in enumerate(paragraphs)
    )
    prompt = f"""这是一段双人访谈/播客的英文字幕，两位说话人分别是：
- {speaker_a}：主持人，通常提问、引导话题、做简短过渡或回应
- {speaker_b}：嘉宾，通常给出较长回答、分享经验和专业观点

请对下列每个编号段落完成两件事：
1. 判断这段话是谁说的（{speaker_a} 或 {speaker_b}）
2. 翻译成自然流畅的中文

严格按以下格式输出，不要添加任何其他内容：

[1]
说话人: {speaker_a}或{speaker_b}
译文: 中文翻译

[2]
说话人: ...
译文: ...

---
{numbered}"""

    resp = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.choices[0].message.content.strip()

    # 解析结果
    block_pattern = re.compile(
        r"\[(\d+)\]\s*\n说话人[:：]\s*(.+?)\s*\n译文[:：]\s*(.+?)(?=\n\[\d+\]|\Z)",
        re.DOTALL,
    )
    parsed: dict[int, dict] = {}
    for m in block_pattern.finditer(raw):
        n = int(m.group(1))
        parsed[n] = {
            "speaker": m.group(2).strip(),
            "translation": m.group(3).strip(),
        }

    results = []
    for i, para in enumerate(paragraphs, 1):
        p = parsed.get(i, {})
        results.append({
            **para,
            "speaker": p.get("speaker", ""),
            "translation": p.get("translation", ""),
        })
    return results


# ── 输出 ──────────────────────────────────────────────────────────────────────

def save_bilingual_srt(paragraphs: list[dict], output_path: str):
    lines = []
    for para in paragraphs:
        speaker = para.get("speaker", "")
        zh = para.get("translation", "")
        lines.append(para["index"])
        lines.append(f"{para['time_start']} --> {para['time_end']}")
        lines.append(f"[{speaker}] {para['text']}" if speaker else para["text"])
        if zh:
            lines.append(f"[{speaker}] {zh}" if speaker else zh)
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def save_markdown(paragraphs: list[dict], title: str, output_path: str):
    lines = [f"# {title}\n"]
    last_speaker = None
    for para in paragraphs:
        speaker = para.get("speaker", "")
        zh = para.get("translation", "")
        if speaker and speaker != last_speaker:
            lines.append(f"\n### 🎙 {speaker}\n")
            last_speaker = speaker
        lines.append(f"**{para['time_start']} → {para['time_end']}**")
        lines.append(f"> {para['text']}")
        lines.append(f"\n{zh}\n")
        lines.append("---")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def save_html(paragraphs: list[dict], title: str, url: str, output_path: str):
    """生成左右对照阅读 HTML，英文在左，中文在右，含说话人标签和同步滚动。"""
    total = len(paragraphs)

    def speaker_badge(speaker: str) -> str:
        if not speaker:
            return ""
        return f'<span class="spk spk-{speaker.lower()}">{speaker}</span> '

    left_pairs = ""
    right_pairs = ""
    for i, para in enumerate(paragraphs, 1):
        spk = para.get("speaker", "")
        zh = para.get("translation", "")
        ts = f"{para['time_start']} → {para['time_end']}"
        badge = speaker_badge(spk)
        left_pairs += f'''
      <div class="pair" id="p{i}">
        <div class="num">{i}</div>
        <div class="text">{badge}<span class="ts">{ts}</span><br>{para["text"]}</div>
      </div>'''
        right_pairs += f'''
      <div class="pair" id="p{i}">
        <div class="num">{i}</div>
        <div class="text">{badge}{zh}</div>
      </div>'''

    html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0f1117;--surface:#161b27;--surface2:#1c2333;--border:#252d3d;
  --accent-en:#58a6ff;--accent-zh:#f78166;
  --text-en:#9ec8fb;--text-zh:#f0c9a0;--text-dim:#8b949e;--text-mute:#3d4451;
  --active-bg:rgba(88,166,255,0.07);--hover-bg:rgba(255,255,255,0.03);
}}
html,body{{height:100%;overflow:hidden}}
body{{font-family:'Inter',-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--text-dim);display:flex;flex-direction:column}}
.header{{background:var(--surface);border-bottom:1px solid var(--border);
  padding:12px 24px;flex-shrink:0;display:flex;align-items:center;gap:14px}}
.header-icon{{width:38px;height:38px;border-radius:9px;background:linear-gradient(135deg,#1f3a5f,#2d1b4e);
  display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}}
.header-body{{flex:1;min-width:0}}
.header h1{{font-size:14px;font-weight:600;color:#e6edf3;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
.header .meta{{font-size:12px;color:var(--text-dim);margin-top:2px;display:flex;gap:8px;flex-wrap:wrap}}
.meta a{{color:var(--accent-en);text-decoration:none}}
.progress-bar{{height:2px;background:var(--border);flex-shrink:0}}
.progress-fill{{height:100%;background:linear-gradient(90deg,var(--accent-en),var(--accent-zh));
  width:0%;transition:width .15s ease}}
.main{{display:flex;flex:1;overflow:hidden}}
.panel{{flex:1;overflow-y:scroll}}
.panel::-webkit-scrollbar{{width:5px}}
.panel::-webkit-scrollbar-thumb{{background:var(--border);border-radius:10px}}
.panel-inner{{max-width:640px;margin:0 auto;padding:16px 20px 80px}}
.panel-left{{border-right:1px solid var(--border)}}
.panel-label{{display:flex;align-items:center;gap:8px;padding:10px 0 14px;
  position:sticky;top:0;background:var(--bg);z-index:10;
  font-size:11px;font-weight:600;letter-spacing:.05em}}
.panel-label::after{{content:'';flex:1;height:1px;background:var(--border)}}
.panel-left .panel-label{{color:var(--accent-en)}}
.panel-right .panel-label{{color:var(--accent-zh)}}
.pair{{display:flex;gap:14px;padding:12px 10px;border-radius:9px;
  margin-bottom:2px;transition:background .15s;cursor:default}}
.pair:hover{{background:var(--hover-bg)}}
.pair.active{{background:var(--active-bg)}}
.num{{font-size:11px;font-weight:600;color:var(--text-mute);min-width:24px;
  padding-top:2px;user-select:none;text-align:right;font-variant-numeric:tabular-nums}}
.pair.active .num{{color:var(--accent-en)}}
.panel-right .pair.active .num{{color:var(--accent-zh)}}
.text{{font-size:14px;line-height:1.85}}
.panel-left .text{{color:var(--text-en)}}
.panel-right .text{{color:var(--text-zh)}}
.ts{{font-size:11px;color:var(--text-mute);margin-bottom:3px;display:inline-block}}
.spk{{display:inline-block;font-size:10px;font-weight:600;padding:1px 7px;
  border-radius:4px;margin-right:4px;border:1px solid}}
.spk-host{{color:#58a6ff;background:rgba(88,166,255,.1);border-color:rgba(88,166,255,.25)}}
.spk-guest{{color:#f78166;background:rgba(247,129,102,.1);border-color:rgba(247,129,102,.25)}}
.fab{{position:fixed;bottom:24px;right:24px;width:38px;height:38px;border-radius:50%;
  background:var(--surface2);border:1px solid var(--border);color:var(--text-dim);
  font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center;
  transition:all .2s;box-shadow:0 4px 12px rgba(0,0,0,.4);opacity:0;pointer-events:none;z-index:50}}
.fab.visible{{opacity:1;pointer-events:auto}}
.fab:hover{{background:var(--accent-en);border-color:var(--accent-en);color:#fff;transform:translateY(-2px)}}
.progress-label{{font-size:12px;color:var(--text-dim);font-variant-numeric:tabular-nums;flex-shrink:0}}
@media(max-width:768px){{
  .main{{flex-direction:column}}
  .panel{{max-height:50vh;overflow-y:auto}}
  .panel-left{{border-right:none;border-bottom:1px solid var(--border)}}
}}
</style>
</head>
<body>
<div class="header">
  <div class="header-icon">🎙</div>
  <div class="header-body">
    <h1>{title}</h1>
    <div class="meta">
      <a href="{url}" target="_blank">▶ 原始视频</a>
      <span>·</span><span>共 {total} 段</span>
    </div>
  </div>
  <span class="progress-label" id="progressLabel">0 / {total}</span>
</div>
<div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
<div class="main">
  <div class="panel panel-left" id="panelLeft">
    <div class="panel-inner">
      <div class="panel-label">🇺🇸 English</div>
      {left_pairs}
    </div>
  </div>
  <div class="panel panel-right" id="panelRight">
    <div class="panel-inner">
      <div class="panel-label">🇨🇳 中文</div>
      {right_pairs}
    </div>
  </div>
</div>
<button class="fab" id="fab" title="返回顶部" onclick="panelLeft.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>
const panelLeft=document.getElementById('panelLeft');
const panelRight=document.getElementById('panelRight');
const progressFill=document.getElementById('progressFill');
const progressLabel=document.getElementById('progressLabel');
const fab=document.getElementById('fab');
const leftPairs=Array.from(document.querySelectorAll('#panelLeft .pair'));
const TOTAL={total};
let syncing=false,currentN=null;

panelLeft.addEventListener('scroll',()=>{{
  if(syncing)return;syncing=true;
  const pct=panelLeft.scrollTop/(panelLeft.scrollHeight-panelLeft.clientHeight);
  panelRight.scrollTop=pct*(panelRight.scrollHeight-panelRight.clientHeight);
  onScroll(pct);syncing=false;
}});
panelRight.addEventListener('scroll',()=>{{
  if(syncing)return;syncing=true;
  const pct=panelRight.scrollTop/(panelRight.scrollHeight-panelRight.clientHeight);
  panelLeft.scrollTop=pct*(panelLeft.scrollHeight-panelLeft.clientHeight);
  onScroll(pct);syncing=false;
}});

function onScroll(pct){{
  progressFill.style.width=(pct*100)+'%';
  fab.classList.toggle('visible',panelLeft.scrollTop>200);
  requestAnimationFrame(highlightCurrent);
}}

function highlightCurrent(){{
  const midY=panelLeft.getBoundingClientRect().top+panelLeft.clientHeight/2;
  let found=null;
  for(const p of leftPairs){{
    const r=p.getBoundingClientRect();
    if(r.top<=midY&&r.bottom>=midY){{found=p;break;}}
  }}
  if(!found)return;
  const n=found.id.slice(1);
  if(n===currentN)return;
  if(currentN)document.querySelectorAll(`[id="p${{currentN}}"]`).forEach(p=>p.classList.remove('active'));
  currentN=n;
  document.querySelectorAll(`[id="p${{n}}"]`).forEach(p=>p.classList.add('active'));
  progressLabel.textContent=n+' / '+TOTAL;
}}

panelLeft.addEventListener('scroll',()=>requestAnimationFrame(highlightCurrent));
panelRight.addEventListener('scroll',()=>requestAnimationFrame(highlightCurrent));
highlightCurrent();
</script>
</body>
</html>'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def get_video_title(url: str) -> str:
    result = subprocess.run(
        ["yt-dlp", "--get-title", "--no-playlist", url],
        capture_output=True, text=True,
    )
    title = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "transcript"
    return re.sub(r'[\\/*?:"<>|]', "_", title)[:80]


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YouTube 字幕提取 + 说话人识别 + 中文翻译")
    parser.add_argument("url", help="YouTube 视频链接")
    parser.add_argument("--api-key", help="DeepSeek API Key（或环境变量 DEEPSEEK_API_KEY）")
    parser.add_argument("--speakers", nargs=2, metavar=("A", "B"), default=["Host", "Guest"],
                        help="两位说话人的名字，主持人在前，嘉宾在后（默认 Host Guest）")
    parser.add_argument("--output-dir", default=".", help="输出目录")
    parser.add_argument("--format", choices=["srt", "md", "html", "both"], default="both")
    parser.add_argument("--min-chars", type=int, default=200)
    parser.add_argument("--max-chars", type=int, default=500)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误：请提供 DEEPSEEK_API_KEY")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    speaker_a, speaker_b = args.speakers

    print(f"📺 获取视频信息...")
    title = get_video_title(args.url)
    print(f"   标题：{title}")

    with tempfile.TemporaryDirectory() as tmpdir:
        print("⬇️  提取字幕中...")
        srt_path = extract_subtitles(args.url, tmpdir)
        if not srt_path:
            print("❌ 未找到英文字幕")
            sys.exit(1)
        segments = srt_to_segments(srt_path)
        print(f"   原始字幕：{len(segments)} 条")

    paragraphs = segments_to_paragraphs(segments, args.min_chars, args.max_chars)
    print(f"   合并段落：{len(paragraphs)} 段")

    batches = batch_paragraphs(paragraphs, batch_size=8)
    all_results: list[dict] = []

    print(f"🤖 DeepSeek 识别说话人 + 翻译（共 {len(batches)} 批）...")
    for i, batch in enumerate(batches, 1):
        print(f"   第 {i}/{len(batches)} 批（{len(batch)} 段）", end="", flush=True)
        results = identify_and_translate_batch(client, batch, speaker_a, speaker_b)
        all_results.extend(results)
        print(" ✓")

    os.makedirs(args.output_dir, exist_ok=True)
    safe_title = title.replace(" ", "_")

    if args.format in ("srt", "both"):
        out = os.path.join(args.output_dir, f"{safe_title}_bilingual.srt")
        save_bilingual_srt(all_results, out)
        print(f"✅ 双语 SRT：{out}")

    if args.format in ("md", "both"):
        out = os.path.join(args.output_dir, f"{safe_title}_bilingual.md")
        save_markdown(all_results, title, out)
        print(f"✅ 双语 Markdown：{out}")

    if args.format in ("html", "both"):
        out = os.path.join(args.output_dir, f"{safe_title}_bilingual.html")
        save_html(all_results, title, args.url, out)
        print(f"✅ 左右对照 HTML：{out}")

    print("\n🎉 完成！")


if __name__ == "__main__":
    main()
