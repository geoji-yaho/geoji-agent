"""
CLI Interface for Meme Doodle Agent.
Enables command-line testing and multi-agent invocation.
"""
import argparse
import sys
import json
from meme_doodle_agent.agent import MemeDoodleAgent
from meme_doodle_agent.models import MemeConversionRequest


def main():
    parser = argparse.ArgumentParser(description="Meme Doodle Agent CLI")
    parser.add_argument("image_path", help="Path to input meme image")
    parser.add_argument("-o", "--output", help="Path to output doodle image", default=None)
    parser.add_argument("-s", "--subtitles", nargs="*", help="Optional custom subtitles override", default=None)
    parser.add_argument("--json", action="store_true", help="Output result as JSON for agent integration")

    args = parser.parse_args()

    agent = MemeDoodleAgent()
    request = MemeConversionRequest(
        image_path=args.image_path,
        output_path=args.output,
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
