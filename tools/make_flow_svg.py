# -*- coding: utf-8 -*-
"""生成现状流程图（泳道 + 判断点 + 异常路径），无任何 AI 介入节点。

    python tools/make_flow_svg.py        # 输出 docs/05_现状流程图_*.svg
    node tmp/render.js                   # 输出 PNG（resvg，2x）

版面：画布 1700x1210；6 条泳道；6 列（列宽 190，列距 244）。
泳道归属按执行角色：P1/P2 属单证、P5 属业务负责人，异常框就近挂在触发行。
"""
import os
from html import escape

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "docs", "05_现状流程图_f250118_f25015243_姚晔.svg")

W, H = 1700, 1210
FONT = "Microsoft YaHei, PingFang SC, sans-serif"

C = {
    "bg": "#ffffff", "lane_a": "#f8fbfe", "lane_b": "#ffffff", "lane_line": "#dbe7f3",
    "label_bg": "#eaf2fb", "label": "#12467d", "ink": "#1c2b3a", "sub": "#5b6f83",
    "box_fill": "#e9f3fd", "box_line": "#8fb3d9", "box_title": "#0f3f70",
    "ctrl_fill": "#fdeee9", "ctrl_line": "#c26450",
    "excp_fill": "#fff8ea", "excp_line": "#cf9a46",
    "dia_fill": "#eef8f1", "dia_line": "#5f9d7c",
    "main": "#33506e", "exc": "#c0503c", "ret": "#2f7d5a",
}

LANES = [
    (130, 260, "客户", ["发起委托", "收货提异议"]),
    (260, 660, "单证操作员", ["张伟", "建单/订舱/录费用"]),
    (660, 780, "供应商·船司", ["5 家船司", "回传账单"]),
    (780, 960, "结算专员", ["陈莉莉", "汇总/核对/出账"]),
    (960, 1050, "客服主管", ["李文静", "受理客户异议"]),
    (1050, 1140, "财务", ["周慧", "核销 / 月结"]),
]

COL = {1: 210, 2: 454, 3: 698, 4: 942, 5: 1186, 6: 1430}
BW = 190


def box(col, y, num, title, caps, kind="main", h=None):
    h = h or (76 if len(caps) <= 1 else 88)
    x = COL[col]
    fill, line = {"main": (C["box_fill"], C["box_line"]),
                  "ctrl": (C["ctrl_fill"], C["ctrl_line"]),
                  "excp": (C["excp_fill"], C["excp_line"])}[kind]
    dash = ' stroke-dasharray="5 4"' if kind == "excp" else ""
    s = [f'<rect x="{x}" y="{y}" width="{BW}" height="{h}" rx="9" fill="{fill}" '
         f'stroke="{line}" stroke-width="1.4"{dash}/>',
         f'<text x="{x + 13}" y="{y + 27}" font-size="13.5" font-weight="bold" '
         f'fill="{C["box_title"]}">{escape((num + " ") if num else "")}{escape(title)}</text>']
    for i, cap in enumerate(caps):
        s.append(f'<text x="{x + 13}" y="{y + 48 + i * 16}" font-size="11" '
                 f'fill="{C["sub"]}">{escape(cap)}</text>')
    return "".join(s), dict(x=x, y=y, w=BW, h=h, cx=x + BW / 2, cy=y + h / 2,
                            right=x + BW, bottom=y + h)


def diamond(col, cy, text, sub="", rx=76, ry=42):
    cx = COL[col] + BW / 2
    pts = f"{cx},{cy - ry} {cx + rx},{cy} {cx},{cy + ry} {cx - rx},{cy}"
    s = [f'<polygon points="{pts}" fill="{C["dia_fill"]}" stroke="{C["dia_line"]}" stroke-width="1.4"/>',
         f'<text x="{cx}" y="{cy - 1}" text-anchor="middle" font-size="11.5" font-weight="bold" '
         f'fill="#1d5c41">{escape(text)}</text>']
    if sub:
        s.append(f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="9.5" '
                 f'fill="{C["sub"]}">{escape(sub)}</text>')
    return "".join(s), dict(cx=cx, cy=cy, rx=rx, ry=ry,
                            left=cx - rx, right=cx + rx, top=cy - ry, bottom=cy + ry)


def arrow(pts, kind="main", label=None, at=None, anchor="middle"):
    color = {"main": C["main"], "exc": C["exc"], "ret": C["ret"]}[kind]
    sw = 2.0 if kind == "main" else 1.6
    dash = "" if kind == "main" else ' stroke-dasharray="7 5"'
    d = f"M {pts[0][0]},{pts[0][1]} " + " ".join(f"L {x},{y}" for x, y in pts[1:])
    s = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dash} '
         f'marker-end="url(#ah-{kind})" stroke-linejoin="round" stroke-linecap="round"/>']
    if label:
        x, y = at
        s.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="11" fill="{color}" '
                 f'paint-order="stroke" stroke="{C["bg"]}" stroke-width="4" '
                 f'stroke-linejoin="round">{escape(label)}</text>')
    return "".join(s)


def build():
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="{FONT}">', "<defs>"]
    for k, col in (("main", C["main"]), ("exc", C["exc"]), ("ret", C["ret"])):
        out.append(f'<marker id="ah-{k}" viewBox="0 0 10 10" refX="9" refY="5" '
                   f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                   f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{col}"/></marker>')
    out += ["</defs>", f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>']

    # ---- 标题 ----
    out.append('<text x="54" y="52" font-size="26" font-weight="bold" fill="#0b2a4a">'
               '海川服贸 · 贸易服务结算与费用追溯 — 现状流程图（AS-IS）</text>')
    out.append(f'<text x="54" y="80" font-size="12" fill="{C["sub"]}">'
               '依据：6 人角色访谈 285 分钟 · 跟岗 2 小时 · 24 条证据（E-001~E-025）</text>')
    out.append(f'<text x="54" y="100" font-size="12" fill="{C["sub"]}">'
               '13 步主流程 + 3 条异常路径（X-01~X-03）+ 5 个判断点（P1~P5）'
               '　｜　全程人工操作，无任何 AI 介入</text>')

    # ---- 图例 ----
    out.append('<rect x="1108" y="26" width="552" height="86" rx="8" fill="#f7fafd" stroke="#e2ecf6"/>')
    for i, (k, name, desc) in enumerate([("main", "主导流程", "实线"),
                                        ("exc", "异常路径", "红色虚线"),
                                        ("ret", "退回 / 补正", "绿色虚线")]):
        y = 50 + i * 24
        out.append(arrow([(1122, y), (1168, y)], kind=k))
        out.append(f'<text x="1178" y="{y + 4}" font-size="11.5" fill="{C["ink"]}">{name}'
                   f'<tspan fill="{C["sub"]}"> {desc}</tspan></text>')
    out.append(f'<polygon points="1400,40 1430,56 1400,72 1370,56" fill="{C["dia_fill"]}" '
               f'stroke="{C["dia_line"]}"/>')
    out.append(f'<text x="1440" y="53" font-size="11.5" fill="{C["ink"]}">判断点'
               f'<tspan fill="{C["sub"]}"> 人工决策</tspan></text>')
    out.append(f'<rect x="1370" y="82" width="60" height="20" rx="5" fill="{C["ctrl_fill"]}" '
               f'stroke="{C["ctrl_line"]}"/>')
    out.append(f'<text x="1440" y="97" font-size="11.5" fill="{C["ink"]}">人工控制点'
               f'<tspan fill="{C["sub"]}"> 须留痕</tspan></text>')

    # ---- 泳道 ----
    for i, (y1, y2, name, subs) in enumerate(LANES):
        out.append(f'<rect x="40" y="{y1}" width="{W - 80}" height="{y2 - y1}" '
                   f'fill="{C["lane_a"] if i % 2 == 0 else C["lane_b"]}" stroke="{C["lane_line"]}"/>')
        out.append(f'<rect x="40" y="{y1}" width="130" height="{y2 - y1}" '
                   f'fill="{C["label_bg"]}" stroke="{C["lane_line"]}"/>')
        cy = (y1 + y2) / 2
        out.append(f'<text x="105" y="{cy - 4}" text-anchor="middle" font-size="13.5" '
                   f'font-weight="bold" fill="{C["label"]}">{escape(name)}</text>')
        for j, t in enumerate(subs):
            out.append(f'<text x="105" y="{cy + 14 + j * 13}" text-anchor="middle" '
                       f'font-size="9.5" fill="{C["sub"]}">{escape(t)}</text>')

    f = []

    # ===== 节点 =====
    s, b1 = box(1, 157, "①", "客户委托下单", ["邮件 / 微信 / 客户系统"]); f.append(s)
    s, d4 = diamond(6, 195, "7 日内提异议？", "P4 · 合同 §7.2"); f.append(s)

    s, b2 = box(1, 290, "②", "建单·生成费用模板", ["CargoWare 建单", "空白费用行，无校验"]); f.append(s)
    s, p1 = diamond(2, 334, "新客户 / 新航线？", "P1"); f.append(s)
    s, b3 = box(3, 290, "③", "订舱 / 报关 / 派车", ["生成订舱号·提单号", "作业项决定应收科目"]); f.append(s)
    s, b4 = box(4, 290, "④", "录入应付预估", ["附加费写成自由文本", "fee_remark 非字典值"]); f.append(s)
    s, bappr = box(2, 404, None, "报价审批（赵磊）", ["→ 更新共享盘费率表"], kind="ctrl", h=64); f.append(s)

    s, p2 = diamond(3, 560, "与预估一致？", "P2 · 人工目视比对"); f.append(s)
    s, bdiff = box(2, 522, None, "记入差异台账.xlsx", ["人工比对，事后无跟踪"], kind="excp", h=76); f.append(s)

    s, b5 = box(4, 690, "⑤", "船司/供应商回传账单", ["邮件 PDF/Excel，无固定模板", "平均晚到 6.8 天（P90 11 天）"]); f.append(s)
    s, bx1 = box(5, 690, None, "甩柜 / 改配（X-01）", ["只在微信群通知", "附加费未进系统"], kind="excp", h=76); f.append(s)

    s, b6 = box(3, 800, "⑥", "汇总应收（导出 Excel）", ["只有金额合计", "无附加费明细"]); f.append(s)
    s, b7 = box(4, 800, "⑦", "按费率卡核对金额", ["共享盘 7 个“最终版”", "P3 合同项靠翻 PDF 判断"]); f.append(s)
    s, b8 = box(5, 800, "⑧", "Excel 拼账单（41 行）", ["自建模板 V6/V4/V5", "手填，无校验"]); f.append(s)
    s, b9 = box(6, 800, "⑨", "发客户账单", ["邮件发出即起算", "异议窗口"]); f.append(s)
    s, bx2 = box(5, 900, None, "多箱分批 → 模板复制（X-02）", ["同一费用行重复计入"], kind="excp", h=56); f.append(s)

    s, b11 = box(4, 966, "⑪", "受理异议·转结算", ["仅能转单，无明细权限", "答复周期 1.2 天"]); f.append(s)
    s, p5 = diamond(3, 1002, "是否补收 / 减免？", "P5 · 赵磊拍板", rx=74, ry=38); f.append(s)

    s, b12 = box(3, 1060, "⑫", "收款核销", ["用友 U8"], h=62); f.append(s)
    s, b13 = box(4, 1060, "⑬", "月结对账", ["差异挂“待查”，季末 ¥120 万"], h=62); f.append(s)
    s, bx3 = box(5, 1060, None, "汇率口径（X-03）", ["开票日 vs 发生日"], kind="excp", h=62); f.append(s)

    # ===== 连线 =====
    f.append(arrow([(b1["cx"], b1["bottom"]), (b1["cx"], b2["y"])]))
    f.append(arrow([(b2["right"], 334), (p1["left"], 334)]))
    f.append(arrow([(p1["right"], 334), (b3["x"], 334)], label="否", at=(661, 326)))
    f.append(arrow([(p1["cx"], p1["bottom"]), (p1["cx"], bappr["y"])], kind="ret",
                   label="是", at=(561, 396), anchor="start"))
    f.append(arrow([(bappr["right"], 436), (b4["cx"], 436), (b4["cx"], b4["bottom"])], kind="ret"))
    f.append(arrow([(b3["right"], 334), (b4["x"], 334)]))

    # ④ → ⑤：账单到达
    f.append(arrow([(b4["cx"], b4["bottom"]), (b4["cx"], b5["y"])],
                   label="账单到达（平均 6.8 天）", at=(1045, 640), anchor="start"))
    # ⑤ → P2：回传后交单证核对（跨泳道）
    f.append(arrow([(b5["x"], b5["cy"]), (900, b5["cy"]), (900, p2["cy"]), (p2["right"], p2["cy"])]))
    # P2 否 → 差异台账
    f.append(arrow([(p2["left"], p2["cy"]), (bdiff["right"], p2["cy"])], kind="exc",
                   label="否", at=(686, p2["cy"] - 9)))
    # P2 是 → ⑥
    f.append(arrow([(p2["cx"], p2["bottom"]), (p2["cx"], b6["y"])], label="是", at=(810, 720), anchor="start"))
    # ⑤ → X-01
    f.append(arrow([(b5["right"], b5["cy"]), (bx1["x"], b5["cy"])], kind="exc"))
    # X-01 → ④：应收未同步
    f.append(arrow([(bx1["cx"], bx1["y"]), (bx1["cx"], 470), (1100, 470), (1100, b4["bottom"])], kind="exc"))
    f.append(f'<text x="1290" y="498" font-size="10.5" fill="{C["exc"]}">应收未同步</text>')

    # ⑥→⑦→⑧→⑨
    f.append(arrow([(b6["right"], 844), (b7["x"], 844)]))
    f.append(arrow([(b7["right"], 844), (b8["x"], 844)]))
    f.append(arrow([(b8["right"], 844), (b9["x"], 844)]))
    # X-02 → ⑧
    f.append(arrow([(bx2["cx"], bx2["y"]), (bx2["cx"], b8["bottom"])], kind="exc"))

    # ⑨ → ⑩
    f.append(arrow([(b9["cx"], b9["y"]), (b9["cx"], d4["bottom"])],
                   label="账单到达客户", at=(1533, 470), anchor="start"))
    # ⑩ 是 → ⑪
    f.append(arrow([(d4["left"], d4["cy"]), (1170, d4["cy"]), (1170, 932), (b11["cx"], 932),
                    (b11["cx"], b11["y"])], kind="exc",
                   label="是 · 提异议", at=(1300, 186)))
    # ⑩ 否 → ⑫
    f.append(arrow([(d4["right"], d4["cy"]), (1678, d4["cy"]), (1678, 1132), (b12["cx"], 1132),
                    (b12["cx"], b12["bottom"])], kind="ret",
                   label="否 · 默认确认", at=(1666, 646), anchor="end"))
    # ⑪ → P5
    f.append(arrow([(b11["x"], b11["cy"]), (p5["right"], p5["cy"])], kind="exc",
                   label="转追溯", at=(905, 996)))
    # P5 → ⑫
    f.append(arrow([(p5["cx"], p5["bottom"]), (p5["cx"], b12["y"])], kind="ret"))
    # ⑫ → ⑬ → X-03
    f.append(arrow([(b12["right"], 1091), (b13["x"], 1091)]))
    f.append(arrow([(b13["right"], 1091), (bx3["x"], 1091)], kind="exc"))

    out.extend(f)

    # ---- 底部说明 ----
    out.append(f'<rect x="40" y="1152" width="{W - 80}" height="50" rx="8" fill="#f7fafd" stroke="#e2ecf6"/>')
    out.append(f'<text x="56" y="1170" font-size="10.5" fill="{C["sub"]}">'
               '读图：实线为主流程；红色虚线为异常/遗漏路径（X-01 改配费只在群里通知、X-02 多箱分批模板复制、X-03 汇率口径不一）；绿色虚线为退回补正。</text>')
    out.append(f'<text x="56" y="1183" font-size="10.5" fill="{C["sub"]}">'
               '菱形为必须由人判断的节点：P1 新客户/新航线、P2 账单与预估比对、P3 合同是否含该费用项、P4 异议是否成立、P5 是否补收/减免。</text>')
    out.append(f'<text x="56" y="1196" font-size="10.5" fill="{C["sub"]}">'
               '说明：本图为现状还原（AS-IS），不含任何 AI 环节；序号 ①~⑬ 与《03 现场调研分析报告》第二章逐步说明一一对应。</text>')
    out.append("</svg>")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out))
    print("written:", OUT)


if __name__ == "__main__":
    build()
