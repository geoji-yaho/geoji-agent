import os
import sys
from fetch_memes import download_meme_samples
from meme_line_extractor import MemeLineExtractor


def run():
    print("=" * 60)
    print("🚀 거지방 밈짤 선화(Line Art) 자동 변환 파이프라인 시작")
    print("=" * 60)

    input_dir = "inputs"
    output_dir = "outputs"

    # Step 1: Download meme samples if inputs folder is empty
    if not os.path.exists(input_dir) or len(os.listdir(input_dir)) == 0:
        print("\n[Step 1] 샘플 밈짤 수집 (다운로드) 진행 중...")
        downloaded = download_meme_samples(output_dir=input_dir)
        print(f"✅ 총 {len(downloaded)}개의 샘플 밈짤 다운로드 완료!")
    else:
        print(f"\n[Step 1] '{input_dir}' 폴더 내 기존 이미지를 활용합니다.")

    # Step 2: Extract line art
    print("\n[Step 2] AI 배경 제거 및 투명 배경 선화(PNG) 추출 진행 중...")
    extractor = MemeLineExtractor()
    processed_count = extractor.process_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        remove_bg=True,
        thickness=1,
    )

    print("\n" + "=" * 60)
    print(f"🎉 변환 완료! 총 {processed_count}개의 밈짤이 투명 PNG 선화로 변환되었습니다.")
    print(f"📁 결과물 저장 위치: {os.path.abspath(output_dir)}")
    print("=" * 60)


if __name__ == "__main__":
    run()
