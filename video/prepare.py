"""Read-only extraction of genuine repository evidence and existing app captures."""
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MEDIA = HERE / 'public' / 'media'
MEDIA.mkdir(parents=True, exist_ok=True)
copies = {
    'integrate.png': 'var/public/integrate.png',
    'duplicate-first.png': 'var/dark/run-step-1.png',
    'duplicate-final.png': 'var/public/duplicates-expanded.png',
    'recovery.png': 'var/public/recovery.png',
    'conflict.png': 'var/public/conflict.png',
}
for target, source in copies.items():
    if not (MEDIA / target).exists():
        shutil.copy2(ROOT / source, MEDIA / target)

test = (ROOT / 'tests/test_integration_api.py').read_text()
test = test.split('def test_repeated_success_notifications_confirm_one_amount')[1].split('\n\ndef ')[0]
test = 'def test_repeated_success_notifications_confirm_one_amount' + test
commit = subprocess.check_output(['git', 'show', '-s', '--format=full', 'ac20202'], cwd=ROOT, text=True)
decisions = (ROOT / 'docs/decisions.md').read_text().split('## The integration API is absent without a key')[1].split('\n## ')[0]
scenes = [
    dict(type='problem',start=0,duration=20),
    dict(type='media',start=20,duration=8,title='The service behind the demonstration',label='ACTUAL APP · EXISTING CAPTURE',media='integrate.png',crop=[40,440],note='HISAAB — built as Refund Consistency · API contract documentation'),
    dict(type='architecture',start=28,duration=10),
    dict(type='media',start=38,duration=7,title='First delivery: both records show ₹300',label='ACTUAL APP · EXISTING CAPTURE',media='duplicate-first.png',crop=[150,150]),
    dict(type='media',start=45,duration=7,title='Second delivery: ₹600 / ₹300',label='ASSEMBLY · CAPTURE REQUIRED',missing='Real duplicate_success second-delivery capture. This card is not application evidence.'),
    dict(type='media',start=52,duration=8,title='Three deliveries. One protected amount.',label='ACTUAL APP · EXISTING CAPTURE',media='duplicate-final.png',crop=[180,455],note='Naive ₹900 · protected ₹300 · recorded amounts, not payouts'),
    dict(type='media',start=60,duration=7,title='Missing evidence can be recovered',label='SIMULATED PROVIDER LOOKUP',media='recovery.png',crop=[165,335],note='lookup_recovers_success · Reconcile at tick 3 · verify replacement recording'),
    dict(type='media',start=67,duration=7,title='Conflicting evidence stays under review',label='SIMULATED PROVIDER LOOKUP',media='conflict.png',crop=[190,345],note='lookup_conflict · Run to end, then Reconcile at tick 4'),
    dict(type='media',start=74,duration=16,title='Signed documented sample · local HTTP test',label='ASSEMBLY · CAPTURE REQUIRED',missing='Actual tools/hyperswitch_demo.py repeat output: unchanged 30000 paise and invalid-signature rejection. Not a live Hyperswitch delivery.'),
    dict(type='media',start=90,duration=7,title='Task · One bounded API task',label='ASSEMBLY · ALAN CAPTURE REQUIRED',missing='Real Alan conversation crop. Fallback narration: I broke the API work into small tasks.'),
    dict(type='evidence',start=97,duration=8,title='Test · Count the same success event once',label='GENUINE TEST SOURCE · NOT AN EXECUTION',text=test.strip(),fontSize=23),
    dict(type='evidence',start=105,duration=7,title='Commit · A traceable checkpoint',label='HISTORICAL COMMIT · ac20202',text='\n'.join(commit.splitlines()[:13])+'\n\nHistorical checkpoint; any checks in this commit describe that checkpoint.',fontSize=25),
    dict(type='close',start=112,duration=5),
]
edit = dict(voice=None,music=None,captions=[],scenes=scenes)
if not (HERE / 'edit.json').exists():
    (HERE / 'edit.json').write_text(json.dumps(edit,ensure_ascii=False,indent=2),encoding='utf-8')
manifest = dict(status='assembly; not submission-ready',fps=30,duration_seconds=117,assets=[dict(file=k,source=v,kind='authentic existing screenshot',status='present; verify content before final') for k,v in copies.items()],missing=['app-proof.mp4 (including 600/300 intermediate)','adapter-proof.mp4','alan-proof.mp4 or actual task still','voice.wav or voice.m4a','music track and any required credit','official organizer links'],evidence=dict(test_source='tests/test_integration_api.py::test_repeated_success_notifications_confirm_one_amount',test_executed=False,commit='ac2020244e58f14eb21168cdfb01d87ac54732f3'))
if not (HERE / 'asset-manifest.json').exists():
    (HERE / 'asset-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
plan=Path(r'C:\Users\seven\Downloads\HISAAB_9PM_Plan (1).md').read_text(encoding='utf-8')
script=plan.split('## Final narration')[1].split('## Exact screen direction')[0]
(HERE / 'SCRIPT.md').write_text('# HISAAB narration — recording draft\n\nPreserve usable voice paragraphs. No TTS. Remove the superseded implementation paragraph.\n\n'+script,encoding='utf-8')
(HERE / 'historical-commit.txt').write_text('\n'.join(x.rstrip() for x in commit.splitlines()).rstrip()+'\n',encoding='utf-8')
print('Prepared existing media, genuine source evidence and 117-second assembly.')
