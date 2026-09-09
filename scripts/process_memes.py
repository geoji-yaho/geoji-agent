import os
import glob
import cv2
import numpy as np

def convert_to_colored_pencil(image_path, output_path):
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image at {image_path}")

    height, width = img.shape[:2]

    # Apply colored pencil sketch filter
    dst_gray, dst_color = cv2.pencilSketch(
        img, 
        sigma_s=50, 
        sigma_r=0.07, 
        shade_factor=0.04
    )

    # Enhance saturation and warmth to emphasize hand-drawn colored pencil feel
    hsv = cv2.cvtColor(dst_color, cv2.COLOR_BGR2HSV)
    hsv[:, :, 1] = cv2.multiply(hsv[:, :, 1], 1.2)  # boost saturation
    colored_pencil_img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    # Subtitle preservation: Detect bottom subtitle region or overlay sharp original text
    # In meme images, subtitles are typically located in the bottom 25% region or bounded boxes.
    # We blend the original subtitle area with alpha mask to preserve crisp legibility.
    
    # Bottom ~22% subtitle zone
    sub_start_y = int(height * 0.75)
    
    # Create smooth blend mask for bottom subtitle area
    mask = np.zeros((height, width), dtype=np.float32)
    mask[sub_start_y:, :] = 1.0
    
    # Blur transition mask edge for natural look
    mask = cv2.GaussianBlur(mask, (15, 15), 0)
    mask_3d = np.repeat(mask[:, :, np.newaxis], 3, axis=2)

    # Blend: sharp original subtitles on top of colored pencil sketch background
    final_img = (img * mask_3d + colored_pencil_img * (1.0 - mask_3d)).astype(np.uint8)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv2.imwrite(output_path, final_img)
    return output_path

def process_all_inputs(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    extensions = ('*.jpg', '*.jpeg', '*.png', '*.webp')
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(input_dir, ext)))
    
    results = []
    for fpath in sorted(files):
        fname = os.path.basename(fpath)
        out_path = os.path.join(output_dir, fname)
        res = convert_to_colored_pencil(fpath, out_path)
        results.append(res)
    return results

if __name__ == '__main__':
    inp = '/Users/hyun/dev/geoji/inputs'
    out = '/Users/hyun/dev/geoji/outputs'
    processed = process_all_inputs(inp, out)
    print(f"Processed {len(processed)} images.")
