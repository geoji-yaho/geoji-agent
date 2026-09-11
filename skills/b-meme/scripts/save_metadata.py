"""Codex의 시각 분류를 검증해 이미지 옆 JSON과 생성 manifest에 저장한다.

표준 라이브러리만 사용한다. 이미지 해석은 호출한 Codex가 수행한다.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


TAXONOMY = json.loads(
    (Path(__file__).resolve().parents[1] / 'references/taxonomy.json').read_text()
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strings(value, field, allowed=None, limit=None):
    require(isinstance(value, list), f'{field}: 문자열 배열이어야 합니다')
    require(all(isinstance(v, str) and v.strip() for v in value),
            f'{field}: 빈 문자열이나 잘못된 항목')
    require(len(value) == len(set(value)), f'{field}: 중복 항목')
    if allowed is not None:
        require(all(v in allowed for v in value), f'{field}: 허용하지 않은 분류값')
    if limit is not None:
        require(len(value) <= limit, f'{field}: 최대 {limit}개')


def validate(data):
    fields = {'emotions', 'keywords', 'expense_categories', 'subject',
              'panels', 'evidence', 'uncertainties'}
    require(isinstance(data, dict) and set(data) == fields, '분류 필드 누락 또는 알 수 없는 필드')
    strings(data['emotions'], 'emotions', TAXONOMY['emotions'])
    strings(data['keywords'], 'keywords', limit=5)
    strings(data['expense_categories'], 'expense_categories', TAXONOMY['expense_categories'])
    strings(data['uncertainties'], 'uncertainties')
    require(data['subject'] in TAXONOMY['subjects'], 'subject: 허용하지 않은 대상')
    require(isinstance(data['evidence'], str) and data['evidence'].strip(), 'evidence: 근거 필수')
    require(isinstance(data['panels'], list) and data['panels'], 'panels: 최소 한 컷 필요')
    for panel in data['panels']:
        require(isinstance(panel, dict) and set(panel) == {'expression', 'pose', 'captions'},
                'panels: expression, pose, captions 필요')
        for field in ('expression', 'pose'):
            require(isinstance(panel[field], str) and panel[field].strip(), f'panels: {field} 필수')
        # 같은 자막이 한 컷에서 반복되는 것은 허용한다.
        require(isinstance(panel['captions'], list) and
                all(isinstance(v, str) and v.strip() for v in panel['captions']),
                'panels: captions는 비어 있거나 문자열 배열이어야 합니다')


def atomic_json(path, data):
    """중단으로 기존 JSON이 잘리지 않도록 같은 디렉터리에서 교체한다."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def save(manifest_path, annotations_path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    annotations = json.loads(annotations_path.read_text(encoding='utf-8'))
    require(isinstance(manifest, dict) and isinstance(manifest.get('entries'), list),
            'manifest.entries 배열 필요')
    require(isinstance(annotations, dict) and annotations, '분류할 annotations 객체 필요')
    outputs = {}
    sidecar_paths = set()
    for entry in manifest['entries']:
        require(isinstance(entry, dict), 'manifest 항목은 객체여야 합니다')
        if not entry.get('output_path'):
            continue
        path = Path(entry['output_path'])
        if not path.is_absolute():
            path = manifest_path.parent / path
        require(path.name not in outputs, '중복 출력 파일명: 고유 이름으로 저장하세요')
        sidecar_path = path.with_suffix('.metadata.json').resolve()
        require(sidecar_path not in sidecar_paths, '메타데이터 경로 중복: 이미지 stem을 고유하게 지정하세요')
        sidecar_paths.add(sidecar_path)
        outputs[path.name] = (entry, path)

    writes = []
    for name, classification in annotations.items():
        require(name in outputs, f'manifest에 없는 출력: {name}')
        entry, asset = outputs[name]
        require(asset.is_file(), f'이미지 파일 없음: {asset}')
        require(entry.get('status') in ('completed', 'needs_review'), '생성 결과 검수 후 분류하세요')
        validate(classification)
        sidecar = asset.with_suffix('.metadata.json')
        require(sidecar.resolve() not in (manifest_path.resolve(), annotations_path.resolve()),
                '메타데이터 출력과 입력 JSON 경로 충돌')
        needs_review = (entry['status'] != 'completed' or not classification['emotions'] or
                        bool(classification['uncertainties']))
        metadata = {
            'schema_version': 1,
            'taxonomy_version': TAXONOMY['version'],
            'asset_path': asset.name,
            'asset_sha256': hashlib.sha256(asset.read_bytes()).hexdigest(),
            'classification': classification,
            'generated_by': 'b-meme/codex-visual-v1',
            'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'needs_review': needs_review,
            # 이미지 분위기로 유무죄/전략을 확정하지 않는다. 등록 단계의 별도 결정이다.
            'tag': None,
            'strategies': [],
            'is_active': False,
        }
        writes.append((sidecar, metadata))
        entry['classification'] = classification
        entry['metadata_path'] = os.path.relpath(sidecar, manifest_path.parent)
        entry['classification_needs_review'] = needs_review

    # 모든 항목 검증 후 쓰기 시작. 중간 I/O 실패 시 같은 명령으로 재실행 가능하다.
    for path, metadata in writes:
        atomic_json(path, metadata)
    atomic_json(manifest_path, manifest)
    return len(writes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--annotations', type=Path, required=True)
    args = parser.parse_args()
    try:
        count = save(args.manifest.resolve(), args.annotations.resolve())
    except (OSError, ValueError, TypeError) as error:
        print(f'메타데이터 저장 실패: {error}', file=sys.stderr)
        return 1
    print(f'분류 저장 완료: {count}장')
    return 0


if __name__ == '__main__':
    sys.exit(main())
