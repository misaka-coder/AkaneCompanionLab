"""One daily QQ cache card, using the existing character stationery."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from .cache_usage import LOCAL_TZ
from .qq_card_artwork import CECILIA_STATIONERY, _background, _font_paths, cached_card, draw_card_heading


def cache_report_text(report: dict) -> str:
    ratio = report["ratio"]
    value = f"{ratio:.1%}" if ratio is not None else "暂无有效样本"
    return (f"{report['day']} · 当前 QQ 会话\n缓存命中：{value}\n"
            f"有效 {report['reported']} 次 · 未报告 {report['unknown']} 次\n"
            "按输入 token 加权，未报告不计入比例。")


def render_qq_cache_card(document: dict, cache_dir: Path) -> Path:
    from PIL import ImageDraw, ImageFont

    report = document["report"]
    regular, bold = _font_paths()
    themed = document.get("character_pack_id") == "cecilia"
    artwork = CECILIA_STATIONERY if themed else None
    fonts = {size: ImageFont.truetype(str(bold if size >= 38 else regular), size) for size in (22, 25, 28, 32, 38, 62)}
    models = sorted(report["models"], key=lambda row: row["requests"], reverse=True)
    height = 1450 + len(models) * 57
    signature = {"version": 1, "document": document,
                 "fonts": [(p.name, p.stat().st_mtime_ns) for p in (regular, bold)],
                 "art": artwork.stat().st_mtime_ns if artwork else None}

    def paint():
        canvas = _background(artwork, height)
        draw = ImageDraw.Draw(canvas)
        draw_card_heading(canvas, title="缓存小记", label="/缓存")
        ink, sage, muted, pink = "#4E5D54", "#718B77", "#89968E", "#B48997"
        draw.text((105, 584), report["day"] + "  ·  北京时间", font=fonts[38], fill=sage)
        draw.text((105, 638), "当前群聊" if document.get("is_group") else "当前私聊", font=fonts[25], fill=muted)
        labels = (("有效样本命中率", f"{report['ratio']:.1%}" if report["ratio"] is not None else "—"),
                  ("有效请求", str(report["reported"])), ("未报告", str(report["unknown"])))
        for x, (label, value) in zip((105, 390, 650), labels):
            draw.text((x, 699), value, font=fonts[62], fill=sage if label != "未报告" else pink)
            draw.text((x, 777), label, font=fonts[25], fill=muted)
        draw.text((105, 854), "每小时趋势", font=fonts[32], fill=ink)
        left, right, top, bottom = 148, 780, 930, 1170
        for pct in (0, 50, 100):
            y = bottom - (bottom - top) * pct / 100
            draw.line((left, y, right, y), fill="#E1E8E1", width=2)
            draw.text((left - 16, y - 13), str(pct) + "%", font=fonts[22], fill=muted, anchor="ra")
        target_y = bottom - (bottom - top) * .95
        for x in range(left, right, 14):
            draw.line((x, target_y, min(x + 7, right), target_y), fill="#C8B5BC", width=1)
        draw.text((right, top - 29), "95% 参考线", font=fonts[22], fill=pink, anchor="ra")
        previous = None
        for row in report["hours"]:
            x = left + (right - left) * row["hour"] / 23
            if row["ratio"] is not None:
                y = bottom - (bottom - top) * row["ratio"]
                if previous and previous[0] == row["hour"] - 1:
                    draw.line((previous[1], previous[2], x, y), fill=sage, width=4)
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=sage)
                previous = (row["hour"], x, y)
            else:
                previous = None
            if row["unknown"]:
                draw.ellipse((x - 3, bottom + 45, x + 3, bottom + 51), fill="#A5ABA6")
        for hour in (0, 4, 8, 12, 16, 20, 23):
            x = left + (right - left) * hour / 23
            draw.text((x, bottom + 13), f"{hour:02d}", font=fonts[22], fill=muted, anchor="ma")
        if not report["reported"]:
            message = "今天还没有记录，聊几轮再来看吧" if not report["requests"] else "上游尚未提供有效缓存数据"
            if report["day"] != datetime.now(LOCAL_TZ).date().isoformat():
                message = "这一天还没有可展示的缓存数据"
            draw.text((450, 1027), message, font=fonts[25], fill=muted, anchor="ma")
        draw.text((105, 1242), "灰点：该小时含未报告请求", font=fonts[22], fill=muted)
        for index, row in enumerate(models):
            label = row["model"]
            while draw.textlength(label, font=fonts[25]) > 425:
                label = label[:-2].rstrip("…") + "…"
            value = f"{row['ratio']:.1%}" if row["ratio"] is not None else "未报告"
            y = 1290 + index * 57
            draw.text((105, y), label, font=fonts[25], fill=ink)
            draw.text((790, y), f"{value}  ·  {row['requests']} 次", font=fonts[25], fill=sage, anchor="ra")
        draw.text((450, height - 118), "按输入 token 加权 · 未报告不计入比例", font=fonts[22], fill=muted, anchor="ma")
        draw.text((450, height - 79), "/缓存 昨天   ·   /缓存 年-月-日", font=fonts[22], fill=sage, anchor="ma")
        return canvas

    return cached_card(cache_dir, "qq-cache-usage", signature, paint)
