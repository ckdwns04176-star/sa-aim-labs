"""스카우팅 오버레이 레이아웃 미리보기 PNG (개발용).

실제 Windows GDI+ 대신 Pillow 로 같은 그리기 명세(prims)를 그려, 글씨 잘림·
과한 여백을 눈으로 확인한다.  앱은 이 파일을 쓰지 않는다.

    python3 tools/scout_preview.py [출력경로]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import native_overlay as no, overlay  # noqa: E402

FONT = "/usr/share/fonts/pickle-hwp/NotoSansCJKkr-Regular.otf"

ROWS = [
    {"name": "휴가중.", "solo_stats": {"short": "그마 II", "win": 52.0, "kd": 58.0, "games": 12},
     "party_stats": {"short": "마스터 I", "win": 47.0, "kd": 55.0, "games": 8}},
    {"name": "시티캣", "solo_stats": {"short": "별레", "win": 57.1, "kd": 61.2, "games": 20},
     "party_stats": {"short": "그마 III", "win": 48.3, "kd": 55.0, "games": 20}},
    {"name": "프로방스", "solo_stats": {"short": "언랭", "win": None, "kd": None, "games": 0},
     "party_stats": {"short": "언랭", "win": None, "kd": None, "games": 0}},
    {"name": "크로스포트", "solo_stats": {"short": "불레", "win": 52.0, "kd": 58.0, "games": 14},
     "party_stats": {"short": "레전", "win": 46.0, "kd": 54.0, "games": 9}},
    {"name": "상대에이스1", "solo_stats": {"short": "마스터 III", "win": 47.0, "kd": 49.0, "games": 11},
     "party_stats": {"short": "골드 I", "win": 53.0, "kd": 56.0, "games": 6}},
    {"name": "적팀스나1", "solo_stats": {"short": "그마 I", "win": 50.0, "kd": 57.0, "games": 18},
     "party_stats": {"short": "마스터 II", "win": 49.0, "kd": 53.0, "games": 12}},
    {"name": "적팀라플2", "solo_stats": {"short": "마스터 I", "win": 45.0, "kd": 51.0, "games": 20},
     "party_stats": {"short": "언랭", "win": None, "kd": None, "games": 0}},
    {"name": "이름이열두글자임", "solo_stats": {"short": "레전", "win": 54.0, "kd": 59.0, "games": 20},
     "party_stats": {"short": "별레", "win": 51.0, "kd": 57.0, "games": 20}},
    {"name": "적팀4", "solo_stats": {"short": "골드 I", "win": 41.0, "kd": 44.0, "games": 8},
     "party_stats": {"short": "실버 II", "win": 38.0, "kd": 42.0, "games": 5}},
    {"name": "적팀5", "solo_stats": {"short": "언랭", "win": None, "kd": None, "games": 0},
     "party_stats": {"short": "골드 III", "win": 50.0, "kd": 55.0, "games": 7}},
]


def render(path: str, game: tuple[int, int, int, int] = (0, 0, 1920, 1080)) -> dict:
    rect = overlay.scout_overlay_rect(game)
    payload = {"tab": "scout", "top_team": ROWS[:5], "bottom_team": ROWS[5:], "loading": False}
    spec = no.build_spec("match", payload, rect["w"], rect["h"])
    width, height = spec["w"], spec["h"]
    img = Image.new("RGBA", (width, height), (26, 29, 36, 255))
    draw = ImageDraw.Draw(img)

    def color(name: str, alpha: float = 1.0):
        hexv = no.COLORS.get(name, name).lstrip("#")
        return (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16),
                int(255 * max(0.0, min(1.0, alpha))))

    overflows = []
    for prim in spec["prims"]:
        if prim["type"] == "rect":
            draw.rounded_rectangle([prim["x"], prim["y"], prim["x"] + prim["w"], prim["y"] + prim["h"]],
                                   radius=prim.get("radius") or 0,
                                   fill=color(prim["fill"], prim.get("alpha", 1.0)),
                                   outline=color(prim["stroke"], prim.get("stroke_alpha", 1.0))
                                   if prim.get("stroke") else None)
        elif prim["type"] == "line":
            draw.line([prim["x1"], prim["y1"], prim["x2"], prim["y2"]],
                      fill=color(prim["color"], prim.get("alpha", 1.0)),
                      width=max(1, int(prim.get("width") or 1)))
        elif prim["type"] == "text":
            font = ImageFont.truetype(FONT, max(8, int(round(prim["size"]))))
            anchor = {"left": "lm", "center": "mm", "right": "rm"}[prim.get("align", "left")]
            x = prim["x"] if prim.get("align") == "left" else (
                prim["x"] + prim["w"] if prim.get("align") == "right" else prim["x"] + prim["w"] / 2)
            y = prim["y"] + prim["h"] / 2
            draw.text((x, y), prim["text"], font=font, anchor=anchor,
                      fill=color(prim["color"], prim.get("alpha", 1.0)))
            box = draw.textbbox((x, y), prim["text"], font=font, anchor=anchor)
            if box[2] > width - 4 or box[3] > height - 2:
                overflows.append((prim["text"], box))
    img.convert("RGB").save(path)
    return {"path": path, "rect": rect, "overflows": overflows}


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path("/workspace/downloads/scout_overlay_preview.png"))
    info = render(out)
    print(info)
