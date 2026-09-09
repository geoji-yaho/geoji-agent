import os
import glob
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

def extract_line_art(img):
    """
    Extract bold black-and-white comic line art from a photo.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Bilateral filter to smooth skin/background while keeping crisp facial edges
    smoothed = cv2.bilateralFilter(gray, d=9, sigmaColor=75, sigmaSpace=75)
    
    # Adaptive thresholding to convert to line drawing
    line_art = cv2.adaptiveThreshold(
        smoothed,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=13,
        C=4
    )
    
    # Make strokes bolder using a 2x2 erosion kernel
    kernel = np.ones((2, 2), np.uint8)
    bold_lines = cv2.erode(line_art, kernel, iterations=1)
    
    # Convert back to 3-channel RGB image
    return cv2.cvtColor(bold_lines, cv2.COLOR_GRAY2RGB)

def add_subtitle_box(pil_img, subtitle_text):
    """
    Draw a rounded gray box with crisp subtitle text at the bottom.
    """
    if not subtitle_text:
        return pil_img

    draw = ImageDraw.Draw(pil_img, "RGBA")
    width, height = pil_img.size
    
    box_w = int(width * 0.65)
    box_h = int(height * 0.16)
    box_x0 = (width - box_w) // 2
    box_y0 = int(height * 0.75)
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h
    
    box_color = (210, 210, 210, 230)
    border_color = (120, 120, 120, 255)
    draw.rounded_rectangle([box_x0, box_y0, box_x1, box_y1], radius=15, fill=box_color, outline=border_color, width=2)
    
    font_size = max(16, int(box_h * 0.45))
    font = None
    possible_fonts = [
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/Library/Fonts/Arial.ttf"
    ]
    for font_path in possible_fonts:
        if os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    text_draw = ImageDraw.Draw(pil_img)
    left, top, right, bottom = text_draw.textbbox((0, 0), subtitle_text, font=font)
    text_w = right - left
    text_h = bottom - top
    text_x = box_x0 + (box_w - text_w) // 2
    text_y = box_y0 + (box_h - text_h) // 2 - 2

    outline_color = (0, 0, 0)
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
        text_draw.text((text_x + dx, text_y + dy), subtitle_text, font=font, fill=outline_color)
    text_draw.text((text_x, text_y), subtitle_text, font=font, fill=(255, 255, 255))
    
    return pil_img

def convert_photo_to_line_art_meme(image_path, output_path, subtitle_text=None):
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image at {image_path}")

    line_art_rgb = extract_line_art(img)
    pil_img = Image.fromarray(line_art_rgb)
    
    if subtitle_text:
        pil_img = add_subtitle_box(pil_img, subtitle_text)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pil_img.save(output_path)
    return output_path

def process_all_line_art(input_dir, output_dir, subtitle_text=None):
    os.makedirs(output_dir, exist_ok=True)
    extensions = ('*.jpg', '*.jpeg', '*.png', '*.webp')
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(input_dir, ext)))
    
    results = []
    for fpath in sorted(files):
        fname = os.path.basename(fpath)
        out_path = os.path.join(output_dir, fname)
        res = convert_photo_to_line_art_meme(fpath, out_path, subtitle_text=subtitle_text)
        results.append(res)
    return results

if __name__ == '__main__':
    inp = '/Users/hyun/dev/geoji/inputs'
    out = '/Users/hyun/dev/geoji/outputs/line_art'
    results = process_all_line_art(inp, out, subtitle_text=None)
    print(f"Successfully processed {len(results)} images to pure line art in {out}.")
