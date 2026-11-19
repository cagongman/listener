"""Tray artwork with no speech-engine imports."""
from PIL import Image, ImageDraw


def make_icon(color, size=64):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    draw.rounded_rectangle((size * .38, size * .2, size * .62, size * .6), radius=8, fill="white")
    draw.rectangle((size * .48, size * .6, size * .52, size * .78), fill="white")
    return image
