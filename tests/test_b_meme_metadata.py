"""이미지 생성 호출 없이 분류 저장 계약과 실패 복구를 검증한다."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/b-meme/scripts/save_metadata.py'


def annotation():
    return {
        'emotions': ['RESIGNATION'],
        'keywords': ['돈없음', '체념'],
        'expense_categories': [],
        'subject': 'human',
        'panels': [{'expression': '담담한 표정', 'pose': '얼굴 클로즈업',
                    'captions': ['손에 구겨진 지폐 한장조차 없어요']}],
        'evidence': '담담한 얼굴과 돈이 없다는 자막',
        'uncertainties': [],
    }


def run_save(tmp_path, data, status='completed'):
    image = tmp_path / '001.png'
    image.write_bytes(b'image fixture')
    manifest = tmp_path / 'manifest.json'
    if not manifest.exists():
        manifest.write_text(json.dumps({'entries': [{
            'output_path': '001.png', 'status': status, 'prompt': 'keep me'
        }]}))
    annotations = tmp_path / 'annotations.json'
    annotations.write_text(json.dumps({'001.png': data}))
    return subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(manifest),
                           '--annotations', str(annotations)], capture_output=True, text=True)


def test_save_categories_and_repeat_without_losing_generation_record(tmp_path):
    result = run_save(tmp_path, annotation())
    assert result.returncode == 0, result.stderr
    sidecar = tmp_path / '001.metadata.json'
    first = json.loads(sidecar.read_text())
    assert first['classification']['emotions'] == ['RESIGNATION']
    assert first['classification']['expense_categories'] == []
    assert first['asset_path'] == '001.png'
    assert first['is_active'] is False
    assert first['tag'] is None
    assert len(first['asset_sha256']) == 64
    assert run_save(tmp_path, annotation()).returncode == 0
    saved = json.loads((tmp_path / 'manifest.json').read_text())['entries'][0]
    assert saved['prompt'] == 'keep me'
    assert saved['classification'] == first['classification']
    assert saved['metadata_path'] == '001.metadata.json'


@pytest.mark.parametrize('field,value', [
    ('emotions', ['SELF_MOCKERY']), ('expense_categories', ['FOOD']),
    ('keywords', ['a'] * 6), ('subject', 'celebrity'),
    ('panels', []), ('evidence', ''), ('uncertainties', 'none'),
])
def test_invalid_annotations_do_not_mutate_manifest(tmp_path, field, value):
    data = annotation()
    data[field] = value
    result = run_save(tmp_path, data)
    assert result.returncode == 1
    assert '메타데이터 저장 실패' in result.stderr
    assert not (tmp_path / '001.metadata.json').exists()
    saved = json.loads((tmp_path / 'manifest.json').read_text())['entries'][0]
    assert 'classification' not in saved


def test_uncertain_or_unclassified_results_require_review(tmp_path):
    data = annotation()
    data['emotions'] = []
    data['uncertainties'] = ['감정 판독 어려움']
    assert run_save(tmp_path, data, status='needs_review').returncode == 0
    saved = json.loads((tmp_path / '001.metadata.json').read_text())
    assert saved['needs_review'] is True


def test_missing_image_rejected_before_writes(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'entries': [{
        'output_path': 'missing/001.png', 'status': 'completed'
    }]}))
    result = run_save(tmp_path, annotation())
    assert result.returncode == 1
    assert '이미지 파일 없음' in result.stderr
    assert not (tmp_path / '001.metadata.json').exists()


def test_invalid_later_item_does_not_partially_save_valid_item(tmp_path):
    manifest = tmp_path / 'manifest.json'
    original = json.dumps({'entries': [
        {'output_path': '001.png', 'status': 'completed'},
        {'output_path': '002.png', 'status': 'completed'}]})
    manifest.write_text(original)
    for name in ['001.png', '002.png']:
        (tmp_path / name).write_bytes(b'image fixture')
    invalid = annotation()
    invalid['emotions'] = ['WRONG']
    annotations = tmp_path / 'annotations.json'
    annotations.write_text(json.dumps({'001.png': annotation(), '002.png': invalid}))
    result = subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(manifest),
                             '--annotations', str(annotations)], capture_output=True)
    assert result.returncode == 1
    assert manifest.read_text() == original
    assert not list(tmp_path.glob('*.metadata.json'))


def test_same_stem_different_extensions_cannot_overwrite_sidecar(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'entries': [
        {'output_path': '001.png', 'status': 'completed'},
        {'output_path': '001.jpg', 'status': 'completed'}]}))
    for name in ['001.png', '001.jpg']:
        (tmp_path / name).write_bytes(b'image fixture')
    annotations = tmp_path / 'annotations.json'
    annotations.write_text(json.dumps({'001.png': annotation(), '001.jpg': annotation()}))
    result = subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(manifest),
                             '--annotations', str(annotations)], capture_output=True, text=True)
    assert result.returncode == 1
    assert '메타데이터 경로 중복' in result.stderr
    assert not list(tmp_path.glob('*.metadata.json'))
