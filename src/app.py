from flask import Flask, request, send_file, render_template
import subprocess
import json
import os
import uuid
from PIL import Image, ImageDraw
import tempfile

app = Flask(__name__)

def render_draw_path(path, video_width, video_height):
    """Render a single path as a transparent PNG."""
    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if len(path) >= 2:
        points = [(int(p["x"] * video_width), int(p["y"] * video_height)) for p in path]
        draw.line(points, fill=(255, 0, 0, 255), width=3)

    temp_dir = tempfile.gettempdir()
    out_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.png")
    img.save(out_path)
    return out_path

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_video():
    temp_files = []
    video_file = request.files['video']
    annotations = json.loads(request.form['annotations'])

    temp_dir = tempfile.gettempdir()
    input_filename = os.path.join(temp_dir, f"{uuid.uuid4().hex}.mp4")
    output_filename = os.path.join(temp_dir, f"{uuid.uuid4().hex}.mp4")
    temp_files.extend([input_filename, output_filename])
    video_file.save(input_filename)

    # Get video dimensions
    try:
        video_info_cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of', 'json', input_filename
        ]
        result = subprocess.run(video_info_cmd, capture_output=True, text=True, check=True)
        video_info = json.loads(result.stdout)['streams'][0]
        video_width = video_info['width']
        video_height = video_info['height']
    except Exception as e:
        print(f"Error getting video info: {e}")
        video_width = 1280
        video_height = 720

    filter_chains = []

    # Separate draw annotations
    draw_annotations = [a for a in annotations if a['type'] == 'draw']
    other_annotations = [a for a in annotations if a['type'] != 'draw']

    # Non-draw annotations
    for a in other_annotations:
        x = a['x'] * video_width
        y = a['y'] * video_height
        w = a['width'] * video_width
        h = a['height'] * video_height
        start_time = a['timestamp']
        end_time = start_time + 1
        enable_str = f"enable='between(t,{start_time},{end_time})'"
        text = a.get('text', '').replace("'", "\\'")

        if a['type'] == 'text':
            size_map = {'small': 20, 'medium': 40, 'large': 60}
            font_size = size_map.get(a.get('fontSizeName'), 40)
            filter_chains.append(
                f"drawtext=text='{text}':x={x}:y={y}:fontsize={font_size}:"
                f"fontcolor=white:box=1:boxcolor=black@0.5:{enable_str}"
            )
        elif a['type'] == 'arrow':
            font_size = h * 0.8
            filter_chains.append(
                f"drawtext=text='{text}':x={x}:y={y}:fontsize={font_size}:"
                f"fontcolor=white:{enable_str}"
            )
        elif a['type'] == 'patch':
            filter_chains.append(
                f"drawbox=x={x}:y={y}:w={w}:h={h}:color=red@0.5:t=fill:{enable_str}"
            )
        elif a['type'] == 'scalometer':
            rating = float(a.get('rating', 5))
            filter_chains.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black@0.7:t=fill:{enable_str}")
            title_fontsize = h * 0.25
            title_x_pos = x + w*0.05
            title_y_pos = y + h*0.1
            filter_chains.append(
                f"drawtext=text='{text}':x={title_x_pos}:y={title_y_pos}:fontsize={title_fontsize}:fontcolor=white:{enable_str}"
            )
            bar_y = y + h * 0.6
            bar_x = x + w * 0.1
            bar_width = w * 0.8
            filter_chains.append(f"drawbox=x={bar_x}:y={bar_y}:w={bar_width}:h=2:color=gray:t=fill:{enable_str}")
            num_fontsize = h * 0.18
            num_y = bar_y + h * 0.1
            for j in range(0, 11, 2):
                num_x_offset = (j / 10.0) * bar_width
                num_x = bar_x + num_x_offset - (num_fontsize / (4 if j > 0 else 2))
                filter_chains.append(
                    f"drawtext=text='{j}':x={num_x}:y={num_y}:fontsize={num_fontsize}:fontcolor=white:{enable_str}"
                )
            indicator_x = bar_x + (rating / 10.0) * bar_width
            indicator_size = h * 0.15
            filter_chains.append(
                f"drawbox=x={indicator_x - indicator_size/2}:y={bar_y - indicator_size/2}:w={indicator_size}:h={indicator_size}:color=yellow:t=fill:{enable_str}"
            )

    # Process each draw path individually
    video_label = "[0:v]"
    for i, a in enumerate(draw_annotations):
        start_time = a['timestamp']
        end_time = start_time + 1
        for j, path in enumerate(a["drawing"]):
            png_path = render_draw_path(path, video_width, video_height)
            temp_files.append(png_path)
            overlay_label = f"[v_draw_{i}_{j}]"
            filter_chains.append(
                f"movie={png_path}[draw_{i}_{j}];{video_label}[draw_{i}_{j}]overlay=0:0:enable='between(t,{start_time},{end_time})'{overlay_label}"
            )
            video_label = overlay_label

    final_filter = ";".join(filter_chains) if filter_chains else "null"

    ffmpeg_command = [
        'ffmpeg', '-i', input_filename,
        '-vf', final_filter,
        '-map', '0:a?', '-c:a', 'copy',
        '-y', output_filename
    ]

    print(f"Running ffmpeg command: {' '.join(ffmpeg_command)}")
    try:
        subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
        return send_file(
            output_filename,
            as_attachment=True,
            download_name='telestrated_video.mp4'
        )
    except subprocess.CalledProcessError as e:
        print("FFMPEG Error:", e.stderr)
        return "Error processing video.", 500
    finally:
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception as cleanup_error:
                print(f"Cleanup failed for {f}: {cleanup_error}")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
