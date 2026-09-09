import argparse
import sys
import os
from meme_line_extractor import MemeLineExtractor


def parse_args():
    parser = argparse.ArgumentParser(
        description="Meme Line Art Extractor Agent - Extract transparent line art PNGs from meme images"
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Input image file path or directory containing images"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Output PNG file path or output directory"
    )
    parser.add_argument(
        "--no-bg-remove",
        action="store_true",
        help="Disable AI background removal before extracting lines",
    )
    parser.add_argument(
        "--thickness", type=int, default=1, help="Line thickness level (default: 1)"
    )
    parser.add_argument(
        "--threshold1", type=int, default=50, help="Canny edge lower threshold (default: 50)"
    )
    parser.add_argument(
        "--threshold2", type=int, default=150, help="Canny edge upper threshold (default: 150)"
    )
    parser.add_argument(
        "--blur", type=int, default=3, help="Gaussian blur kernel size for noise reduction (default: 3)"
    )
    parser.add_argument(
        "--adaptive", action="store_true", help="Use adaptive thresholding instead of Canny"
    )
    parser.add_argument(
        "--fill-text",
        action="store_true",
        help="Legacy morphological closing of edges (off by default: it blobs small text)",
    )
    parser.add_argument(
        "--text-mode",
        choices=("auto", "ocr", "binarize", "none"),
        default="auto",
        help=(
            "How text is rendered. ocr: recognize with macOS Vision and redraw in a clean "
            "font (most legible). binarize: fill glyph bodies instead of tracing outlines. "
            "none: previous behaviour. auto: ocr when available (default)"
        ),
    )
    parser.add_argument(
        "--text-scale",
        type=int,
        default=3,
        help="Upscale factor used while binarizing text (default: 3)",
    )
    parser.add_argument(
        "--font", default=None, help="Font file used to redraw OCR text"
    )
    parser.add_argument(
        "--text-kernel",
        type=int,
        default=3,
        help="Kernel size for morphological text filling (default: 3)",
    )
    parser.add_argument(
        "--white-line", action="store_true", help="Draw lines in white instead of default black"
    )
    parser.add_argument(
        "--no-categories",
        action="store_true",
        help="Skip the suggested-category JSON sidecar written next to each PNG",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    line_color = (255, 255, 255, 255) if args.white_line else (0, 0, 0, 255)
    remove_bg = not args.no_bg_remove
    fill_text = args.fill_text
    write_categories = not args.no_categories

    extractor = MemeLineExtractor()

    if os.path.isdir(args.input):
        print(f"[Agent] Batch processing directory: {args.input}")
        count = extractor.process_directory(
            input_dir=args.input,
            output_dir=args.output,
            remove_bg=remove_bg,
            line_color=line_color,
            thickness=args.thickness,
            threshold1=args.threshold1,
            threshold2=args.threshold2,
            blur_size=args.blur,
            use_adaptive=args.adaptive,
            fill_text=fill_text,
            text_kernel_size=args.text_kernel,
            text_mode=args.text_mode,
            text_scale=args.text_scale,
            font_path=args.font,
            write_categories=write_categories,
        )
        print(f"[Agent] Completed! Successfully processed {count} images.")
    elif os.path.isfile(args.input):
        print(f"[Agent] Processing single image: {args.input}")
        out_path = extractor.process_file(
            input_path=args.input,
            output_path=args.output,
            remove_bg=remove_bg,
            line_color=line_color,
            thickness=args.thickness,
            threshold1=args.threshold1,
            threshold2=args.threshold2,
            blur_size=args.blur,
            use_adaptive=args.adaptive,
            fill_text=fill_text,
            text_kernel_size=args.text_kernel,
            text_mode=args.text_mode,
            text_scale=args.text_scale,
            font_path=args.font,
            write_categories=write_categories,
        )
        print(f"[Agent] Saved transparent line art PNG to: {out_path}")
    else:
        print(f"[Error] Input path '{args.input}' does not exist.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
