from __future__ import annotations

import argparse
from pathlib import Path

from icnsutil import IcnsFile
from PIL import Image, ImageDraw


ICON_ROOT = Path(__file__).resolve().parents[1] / "resources" / "icons" / "application"
MASTER_PATH = ICON_ROOT / "source" / "application-master-1024.png"
PNG_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = tuple(size for size in PNG_SIZES if size <= 256)
ICNS_FRAMES = {
    "icp4": (16, 1),
    "icp5": (32, 1),
    "icp6": (64, 1),
    "ic07": (128, 1),
    "ic08": (256, 1),
    "ic09": (512, 1),
    "ic11": (16, 2),
    "ic12": (32, 2),
    "ic13": (128, 2),
    "ic14": (256, 2),
    "ic10": (512, 2),
}
ICO_PATH = ICON_ROOT / "windows" / "application.ico"
ICNS_PATH = ICON_ROOT / "macos" / "application.icns"
YELLOW = (255, 218, 68, 255)
BROWN = (121, 80, 43, 255)


def png_path(size: int) -> Path:
    return ICON_ROOT / "png" / f"application-{size}.png"


def output_paths() -> list[Path]:
    return [png_path(size) for size in PNG_SIZES] + [ICO_PATH, ICNS_PATH]


def placeholder(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    drawing = ImageDraw.Draw(image)
    margin = max(1, size // 8)
    border = max(1, size // 16)
    drawing.rectangle(
        (margin, margin, size - margin - 1, size - margin - 1),
        fill=YELLOW,
        outline=BROWN,
        width=border,
    )
    return image


def validate_image(image: Image.Image, size: int, check_placeholder: bool) -> None:
    image.load()
    if image.size != (size, size):
        raise ValueError(f"Expected {size}x{size}, got {image.size}")
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getbbox() is None:
        raise ValueError(f"Empty image at size {size}")
    if check_placeholder:
        margin = max(1, size // 8)
        if rgba.getpixel((size // 2, size // 2)) != YELLOW:
            raise ValueError(f"Missing yellow center at size {size}")
        if rgba.getpixel((margin, size // 2)) != BROWN:
            raise ValueError(f"Missing brown border at size {size}")
        if rgba.getpixel((0, 0))[3] != 0:
            raise ValueError(f"Missing transparent corner at size {size}")


def validate(check_placeholder: bool = False) -> None:
    with Image.open(MASTER_PATH) as master:
        validate_image(master, 1024, check_placeholder)
    for size in PNG_SIZES:
        with Image.open(png_path(size)) as image:
            if image.format != "PNG":
                raise ValueError(f"Not a PNG: {png_path(size)}")
            validate_image(image, size, check_placeholder)
    with Image.open(ICO_PATH) as icon:
        expected = {(size, size) for size in ICO_SIZES}
        if icon.format != "ICO" or icon.ico.sizes() != expected:
            raise ValueError("ICO frame sizes do not match the resource contract")
        for size in ICO_SIZES:
            validate_image(icon.ico.getimage((size, size)), size, check_placeholder)
    with Image.open(ICNS_PATH) as icon:
        if icon.format != "ICNS":
            raise ValueError("Not an ICNS file")
        expected_frames = {(points, points, scale) for points, scale in ICNS_FRAMES.values()}
        if set(icon.info["sizes"]) != expected_frames:
            raise ValueError("ICNS representations do not match the resource contract")
        for points, scale in ICNS_FRAMES.values():
            validate_image(
                icon.icns.getimage((points, points, scale)),
                points * scale,
                check_placeholder,
            )
    issues = list(IcnsFile.verify(str(ICNS_PATH)))
    if issues:
        raise ValueError("Invalid ICNS container: " + "; ".join(issues))
    print("PASS: master, 11 PNG sizes, 9 ICO frames, and 11 ICNS representations decode.")


def generate(create_placeholder: bool, overwrite_generated: bool) -> None:
    paths = output_paths()
    if create_placeholder:
        paths = [MASTER_PATH] + paths
    existing = [path for path in paths if path.exists()]
    if create_placeholder and existing:
        raise FileExistsError("Placeholder creation refuses to replace existing artwork.")
    if existing and not overwrite_generated:
        raise FileExistsError(
            "Generated icons already exist. Use --overwrite-generated to rebuild them "
            "from the master; this discards edits made directly to generated files."
        )
    if create_placeholder:
        master = placeholder(1024)
    else:
        with Image.open(MASTER_PATH) as source:
            validate_image(source, 1024, False)
            master = source.convert("RGBA")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    if create_placeholder:
        master.save(MASTER_PATH)
    for size in PNG_SIZES:
        image = (
            placeholder(size)
            if create_placeholder
            else master.resize((size, size), Image.Resampling.LANCZOS)
        )
        image.save(png_path(size))
    with Image.open(png_path(256)) as base:
        frames = []
        for size in ICO_SIZES:
            with Image.open(png_path(size)) as frame:
                frames.append(frame.convert("RGBA"))
        base.save(
            ICO_PATH,
            format="ICO",
            sizes=[(size, size) for size in ICO_SIZES],
            append_images=frames,
        )
    macos_icon = IcnsFile()
    for key, (points, scale) in ICNS_FRAMES.items():
        macos_icon.add_media(key, file=str(png_path(points * scale)))
    macos_icon.write(str(ICNS_PATH), toc=True)
    validate(check_placeholder=create_placeholder)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and validate application icon resources.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--create-placeholder", action="store_true")
    modes.add_argument("--check", action="store_true")
    parser.add_argument("--overwrite-generated", action="store_true")
    args = parser.parse_args()
    if args.overwrite_generated and (args.create_placeholder or args.check):
        parser.error("--overwrite-generated is only for rebuilding from the master")
    try:
        if args.check:
            validate()
        else:
            generate(args.create_placeholder, args.overwrite_generated)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Icon build failed: {error}\n")


if __name__ == "__main__":
    main()