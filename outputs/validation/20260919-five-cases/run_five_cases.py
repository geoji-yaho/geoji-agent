"""요청한 다섯 사례를 현재 Spring + AI 그래프 + 독립 DB로 검증하는 일회성 실행기."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path('/Users/hyun/dev/geoji')
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from run_local_e2e import Stack, USERS, default_java_home, failure, write_json
from run_local_live_e2e import LiveStack

CASES = [
    dict(key='01-approved', label='구매 승인', intensity='mild', vote='agree', tag='APPROVED', item='업무용 키보드', amountKrw=59000, category='기타', reason='기존 키보드가 고장 나서 업무에 필요한 기본형으로 교체하려고 합니다.'),
    dict(key='02-rejected', label='구매 기각', intensity='spicy', vote='disagree', tag='REJECTED', item='한정판 운동화', amountKrw=289000, category='쇼핑/패션', reason='운동화가 이미 네 켤레 있지만 한정판이라 품절되기 전에 사고 싶어요.'),
    dict(key='03-mild', label='유죄 · 순한맛', intensity='mild', vote='guilty', tag='GUILTY_LIGHT', item='택시', amountKrw=12000, category='교통/택시', reason='알람을 끄고 다시 자서 지각할 것 같아 택시를 탔어요.'),
    dict(key='04-spicy', label='유죄 · 매운맛', intensity='spicy', vote='guilty', tag='GUILTY_LIGHT', item='치킨 배달', amountKrw=31000, category='배달', reason='냉장고에 먹을 게 있었지만 비 오는 날이라 치킨과 사이드를 배달시켰어요.'),
    dict(key='05-hell', label='유죄 · 지옥맛', intensity='hell', vote='guilty', tag='GUILTY_LIGHT', item='게임 스킨 묶음', amountKrw=99000, category='취미/여가', reason='실력은 그대로인데 한정 스킨을 끼면 이길 것 같아서 충동 결제했어요.'),
]

class FiveMixin:
    current_case = None

    async def request(self, method, path, *args, **kwargs):
        if self.current_case and method == 'GET' and path.startswith('/api/posts/') and path.endswith(('/verdict', '/share-card')):
            path += '?room_id=' + self.room_id
        if self.current_case and method == 'POST' and path.startswith('/api/post-submissions'):
            body = dict(kwargs['json'])
            for key in ('item', 'amountKrw', 'category', 'reason'):
                body[key] = self.current_case[key]
            kwargs['json'] = body
        return await super().request(method, path, *args, **kwargs)

    async def run(self):
        await self.boot()
        await self.setup_users()
        await self.seed_catalog()
        self.report.update(
            scope='실제 Spring + AI 그래프 + 격리 Postgres + 로컬 밈 파일 서버. 운영 S3/로그인 검증 제외.',
            mode='live' if self.live else 'fixture-preflight',
            test_cases=CASES,
            source_commits=json.loads((self.args.backend.parent/'sources.json').read_text()),
        )
        rooms_by_intensity = {'mild': self.room_id}
        for index, spec in enumerate(CASES):
            self.current_case = spec
            if spec['intensity'] not in rooms_by_intensity:
                room = (await self.request('POST', '/api/rooms', expected=201, json={
                    'name': '판결 검증 '+spec['label'], 'spiceLevel': spec['intensity'],
                    'voteDeadlineMinutes': 60, 'rules': [],
                })).json()
                self.room_id = room['id']
                for user in USERS[1:4]:
                    await self.request('POST', '/api/rooms/join/'+room['inviteCode'], user)
                rooms_by_intensity[spec['intensity']] = self.room_id
            self.room_id = rooms_by_intensity[spec['intensity']]
            print('CASE_START', spec['key'], spec['label'], flush=True)
            case = await self.case(spec['key'], vote=spec['vote'], tag=spec['tag'])
            case['input'] = spec
            case['room_id'] = self.room_id
            case['browser_url'] = f"http://localhost:3800/posts/{case['post_id']}/card?room={self.room_id}"
            row = await self.db.fetchrow('SELECT * FROM verdicts WHERE post_id=$1', UUID(case['post_id']))
            case['verdict_detail'] = {key: row[key] for key in (
                'jury_result','sentence','sentence_source','sentencing_reason','reason_source',
                'text_status','last_failed_code')}
            record_path = self.directory / f"finalize-{row['id']}.json"
            if record_path.is_file():
                record = json.loads(record_path.read_text())
                draft = record['request'].get('draft')
                case['finalize_draft'] = draft
                case['evaluation'] = record['request'].get('evaluation')
                case['finalize_status'] = record['status']
            if row['meme_image_id']:
                selected = dict(await self.db.fetchrow('SELECT * FROM meme_images WHERE id=$1', row['meme_image_id']))
                image = await self.client.get(selected['image_url'])
                self.check(spec['key']+' image GET 200', image.status_code == 200)
                target = self.args.output / (spec['key']+'-meme.png')
                target.write_bytes(image.content)
                case['selected_meme'] = selected
                case['image_sha256'] = hashlib.sha256(image.content).hexdigest()
                case['image_file'] = str(target)
            print('CASE_RESULT', spec['key'], case['verdict']['textStatus'], case['share_card'].get('headline'), flush=True)
            self.save_report()
        self.report['model_calls'] = [dict(row) for row in await self.db.fetch(
            "SELECT vendor,model_id,node,status,actual_micro_usd,prompt_tokens,completion_tokens,reasoning_tokens,"
            "extract(epoch FROM finished_at-started_at)*1000 AS latency_ms FROM ai.llm_calls ORDER BY started_at"
        )]
        self.report['result'] = 'PASS' if all(c['verdict']['textStatus']=='AI_READY' for c in self.report['cases']) else 'FALLBACK_OBSERVED'
        self.save_report()

    def save_report(self):
        write_json(self.directory/'report.json', self.report)
        write_json(self.args.output/'report.json', self.report)

class FakeFive(FiveMixin, Stack):
    pass
class LiveFive(FiveMixin, LiveStack):
    pass

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    args=parser.parse_args()
    args.backend=args.workspace/'server'
    args.frontend=args.workspace/'web'
    args.jar=None
    args.java_home=default_java_home()
    args.intensity='mild'
    args.keep=True
    args.baseline=True
    args.max_calls=60
    args.cap_usd=0.75
    args.db_port,args.backend_port,args.ai_port,args.issuer_port=55448,18084,18104,18209
    args.output.mkdir(parents=True,exist_ok=True)
    stack=(LiveFive if args.live else FakeFive)(args)
    (args.workspace/'run-directory.txt').write_text(str(stack.directory))
    print('RUN_DIRECTORY',stack.directory, flush=True)
    try:
        await stack.run()
    except BaseException as exc:
        stack.report.update(failure(exc,stack.env))
        stack.save_report()
        print('FAILURE',stack.report.get('error'), flush=True)
    finally:
        stack.save_report()
        await stack.close()
    print('RESULT',stack.report.get('result'),flush=True)
    return 1 if stack.report.get('result')=='FAIL' else 0
if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
