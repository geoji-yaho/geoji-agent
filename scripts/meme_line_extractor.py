import os
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
    
    # Make strokes bolder using a 2x2 erosion kernel (which darkens/thickens black lines)
    kernel = np.ones((2, 2), np.uint8)
    bold_lines = cv2.erode(line_art, kernel, iterations=1)
    
    # Convert back to 3-channel RGB image
    return cv2.cvtColor(bold_lines, cv2.COLOR_GRAY2RGB)

def add_subtitle_box(pil_img, subtitle_text="호박 고구마!!"):
    """
    Draw a rounded gray box with crisp subtitle text at the bottom.
    """
    draw = ImageDraw.Draw(pil_img, "RGBA")
    width, height = pil_img.size
    
    # Calculate box position
    box_w = int(width * 0.65)
    box_h = int(height * 0.16)
    box_x0 = (width - box_w) // 2
    box_y0 = int(height * 0.75)
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h
    
    # Draw rounded rectangle background (semi-transparent gray with dark border)
    box_color = (210, 210, 210, 230)
    border_color = (120, 120, 120, 255)
    draw.rounded_rectangle([box_x0, box_y0, box_x1, box_y1], radius=15, fill=box_color, outline=border_color, width=2)
    
    # Load font
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

    # Draw text centered in the subtitle box
    text_draw = ImageDraw.Draw(pil_img)
    left, top, right, bottom = text_draw.textbbox((0, 0), subtitle_text, font=font)
    text_w = right - left
    text_h = bottom - top
    text_x = box_x0 + (box_w - text_w) // 2
    text_y = box_y0 + (box_h - text_h) // 2 - 2

    # Draw white text with dark outline for high contrast
    outline_color = (0, 0, 0)
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
        text_draw.text((text_x + dx, text_y + dy), subtitle_text, font=font, fill=outline_color)
    text_draw.text((text_x, text_y), subtitle_text, font=font, fill=(255, 255, 255))
    
    return pil_img

def convert_photo_to_line_art_meme(image_path, output_path, subtitle_text="호박 고구마!!"):
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image at {image_path}")

    line_art_rgb = extract_line_art(img)
    pil_img = Image.fromarray(line_art_rgb)
    final_img = add_subtitle_box(pil_img, subtitle_text)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    final_img.save(output_path)
    return output_path

if __name__ == '__main__':
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else '/Users/hyun/dev/geoji/inputs/images-10.jpg'
    out = sys.argv[2] if len(sys.argv) > 2 else '/Users/hyun/dev/geoji/outputs/line_art_meme.png'
    convert_photo_to_line_art_meme(inp, out, "호박 고구마!!")
    print(f"Saved line art meme to {out}")
