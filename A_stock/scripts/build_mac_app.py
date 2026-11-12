"""Build a local .app launcher without installing or copying private runtime data."""
import plistlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def c_string(value):
    # Byte escapes are valid C literals even for quotes and non-ASCII paths.
    return '"' + "".join(f"\\{byte:03o}" for byte in str(value).encode("utf-8")) + '"'


def compile_launcher(output, root=ROOT, entry_mode="-m", entry="aquant.desktop"):
    root = Path(root)
    resources = root / "A-Quant.app/Contents/Resources"
    python = root / ".venv/bin/python"
    query = "import json,sys,sysconfig; from pathlib import Path; print(json.dumps(str(Path(sys.base_prefix)/'lib'/sysconfig.get_config_var('LDLIBRARY'))))"
    library = Path(json.loads(subprocess.check_output([str(python), "-c", query], text=True)))
    if not library.is_file():
        raise FileNotFoundError(f"Python shared library is missing: {library}")
    config = {"AQUANT_PROJECT": root, "AQUANT_PYTHON": python,
              "AQUANT_LIBPYTHON": library, "AQUANT_ICON": resources / "icon.png",
              "AQUANT_ENTRY_MODE": entry_mode, "AQUANT_ENTRY": entry}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Replace the executable atomically, including when the old app is open.
    with tempfile.TemporaryDirectory(prefix=".aquant-build-", dir=output.parent) as tmp:
        header = Path(tmp) / "launcher_config.h"
        header.write_text("".join(f"#define {key} {c_string(value)}\n" for key, value in config.items()))
        subprocess.run(["/usr/bin/clang", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-mmacosx-version-min=13.0", "-I", tmp,
                        str(ROOT / "scripts/mac_launcher.c"), "-o", str(Path(tmp) / "AQuant")], check=True)
        os.replace(Path(tmp) / "AQuant", output)


def build():
    app = ROOT / "A-Quant.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    executable = contents / "MacOS/AQuant"
    executable.parent.mkdir(parents=True, exist_ok=True)
    compile_launcher(executable)
    resources.mkdir(parents=True, exist_ok=True)
    compile_statusbar(resources / "libAQuantMenuBar.dylib")
    plist = {"CFBundleName": "A-Quant", "CFBundleDisplayName": "A-Quant 量化助手",
                  "CFBundleExecutable": "AQuant", "CFBundlePackageType": "APPL",
                  "CFBundleIdentifier": "local.aquant.assistant", "CFBundleVersion": "0.4.0",
                  "CFBundleShortVersionString": "0.4.0", "NSHighResolutionCapable": True,
                  "CFBundleIconFile": "AppIcon", "LSMinimumSystemVersion": "13.0",
                  "LSUIElement": True}
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(plist, stream)
    # Updating an existing application reuses its icons; Pillow is only needed
    # when creating icons for the first build.
    if not all((resources / name).is_file() for name in ("icon.png", "AppIcon.icns")):
        write_icons(resources)
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    print(app)


def write_icons(resources):
    # A small code-drawn app mark; no remote image or generation dependency.
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, 984, 984), radius=210, fill="#17243B")
    draw.line([(230, 744), (494, 250), (758, 744)], fill="white", width=68, joint="curve")
    draw.line([(332, 560), (490, 560), (596, 452), (748, 452)], fill="#62DFC6", width=58, joint="curve")
    draw.ellipse((713, 417, 783, 487), fill="#62DFC6")
    image.save(resources / "icon.png")
    image.save(resources / "AppIcon.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])


def compile_statusbar(output, testing=False):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aquant-menu-", dir=output.parent) as tmp:
        binary = Path(tmp) / output.name
        subprocess.run(["/usr/bin/clang", "-dynamiclib", "-fobjc-arc", "-O2", "-Wall", "-Wextra", "-Werror",
                        *(["-DAQUANT_UI_TESTING"] if testing else []),
                        "-mmacosx-version-min=13.0", "-framework", "Cocoa",
                        str(ROOT / "scripts/mac_statusbar.m"), "-o", str(binary)], check=True)
        os.replace(binary, output)


if __name__ == "__main__":
    build()
