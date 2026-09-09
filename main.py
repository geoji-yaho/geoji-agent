"""
Quick Test Entrypoint for Meme Doodle Agent.
Usage: python main.py <image_path>
"""
import sys
from meme_doodle_agent.cli import main

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default test run
        sys.argv.extend(["sample_meme.jpg", "-o", "sample_doodle_output.png"])
    main()
