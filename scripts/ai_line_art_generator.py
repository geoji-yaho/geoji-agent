import os
import glob
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def smooth_vector_line_art(img):
    """
    Generate clean, smooth, non-choppy black and white line art from an image.
    Uses Bilateral + Gaussian smoothing and Morphological Closing to connect broken strokes.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
    
    # 1. Edge-preserving smoothing to remove noise while keeping bold outlines
    smoothed = cv2.bilateralFilter(gray, d=9, sigmaColor=75, sigmaSpace=75)
    smoothed = cv2.GaussianBlur(smoothed, (3, 3), 0)
    
    # 2. Adaptive thresholding for clean black-and-white lines
    binary = cv2.adaptiveThreshold(
        smoothed,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=5
    )
    
    # 3. Morphological Closing to bridge broken line strokes and smooth jagged edges
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_close)
    
    # 4. Morphological Erosion to thicken and solidify continuous black lines
    kernel_erode = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    bold_lines = cv2.erode(closed, kernel_erode, iterations=1)
    
    # Convert to PIL Image and apply smooth anti-aliasing filter
    pil_lines = Image.fromarray(bold_lines).convert("RGB")
    return pil_lines

def render_crisp_subtitle(pil_img, subtitle_text, font_size=None):
    """
    Renders high-resolution crisp digital Korean text inside a rounded box so every letter is 100% legible.
    """
    if not subtitle_text:
        return pil_img

    width, height = pil_img.size
    overlay = Image.new("RGBA", pil_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    # Subtitle box dimensions
    box_w = int(width * 0.75)
    box_h = int(height * 0.16)
    box_x0 = (width - box_w) // 2
    box_y0 = int(height * 0.76)
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h

    # Rounded rectangle background (Semi-transparent gray box with dark border)
    box_color = (235, 235, 235, 240)
    border_color = (60, 60, 60, 255)
    draw.rounded_rectangle([box_x0, box_y0, box_x1, box_y1], radius=12, fill=box_color, outline=border_color, width=2)

    # Font setup
    if font_size is None:
        font_size = max(18, int(box_h * 0.42))

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

    # Draw centered crisp text with subtle outline for maximum legibility
    left, top, right, bottom = draw.textbbox((0, 0), subtitle_text, font=font)
    text_w = right - left
    text_h = bottom - top
    text_x = box_x0 + (box_w - text_w) // 2
    text_y = box_y0 + (box_h - text_h) // 2 - 2

    # Outline for extra sharpness
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx != 0 or dy != 0:
                draw.text((text_x + dx, text_y + dy), subtitle_text, font=font, fill=(0, 0, 0, 255))
    draw.text((text_x, text_y), subtitle_text, font=font, fill=(255, 255, 255, 255))

    # Composite overlay onto pil_img
    pil_rgb = pil_img.convert("RGBA")
    final_img = Image.alpha_composite(pil_rgb, overlay).convert("RGB")
    return final_img

def process_ai_smooth_line_art(image_path, output_path, subtitle_text=None):
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image at {image_path}")

    pil_lines = smooth_vector_line_art(img)
    final_img = render_crisp_subtitle(pil_lines, subtitle_text)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    final_img.save(output_path)
    return output_path

if __name__ == '__main__':
    inp = '/Users/hyun/dev/geoji/inputs/images-10.jpg'
    out = '/Users/hyun/dev/geoji/outputs/smooth_line_art_meme.png'
    process_ai_smooth_line_art(inp, out, subtitle_text="행복은 돈으로 살 수 없어.")
    print(f"Saved smooth line art meme with crisp subtitle to {out}")
