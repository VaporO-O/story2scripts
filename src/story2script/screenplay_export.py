"""Portable screenplay exports from the supplied version, with explicit validation status."""

from html import escape
from io import BytesIO

from .logic_engine import reason_story
from .plot_state import screenplay_hash


def export_status(script):
    if script.editing and script.editing.status == "needs_check":
        return "草稿 待重新核验"
    if script.plot_state and script.plot_state.logic:
        report = reason_story(script, script.plot_state.logic, script.plot_state.changes)
        return "规则校验通过 仍需作者审阅" if report.valid else "草稿 存在剧情规则问题"
    return "结构化剧本 未建立事件账本"


def sections(script):
    """Groups appear at their first scene; no duplicate or shuffled scenes."""
    for scene in script.scenes:
        for group in script.structure:
            if group.scene_ids[0] == scene.id:
                yield (
                    "group",
                    group.title
                    + (f" · 目标 {group.target_minutes:g} 分钟" if group.target_minutes else ""),
                )
        yield "scene", scene


def script_txt(script, appendix=True):
    names = {c.id: c.name for c in script.characters}
    out = [
        script.title,
        f"{script.adaptation_type} · {script.genre}",
        script.logline,
        export_status(script),
        f"版本 {screenplay_hash(script)[:12]}",
        "",
    ]
    if script.creative_brief:
        out += [f"风格：{script.creative_brief.name} v{script.creative_brief.template_version}", ""]
    for kind, item in sections(script):
        if kind == "group":
            out += [item, ""]
            continue
        out += [f"{item.id}  {item.heading}", f"来源：{item.source_chapter}", ""]
        for e in item.elements:
            if e.type == "dialogue":
                notes = " · ".join(v for v in (e.parenthetical, e.emotion) if v)
                out += [names[e.character] + (f"（{notes}）" if notes else ""), "    " + e.text, ""]
            else:
                out += [e.text, ""]
    if appendix:
        out += [
            "人物表",
            *[f"{c.name}：{c.description}；动机：{c.motivation}" for c in script.characters],
            "",
            "场景与道具表",
        ]
        out += [
            f"{s.id} · {s.location} · {s.time_of_day} · 道具：{'、'.join(s.props) or '无'}"
            for s in script.scenes
        ]
    return "\n".join(out) + "\n"


def script_html(script, appendix=True):
    names = {c.id: c.name for c in script.characters}
    parts = [
        f"<h1>{escape(script.title)}</h1><p>{escape(script.logline)}</p>",
        f"<p>{escape(export_status(script))} · 版本 {screenplay_hash(script)[:12]}</p>",
    ]
    for kind, item in sections(script):
        if kind == "group":
            parts.append(f"<h2>{escape(item)}</h2>")
            continue
        parts.append(
            f'<section><h3>{escape(item.id)} {escape(item.heading)}</h3><p class="source">{escape(item.source_chapter)}</p>'
        )
        for e in item.elements:
            if e.type == "dialogue":
                notes = " · ".join(v for v in (e.parenthetical, e.emotion) if v)
                parts.append(
                    f'<div class="dialogue"><p class="cue">{escape(names[e.character])}</p>'
                )
                if notes:
                    parts.append(f'<p class="delivery">（{escape(notes)}）</p>')
                parts.append(f"<p>{escape(e.text)}</p></div>")
            else:
                parts.append(f'<p class="action">{escape(e.text)}</p>')
        parts.append("</section>")
    if appendix:
        parts.append("<h2>人物表</h2><ul>")
        for c in script.characters:
            parts.append(
                f"<li><strong>{escape(c.name)}</strong>：{escape(c.description)}；动机：{escape(c.motivation)}</li>"
            )
        parts.append(
            "</ul><h2>场景与道具表</h2><table><thead><tr><th>场景</th><th>地点与时间</th><th>道具</th></tr></thead><tbody>"
        )
        for s in script.scenes:
            parts.append(
                f"<tr><td>{escape(s.id)}</td><td>{escape(s.location)} · {escape(s.time_of_day)}</td><td>{escape('、'.join(s.props) or '无')}</td></tr>"
            )
        parts.append("</tbody></table>")
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(script.title)} 剧本</title><style>"
        'body{max-width:760px;margin:40px auto;padding:0 24px;color:#111;font:12pt/1.65 "Microsoft YaHei","Noto Sans CJK SC",sans-serif}'
        "h1,h2,h3{color:#000;break-after:avoid}h1{font-size:24pt}h2{font-size:18pt;margin-top:28px}h3{font-size:13pt;margin-top:24px}"
        "p{white-space:pre-wrap;overflow-wrap:anywhere}.dialogue{margin:18px 12%}.cue{font-weight:bold;margin-bottom:0;break-after:avoid}"
        ".delivery{margin:0;color:#444;break-after:avoid}.source{font-size:10pt;color:#555}.action{margin:14px 0}"
        "table{border-collapse:collapse;width:100%;font-size:10pt}th,td{border:1px solid #d9d9d9;padding:8px;text-align:left}th{background:#eee}"
        "@page{size:letter;margin:22mm}@media print{body{max-width:none;margin:0;padding:0}thead{display:table-header-group}tr{break-inside:avoid}}"
        "</style></head><body>" + "".join(parts) + "</body></html>"
    )


def script_docx(script, appendix=True):
    from docx import Document
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    document = Document()
    # Some installed default templates add a decorative blue rule to Title.
    for border in document.styles.element.xpath(".//w:pBdr"):
        border.getparent().remove(border)
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.8)
    section.left_margin = section.right_margin = Inches(0.9)
    for key in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3"):
        style = document.styles[key]
        style.font.name = "Calibri"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Microsoft YaHei")
    normal = document.styles["Normal"]
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.3
    normal.paragraph_format.space_after = Pt(7)
    document.add_paragraph(script.title, "Title")
    document.add_paragraph(f"{script.adaptation_type} · {script.genre}\n{script.logline}")
    document.add_paragraph(export_status(script))
    document.add_paragraph(f"版本 {screenplay_hash(script)[:12]}")
    if script.creative_brief:
        document.add_paragraph(
            f"风格 {script.creative_brief.name} v{script.creative_brief.template_version}"
        )
    names = {c.id: c.name for c in script.characters}
    for kind, item in sections(script):
        if kind == "group":
            document.add_heading(item, 1)
            continue
        document.add_heading(f"{item.id}  {item.heading}", 2)
        p = document.add_paragraph(f"来源 {item.source_chapter}")
        p.paragraph_format.keep_with_next = True
        for e in item.elements:
            if e.type == "dialogue":
                cue = document.add_paragraph()
                cue.paragraph_format.left_indent = Inches(0.8)
                cue.paragraph_format.keep_with_next = True
                cue.add_run(names[e.character]).bold = True
                notes = " · ".join(v for v in (e.parenthetical, e.emotion) if v)
                if notes:
                    cue.add_run(f"（{notes}）")
                p = document.add_paragraph(e.text)
                p.paragraph_format.left_indent = p.paragraph_format.right_indent = Inches(0.8)
            else:
                document.add_paragraph(e.text)
    if appendix:
        document.add_heading("人物表", 1)
        for c in script.characters:
            p = document.add_paragraph()
            p.add_run(c.name + "  ").bold = True
            p.add_run(c.description + "；动机：" + c.motivation)
        document.add_heading("场景与道具表", 1)
        table = document.add_table(rows=1, cols=3)
        table.autofit = False
        for col, width in zip(table.columns, (1.0, 2.3, 3.4)):
            col.width = Inches(width)
        for cell, text in zip(table.rows[0].cells, ("场景", "地点与时间", "道具")):
            cell.text = text
        repeat = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for scene in script.scenes:
            for cell, text in zip(
                table.add_row().cells,
                (
                    scene.id,
                    f"{scene.location} · {scene.time_of_day}",
                    "、".join(scene.props) or "无",
                ),
            ):
                cell.text = text
        for index, row in enumerate(table.rows):
            for cell in row.cells:
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                props = cell._tc.get_or_add_tcPr()
                borders = OxmlElement("w:tcBorders")
                for edge in ("top", "left", "bottom", "right"):
                    border = OxmlElement("w:" + edge)
                    for k, v in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
                        border.set(qn("w:" + k), v)
                    borders.append(border)
                props.append(borders)
                margins = OxmlElement("w:tcMar")
                for edge in ("top", "left", "bottom", "right"):
                    margin = OxmlElement("w:" + edge)
                    margin.set(qn("w:w"), "100")
                    margin.set(qn("w:type"), "dxa")
                    margins.append(margin)
                props.append(margins)
                if index == 0:
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:fill"), "EEEEEE")
                    props.append(shade)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(3)
                    for run in p.runs:
                        run.font.size = Pt(10)
                        run.bold = index == 0
    footer = section.footer.paragraphs[0]
    footer.add_run(script.title + "  ·  ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    output = BytesIO()
    document.save(output)
    return output.getvalue()
