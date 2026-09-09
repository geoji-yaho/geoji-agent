# Meme Doodle Agent (B급 손그림 밈 변환 에이전트)

임의의 밈/이미지(1단~2단 컷)를 입력받아 **'B급 투박한 손그림(MS Paint doodle style) 짤방 캐릭터 일러스트'**로 자동 변환·생성해 주는 에이전트 패키지입니다.

Linux/macOS 크로스플랫폼 환경 지원 및 다른 AI 에이전트와의 호환성을 고려하여 설계되었습니다.

---

## 🚀 직접 테스트해보기 (Quick Start)

### 1. 환경 설정 및 의존성 설치
```bash
pip install -r requirements.txt
```

### 2. 단 한 줄로 직접 변환 테스트 실행
```bash
# 기본 테스트 실행
PYTHONPATH=. python main.py sample_meme.jpg -o output_doodle.png

# 원하는 자막 직접 지정해서 변환하기
PYTHONPATH=. python -m meme_doodle_agent.cli input.png -o output.png -s "행복은 돈으로 살수 없어" "ㅅㅂ 돈이 없으니까"
```

### 3. 다른 에이전트 연동용 JSON 출력 테스트
```bash
PYTHONPATH=. python -m meme_doodle_agent.cli input.png --json
```

### 4. 전체 단위 테스트(Pytest) 실행
```bash
PYTHONPATH=. pytest tests/
```

---

## 📦 모듈 구조 (Architecture)

- `meme_doodle_agent/models.py`: Pydantic 데이터 모델 (`MemeAnalysis`, `MemeConversionRequest`, `MemeConversionResult`)
- `meme_doodle_agent/prompt_builder.py`: B급 손그림 도돌이 밈 특화 프롬프트 생성기 (`CrudeDoodlePromptBuilder`)
- `meme_doodle_agent/analyzer.py`: 밈 메타데이터 분석 및 카테고리/태그 분류기 (`MemeCategorizer`, `MemeAnalyzer`)
- `meme_doodle_agent/agent.py`: 메인 에이전트 오케스트레이터 (`MemeDoodleAgent`)
- `meme_doodle_agent/cli.py`: CLI 명령어 및 멀티 에이전트 I/O 인터페이스
