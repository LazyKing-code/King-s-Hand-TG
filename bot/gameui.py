from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot.config import ROOT

_ASSETS = ROOT / "assets"
_W, _H = 960, 540
_NAVY = (11, 18, 32, 255)
_GOLD = (212, 175, 55, 255)
_WHITE = (245, 247, 250, 255)
_MUTED = (139, 155, 180, 255)
_GREEN = (34, 140, 90, 255)
_RED = (220, 50, 60, 255)
_PANEL = (16, 24, 42, 230)


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    )
    dirs = (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("C:/Windows/Fonts"),
        _ASSETS,
    )
    for folder in dirs:
        for name in names:
            path = folder / name
            if path.is_file():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, size: int, max_w: int, *, bold: bool = False) -> ImageFont.ImageFont:
    font = _font(size, bold=bold)
    while size > 14:
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
        font = _font(size, bold=bold)
    return font


def _base(accent: tuple[int, int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (_W, _H), _NAVY)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _W, 8], fill=accent)
    draw.ellipse([-180, -220, 320, 280], fill=(accent[0], accent[1], accent[2], 28))
    draw.ellipse([680, 280, 1180, 780], fill=(34, 80, 140, 40))
    draw.rounded_rectangle([28, 28, _W - 28, _H - 28], radius=28, fill=_PANEL)
    draw.rounded_rectangle([28, 28, _W - 28, _H - 28], radius=28, outline=(255, 255, 255, 28), width=2)
    return img, draw


def _png(img: Image.Image) -> bytes:
    rgb = Image.new("RGB", img.size, (11, 18, 32))
    rgb.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
    rgb = rgb.filter(ImageFilter.SMOOTH)
    buf = BytesIO()
    rgb.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def png_file(data: bytes) -> BytesIO:
    buf = BytesIO(data)
    buf.name = "kings-hand-game.png"
    return buf


def render_cricket(
    *,
    name_a: str,
    name_b: str,
    score_a: int,
    score_b: int,
    batter_is_a: bool,
    innings: int,
    ball: int,
    target: int | None,
    last: str,
    waiting: str,
) -> bytes:
    from bot.cricket import draw_name

    name_a, name_b = draw_name(name_a), draw_name(name_b)
    img, draw = _base(_GREEN)
    draw.text((56, 48), "HAND CRICKET", font=_font(28, bold=True), fill=_GOLD)
    if innings == 2 and target is not None:
        chase = score_b if not batter_is_a else score_a
        need = max(0, target + 1 - chase)
        sub = f"CHASE  ·  ball {min(max(ball, 1), 6)}/6  ·  need {need}"
    else:
        sub = f"FIRST INNINGS  ·  ball {min(max(ball, 1), 6)}/6"
    draw.text((56, 92), sub, font=_font(18), fill=_MUTED)

    bat, bowl = (name_a, name_b) if batter_is_a else (name_b, name_a)
    draw.rounded_rectangle([56, 130, 470, 280], radius=18, fill=(8, 14, 26, 255))
    draw.text((80, 148), "BATTING", font=_font(14, bold=True), fill=_GREEN)
    draw.text((80, 176), bat, font=_fit(draw, bat, 32, 350, bold=True), fill=_WHITE)
    draw.text((80, 226), "BOWLING", font=_font(14, bold=True), fill=_MUTED)
    draw.text((80, 248), bowl, font=_fit(draw, bowl, 22, 350, bold=True), fill=_MUTED)

    draw.rounded_rectangle([494, 130, 904, 280], radius=18, fill=(8, 14, 26, 255))
    draw.text((520, 148), name_a, font=_fit(draw, name_a, 16, 160), fill=_MUTED)
    draw.text((520, 176), str(score_a), font=_font(44, bold=True), fill=_GOLD if batter_is_a else _WHITE)
    draw.text((720, 148), name_b, font=_fit(draw, name_b, 16, 160), fill=_MUTED)
    draw.text((720, 176), str(score_b), font=_font(44, bold=True), fill=_GOLD if not batter_is_a else _WHITE)
    if innings == 2 and target is not None:
        draw.text((520, 236), f"Target {target + 1}", font=_font(18, bold=True), fill=_GOLD)
    else:
        draw.text((520, 236), "Same number = OUT", font=_font(16), fill=_MUTED)

    draw.rounded_rectangle([56, 304, 904, 488], radius=18, fill=(8, 14, 26, 200))
    headline = last or "Both tap 1–6. Same number is out."
    draw.text((80, 340), headline, font=_fit(draw, headline, 26, 800, bold=True), fill=_WHITE)
    draw.text((80, 410), waiting or "One tap each. Then the ball is bowled.", font=_fit(draw, waiting, 20, 800), fill=_MUTED)
    return _png(img)


def render_duel(
    *,
    title: str,
    name_a: str,
    name_b: str,
    line: str,
    waiting: str,
    accent: tuple[int, int, int, int] = _GOLD,
) -> bytes:
    img, draw = _base(accent)
    draw.text((56, 48), f"KING'S HAND  ·  {title.upper()}", font=_font(22, bold=True), fill=_GOLD)
    af = _fit(draw, name_a, 32, 380, bold=True)
    bf = _fit(draw, name_b, 32, 380, bold=True)
    draw.rounded_rectangle([56, 120, 450, 260], radius=18, fill=(8, 14, 26, 255))
    draw.rounded_rectangle([510, 120, 904, 260], radius=18, fill=(8, 14, 26, 255))
    draw.text((80, 160), name_a, font=af, fill=_WHITE)
    draw.text((534, 160), name_b, font=bf, fill=_WHITE)
    draw.text((_W // 2 - 16, 168), "VS", font=_font(22, bold=True), fill=_GOLD)
    draw.rounded_rectangle([56, 290, 904, 488], radius=18, fill=(8, 14, 26, 200))
    lf = _fit(draw, line, 24, 800, bold=True)
    draw.text((80, 320), line, font=lf, fill=_WHITE)
    draw.text((80, 390), waiting, font=_fit(draw, waiting, 18, 800), fill=_MUTED)
    return _png(img)


def render_result(
    *,
    title: str,
    headline: str,
    detail: str,
    accent: tuple[int, int, int, int] = _GOLD,
) -> bytes:
    img, draw = _base(accent)
    draw.text((56, 48), f"KING'S HAND  ·  {title.upper()}", font=_font(22, bold=True), fill=_GOLD)
    hf = _fit(draw, headline, 40, 840, bold=True)
    draw.text((56, 160), headline, font=hf, fill=_WHITE)
    df = _fit(draw, detail, 22, 840)
    draw.text((56, 240), detail, font=df, fill=_MUTED)
    draw.text((56, 430), "Coins are for fun only — nothing is real money.", font=_font(16), fill=_MUTED)
    return _png(img)


def render_four(
    *,
    name_a: str,
    name_b: str,
    board: str,
    turn_is_a: bool,
    event: str,
) -> bytes:
    img, draw = _base((64, 160, 200, 255))
    draw.text((56, 44), "KING'S HAND  ·  FOUR IN A ROW", font=_font(22, bold=True), fill=_GOLD)
    af = _fit(draw, name_a, 20, 280, bold=True)
    bf = _fit(draw, name_b, 20, 280, bold=True)
    draw.ellipse([56, 84, 84, 112], fill=_GOLD)
    draw.text((96, 86), name_a, font=af, fill=_WHITE if turn_is_a else _MUTED)
    draw.ellipse([500, 84, 528, 112], fill=(70, 190, 210, 255))
    draw.text((540, 86), name_b, font=bf, fill=_WHITE if not turn_is_a else _MUTED)

    cols, rows = 7, 6
    left, top = 88, 124
    gap = 8
    cell = 48
    draw.rounded_rectangle(
        [left - 16, top - 16, left + cols * (cell + gap) - gap + 16, top + rows * (cell + gap) - gap + 16],
        radius=18,
        fill=(8, 22, 48, 255),
    )
    cells = (board + "0" * 42)[:42]
    for r in range(rows):
        for c in range(cols):
            x = left + c * (cell + gap)
            y = top + r * (cell + gap)
            piece = cells[r * cols + c]
            if piece == "1":
                fill = _GOLD
            elif piece == "2":
                fill = (70, 190, 210, 255)
            else:
                fill = (18, 32, 52, 255)
            draw.ellipse([x, y, x + cell, y + cell], fill=fill)
            draw.ellipse([x, y, x + cell, y + cell], outline=(255, 255, 255, 30), width=2)

    ev = event or "Drop a disc. First to four in a line wins."
    draw.text((56, 492), ev, font=_fit(draw, ev, 18, 840, bold=True), fill=_WHITE)
    return _png(img)


def render_penalty(
    *,
    name_a: str,
    name_b: str,
    goals_a: int,
    goals_b: int,
    shooter_is_a: bool,
    kick: int,
    event: str,
    waiting: str,
) -> bytes:
    img, draw = _base((30, 130, 70, 255))
    draw.text((56, 48), "KING'S HAND  ·  PENALTY DUEL", font=_font(22, bold=True), fill=_GOLD)
    draw.text((56, 88), f"KICK {kick}", font=_font(16), fill=_MUTED)
    draw.rounded_rectangle([56, 120, 450, 250], radius=18, fill=(8, 14, 26, 255))
    draw.rounded_rectangle([510, 120, 904, 250], radius=18, fill=(8, 14, 26, 255))
    draw.text((80, 140), name_a, font=_fit(draw, name_a, 24, 340, bold=True), fill=_WHITE)
    draw.text((80, 184), str(goals_a), font=_font(44, bold=True), fill=_GOLD)
    draw.text((534, 140), name_b, font=_fit(draw, name_b, 24, 340, bold=True), fill=_WHITE)
    draw.text((534, 184), str(goals_b), font=_font(44, bold=True), fill=_GOLD)
    role = f"{name_a if shooter_is_a else name_b} shoots   ·   other keeps"
    draw.text((56, 270), role, font=_fit(draw, role, 20, 840, bold=True), fill=_GREEN)
    draw.rounded_rectangle([56, 310, 904, 488], radius=18, fill=(8, 14, 26, 200))
    draw.text((80, 336), event, font=_fit(draw, event, 22, 800, bold=True), fill=_WHITE)
    draw.text((80, 400), waiting, font=_fit(draw, waiting, 18, 800), fill=_MUTED)
    draw.text((80, 444), "Same side = save. Different sides = goal. Picks lock.", font=_font(15), fill=_MUTED)
    return _png(img)


def render_vault(
    *,
    name_a: str,
    name_b: str,
    line: str,
    waiting: str,
) -> bytes:
    img, draw = _base((180, 130, 40, 255))
    draw.text((56, 48), "KING'S HAND  ·  THE VAULT", font=_font(22, bold=True), fill=_GOLD)
    draw.rounded_rectangle([56, 120, 450, 280], radius=20, fill=(8, 14, 26, 255))
    draw.rounded_rectangle([510, 120, 904, 280], radius=20, fill=(8, 14, 26, 255))
    draw.text((80, 150), name_a, font=_fit(draw, name_a, 28, 340, bold=True), fill=_WHITE)
    draw.text((80, 210), "SHARE or TAKE", font=_font(16), fill=_MUTED)
    draw.text((534, 150), name_b, font=_fit(draw, name_b, 28, 340, bold=True), fill=_WHITE)
    draw.text((534, 210), "SHARE or TAKE", font=_font(16), fill=_MUTED)
    draw.text((_W // 2 - 18, 178), "VS", font=_font(22, bold=True), fill=_GOLD)
    draw.rounded_rectangle([56, 310, 904, 488], radius=18, fill=(8, 14, 26, 200))
    draw.text((80, 340), line, font=_fit(draw, line, 22, 800, bold=True), fill=_WHITE)
    draw.text((80, 410), waiting, font=_fit(draw, waiting, 18, 800), fill=_MUTED)
    return _png(img)


async def send_or_edit_card(
    query,
    png: bytes,
    caption: str,
    markup=None,
    *,
    edit_message_id: int | None = None,
) -> int | None:
    from telegram import InputFile, InputMediaPhoto
    from telegram.error import BadRequest

    caption = (caption or "").strip()[:1024]
    bot = query.get_bot()
    message = query.message
    chat_id = message.chat_id if message else None
    if not chat_id:
        return None

    async def edit(mid: int) -> bool:
        media_kw = {
            "media": InputFile(png_file(png), filename="kings-hand-game.png"),
            "caption": caption or None,
        }
        if caption and "<" in caption:
            media_kw["parse_mode"] = "HTML"
        media = InputMediaPhoto(**media_kw)
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=mid,
            media=media,
            reply_markup=markup,
        )
        return True

    targets: list[int] = []
    if edit_message_id:
        targets.append(int(edit_message_id))
    if message and message.photo:
        targets.append(message.message_id)

    seen: set[int] = set()
    for mid in targets:
        if mid in seen:
            continue
        seen.add(mid)
        try:
            await edit(mid)
            return mid
        except BadRequest as err:
            text = str(err).lower()
            if "not modified" in text:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=chat_id, message_id=mid, reply_markup=markup
                    )
                except Exception:
                    pass
                return mid
            continue
        except Exception:
            continue

    send_kw = {
        "chat_id": chat_id,
        "photo": png_file(png),
        "caption": caption or None,
        "reply_markup": markup,
    }
    if caption and "<" in caption:
        send_kw["parse_mode"] = "HTML"
    sent = await bot.send_photo(**send_kw)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    return sent.message_id if sent else None
