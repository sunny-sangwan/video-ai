from flask import Flask, request, send_file, render_template
import subprocess
import json
from PIL import Image, ImageDraw
import uuid
import os
import tempfile
import math

FONT_PATH = "C\\:/Windows/Fonts/Arial.ttf"
STROKE_WIDTH = 2

app = Flask(__name__, template_folder="src")


# -------------------------------
# Helpers
# -------------------------------

def ffmpeg_escape_text(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace(",", "\\,")
            .replace("\n", " ")
    )


def enable_expr(start, duration=1.0):
    return f"enable='between(t,{start},{start + duration})'"

def compute_font_size(h, size: str) -> int:
    scale = {
        "small": 0.25,
        "medium": 0.45,
        "large": 0.7
    }.get(size, 0.45)

    fs = int(h * scale)

    # Absolute clamps (CRITICAL)
    return max(12, min(fs, 64))


# -------------------------------
# Draw → PNG
# -------------------------------

def render_draw_png(strokes, w, h):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for stroke in strokes:
        if len(stroke) < 2:
            continue
        pts = [(int(p["x"] * w), int(p["y"] * h)) for p in stroke]
        draw.line(pts, fill=(255, 0, 0, 255), width=3)

    path = os.path.join(tempfile.gettempdir(), f"draw_{uuid.uuid4().hex}.png")
    img.save(path, "PNG")
    return path


# -------------------------------
# Filter builder (FIXED)
# -------------------------------

def build_filter_complex(annotations, video_width, video_height):
    """Builds a complex filter string for ffmpeg."""
    overlay_images = []
    filter_parts = []
    stream_label = "[0:v]"
    out_index = 0

    for i, ann in enumerate(annotations):
        input_stream = stream_label
        next_label = f"[v{out_index}]"

        try:
            timestamp = float(ann.get('timestamp', 0))
        except (ValueError, TypeError):
            continue

        x = float(ann.get('x', 0)) * video_width
        y = float(ann.get('y', 0)) * video_height
        w = float(ann.get('width', 0)) * video_width
        h = float(ann.get('height', 0)) * video_height
        enable_filter = f"enable='between(t,{timestamp},{timestamp + 1})'"

        if ann['type'] == 'text':
            text = ann.get('text', '').replace("'", "\\'\\''")
            font_size = h * 0.6
            filter_parts.append(
                f"{input_stream}drawtext="
                f"fontfile='{FONT_PATH}':"
                f"text='{text}':"
                f"x={x}:y={y}:"
                f"fontsize={font_size}:"
                f"fontcolor=white:"
                f"box=1:boxcolor=black@0.7:boxborderw=10:"
                f"{enable_filter}{next_label}"
            )
            stream_label = next_label
            out_index += 1

        elif ann['type'] == 'patch':
            filter_parts.append(
                f"{input_stream}drawbox="
                f"x={x}:y={y}:w={w}:h={h}:"
                f"color=red@0.5:t=fill:"
                f"{enable_filter}{next_label}"
            )
            stream_label = next_label
            out_index += 1

        # elif ann['type'] == 'circle':
        #     filter_parts.append(
        #         f"{input_stream}drawellipse="
        #         f"x={x}:y={y}:w={w}:h={h}:"
        #         f"color=red:t=4:"
        #         f"{enable_filter}{next_label}"
        #     )
        #     stream_label = next_label
        #     out_index += 1
        elif ann["type"] == "circle":
            png_path = render_circle_annotation_png(ann, video_width, video_height)
            overlay_images.append(png_path)

            input_index = len(overlay_images)
            filter_parts.append(
                f"{stream_label}[{input_index}:v]overlay=0:0:"
                f"{enable_filter}{next_label}"
            )

            stream_label = next_label
            out_index += 1

        elif ann["type"] == "polygon":
            png_path = render_polygon_annotation_png(
                ann, video_width, video_height
            )
            overlay_images.append(png_path)

            input_index = len(overlay_images)
            filter_parts.append(
                f"{stream_label}[{input_index}:v]overlay=0:0:"
                f"{enable_filter}{next_label}"
            )

            stream_label = next_label
            out_index += 1

        elif ann["type"] == "arrow":
            png_path = render_arrow_annotation_png(
                ann, video_width, video_height
            )
            overlay_images.append(png_path)

            input_index = len(overlay_images)
            filter_parts.append(
                f"{stream_label}[{input_index}:v]overlay=0:0:"
                f"{enable_filter}{next_label}"
            )

            stream_label = next_label
            out_index += 1



        elif ann['type'] == 'scalometer':
            text = ann.get('text', '').replace("'", "\\'\\''")
            rating = float(ann.get('rating', 5))

            # Dimensions
            bar_x = x + 0.1 * w
            bar_y = y + 0.65 * h
            bar_w = 0.8 * w
            bar_h = max(5, int(0.05 * h))
            title_fontsize = max(10, int(0.25 * h))
            num_fontsize = max(8, int(0.15 * h))
            indicator_size = max(10, int(0.15 * h))

            # Background bar
            filter_parts.append(
                f"{stream_label}drawbox="
                f"x={bar_x}:y={bar_y}:w={bar_w}:h={bar_h}:"
                f"color=gray@0.95:t=fill:{enable_filter}{next_label}"
            )
            stream_label = next_label
            out_index += 1

            # Title
            filter_parts.append(
                f"{stream_label}drawtext="
                f"text='{text}':"
                f"x={x + 5}:y={y + 5}:"
                f"fontsize={title_fontsize}:"
                f"fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=5:"
                f"{enable_filter}{next_label}"
            )
            stream_label = next_label
            out_index += 1

            # Numbers along the scale
            for j in range(0, 11, 2):
                num_x = bar_x + (j / 10) * bar_w - (num_fontsize / 4)
                num_y = bar_y + bar_h + 4
                filter_parts.append(
                    f"{stream_label}drawtext="
                    f"text='{j}':"
                    f"x={num_x}:y={num_y}:"
                    f"fontsize={num_fontsize}:"
                    f"fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=3:{enable_filter}{next_label}"
                )
                stream_label = next_label
                out_index += 1

            # Rating indicator
            indicator_x = bar_x + (rating / 10.0) * bar_w - indicator_size/2
            indicator_y = bar_y + bar_h/2 - indicator_size/2
            filter_parts.append(
                f"{stream_label}drawbox="
                f"x={indicator_x}:y={indicator_y}:w={indicator_size}:h={indicator_size}:"
                f"color=red@0.9:t=fill:{enable_filter}{next_label}"
            )
            stream_label = next_label
            out_index += 1

        # elif ann["type"] == "draw":
        #     for stroke in ann.get("drawing", []):
        #         if len(stroke) < 2:
        #             continue
        #         png_path = render_draw_annotation_png({"drawing": [stroke]}, video_width, video_height)
        #         overlay_images.append(png_path)
        #         input_index = len(overlay_images)
        #         filter_parts.append(
        #             f"{stream_label}[{input_index}:v]overlay=0:0:{enable_filter}{next_label}"
        #         )
        #         stream_label = next_label
        #         out_index += 1

    if not filter_parts:
        return None, None, None

    return ";".join(filter_parts), stream_label, overlay_images


def render_circle_annotation_png(ann, video_width, video_height):
    """
    Renders a circle annotation as a transparent PNG.
    """
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = ann['x'] * video_width
    y = ann['y'] * video_height
    w = ann['width'] * video_width
    h = ann['height'] * video_height

    bbox = [
        int(x),
        int(y),
        int(x + w),
        int(y + h)
    ]

    # Red outline only, no fill
    draw.ellipse(
        bbox,
        outline=(255, 0, 0, 255),
        width=3
    )

    path = os.path.join(
        tempfile.gettempdir(),
        f"circle_{uuid.uuid4().hex}.png"
    )
    img.save(path, "PNG")
    return path

def render_polygon_annotation_png(ann, video_width, video_height):
    """
    Renders a rectangular polygon (quadrilateral) as a transparent PNG.
    """
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = ann['x'] * video_width
    y = ann['y'] * video_height
    w = ann['width'] * video_width
    h = ann['height'] * video_height

    # Rectangle corners (quadrilateral)
    points = [
        (int(x), int(y)),
        (int(x + w), int(y)),
        (int(x + w), int(y + h)),
        (int(x), int(y + h)),
        (int(x), int(y))  # close path
    ]

    draw.line(
        points,
        fill=(255, 0, 0, 255),
        width=3  # SAME thickness as circle
    )

    path = os.path.join(
        tempfile.gettempdir(),
        f"polygon_{uuid.uuid4().hex}.png"
    )
    img.save(path, "PNG")
    return path


def render_arrow_annotation_png(ann, video_width, video_height):
    """
    Renders a horizontal arrow with optional flip (ltr / rtl).
    Height controls thickness only.
    """
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Bounding box (same schema as polygon/circle)
    x = ann["x"] * video_width
    y = ann["y"] * video_height
    w = ann["width"] * video_width
    h = ann["height"] * video_height

    direction = ann.get("direction", "ltr")

    center_y = y + h / 2

    # Horizontal arrow only
    if direction == "rtl":
        x1 = x + w
        x2 = x
    else:  # ltr
        x1 = x
        x2 = x + w

    y1 = y2 = center_y

    # ---- main shaft ----
    draw.line(
        [(x1, y1), (x2, y2)],
        fill=(255, 0, 0, 255),
        width=STROKE_WIDTH
    )

    # ---- arrowhead (small, non-clumsy) ----
    head_len = min(14, w * 0.10)
    head_half_height = h * 0.12

    if direction == "rtl":
        head = [
            (x2, y2),
            (x2 + head_len, y2 - head_half_height),
            (x2 + head_len, y2 + head_half_height),
        ]
    else:  # ltr
        head = [
            (x2, y2),
            (x2 - head_len, y2 - head_half_height),
            (x2 - head_len, y2 + head_half_height),
        ]

    draw.polygon(head, fill=(255, 0, 0, 255))

    path = os.path.join(
        tempfile.gettempdir(),
        f"arrow_{uuid.uuid4().hex}.png"
    )
    img.save(path, "PNG")
    return path


# -------------------------------
# Routes
# -------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    annotations = json.loads(request.form["annotations"])

    temp = tempfile.gettempdir()
    in_path = os.path.join(temp, f"{uuid.uuid4().hex}.mp4")
    out_path = os.path.join(temp, f"{uuid.uuid4().hex}.mp4")

    request.files["video"].save(in_path)

    probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "json", in_path],
        capture_output=True, text=True
    )
    info = json.loads(probe.stdout)
    vw = info["streams"][0]["width"]
    vh = info["streams"][0]["height"]

    graph, last, overlays = build_filter_complex(annotations, vw, vh)
    if not graph:
        return send_file(
            in_path,
            as_attachment=True,
            download_name="telestrated_video.mp4",
            mimetype="video/mp4"
        )

    cmd = ["ffmpeg", "-y", "-i", in_path]
    for o in overlays:
        cmd += ["-i", o]

    cmd += [
        "-filter_complex", graph,
        "-map", last,
        "-map", "0:a?",
        "-c:a", "copy",
        out_path
    ]

    subprocess.run(cmd, check=True)

    response = send_file(
        out_path,
        as_attachment=True,
        download_name="telestrated_video.mp4",
        mimetype="video/mp4",
        conditional=False
    )

    def cleanup():
        for f in overlays:
            try:
                os.remove(f)
            except Exception:
                pass
        for f in (in_path, out_path):
            try:
                os.remove(f)
            except Exception:
                pass

    response.call_on_close(cleanup)
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False)
