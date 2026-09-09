"""
CLI Interface for Meme Doodle Agent.
Supports single image processing and inputs/ directory batch processing.
"""
import os
import argparse
import sys
import json
from pathlib import Path
from meme_doodle_agent.agent import MemeDoodleAgent
from meme_doodle_agent.models import MemeConversionRequest


def process_batch(input_dir: str, output_dir: str, agent: MemeDoodleAgent, json_output: bool = False):
    """Processes all images in input_dir and saves results to output_dir."""
    if not os.path.exists(input_dir):
        print(f"❌ Input directory '{input_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    supported_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    image_files = [
        f for f in os.listdir(input_dir)
        if Path(f).suffix.lower() in supported_exts
    ]

    if not image_files:
        print(f"⚠️ No image files found in '{input_dir}'", file=sys.stderr)
        return

    results = []
    print(f"🚀 Processing {len(image_files)} images from '{input_dir}' -> '{output_dir}'...")

    for img_file in image_files:
        in_path = os.path.join(input_dir, img_file)
        out_path = os.path.join(output_dir, f"{Path(img_file).stem}_doodle.png")
        
        req = MemeConversionRequest(image_path=in_path, output_path=out_path)
        res = agent.process_request(req)
        results.append(res.model_dump())

        if res.success:
            print(f"  ✅ [SUCCESS] {img_file} -> {out_path} ({res.category})")
        else:
            print(f"  ❌ [FAILED] {img_file}: {res.error_message}")

    if json_output:
        print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Meme Doodle Agent CLI")
    parser.add_argument("image_path", nargs="?", help="Path to input meme image or directory", default="inputs")
    parser.add_argument("-o", "--output", help="Path or directory for output doodle images", default="outputs")
    parser.add_argument("-s", "--subtitles", nargs="*", help="Optional custom subtitles override", default=None)
    parser.add_argument("--batch", action="store_true", help="Batch process all images in inputs/ directory")
    parser.add_argument("--json", action="store_true", help="Output result as JSON for agent integration")

    args = parser.parse_args()
    agent = MemeDoodleAgent(output_dir=args.output)

    # Check if image_path is a directory or if --batch flag is set
    if args.batch or (args.image_path and os.path.isdir(args.image_path)):
        input_dir = args.image_path if os.path.isdir(args.image_path) else "inputs"
        process_batch(input_dir, args.output, agent, json_output=args.json)
        return

    request = MemeConversionRequest(
        image_path=args.image_path,
        output_path=args.output if args.output and not os.path.isdir(args.output) else None,
        custom_subtitles=args.subtitles
    )

    result = agent.process_request(request)

    if args.json:
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    else:
        if result.success:
            print("==========================================")
            print("🎨 Meme Doodle Agent Conversion Success!")
            print(f"📁 Output Path: {result.output_path}")
            print(f"🏷️ Category: {result.category}")
            print(f"🏷️ Tags: {', '.join(result.tags)}")
            print("------------------------------------------")
            print(f"📝 Prompt:\n{result.generated_prompt}")
            print("==========================================")
        else:
            print(f"❌ Error: {result.error_message}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
