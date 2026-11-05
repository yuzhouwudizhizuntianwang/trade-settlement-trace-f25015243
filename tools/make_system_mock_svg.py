# -*- coding: utf-8 -*-
"""生成「系统截图还原件」：CargoWare 费用行 + 共享盘费率表目录。

    python tools/make_system_mock_svg.py
    node tmp/render.js   （或用通用渲染脚本）

输出：docs/04-项目原有资料/M-03_系统截图_费用行与费率表.svg
用途：作为证据 E-007（7 个费率表版本）与 E-018（fee_remark 为自由文本）的可见载体。
"""
import os
from html import escape

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "docs", "04-项目原有资料", "M-03_系统截图_费用行与费率表.svg")

W, H = 1420, 700
FONT = "Microsoft YaHei, PingFang SC, sans-serif"

INK, SUB = "#1c2b3a", "#5b6f83"
CHROME, CHROME2 = "#eef2f7", "#dfe7f0"
LINE, HEAD = "#c7d5e4", "#1a5fa8"

ROWS = [
    ("OCF", "海运费", "应收", "USD", "1,850.00", "40HQ 宁波—鹿特丹", "张伟"),
    ("THC", "码头操作费", "应收", "CNY", "1,260.00", "-", "张伟"),
    ("CUS", "报关费", "应收", "CNY", "380.00", "单证 2 份", "张伟"),
    ("TRK", "拖车费", "应收", "CNY", "1,150.00", "北仑—工厂", "张伟"),
    ("AGY", "代理费", "应收", "CNY", "720.00", "协议价", "张伟"),
    ("SUR", "附加费", "应收", "CNY", "0.00", "LSS1180 / PSS980（口头待确认）", "张伟"),
]

FILES = [
    ("费率表_2026_V1.xlsx", "2026-01-03 09:12", ""),
    ("费率表_2026_V2.xlsx", "2026-03-15 16:40", ""),
    ("费率表_2026_V3.xlsx", "2026-06-30 11:05", ""),
    ("费率表_2026_V3最终版.xlsx", "2026-09-12 17:22", "← 10-25 出账实际引用"),
    ("费率表_2026_V3最终版_改.xlsx", "2026-10-09 08:31", "← LSS 调价真实生效"),
    ("费率表_2026_最终版2.xlsx", "2026-10-16 14:58", ""),
    ("费率表_临时试算.xlsx", "2026-10-20 19:03", ""),
]

# 左窗口表格列（x 相对窗口左边）
COLS = [("费用代码", 14, 78), ("费用名称", 96, 104), ("方向", 204, 48), ("币种", 256, 48),
        ("金额", 308, 92), ("备注 fee_remark", 404, 258), ("录入人", 668, 60)]
LW, LX, LY = 760, 40, 96          # 左窗口 w/x/y
RW, RX, RY = 540, 838, 186        # 右窗口 w/x/y


def window(x, y, w, h, title, icon="▣"):
    s = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#ffffff" '
         f'stroke="{LINE}" stroke-width="1.2"/>',
         f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="6" fill="{CHROME}" stroke="{LINE}"/>',
         f'<rect x="{x}" y="{y + 24}" width="{w}" height="10" fill="{CHROME}"/>',
         f'<text x="{x + 16}" y="{y + 23}" font-size="12" fill="{INK}">{escape(icon)}  '
         f'{escape(title)}</text>']
    for i, c in enumerate(("#e2685f", "#e8b24a", "#61b46a")):
        s.append(f'<circle cx="{x + w - 22 - i * 20}" cy="{y + 17}" r="5" fill="{c}"/>')
    return "".join(s)


def build():
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{FONT}">',
         f'<rect width="{W}" height="{H}" fill="#f4f7fb"/>']

    # 页眉说明
    o.append(f'<text x="40" y="46" font-size="20" font-weight="bold" fill="#0b2a4a">'
             f'现场系统截图还原 · CargoWare 费用行字段与共享盘费率表目录</text>')
    o.append(f'<text x="40" y="70" font-size="11.5" fill="{SUB}">'
             f'对应证据：E-018（附加费仅有自由文本备注，无来源单据字段）、E-007（共享盘 7 个费率表版本，无版本号与生效期）'
             f'　｜　企业与人名为实训构造</text>')

    # ---- 左窗口：CargoWare 费用行 ----
    o.append(window(LX, LY, LW, 470, "CargoWare · 订舱单 BK2610-0187 / 提单 HLCUNB26100457 · 费用行"))
    # 工具条
    o.append(f'<rect x="{LX}" y="{LY + 34}" width="{LW}" height="30" fill="#f7fafd" stroke="{LINE}"/>')
    for i, lab in enumerate(["保存(F8)", "费用校验", "导出 Excel", "打印"]):
        bx = LX + 12 + i * 96
        o.append(f'<rect x="{bx}" y="{LY + 41}" width="86" height="17" rx="3" fill="#ffffff" '
                 f'stroke="{LINE}"/>')
        o.append(f'<text x="{bx + 43}" y="{LY + 53}" text-anchor="middle" font-size="10" '
                 f'fill="{INK}">{escape(lab)}</text>')
    o.append(f'<text x="{LX + LW - 14}" y="{LY + 53}" text-anchor="end" font-size="10" '
             f'fill="#b03a2e">校验规则：未配置</text>')

    # 表头
    ty = LY + 74
    o.append(f'<rect x="{LX}" y="{ty}" width="{LW}" height="26" fill="{HEAD}"/>')
    for name, dx, cw in COLS:
        o.append(f'<text x="{LX + dx}" y="{ty + 18}" font-size="10.5" fill="#ffffff">'
                 f'{escape(name)}</text>')
    # 行
    for i, r in enumerate(ROWS):
        ry = ty + 26 + i * 46
        fill = "#fff8ea" if r[0] == "SUR" else "#ffffff"
        o.append(f'<rect x="{LX}" y="{ry}" width="{LW}" height="46" fill="{fill}" stroke="{LINE}"/>')
        vals = [r[0], r[1], r[2], r[3], r[4], r[5], r[6]]
        for (name, dx, cw), v in zip(COLS, vals):
            color = "#b03a2e" if r[0] == "SUR" and name.startswith("备注") else INK
            o.append(f'<text x="{LX + dx}" y="{ry + 28}" font-size="10.5" fill="{color}">'
                     f'{escape(v)}</text>')
    # 表格底边 + 状态栏
    by = ty + 26 + len(ROWS) * 46
    o.append(f'<rect x="{LX}" y="{by}" width="{LW}" height="30" fill="#f7fafd" stroke="{LINE}"/>')
    o.append(f'<text x="{LX + 14}" y="{by + 20}" font-size="10.5" fill="{SUB}">'
             f'共 {len(ROWS)} 行　费用合计 USD 1,850.00 / CNY 3,510.00　'
             f'<tspan fill="#b03a2e">来源单据编号：字段不存在</tspan></text>')

    # 标注
    o.append(f'<text x="{LX + 410}" y="{by + 58}" font-size="10.5" fill="#b03a2e">'
             f'↑ 附加费金额为 0，实际金额只写在备注里（¥1,180 + ¥980）</text>')
    o.append(f'<text x="{LX + 410}" y="{by + 76}" font-size="10.5" fill="#b03a2e">'
             f'↑ 改配费 ¥450 连备注都没有，只在微信群里说过</text>')

    # ---- 右窗口：共享盘费率目录 ----
    o.append(window(RX, RY, RW, 380, "\\\\HC-FS01\\结算\\费率\\"))
    o.append(f'<rect x="{RX + 8}" y="{RY + 42}" width="{RW - 16}" height="24" rx="3" '
             f'fill="#ffffff" stroke="{LINE}"/>')
    o.append(f'<text x="{RX + 16}" y="{RY + 58}" font-size="10" fill="{SUB}">'
             f'\\\\HC-FS01\\结算\\费率\\</text>')
    o.append(f'<rect x="{RX}" y="{RY + 72}" width="{RW}" height="24" fill="#eef3f9" stroke="{LINE}"/>')
    o.append(f'<text x="{RX + 12}" y="{RY + 88}" font-size="10" fill="{INK}">名称</text>')
    o.append(f'<text x="{RX + RW - 12}" y="{RY + 88}" text-anchor="end" font-size="10" '
             f'fill="{INK}">修改日期</text>')
    for i, (name, date, note) in enumerate(FILES):
        fy = RY + 96 + i * 36
        hot = "最终版" in name
        o.append(f'<rect x="{RX}" y="{fy}" width="{RW}" height="36" '
                 f'fill="{"#fff8ea" if hot else "#ffffff"}" stroke="{LINE}"/>')
        o.append(f'<text x="{RX + 12}" y="{fy + 15}" font-size="10.5" '
                 f'fill="{"#b03a2e" if hot else INK}">📄 {escape(name)}</text>')
        if note:
            o.append(f'<text x="{RX + 12}" y="{fy + 29}" font-size="9" fill="#b03a2e">'
                     f'{escape(note)}</text>')
        o.append(f'<text x="{RX + RW - 12}" y="{fy + 15}" text-anchor="end" font-size="9.5" '
                 f'fill="{SUB}">{escape(date)}</text>')
    o.append(f'<text x="{RX + 12}" y="{RY + 96 + len(FILES) * 36 + 20}" font-size="10" '
             f'fill="#b03a2e">7 个版本 · 无版本号字段 · 无生效期字段 · 无审批记录</text>')

    # 底部结论条
    o.append(f'<rect x="40" y="592" width="{W - 80}" height="88" rx="8" fill="#ffffff" '
             f'stroke="{LINE}"/>')
    o.append(f'<text x="58" y="620" font-size="11.5" fill="{INK}">'
             f'读图要点：① 费用行只有“备注”一个自由文本字段，附加费无法结构化，也无法回指来源单据（制度第 3.2 条要求有编号）；</text>')
    o.append(f'<text x="58" y="642" font-size="11.5" fill="{INK}">'
             f'② 费率表以“最终版/最终版2/改”命名，新老版本并存，出账取哪一版全凭记忆（制度第 5.1 条要求登记版本与生效期）。</text>')
    o.append(f'<text x="58" y="664" font-size="10.5" fill="{SUB}">'
             f'结论：制度有要求、系统无承载、执行靠人记——这正是阶段 02 四来源收敛中 Operator 与 Buyer 分歧（M-01）的物证。</text>')

    o.append("</svg>")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write("\n".join(o))
    print("written:", OUT)


if __name__ == "__main__":
    build()
